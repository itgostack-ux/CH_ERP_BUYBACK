import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime, flt, nowdate, add_days, cint

from buyback.exceptions import BuybackStatusError
from buyback.utils import log_audit, validate_indian_phone


def _normalize_customer_approval_method(method: str | None) -> str:
    method = (method or "In-Store Signature").strip()
    aliases = {
        "OTP": "App Confirmation",
        "OTP Verification": "App Confirmation",
        "App OTP": "App Confirmation",
        "Approval Link": "SMS Link",
        "Token Link": "SMS Link",
    }
    method = aliases.get(method, method)
    allowed = {"In-Store Signature", "SMS Link", "App Confirmation"}
    if method not in allowed:
        frappe.throw(
            _("Invalid customer approval method: {0}").format(method),
            title=_("Buyback Order Error"),
        )
    return method


MANAGER_APPROVAL_ROLES = {"Buyback Manager", "Buyback Admin", "System Manager"}


def _require_manager_approval_role():
    """Server-side guard for manager approval actions.

    DocPerm write access is broader than the approval workflow. Keep the
    controller method aligned with the workflow role gate so API/Desk calls
    cannot approve just because the user can edit the document.
    """
    user = frappe.session.user
    if user == "Administrator":
        return
    roles = set(frappe.get_roles(user))
    if roles.intersection(MANAGER_APPROVAL_ROLES):
        return
    frappe.throw(
        _("Only a Buyback Manager can approve or reject a buyback order."),
        exc=frappe.PermissionError,
    )


class BuybackOrder(Document):
    def before_insert(self):
        """Validate uniqueness, assign sequential order_id, generate approval token."""
        self._check_duplicate_active_order()
        self._assign_order_id()
        self._set_status("Draft")
        if not self.approval_token:
            self.approval_token = frappe.generate_hash(length=32)

    def validate_workflow(self):
        """Skip Frappe's workflow transition re-validation on save.

        The buyback status machine is server-managed: every transition is
        enforced by explicit status gates in this controller and the API
        layer, and `workflow_state` is only a mirror of `status` for desk
        visibility (see _sync_workflow_state). Frappe would otherwise
        re-validate the mirror write as a user-driven transition and crash
        on legitimate server moves the desk workflow doesn't model — e.g.
        POS advances status to 'Awaiting Customer Approval' via db_set,
        then the customer's guest payout save syncs the mirror and dies
        with WorkflowPermissionError ('transition not allowed from
        Approved to Awaiting Customer Approval').

        Desk workflow buttons stay safe: apply_workflow() validates the
        transition (roles + condition) itself before saving.
        """
        return

    def _has_workflow_state_field(self):
        return bool(self.meta.has_field("workflow_state"))

    def _set_status(self, status):
        self.status = status
        if self._has_workflow_state_field():
            self.workflow_state = status

    def _status_update(self, status, **extra):
        updates = {"status": status}
        if self._has_workflow_state_field():
            updates["workflow_state"] = status
        updates.update(extra)
        return updates

    def _sync_workflow_state(self):
        """Keep workflow_state aligned when server code changes status directly."""
        if self.status and self._has_workflow_state_field() and self.workflow_state != self.status:
            self.workflow_state = self.status

    def _sync_approval_status_after_price_change(self):
        """Repair pending approval state when final_price crosses the threshold."""
        if self.docstatus != 1:
            return
        if self.status in ("Draft", "Awaiting Approval") and not cint(self.requires_approval):
            self._set_status("Approved")
        elif self.status == "Draft" and cint(self.requires_approval):
            self._set_status("Awaiting Approval")
        elif (
            self.status == "Approved"
            and cint(self.requires_approval)
            and (not self.approved_by or flt(self.approved_price) != flt(self.final_price))
        ):
            self.approved_by = None
            self.approved_price = 0
            self.approval_date = None
            self._set_status("Awaiting Approval")

    def _notify_manager_approval_required(self):
        try:
            from buyback.buyback.alerts import alert_manager_approval_required

            threshold = frappe.db.get_single_value(
                "Buyback Settings", "require_manager_approval_above"
            ) or 0
            alert_manager_approval_required(self.name, self.final_price, threshold)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                title=f"Manager approval alert failed for {self.name}",
            )

    def _check_duplicate_active_order(self):
        """Prevent creating a second active buyback order for the same serial number."""
        if not self.imei_serial:
            return
        existing = frappe.db.get_value(
            "Buyback Order",
            {
                "imei_serial": self.imei_serial,
                "docstatus": ["!=", 2],
                "status": ["not in", ("Cancelled", "Rejected", "Closed")],
            },
            "name",
        )
        if existing:
            frappe.throw(
                _("An active buyback order {0} already exists for serial/IMEI {1}. "
                  "Cancel it before creating a new one.").format(
                    frappe.bold(existing), frappe.bold(self.imei_serial)
                ),
                title=_("Duplicate Buyback Order"),
            )

    def _assign_order_id(self):
        """Assign a unique sequential order_id using a row-level lock to prevent races."""
        # FOR UPDATE on the same statement that reads the value — keeps the lock
        # until the INSERT commits, preventing two concurrent inserts from reading
        # the same MAX and colliding on order_id.
        last = frappe.db.sql(
            "SELECT MAX(order_id) FROM `tabBuyback Order` FOR UPDATE"
        )[0][0] or 0
        self.order_id = last + 1

    def _sync_serial_no_aliases(self):
        """Keep `serial_no` (new canonical) and `imei_serial` (legacy) in lock-step.

        - If only one side is populated, copy it to the other.
        - If both are populated and disagree, `serial_no` (new canonical) wins
          and overwrites `imei_serial` — newer code paths set this field.
        - Whitespace is stripped on both sides so trailing spaces never cause
          a mirror drift.

        Backward-compatible additive rename: existing call sites that read
        `self.imei_serial` continue to work unchanged because this method
        guarantees both fields hold the same value at save time.
        """
        new_val = (self.serial_no or "").strip() or None
        old_val = (self.imei_serial or "").strip() or None

        if new_val and old_val and new_val != old_val:
            # Disagreement → new field wins. Log so we can audit accidental
            # writes to the legacy field after rename.
            frappe.logger().info(
                f"Buyback Order {self.name or '<new>'}: serial_no/imei_serial "
                f"diverged ('{new_val}' vs '{old_val}') — keeping serial_no."
            )
            self.imei_serial = new_val
        elif new_val and not old_val:
            self.imei_serial = new_val
        elif old_val and not new_val:
            self.serial_no = old_val

    def validate(self):
        self._sync_serial_no_aliases()
        self._ensure_mobile_no()
        if self.mobile_no:
            self.mobile_no = validate_indian_phone(self.mobile_no, "Mobile No")
        self._update_customer_mobile()
        self._sync_customer_id()
        self._check_imei_blacklist()
        self._populate_item_hierarchy()
        self._link_assessment()
        self._calculate_price_variance()
        self._calculate_exchange_totals()
        self._calculate_payment_totals()
        self._validate_payment_rows()
        self._validate_paid_status_consistency()
        self._check_approval_requirement()
        self._validate_imei_check_before_kyc()
        self._validate_lock_clearance_before_kyc()
        self._validate_ownership_proof_threshold()
        self._validate_kyc_for_otp_stage()
        self._check_exchange_value_override()
        self._sync_workflow_state()

    def _sync_customer_id(self):
        """Populate ch_customer_id / ch_membership_id from Customer master."""
        if not self.customer or (self.ch_customer_id and self.ch_membership_id):
            return
        cust = frappe.db.get_value(
            "Customer", self.customer,
            ["ch_customer_id", "ch_membership_id"],
            as_dict=True,
        )
        if cust:
            if not self.ch_customer_id:
                self.ch_customer_id = cust.ch_customer_id
            if not self.ch_membership_id:
                self.ch_membership_id = cust.ch_membership_id

    def before_update_after_submit(self):
        self._check_approval_requirement()
        self._sync_approval_status_after_price_change()
        self._sync_workflow_state()
        self._calculate_payment_totals()
        self._validate_payment_rows()
        # Field updates on an already-submitted doc go through update_after_submit,
        # NOT validate() — Frappe's _validate() only calls run_method("validate")
        # when self._action == "save". By the time status reaches "Awaiting OTP"
        # the order is already submitted (on_submit fires at order creation), so
        # this is the hook that actually fires for send_otp()/customer_approve()
        # transitioning a submitted doc. Without it here, the gate added to
        # validate() is silently never enforced on the real-world path.
        self._validate_imei_check_before_kyc()
        self._validate_lock_clearance_before_kyc()
        self._validate_ownership_proof_threshold()

    def _check_imei_blacklist(self):
        if self.imei_serial:
            from buyback.buyback.doctype.buyback_imei_blacklist.buyback_imei_blacklist import check_imei_and_block
            check_imei_and_block(self.imei_serial)

    def _ensure_mobile_no(self):
        """Fallback chain: Assessment → Customer alternate/whatsapp phone."""
        if self.mobile_no:
            return
        # 1. Try Buyback Assessment
        if self.buyback_assessment:
            self.mobile_no = frappe.db.get_value(
                "Buyback Assessment", self.buyback_assessment, "mobile_no"
            )
        # 2. Try Customer alternate phone / whatsapp
        if not self.mobile_no and self.customer:
            cust = frappe.db.get_value(
                "Customer", self.customer,
                ["mobile_no", "ch_alternate_phone", "ch_whatsapp_number"],
                as_dict=True,
            )
            if cust:
                self.mobile_no = cust.mobile_no or cust.ch_alternate_phone or cust.ch_whatsapp_number

    def _update_customer_mobile(self):
        """Write mobile_no back to Customer if Customer has none."""
        if not self.mobile_no or not self.customer:
            return
        cust_mobile = frappe.db.get_value("Customer", self.customer, "mobile_no")
        if not cust_mobile:
            frappe.db.set_value("Customer", self.customer, "mobile_no", self.mobile_no)

    def _populate_item_hierarchy(self):
        """Auto-fill brand, item_name from Item if not set."""
        if not self.item:
            return
        if not self.brand or not self.item_name:
            item_data = frappe.db.get_value(
                "Item", self.item,
                ["brand", "item_name"],
                as_dict=True,
            )
            if item_data:
                if not self.brand:
                    self.brand = item_data.brand
                if not self.item_name:
                    self.item_name = item_data.item_name

    def _link_assessment(self):
        """Auto-link buyback assessment from inspection."""
        if self.buyback_assessment:
            return
        if self.buyback_inspection:
            assessment = frappe.db.get_value(
                "Buyback Inspection", self.buyback_inspection, "buyback_assessment"
            )
            if assessment:
                self.buyback_assessment = assessment

    def _calculate_price_variance(self):
        """Compute price variance between original assessment quote and inspection-revised price."""
        if self.buyback_inspection:
            insp = frappe.db.get_value(
                "Buyback Inspection", self.buyback_inspection,
                ["quoted_price", "revised_price"], as_dict=True
            )
            if insp:
                self.original_quoted_price = flt(insp.quoted_price)
                self.revised_inspection_price = flt(insp.revised_price) or flt(insp.quoted_price)
        elif self.buyback_assessment:
            self.original_quoted_price = flt(
                frappe.db.get_value("Buyback Assessment", self.buyback_assessment, "quoted_price")
            ) or flt(
                frappe.db.get_value("Buyback Assessment", self.buyback_assessment, "estimated_price")
            )
            self.revised_inspection_price = self.original_quoted_price

        if flt(self.original_quoted_price):
            self.price_variance = flt(self.revised_inspection_price) - flt(self.original_quoted_price)
            self.price_variance_pct = (
                self.price_variance / flt(self.original_quoted_price) * 100
            )
        else:
            self.price_variance = 0
            self.price_variance_pct = 0

    def _calculate_exchange_totals(self):
        """Calculate exchange discount and balance when settlement_type is Exchange."""
        if self.settlement_type != "Exchange":
            return
        self.exchange_discount = flt(self.final_price)
        self.balance_to_pay = max(flt(self.new_device_price) - flt(self.exchange_discount), 0)

    def on_update_after_submit(self):
        """Handle status transitions triggered by workflow on submitted docs.

        For the Buyback Order Workflow, this fires when the doc is already
        submitted (docstatus=1) and a workflow action changes state, e.g.
        Paid → Closed.  The "Confirm Payment" transition (docstatus 0→1)
        fires on_submit() instead — JE/SE creation is handled there too.
        """
        status = self.status or self.workflow_state

        # Auto-dispatch OTP whenever the doc enters "Awaiting OTP" via any
        # path (workflow Action button, direct status edit, etc.). The
        # workflow transition just sets the state field — it does NOT call
        # send_otp(), so without this hook the customer never receives the
        # code and staff have to click "Resend OTP". Dedupe via a transient
        # flag so the explicit send_otp() call (which sets status itself
        # before saving) doesn't trigger a second OTP here.
        if (
            status == "Awaiting OTP"
            and not self.otp_verified
            and not self.flags.get("otp_just_dispatched")
            and self.mobile_no
        ):
            try:
                self._dispatch_otp()
                self.flags.otp_just_dispatched = True
            except Exception:
                frappe.log_error(title=f"Auto OTP dispatch failed for {self.name}")

        previous = self.get_doc_before_save()
        previous_status = (getattr(previous, "status", None) or getattr(previous, "workflow_state", None) or "") if previous else ""

        if status == "Awaiting Approval" and previous_status != "Awaiting Approval":
            self._notify_manager_approval_required()

        # Safety net: create JE + SE only on the transition into Paid.
        # A later edit to an already-paid order (for example payout-preference
        # capture from the approval link) must not replay accounting.
        if (
            status == "Paid"
            and previous_status != "Paid"
            and not self.journal_entry
            and not self.stock_entry
        ):
            # Phase B — indemnity is mandatory before Paid.
            self._require_indemnity_before_paid()
            self._create_accounting_entries()

        # Update serial lifecycle on Paid (don't wait for Closed)
        if status == "Paid":
            self._update_serial_no_bought_back()
            self._ensure_exchange_order_exists()

        if status == "Closed":
            # ── Hard block: no replacement dispatch without finance (#13) ──
            self._block_close_without_finance()
            self._ensure_exchange_order_exists()
            # Award loyalty points (once)
            if not self.loyalty_points_earned:
                self._award_loyalty_points()
            # Update Customer activity summary & device photos
            self._update_customer_activity()
            # Mark Serial No as Bought Back
            self._update_serial_no_bought_back()

    def on_submit(self):
        """Submit order — JE/SE created at payment stage.

        The workflow "Confirm Payment" transitions from Ready to Pay (docstatus=0)
        to Paid (docstatus=1), which triggers on_submit (NOT on_update_after_submit).
        The workflow sets self.status = "Paid" BEFORE submit, so we check here.
        """
        if self.status == "Draft":
            new_status = "Awaiting Approval" if self.requires_approval else "Approved"
            self._set_status(new_status)
            # db_set is required: Frappe saves the doc before firing on_submit,
            # so in-memory changes to self.status are not persisted automatically.
            self.db_set(self._status_update(new_status), notify=True)
            if new_status == "Awaiting Approval":
                self._notify_manager_approval_required()

        log_audit("Order Created", "Buyback Order", self.name,
                  new_value={"final_price": self.final_price, "status": self.status})

        # Update Serial No status
        from buyback.serial_no_utils import update_serial_buyback_status
        if self.status == "Paid":
            # Workflow brought us directly to Paid — create accounting entries
            self._create_accounting_entries()
            self._ensure_exchange_order_exists()
            update_serial_buyback_status(
                self.imei_serial,
                status="Bought Back",
                order_name=self.name,
                customer=self.customer,
                comment=f"Buyback Order {self.name} paid — price ₹{self.final_price}",
            )
        else:
            update_serial_buyback_status(
                self.imei_serial,
                status="Under Inspection",
                order_name=self.name,
                customer=self.customer,
                comment=f"Buyback Order {self.name} submitted — price ₹{self.final_price}",
            )

    def on_cancel(self):
        """Reverse GL and Stock entries on cancellation."""
        self._cancel_linked_entry("Journal Entry", self.journal_entry)
        self._cancel_linked_entry("Stock Entry", self.stock_entry)
        self._set_status("Cancelled")
        if self.name:
            self.db_set(self._status_update("Cancelled"), notify=True)
        log_audit("Order Cancelled", "Buyback Order", self.name,
                  new_value={"status": "Cancelled"})

    def _ensure_exchange_order_exists(self):
        """Create Buyback Exchange Order once for Exchange settlements.

        This is intentionally idempotent and non-blocking for core payment/close
        flow: when mandatory exchange fields are missing, we log and return so
        staff can fix data without breaking accounting closure.
        """
        if self.settlement_type != "Exchange":
            return None

        existing = frappe.db.get_value(
            "Buyback Exchange Order",
            {"buyback_order": self.name, "docstatus": ["<", 2]},
            "name",
        )
        if existing:
            return existing

        missing = []
        for field in ("customer", "mobile_no", "store", "item", "new_item"):
            if not self.get(field):
                missing.append(field)
        if missing:
            frappe.log_error(
                title=_("Buyback Order {0}: Exchange creation skipped (missing fields)").format(self.name),
                message=", ".join(missing),
            )
            return None

        exchange = frappe.get_doc({
            "doctype": "Buyback Exchange Order",
            "buyback_order": self.name,
            "customer": self.customer,
            "mobile_no": self.mobile_no,
            "store": self.store,
            "old_item": self.item,
            "old_imei_serial": self.imei_serial,
            "old_condition_grade": self.condition_grade,
            "buyback_amount": flt(self.final_price),
            "new_item": self.new_item,
            "new_imei_serial": self.new_item_imei,
            "new_device_price": flt(self.new_device_price),
            "exchange_discount": 0,
        })
        exchange.insert(ignore_permissions=True)
        exchange.submit()

        log_audit(
            "Exchange Auto-Created",
            "Buyback Order",
            self.name,
            new_value={"exchange_order": exchange.name, "status": exchange.status},
        )
        return exchange.name

    def _cancel_linked_entry(self, doctype, name):
        """Cancel a linked JE or SE if it exists and is submitted."""
        if not name:
            return
        try:
            doc = frappe.get_doc(doctype, name)
            if doc.docstatus == 1:
                doc.cancel()
        except (frappe.DoesNotExistError, frappe.ValidationError, frappe.LinkExistsError):
            frappe.log_error(
                title=_("{0} {1} cancellation failed for {2}").format(doctype, name, self.name),
            )

    def _calculate_payment_totals(self):
        """Sum up all payment rows."""
        self.total_paid = sum(flt(p.amount) for p in (self.payments or []))
        if self.total_paid == 0:
            self.payment_status = "Unpaid"
        elif self.total_paid < flt(self.final_price):
            self.payment_status = "Partially Paid"
        elif self.total_paid == flt(self.final_price):
            self.payment_status = "Paid"
        else:
            self.payment_status = "Overpaid"

    def _validate_payment_rows(self):
        """Validate populated payment rows.

        Rows that are entirely blank (zero amount AND no method AND no
        reference) are silently dropped — they come from JS placeholder
        appends or interrupted prior runs and would otherwise block
        every subsequent save with a misleading "amount must be > 0".
        """
        kept = []
        for row in (self.payments or []):
            amount = flt(row.amount)
            method = (row.payment_method or "").strip()
            ref = (row.transaction_reference or "").strip()
            # Truly blank row → drop on the fly. mutating doc.payments
            # is safe before save persists the child table.
            if amount == 0 and not method and not ref:
                continue
            kept.append(row)

            if amount <= 0:
                frappe.throw(_("Payment row #{0}: amount must be greater than zero.").format(row.idx), title=_("Buyback Order Error"))
            if not method:
                frappe.throw(_("Payment row #{0}: payment method is required.").format(row.idx), title=_("Buyback Order Error"))
            if not row.payment_date:
                frappe.throw(_("Payment row #{0}: payment date is required.").format(row.idx), title=_("Buyback Order Error"))

            mode_type = (frappe.db.get_value("Mode of Payment", method, "type") or "").strip()
            requires_reference = mode_type != "Cash"
            if requires_reference and not ref:
                frappe.throw(
                    _("Payment row #{0}: transaction reference is required for {1}.").format(
                        row.idx, frappe.bold(method)
                    )
                )

        # Re-index the surviving rows so idx stays contiguous; only
        # reassign when we actually dropped something to avoid touching
        # the doc unnecessarily.
        if len(kept) != len(self.payments or []):
            self.payments = kept
            for i, row in enumerate(self.payments, start=1):
                row.idx = i

    def _validate_paid_status_consistency(self):
        if self.payment_status == "Overpaid":
            frappe.throw(
                _("Total payments cannot exceed final price. Final price: ₹{0}, paid: ₹{1}.").format(
                    flt(self.final_price), flt(self.total_paid)
                )
            )

        if self.status in ("Paid", "Closed") and self.payment_status != "Paid":
            frappe.throw(
                _("Order cannot be {0} while payment status is {1}.").format(
                    frappe.bold(self.status), frappe.bold(self.payment_status)
                )
            )

    def _check_approval_requirement(self):
        """Set requires_approval flag based on settings."""
        threshold = frappe.db.get_single_value(
            "Buyback Settings", "require_manager_approval_above"
        ) or 0
        self.requires_approval = 1 if flt(self.final_price) > flt(threshold) else 0

    def _check_exchange_value_override(self):
        """Log exception when buyback value is overridden from original assessment (#2).

        Detects when final_price differs from the original quoted price by more
        than 10% and creates an Exchange Value Override exception for audit.
        """
        if not flt(self.original_quoted_price) or not flt(self.final_price):
            return
        variance_pct = abs(flt(self.price_variance_pct))
        if variance_pct <= 10:
            return  # Within acceptable tolerance

        try:
            if not frappe.db.exists("CH Exception Type", "Exchange Value Override"):
                return
            from ch_item_master.ch_item_master.exception_api import raise_exception
            raise_exception(
                exception_type="Exchange Value Override",
                company=self.company,
                reason=(
                    f"Buyback value overridden from ₹{self.original_quoted_price} "
                    f"to ₹{self.final_price} ({self.price_variance_pct:+.1f}%)"
                ),
                requested_value=abs(flt(self.price_variance)),
                original_value=flt(self.original_quoted_price),
                reference_doctype="Buyback Order",
                reference_name=self.name,
                item_code=self.item,
                serial_no=self.imei_serial,
                store_warehouse=self.store,
                customer=self.customer,
            )
        except Exception:
            # This audit exception is best-effort only and must never block
            # Buyback Order save.
            frappe.log_error(
                frappe.get_traceback(),
                title="Exchange Value Override exception creation failed",
            )

    def approve(self, remarks=None):
        """Manager approves the order."""
        _require_manager_approval_role()
        if self.status != "Awaiting Approval":
            frappe.throw(
                _("Can only approve orders in 'Awaiting Approval' status."),
                exc=BuybackStatusError,
            )
        self._set_status("Approved")
        self.approved_by = frappe.session.user
        self.approved_price = self.final_price
        self.approval_date = now_datetime()
        if remarks:
            self.approval_remarks = remarks
        self.save()
        log_audit("Order Approved", "Buyback Order", self.name,
                  new_value={"approved_by": self.approved_by, "approved_price": self.approved_price})

    def reject(self, remarks=None):
        """Manager rejects the order."""
        _require_manager_approval_role()
        if self.status != "Awaiting Approval":
            frappe.throw(
                _("Can only reject orders in 'Awaiting Approval' status."),
                exc=BuybackStatusError,
            )
        self._set_status("Rejected")
        self.approved_by = frappe.session.user
        self.approval_date = now_datetime()
        if remarks:
            self.approval_remarks = remarks
        self.save()
        log_audit("Order Rejected", "Buyback Order", self.name,
                  new_value={"rejected_by": frappe.session.user, "reason": remarks})

    def customer_approve(self, method="In-Store Signature"):
        """Customer approves the revised/final price.

        This is required when the inspection price differs from the
        original quoted price.  The customer must confirm they accept
        the new offer before proceeding.
        """
        allowed = ("Approved", "Awaiting Customer Approval")
        if self.status not in allowed:
            frappe.throw(
                _("Customer approval is only applicable in {0} status.").format(
                    " or ".join(allowed)
                ),
                exc=BuybackStatusError,
            )
        method = _normalize_customer_approval_method(method)
        self.customer_approved = 1
        self.customer_approved_at = now_datetime()
        self.customer_approval_method = method
        self._set_status("Customer Approved")
        self.save()
        log_audit("Customer Approved", "Buyback Order", self.name,
                  new_value={"method": method, "final_price": self.final_price})

    def select_settlement_type(self, settlement_type, new_item=None, new_device_price=None):
        """Set buyback vs exchange settlement.

        Args:
            settlement_type: "Buyback" or "Exchange"
            new_item: Item code for new device (required if Exchange)
            new_device_price: Price of new device (required if Exchange)
        """
        if settlement_type not in ("Buyback", "Exchange"):
            frappe.throw(_("Invalid settlement type: {0}").format(settlement_type), title=_("Buyback Order Error"))

        self.settlement_type = settlement_type
        if settlement_type == "Exchange":
            if not new_item:
                frappe.throw(_("New device item is required for exchange."), title=_("Buyback Order Error"))
            self.new_item = new_item
            if new_device_price is not None:
                self.new_device_price = flt(new_device_price)
            else:
                # Fetch standard selling price from Item
                price = frappe.db.get_value("Item Price", {
                    "item_code": new_item,
                    "selling": 1,
                    "price_list": frappe.db.get_single_value("Selling Settings", "selling_price_list"),
                }, "price_list_rate")
                self.new_device_price = flt(price)

        self.save()
        log_audit("Settlement Type Changed", "Buyback Order", self.name,
                  new_value={"settlement_type": settlement_type, "new_item": new_item})

    def send_otp(self):
        """Send OTP for customer verification.

        Allowed states:
          • "Approved" / "Awaiting OTP" — staff-driven flow (manager approved
            the order, now requesting customer's OTP confirmation).
          • "Awaiting Customer Approval" — customer-portal flow where the
            customer themselves approve the buyback by entering the OTP
            (the OTP IS the approval, not a step after a separate approval).
        """
        if self.status not in ("Approved", "Awaiting OTP", "Awaiting Customer Approval"):
            frappe.throw(
                _("OTP can only be sent once the order has reached customer approval. "
                  "Current status: {0}.").format(self.status),
                exc=BuybackStatusError,
            )

        # Issue #3: customer may have no phone number at all.
        # Provide a clear message so staff uses In-Store Signature instead.
        if not self.mobile_no:
            frappe.throw(
                _("No mobile number on record for this customer. "
                  "Use \"Customer Approve (In-Store)\" to collect a physical signature instead."),
                title=_("Mobile Number Required"),
                exc=BuybackStatusError,
            )

        # Check BEFORE dispatching — _dispatch_otp() sends a real WhatsApp/
        # email message. Without this, the gate inside save()/validate()
        # would still block the status transition, but only AFTER a live
        # OTP was already sent to the customer for a transaction that
        # can't proceed.
        self._require_pre_otp_gates_clear(_("OTP can be sent"))

        otp_code = self._dispatch_otp()
        self.flags.otp_just_dispatched = True
        self._set_status("Awaiting OTP")
        self.save()
        return otp_code

    def _dispatch_otp(self):
        """Generate an OTP and deliver via WhatsApp + Email. No save() side effects.

        Used both by the explicit send_otp() flow and by the workflow auto-send
        path in on_update_after_submit() so the OTP gets dispatched whether
        staff click "Send OTP" in the form, the workflow Action button, or any
        other code path that simply moves the doc into 'Awaiting OTP'.
        """
        from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog

        if not self.mobile_no:
            return None

        otp_code = CHOTPLog.generate_otp(
            self.mobile_no,
            "Buyback Confirmation",
            reference_doctype="Buyback Order",
            reference_name=self.name,
        )
        log_audit("OTP Sent", "Buyback Order", self.name)

        # Deliver OTP across all channels — SMS, WhatsApp and Email. Failures
        # are logged, not raised, so a delivery hiccup never blocks the workflow.
        try:
            from buyback.buyback.whatsapp_notifications import send_otp as _send_otp
            customer_email = ""
            if self.customer:
                customer_email = frappe.db.get_value("Customer", self.customer, "email_id") or ""
            _send_otp(self.mobile_no, otp_code, "Buyback Confirmation",
                      ref_doctype="Buyback Order", ref_name=self.name,
                      email=customer_email or None)
        except Exception:
            frappe.log_error(title="OTP delivery failed")

        return otp_code

    def verify_otp(self, otp_code):
        """Verify customer OTP."""
        from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog

        # Idempotent: if already verified, treat retry as success (Oracle/Stripe pattern).
        if self.status == "OTP Verified" or self.otp_verified:
            return {
                "valid": True,
                "already_verified": True,
                "message": _("OTP already verified."),
            }

        if self.status != "Awaiting OTP":
            frappe.throw(
                _("OTP verification only applicable in 'Awaiting OTP' status."),
                exc=BuybackStatusError,
            )

        result = CHOTPLog.verify_otp(
            self.mobile_no,
            "Buyback Confirmation",
            otp_code,
            reference_doctype="Buyback Order",
            reference_name=self.name,
        )

        if result["valid"]:
            verified_at = now_datetime()
            approval_method = _normalize_customer_approval_method("OTP")
            updates = {
                "otp_verified": 1,
                "otp_verified_at": verified_at,
                # Successful OTP verification IS customer approval — the customer
                # received the OTP on their registered mobile and entered it back,
                # which is a stronger consent than an in-store signature. Without
                # this, settlement is blocked because customer_approved stays 0.
                "customer_approved": 1,
                "customer_approved_at": verified_at,
                "customer_approval_method": approval_method,
                **self._status_update("OTP Verified"),
            }

            # OTP verification is often done after submit from customer flows.
            # Use db_set for submitted docs to avoid update-after-submit errors.
            if self.docstatus == 1:
                self.db_set(updates, update_modified=True)
                self.reload()
            else:
                self.otp_verified = 1
                self.otp_verified_at = verified_at
                self.customer_approved = 1
                self.customer_approved_at = verified_at
                self.customer_approval_method = approval_method
                self._set_status("OTP Verified")
                self.save()

            log_audit("OTP Verified", "Buyback Order", self.name)

        return result

    @frappe.whitelist()
    def bypass_otp_instore(self, remarks=None):
        """Skip OTP verification for in-store approvals.

        Used when the customer is physically present and verbal/signature
        approval is sufficient, or when no mobile number is available.
        Logs an audit trail with the staff member's remarks.
        """
        if self.status not in ("Awaiting OTP", "Approved"):
            frappe.throw(
                _("OTP bypass is only applicable when status is 'Awaiting OTP' or 'Approved'."),
                exc=BuybackStatusError,
            )
        self._require_pre_otp_gates_clear(_("OTP can be bypassed"))

        verified_at = now_datetime()
        updates = {
            "otp_verified": 1,
            "otp_verified_at": verified_at,
            "customer_approved": 1,
            "customer_approved_at": verified_at,
            **self._status_update("OTP Verified"),
        }

        if self.docstatus == 1:
            self.db_set(updates, update_modified=True)
            self.reload()
        else:
            self.otp_verified = 1
            self.otp_verified_at = verified_at
            self.customer_approved = 1
            self.customer_approved_at = verified_at
            self._set_status("OTP Verified")
            self.save()

        audit_note = f"In-Store OTP Bypass — {remarks}" if remarks else "In-Store OTP Bypass (no OTP)"
        log_audit(audit_note, "Buyback Order", self.name)
        return {"success": True}

    def mark_ready_to_pay(self):
        """Move to payment stage."""
        if self.status != "OTP Verified":
            frappe.throw(
                _("Must verify OTP before proceeding to payment."),
                exc=BuybackStatusError,
            )
        self._set_status("Ready to Pay")
        self.save()

    def mark_paid(self):
        """Mark as fully paid."""
        if self.payment_status != "Paid":
            frappe.throw(
                _("Total paid must equal final price."),
                exc=BuybackStatusError,
            )
        self._set_status("Paid")
        self.save()
        log_audit("Payment Made", "Buyback Order", self.name,
                  new_value={"total_paid": self.total_paid})

    def close(self):
        """Close the order after payment.

        Market-standard closure gate (SAP RA / Oracle EBS / MS Dynamics):
          1. Order must be in 'Paid' status.
          2. Journal Entry + Stock Entry must exist (finance posted).
          3. For bank-mode payouts, either the linked Bank Payment Request
             must be in a terminal state (Payment Entry generated) or the
             corresponding Payment Entry must be present.
        Any missing link raises an exception request via _block_close_without_finance
        for audit-trail parity with HRMS/India Compliance approval flows.
        """
        if self.status != "Paid":
            frappe.throw(
                _("Can only close paid orders."),
                exc=BuybackStatusError,
            )

        # Hard block: JE + SE must exist before we close the lifecycle.
        # This method already logs a CH Exception Request and raises BuybackStatusError.
        self._block_close_without_finance()

        # For bank-based payouts, require the money leg to have settled.
        _pmode = (self.get("customer_payout_mode") or "").lower()
        if any(x in _pmode for x in ("bank", "transfer", "neft", "imps", "rtgs", "upi")):
            bpr = self.get("custom_bank_payment_request")
            has_pe = False
            if bpr:
                bpr_row = frappe.db.get_value(
                    "Bank Payment Request",
                    bpr,
                    ["docstatus", "payment_status", "payment_entry"],
                    as_dict=True,
                )
                if bpr_row:
                    # Terminal states used by ch_payments.bank_payments
                    # ("Processed"/"Reconciled") — or a Payment Entry link.
                    has_pe = bool(bpr_row.get("payment_entry")) or (
                        bpr_row.get("payment_status") in ("Processed", "Reconciled")
                        and bpr_row.get("docstatus") == 1
                    )
            if not has_pe:
                frappe.throw(
                    _(
                        "Cannot close Buyback Order {0}: bank payout has not "
                        "settled. Bank Payment Request must be Processed / "
                        "Reconciled with a Payment Entry before closing."
                    ).format(frappe.bold(self.name)),
                    exc=BuybackStatusError,
                    title=_("Bank Payout Not Settled"),
                )

        self._set_status("Closed")
        self.save()
        log_audit("Order Closed", "Buyback Order", self.name)

    def _block_close_without_finance(self):
        """Hard block: cannot close/dispatch without finance posted (#13).

        For exchange orders the replacement device dispatch happens at close,
        so accounting entries MUST exist before that point.

        Both cash-mode and bank-mode payouts now post a Journal Entry at
        Paid — cash-mode as a direct Dr Buyback Expense / Cr Cash entry,
        bank-mode as an accrual JE (Dr Buyback Expense / Cr Debtors[Customer])
        that is later netted by the Payment Entry created off the BPR
        settlement. So the JE + SE gate applies uniformly. The additional
        bank-mode settlement gate is enforced separately in ``close()``.
        """
        missing = []
        if not self.journal_entry:
            missing.append(_("Journal Entry"))
        if not self.stock_entry:
            missing.append(_("Stock Entry"))

        if not missing:
            return

        # Create exception request for audit trail
        try:
            if frappe.db.exists("CH Exception Type", "Replacement Without Finance"):
                from ch_item_master.ch_item_master.exception_api import raise_exception
                raise_exception(
                    exception_type="Replacement Without Finance",
                    company=self.company,
                    reason=f"Closing order {self.name} without {', '.join(missing)}",
                    requested_value=flt(self.final_price),
                    original_value=0,
                    reference_doctype="Buyback Order",
                    reference_name=self.name,
                    item_code=self.item,
                    serial_no=self.imei_serial,
                    store_warehouse=self.store,
                    customer=self.customer,
                )
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                title="Replacement Without Finance exception creation failed",
            )

        frappe.throw(
            _("Cannot close Buyback Order {0} — missing: {1}.<br><br>"
              "Finance entries must be created before closing/dispatching "
              "the replacement device.").format(
                frappe.bold(self.name),
                ", ".join(missing),
            ),
            title=_("Finance Closure Required"),
            exc=BuybackStatusError,
        )

    def _require_indemnity_before_paid(self):
        """Phase B — market-standard indemnity/NOC gate before payout.

        Cashify, Samsung Exchange, Best Buy Trade-In and Apple Trade In all
        require a signed customer declaration of ownership + consent to
        transfer before releasing money. We enforce it on the Paid transition
        to keep POS + kiosk flows unblocked earlier in the lifecycle.
        """
        # Skip when Buyback Settings toggles the gate off (pilot rollouts).
        gate_on = frappe.db.get_single_value(
            "Buyback Settings", "require_indemnity_before_paid"
        )
        # Default ON when the setting is absent / unset — safer default.
        if gate_on is not None and not int(gate_on or 0):
            return
        if not self.get("indemnity_signed"):
            frappe.throw(
                _(
                    "Cannot mark Buyback Order {0} Paid: customer indemnity / "
                    "NOC has not been captured. Use 'Record Indemnity' on the "
                    "order form (or the kiosk consent flow) first."
                ).format(frappe.bold(self.name)),
                exc=BuybackStatusError,
                title=_("Indemnity Required"),
            )

    def _validate_imei_check_before_kyc(self):
        """Require a completed Sanchar Saathi (CEIR) IMEI check before customer-facing stages.

        This is a different signal from `_check_imei_blacklist()` — that only
        catches devices OUR own stores have flagged before. The national CEIR
        registry catches a device reported lost/stolen anywhere in India, on
        its very first attempt at any store. Sanchar Saathi has no public API,
        so staff must log into ceir.sancharsaathi.gov.in themselves, look up
        the IMEI, and upload a screenshot of the result via
        `submit_imei_validation()` before the order can move toward customer
        approval, KYC collection, or OTP.

        Runs in validate() — fires on the transition INTO any gated status,
        same idempotent "only check on transition" pattern as
        `_validate_kyc_for_otp_stage()`.
        """
        gated_statuses = (
            "Awaiting Customer Approval", "Customer Approved", "Awaiting OTP",
            "OTP Verified", "Ready to Pay", "Paid", "Closed",
        )
        status = self.status or self.workflow_state
        if status not in gated_statuses:
            return
        prev_status = None
        if hasattr(self, "_doc_before_save") and self._doc_before_save:
            prev_status = self._doc_before_save.status
        if prev_status in gated_statuses:
            return  # already past this gate

        if self.imei_validation_status != "Verified Clean" or not self.imei_validation_screenshot:
            frappe.throw(
                _("Sanchar Saathi IMEI validation must be completed (checked at "
                  "ceir.sancharsaathi.gov.in, status = 'Verified Clean', with "
                  "screenshot uploaded) before customer approval, KYC, or OTP "
                  "can proceed. Current validation status: {0}.").format(
                    self.imei_validation_status or "Pending"
                ),
                exc=BuybackStatusError,
            )

    def _validate_ownership_proof_threshold(self):
        """Require purchase/ownership proof above a configurable price threshold.

        KYC proves who the seller is, not that they own this specific device.
        A clean IMEI + verified ID doesn't rule out a borrowed/unreported-stolen
        phone. Below the threshold this is optional (most second-hand phones
        won't have an original invoice); above it, staff must either attach a
        document or record why one isn't available.
        """
        gated_statuses = (
            "Awaiting Customer Approval", "Customer Approved", "Awaiting OTP",
            "OTP Verified", "Ready to Pay", "Paid", "Closed",
        )
        status = self.status or self.workflow_state
        if status not in gated_statuses:
            return
        prev_status = None
        if hasattr(self, "_doc_before_save") and self._doc_before_save:
            prev_status = self._doc_before_save.status
        if prev_status in gated_statuses:
            return  # already past this gate

        threshold = flt(frappe.db.get_single_value("Buyback Settings", "require_ownership_proof_above"))
        if threshold <= 0 or flt(self.final_price) <= threshold:
            return

        if not self.ownership_proof_type:
            frappe.throw(
                _("Ownership/purchase proof is required for buybacks above ₹{0} "
                  "(this device: ₹{1}). Attach a Purchase Invoice/Original Box-Bill/"
                  "Insurance Document, or select 'Not Available' with a reason.").format(
                    threshold, flt(self.final_price)
                ),
                exc=BuybackStatusError,
            )
        if self.ownership_proof_type == "Not Available" and not (self.ownership_proof_remarks or "").strip():
            frappe.throw(
                _("Please explain why ownership proof is not available."),
                exc=BuybackStatusError,
            )
        if self.ownership_proof_type != "Not Available" and not self.ownership_proof_document:
            frappe.throw(
                _("Please attach the {0} document, or select 'Not Available' with a reason.").format(
                    self.ownership_proof_type
                ),
                exc=BuybackStatusError,
            )

    def _validate_lock_clearance_before_kyc(self):
        """Require FRP/iCloud account-lock clearance before customer-facing stages.

        Normally set on `Buyback Inspection` and carried forward here when an
        inspection exists (see `pos_complete_inspection`/`create_order`). The
        walk-in path (`pos_start_buyback_order` direct from a Buyback
        Assessment with no Inspection record) never goes through Inspection
        at all, so this field/gate also exists directly on Buyback Order —
        otherwise a walk-in buyback could pay for a device still locked to
        the previous owner's account, making it unsellable.
        """
        gated_statuses = (
            "Awaiting Customer Approval", "Customer Approved", "Awaiting OTP",
            "OTP Verified", "Ready to Pay", "Paid", "Closed",
        )
        status = self.status or self.workflow_state
        if status not in gated_statuses:
            return
        prev_status = None
        if hasattr(self, "_doc_before_save") and self._doc_before_save:
            prev_status = self._doc_before_save.status
        if prev_status in gated_statuses:
            return  # already past this gate

        if not self.account_lock_cleared:
            frappe.throw(
                _("Confirm 'FRP / iCloud Lock Cleared' before customer approval, KYC, or OTP "
                  "can proceed — a device still signed into the previous owner's account "
                  "cannot be resold."),
                exc=BuybackStatusError,
            )

    def _require_pre_otp_gates_clear(self, action: str):
        """Hard guard called BEFORE any side-effecting action (e.g. dispatching
        a real OTP) that the validate()/before_update_after_submit() gates
        would otherwise only catch AFTER the side effect already happened.

        Also covers `bypass_otp_instore()`'s db_set() path for already-
        submitted orders, which skips validate() entirely — without this
        explicit check, a stolen-device gate could be bypassed via that
        shortcut. Checks the IMEI gate, lock-clearance, and the
        ownership-proof threshold so none can be bypassed via the same
        shortcuts.
        """
        if self.imei_validation_status != "Verified Clean" or not self.imei_validation_screenshot:
            frappe.throw(
                _("Sanchar Saathi IMEI validation must be completed and "
                  "verified clean (with screenshot) before {0}.").format(action),
                exc=BuybackStatusError,
            )

        if not self.account_lock_cleared:
            frappe.throw(
                _("Confirm 'FRP / iCloud Lock Cleared' before {0}.").format(action),
                exc=BuybackStatusError,
            )

        threshold = flt(frappe.db.get_single_value("Buyback Settings", "require_ownership_proof_above"))
        if threshold > 0 and flt(self.final_price) > threshold:
            if not self.ownership_proof_type:
                frappe.throw(
                    _("Ownership/purchase proof is required for buybacks above ₹{0} "
                      "before {1}.").format(threshold, action),
                    exc=BuybackStatusError,
                )
            if self.ownership_proof_type == "Not Available" and not (self.ownership_proof_remarks or "").strip():
                frappe.throw(
                    _("Please explain why ownership proof is not available, before {0}.").format(action),
                    exc=BuybackStatusError,
                )
            if self.ownership_proof_type != "Not Available" and not self.ownership_proof_document:
                frappe.throw(
                    _("Please attach the {0} document before {1}, or select 'Not Available' with a reason.").format(
                        self.ownership_proof_type, action
                    ),
                    exc=BuybackStatusError,
                )

    @frappe.whitelist()
    def submit_imei_validation(self, status: str, screenshot: str | None = None, remarks: str | None = None) -> dict:
        """Record the manual Sanchar Saathi (CEIR) IMEI check result.

        Staff perform the lookup themselves on ceir.sancharsaathi.gov.in
        (dial *#06# for IMEI, or SMS "KYM <imei>" to 14422) since there is
        no public API to call automatically, then report the result here
        with a screenshot as proof.

        status: "Verified Clean" | "Blacklisted" | "Duplicate IMEI" |
                "Already In Use" | "Could Not Verify"
        Only "Verified Clean" unlocks customer approval/KYC/OTP. The three
        "bad" outcomes auto-reject the order outright — a device reported
        lost/stolen nationally must not proceed any further. "Could Not
        Verify" leaves the order blocked at its current stage for retry
        (e.g. portal was down) without rejecting it.
        """
        allowed = {"Verified Clean", "Blacklisted", "Duplicate IMEI", "Already In Use", "Could Not Verify"}
        status = (status or "").strip()
        if status not in allowed:
            frappe.throw(
                _("Invalid validation status: {0}. Allowed: {1}").format(
                    status, ", ".join(sorted(allowed))
                ),
                exc=BuybackStatusError,
            )

        bad_outcomes = {"Blacklisted", "Duplicate IMEI", "Already In Use"}
        if status in bad_outcomes or status == "Verified Clean":
            if not screenshot:
                frappe.throw(
                    _("A screenshot of the Sanchar Saathi result is required for status {0}.").format(status),
                    exc=BuybackStatusError,
                )
        elif status == "Could Not Verify" and not (remarks or "").strip():
            frappe.throw(
                _("Remarks are required when the portal could not be checked (e.g. portal down, no response)."),
                exc=BuybackStatusError,
            )

        checked_at = now_datetime()
        checked_by = frappe.session.user
        updates = {
            "imei_validation_status": status,
            "imei_validation_checked_by": checked_by,
            "imei_validation_checked_at": checked_at,
        }
        if screenshot:
            updates["imei_validation_screenshot"] = screenshot
        if remarks is not None:
            updates["imei_validation_remarks"] = remarks

        if self.docstatus == 1:
            self.db_set(updates, update_modified=True)
            self.reload()
        else:
            self.update(updates)
            self.save()

        audit_action = "IMEI Validation Completed" if status == "Verified Clean" else "IMEI Validation Failed"
        log_audit(audit_action, "Buyback Order", self.name,
                  new_value={"status": status, "checked_by": checked_by})

        blocked = status != "Verified Clean"
        result = {
            "name": self.name,
            "imei_validation_status": status,
            "blocked": blocked,
            "order_status": self.status,
        }

        if status in bad_outcomes:
            # Definitive national-registry hit — reject outright, do not
            # leave the order sitting in limbo for staff to "fix" later.
            self.db_set(self._status_update("Rejected"), update_modified=True)
            self.db_set(
                "approval_remarks",
                f"Auto-rejected: Sanchar Saathi IMEI check = {status}"
                + (f" — {remarks}" if remarks else ""),
                update_modified=False,
            )
            self.reload()
            log_audit("Order Rejected", "Buyback Order", self.name,
                      new_value={"reason": f"IMEI {status} on Sanchar Saathi"})
            result["order_status"] = self.status
            result["message"] = _(
                "Device flagged as '{0}' on the Sanchar Saathi national registry. "
                "This order has been rejected and cannot proceed."
            ).format(status)
        elif status == "Could Not Verify":
            result["message"] = _(
                "Could not verify on Sanchar Saathi. Please retry the check before proceeding."
            )
        else:
            result["message"] = _("IMEI verified clean. You may proceed with customer approval / KYC / OTP.")

        return result

    def _validate_kyc_for_otp_stage(self):
        """Ensure mandatory KYC fields and device photos are filled before OTP stage.

        This runs in validate() — triggered by both workflow and API saves.
        Only enforced when status moves to Awaiting OTP or beyond.
        """
        otp_and_beyond = ("Awaiting OTP", "OTP Verified", "Ready to Pay", "Paid", "Closed")
        status = self.status or self.workflow_state
        if status not in otp_and_beyond:
            return
        # Only check on transition (not every re-save of a Paid/Closed order)
        prev_status = None
        if hasattr(self, "_doc_before_save") and self._doc_before_save:
            prev_status = self._doc_before_save.status
        if prev_status in otp_and_beyond:
            return  # already past this gate

        missing = []
        if not self.customer_photo:
            missing.append(_("Customer Photo"))
        if not self.customer_id_type:
            missing.append(_("ID Proof Type"))
        if not self.customer_id_number:
            missing.append(_("ID Number"))
        if not self.customer_id_front:
            missing.append(_("ID Front Image"))
        if not self.device_photo_front:
            missing.append(_("Device Front Photo"))
        if not self.device_photo_back:
            missing.append(_("Device Back Photo"))
        if missing:
            frappe.throw(
                _("The following are mandatory before sending OTP: {0}").format(
                    ", ".join(missing)
                ),
                exc=BuybackStatusError,
            )

    def verify_kyc(self):
        """Mark KYC as verified by the current user and sync to Customer."""
        if not self.customer_id_type or not self.customer_id_number:
            frappe.throw(
                _("ID proof type and number are required for KYC verification."),
                exc=BuybackStatusError,
            )
        if not self.customer_photo:
            frappe.throw(
                _("Customer photo is required for KYC verification."),
                exc=BuybackStatusError,
            )
        if not self.customer_id_front:
            frappe.throw(
                _("ID front image is required for KYC verification."),
                exc=BuybackStatusError,
            )
        self.kyc_verified = 1
        self.kyc_verified_by = frappe.session.user
        self.kyc_verified_at = now_datetime()
        self.save()
        # Sync KYC data to Customer master
        self._sync_kyc_to_customer()
        log_audit("KYC Verified", "Buyback Order", self.name,
                  new_value={"id_type": self.customer_id_type, "verified_by": self.kyc_verified_by})

    def _sync_kyc_to_customer(self):
        """Copy KYC data and photos from this order to the Customer record."""
        if not self.customer:
            return
        try:
            update = {
                "ch_customer_photo": self.customer_photo,
                "ch_customer_photo_source": f"Buyback Order {self.name}",
                "ch_id_type": self.customer_id_type,
                "ch_id_number": self.customer_id_number,
                "ch_id_front_image": self.customer_id_front,
                "ch_id_back_image": self.customer_id_back,
                "ch_kyc_verified": 1,
                "ch_kyc_verified_by": self.kyc_verified_by,
                "ch_kyc_verified_on": nowdate(),
                "ch_kyc_source_order": self.name,
            }
            # Extract Aadhaar number specifically if ID type is Aadhar
            if self.customer_id_type == "Aadhar Card":
                update["ch_aadhaar_number"] = self.customer_id_number

            # Increment total verifications
            cur_count = cint(frappe.db.get_value(
                "Customer", self.customer, "ch_total_kyc_verifications"
            ))
            update["ch_total_kyc_verifications"] = cur_count + 1

            frappe.db.set_value("Customer", self.customer, update, update_modified=False)
        except (frappe.DoesNotExistError, frappe.ValidationError):
            frappe.log_error(
                title=f"KYC Sync Error: {self.name} → {self.customer}",
            )

    def _update_customer_activity(self):
        """Update Customer profile after this order is closed.

        Syncs: total buyback count, loyalty balance, device photos,
        last visit, store visit log.
        """
        if not self.customer:
            return
        try:
            # Count total buyback orders for this customer
            total_buybacks = frappe.db.count(
                "Buyback Order", {"customer": self.customer, "docstatus": 1}
            )

            # Loyalty balance from Loyalty Point Entries
            loyalty_balance = 0
            lp_result = frappe.db.sql(
                """SELECT IFNULL(SUM(loyalty_points), 0) FROM `tabLoyalty Point Entry`
                WHERE customer = %s AND expiry_date >= CURDATE()""",
                self.customer,
            )
            if lp_result:
                loyalty_balance = cint(lp_result[0][0])

            update = {
                "ch_total_buybacks": cint(total_buybacks),
                "ch_loyalty_points_balance": loyalty_balance,
                "ch_last_visit_date": nowdate(),
            }
            if self.store:
                store_name = frappe.db.get_value(
                    "Warehouse", self.store, "warehouse_name"
                ) or self.store
                update["ch_last_visit_store"] = store_name

            # Sync device photos from this order to Customer
            if self.device_photo_front:
                update["ch_device_photo_front"] = self.device_photo_front
            if self.device_photo_back:
                update["ch_device_photo_back"] = self.device_photo_back
            if self.device_photo_screen:
                update["ch_device_photo_screen"] = self.device_photo_screen
            if self.device_photo_imei:
                update["ch_device_photo_imei"] = self.device_photo_imei
            if any(self.get(f) for f in ("device_photo_front", "device_photo_back",
                                         "device_photo_screen", "device_photo_imei")):
                update["ch_device_photo_source"] = f"Buyback Order {self.name}"

            frappe.db.set_value("Customer", self.customer, update, update_modified=False)

            # Log store visit via ch_item_master hooks (if available)
            try:
                from ch_item_master.ch_customer_master.hooks import _log_store_visit
                _log_store_visit(
                    customer=self.customer,
                    company=self.company,
                    visit_type="Buyback",
                    reference_doctype="Buyback Order",
                    reference_name=self.name,
                    store=self.store,
                    staff=frappe.session.user,
                )
            except (ImportError, AttributeError):
                pass  # ch_item_master not installed or hook missing — non-critical
        except (frappe.DoesNotExistError, frappe.ValidationError):
            frappe.log_error(
                title=f"Customer Activity Update Error: {self.name}",
            )

    # def _create_accounting_entries(self):
    #     """Create JE + SE atomically — called when status transitions to Paid."""
    #     _prev_user = (frappe.session.user or "").strip()
    #     restore_user = (
    #         _prev_user
    #         if _prev_user and _prev_user != "None" and frappe.db.exists("User", _prev_user)
    #         else "Guest"
    #     )
    #     try:
    #         frappe.set_user("Administrator")
    #         self._create_journal_entry()
    #         self._create_stock_entry()
    #         # Persist the JE/SE links via db.set_value (avoid recursive save)
    #         frappe.db.set_value("Buyback Order", self.name, {
    #             "journal_entry": self.journal_entry,
    #             "stock_entry": self.stock_entry,
    #         }, update_modified=False)
    #         # Auto-create Material Transfer Material Request from store →
    #         # central Buyback Bin so delivery staff pick the device up.
    #         # Failures must NOT roll back JE/SE (logistics is downstream).
    #         try:
    #             self._create_pickup_request()
    #         except Exception:
    #             frappe.log_error(
    #                 title=_("Buyback Order {0}: pickup MR creation failed").format(self.name),
    #             )
    #     except Exception:
    #         frappe.log_error(
    #             title=_("Buyback Order {0}: JE/SE creation failed").format(self.name),
    #         )
    #         raise
    #     finally:
    #         frappe.set_user(restore_user)


    # update _create_accounting_entries

    def _create_accounting_entries(self):
        """Create JE + SE atomically — called when status transitions to Paid."""
        prev_user = frappe.session.user
        try:
            # In-memory only - do NOT use frappe.set_user (it writes cookies)
            frappe.session.user = "Administrator"
            frappe.local.session_obj = None
            
            self._create_journal_entry()
            self._create_stock_entry()
            # Persist the JE/SE links via db.set_value (avoid recursive save)
            frappe.db.set_value("Buyback Order", self.name, {
                "journal_entry": self.journal_entry,
                "stock_entry": self.stock_entry,
            }, update_modified=False)
            # Auto-create Material Transfer Material Request from store →
            # central Buyback Bin so delivery staff pick the device up.
            # Failures must NOT roll back JE/SE (logistics is downstream).
            try:
                self._create_pickup_request()
            except Exception:
                frappe.log_error(
                    title=_("Buyback Order {0}: pickup MR creation failed").format(self.name),
                )
        except Exception:
            frappe.log_error(
                title=_("Buyback Order {0}: JE/SE creation failed").format(self.name),
            )
            raise
        finally:
            frappe.session.user = prev_user
            frappe.local.session_obj = None

    def _update_serial_no_bought_back(self):
        """Mark Serial No as 'Bought Back' and add timeline comment on close."""
        from buyback.serial_no_utils import update_serial_buyback_status
        update_serial_buyback_status(
            self.imei_serial,
            status="Bought Back",
            order_name=self.name,
            price=self.final_price,
            grade=self.condition_grade,
            customer=self.customer,
            comment=(
                f"✅ Bought back via {self.name} — "
                f"₹{self.final_price}, Grade: {self.condition_grade}, "
                f"Customer: {self.customer}"
            ),
        )

    def _award_loyalty_points(self):
        """Create a Loyalty Point Entry if loyalty points are enabled."""
        settings = frappe.get_single("Buyback Settings")
        if not cint(settings.enable_loyalty_points):
            return
        if not settings.loyalty_program:
            return
        if flt(self.final_price) <= 0:
            return

        points_per_100 = cint(settings.loyalty_points_per_100) or 10
        points = int(flt(self.final_price) / 100) * points_per_100
        if points <= 0:
            return

        expiry_days = cint(settings.loyalty_point_expiry_days) or 365
        company = self.company or settings.default_company

        lpe = frappe.get_doc({
            "doctype": "Loyalty Point Entry",
            "loyalty_program": settings.loyalty_program,
            "customer": self.customer,
            "invoice_type": "Buyback Order",
            "invoice": self.name,
            "loyalty_points": points,
            "purchase_amount": self.final_price,
            "posting_date": nowdate(),
            "expiry_date": add_days(nowdate(), expiry_days),
            "company": company,
        })
        lpe.insert(ignore_permissions=True)
        # Use db.set_value to avoid recursive save (called from on_update)
        frappe.db.set_value("Buyback Order", self.name, {
            "loyalty_points_earned": points,
            "loyalty_point_entry": lpe.name,
        }, update_modified=False)
        self.loyalty_points_earned = points
        self.loyalty_point_entry = lpe.name
        log_audit("Loyalty Points Awarded", "Buyback Order", self.name,
                  new_value={"points": points, "entry": lpe.name})

    def _create_journal_entry(self):
        """
        Create a Journal Entry for the buyback expense.
        Dr: Buyback Expense Account
        Cr: Buyback Payable / Cash / Bank
        """
        # Canonical field is `customer_payout_mode` (see buyback_order.json).
        # Legacy `payout_mode` / `payment_mode` kept in the fallback chain only
        # to survive stale in-memory docs from older code paths.
        _pmode = (
            self.get("customer_payout_mode")
            or self.get("payout_mode")
            or self.get("payment_mode")
            or "Cash"
        ).lower()
        if any(x in _pmode for x in ("bank", "transfer", "neft", "imps", "rtgs", "upi")):
            # Market-standard (SAP FI-AR one-time-customer / ERPNext Expense
            # Claim) two-document pattern for bank payouts:
            #   (1) Accrual JE here: Dr Buyback Expense / Cr Debtors[Customer]
            #       — recognises the expense at Paid, independent of the cash
            #       leg, and tags the customer sub-ledger so audit can trace.
            #   (2) Bank Payment Request → auto-created Payment Entry on
            #       settlement: Dr Debtors[Customer] / Cr Bank — nets the
            #       Debtors sub-ledger for that customer back to zero.
            # Post the accrual first; a failure MUST not silently swallow the
            # BPR side and vice-versa, so each is wrapped in its own try.
            try:
                self._post_bank_payout_accrual_je()
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    f"Buyback accrual JE failed for {self.name}",
                )
            try:
                # NOTE: canonical path is ch_payments.api (there is no
                # ``ch_payments.bank_payments.api`` module — mirrors how
                # buyback.payment_api and ch_payments.doc_events import it).
                from ch_payments.api import create_bank_payment_request
                # ``default_bank_profile`` isn't a Buyback Settings field yet;
                # get_single_value returns None and create_bank_payment_request
                # then falls back to Bank Integration Settings.default_bank_profile.
                _def_profile = frappe.db.get_single_value("Buyback Settings", "default_bank_profile") or None
                _bpr = create_bank_payment_request("Buyback Order", self.name, _def_profile)
                # create_bank_payment_request returns {"name": ..., "doctype": ...};
                # the Link field expects the docname string, not the dict.
                _bpr_name = (_bpr or {}).get("name") if isinstance(_bpr, dict) else _bpr
                if _bpr_name:
                    self.db_set("custom_bank_payment_request", _bpr_name)
                    frappe.msgprint(
                        frappe._("Bank Payment Request {0} created. Finance to approve.").format(
                            frappe.utils.get_link_to_form("Bank Payment Request", _bpr_name)
                        ),
                        indicator="green",
                    )
            except Exception:
                frappe.log_error(frappe.get_traceback(), f"BPR creation failed for Buyback Order {self.name}")
                frappe.msgprint(frappe._("Could not auto-create BPR. Create manually from Bank Payments."), indicator="orange")
            return  # Bank leg's cash entry is posted later by the BPR's Payment Entry.

        settings = frappe.get_single("Buyback Settings")
        if not settings.buyback_expense_account:
            frappe.logger("buyback").warning(
                f"No buyback_expense_account configured — skipping JE for {self.name}"
            )
            return

        if flt(self.final_price) <= 0:
            frappe.logger("buyback").warning(
                f"Buyback Order {self.name} has non-positive final_price — skipping JE"
            )
            return

        company = self.company or settings.default_company
        if not company:
            return

        expense_account = self._resolve_expense_account_for_company(
            settings.buyback_expense_account, company
        )
        if not expense_account:
            return

        # Use default cash/bank account (buyback pays customer cash)
        credit_account = (
            frappe.db.get_value("Company", company, "default_cash_account")
            or frappe.db.get_value("Company", company, "default_bank_account")
            or frappe.db.get_value("Company", company, "default_payable_account")
        )
        if not credit_account:
            frappe.logger("buyback").warning(
                f"No cash/bank/payable account for company {company} — skipping JE"
            )
            return

        je = frappe.get_doc({
            "doctype": "Journal Entry",
            "voucher_type": "Journal Entry",
            "company": company,
            "posting_date": frappe.utils.nowdate(),
            "user_remark": f"Buyback Order {self.name} — {self.item_name or self.item}",
            "accounts": [
                {
                    "account": expense_account,
                    "debit_in_account_currency": flt(self.final_price),
                    "cost_center": frappe.db.get_value(
                        "Company", company, "cost_center"
                    ),
                },
                {
                    "account": credit_account,
                    "credit_in_account_currency": flt(self.final_price),
                },
            ],
        })
        je.flags.ch_system_generated_je = True
        je.insert(ignore_permissions=True)
        je.flags.ignore_permissions = True
        je.submit()
        self.journal_entry = je.name

    def _resolve_expense_account_for_company(self, expense_account, company):
        """Map the configured buyback expense account to this order's company.

        Buyback Settings is a Single, so ``buyback_expense_account`` can only
        point at ONE company's account. On multi-company sites every other
        company's JE then dies in ERPNext's validation ("Account Cost of
        Goods Sold - G does not belong to Company BestBuy Mobiles Pvt Ltd")
        and the order can never be closed. Resolve the same-named ledger in
        the order's company instead; skip the JE (logged) when the company
        has no such account.
        """
        acc = frappe.db.get_value(
            "Account", expense_account, ["company", "account_name"], as_dict=True
        )
        if not acc or acc.company == company:
            return expense_account

        mapped = frappe.db.get_value(
            "Account",
            {
                "account_name": acc.account_name,
                "company": company,
                "is_group": 0,
                "disabled": 0,
            },
            "name",
        )
        if mapped:
            return mapped

        frappe.logger("buyback").warning(
            f"buyback_expense_account {expense_account} belongs to {acc.company} "
            f"and company {company} has no '{acc.account_name}' ledger — "
            f"skipping JE for {self.name}"
        )
        return None

    def _post_bank_payout_accrual_je(self):
        """Accrual JE for bank-mode buyback payouts (SAP FI-AR one-time-customer).

        Posts:
            Dr  Buyback Expense (settings.buyback_expense_account)   final_price
                Cr  Party Account (Debtors[Customer])                       final_price

        Rationale (market-standard):
            * Recognises the buyback expense at Paid, independent of the
              cash-clearing leg. SAP F-53 / Oracle EBS AP / MS Dynamics
              F&O all separate expense recognition from cash settlement so
              period-end P&L is not distorted by unsettled bank payouts.
            * Tagging the customer party on the Debtors credit line means
              the Payment Entry auto-created by the BPR's settlement
              (Dr Debtors[Customer] / Cr Bank) will net the customer
              sub-ledger to zero — no orphan open balance.
            * Mirrors ERPNext's own Expense Claim → Payment Entry pattern
              (payable on submit, cash outflow on PE reconcile).

        Skips (with a log warning, no throw) when the required config is
        absent, so a mis-configured pilot does not brick the workflow:
          - buyback_expense_account not set on Buyback Settings
          - final_price ≤ 0
          - company not resolvable
          - party account not resolvable
        """
        settings = frappe.get_single("Buyback Settings")
        if not settings.buyback_expense_account:
            frappe.logger("buyback").warning(
                f"No buyback_expense_account configured — skipping accrual JE for {self.name}"
            )
            return

        if flt(self.final_price) <= 0:
            frappe.logger("buyback").warning(
                f"Buyback Order {self.name} has non-positive final_price — skipping accrual JE"
            )
            return

        company = self.company or settings.default_company
        if not company:
            return

        expense_account = self._resolve_expense_account_for_company(
            settings.buyback_expense_account, company
        )
        if not expense_account:
            return

        # Use the same resolver Payment Entry uses so JE-credit and
        # PE-debit hit the same account and the party sub-ledger nets.
        try:
            from erpnext.accounts.party import get_party_account
            party_account = get_party_account("Customer", self.customer, company)
        except Exception:
            party_account = None
        if not party_account:
            party_account = frappe.db.get_value("Company", company, "default_receivable_account")
        if not party_account:
            frappe.logger("buyback").warning(
                f"No party/receivable account for {self.customer} @ {company} — skipping accrual JE for {self.name}"
            )
            return

        cost_center = frappe.db.get_value("Company", company, "cost_center")

        je = frappe.get_doc({
            "doctype": "Journal Entry",
            "voucher_type": "Journal Entry",
            "company": company,
            "posting_date": frappe.utils.nowdate(),
            "user_remark": (
                f"Buyback Order {self.name} — accrual "
                f"({self.item_name or self.item}, bank payout via BPR)"
            ),
            "accounts": [
                {
                    "account": expense_account,
                    "debit_in_account_currency": flt(self.final_price),
                    "cost_center": cost_center,
                },
                {
                    "account": party_account,
                    "credit_in_account_currency": flt(self.final_price),
                    "party_type": "Customer",
                    "party": self.customer,
                    "cost_center": cost_center,
                },
            ],
        })
        je.flags.ch_system_generated_je = True
        je.insert(ignore_permissions=True)
        je.flags.ignore_permissions = True
        je.submit()
        self.journal_entry = je.name

    def _create_stock_entry(self):
        """
        Create a Stock Entry (Material Receipt) for the received buyback device.
        The device enters the buyback warehouse as used inventory.
        Skips gracefully if the item is not a stock item.
        """
        # Guard: only create stock entry for stock items
        is_stock_item = frappe.db.get_value("Item", self.item, "is_stock_item")
        if not is_stock_item:
            frappe.logger("buyback").info(
                f"Item {self.item} is not a stock item — skipping Stock Entry for {self.name}"
            )
            return

        settings = frappe.get_single("Buyback Settings")

        # Destination depends on settlement type:
        #   • Buyback  → store's Buyback bin (device quarantine → refurbish),
        #                tagged Buyback so POS excludes it from selling.
        #   • Exchange → store's SELLABLE warehouse, but the serial is tagged
        #                RESERVED (held for the buyback customer, NOT sellable to
        #                other walk-ins). It stays reserved for the duration of
        #                the exchange (so the trade-in can be represented on the
        #                new-device invoice and reversed cleanly), then moves to
        #                the Buyback bin once the exchange invoice is completed
        #                (buyback.exchange_hooks.move_traded_device_to_buyback_on_invoice).
        from buyback.utils import resolve_store_bin_warehouse

        is_exchange = self.settlement_type == "Exchange"
        # Physical warehouse the device is received into…
        warehouse_bin_type = "Sellable" if is_exchange else "Buyback"
        # …and the logical bin the serial is tagged with (Reserved holds an
        # exchange device in the sellable warehouse without exposing it for sale).
        tag_bin_type = "Reserved" if is_exchange else "Buyback"

        # Determine target warehouse: store's bin (created by ensure_store_bins).
        # Falls back to the store base warehouse if the bin doesn't exist yet.
        target_warehouse = resolve_store_bin_warehouse(self.store, self.company, warehouse_bin_type) if self.store else None
        if not target_warehouse:
            target_warehouse = frappe.db.get_value(
                "Company", self.company or settings.default_company, "default_warehouse"
            )
        if not target_warehouse:
            frappe.logger("buyback").warning(
                f"No warehouse resolved for {self.name} — skipping Stock Entry"
            )
            return

        # Determine valuation rate (use final_price as the cost of acquisition)
        valuation_rate = flt(self.final_price)

        se = frappe.get_doc({
            "doctype": "Stock Entry",
            "stock_entry_type": "Material Receipt",
            "company": self.company or settings.default_company,
            "posting_date": frappe.utils.nowdate(),
            "remarks": f"Buyback device received — {self.name}",
            "custom_source_type": "Buyback",
            "custom_buyback_order": self.name,
            "items": [
                {
                    "item_code": self.item,
                    "t_warehouse": target_warehouse,
                    "qty": 1,
                    "basic_rate": valuation_rate,
                    "serial_no": self.imei_serial or "",
                },
            ],
        })
        se.insert(ignore_permissions=True)
        se.flags.ignore_permissions = True
        se.submit()
        self.stock_entry = se.name

        # Tag the serial's logical bin: Buyback (excluded from POS selling) for a
        # buyback; Reserved (held for the exchange customer, excluded from selling
        # to other walk-ins) for an exchange.
        if self.imei_serial:
            try:
                from ch_erp15.ch_erp15.stock_bin_api import move_to_bin
                move_to_bin(
                    self.imei_serial,
                    tag_bin_type,
                    reason=f"Received via Buyback Order {self.name} ({self.settlement_type or 'Buyback'})",
                    reference_doctype="Buyback Order",
                    reference_name=self.name,
                )
            except Exception:
                frappe.log_error(frappe.get_traceback(), f"Bin tag ({tag_bin_type}) failed for {self.imei_serial}")

    # ──────────────────────────────────────────────────────────────
    # Pickup logistics: route bought-back device from store to Buyback Bin
    # ──────────────────────────────────────────────────────────────
    def _create_pickup_request(self, force: bool = False):
        """Create a Material Transfer MR from `self.store` → Buyback Bin.

        Idempotent: skips when a Material Request already exists for this
        Buyback Order. Notifies users with the configured pickup role.
        Silently skips when settings are not yet configured (logged for ops).

        Args:
            force: When True, bypass the ``auto_create_pickup_request`` flag.
                   Used by the manual "Create Pickup Transfer Request" action
                   on the Buyback Order form.
        """
        settings = frappe.get_single("Buyback Settings")
        if not force and not cint(getattr(settings, "auto_create_pickup_request", 0)):
            return
        target_wh = getattr(settings, "buyback_warehouse", None)
        if target_wh and cint(frappe.db.get_value("Warehouse", target_wh, "disabled")):
            frappe.logger("buyback").warning(
                f"Configured Buyback Settings.buyback_warehouse is disabled ({target_wh}) for {self.name}; skipping pickup MR"
            )
            target_wh = None
        if not target_wh:
            frappe.logger("buyback").info(
                f"Buyback Settings.buyback_warehouse not configured — "
                f"skipping pickup MR for {self.name}"
            )
            return
        source_wh = frappe.db.get_value(
            "Warehouse",
            {"parent_warehouse": self.store, "ch_bin_type": "Buyback", "company": self.company},
            "name",
        ) if self.store else None
        if not source_wh:
            source_wh = self.store
        if not source_wh:
            frappe.logger("buyback").warning(
                f"Buyback Order {self.name} has no store warehouse — skipping pickup MR"
            )
            return
        if source_wh == target_wh:
            # Already at the buyback bin — nothing to transfer
            return

        # Idempotency: did we already create a pickup MR for this order?
        existing = frappe.db.get_value(
            "Material Request",
            {"custom_buyback_order": self.name, "docstatus": ["<", 2]},
            "name",
        )
        if existing:
            return

        is_stock_item = frappe.db.get_value("Item", self.item, "is_stock_item")
        if not is_stock_item:
            return

        from frappe.utils import nowdate, add_days
        # Pre-capture the bought-back device's IMEI on the MR row so the
        # pickup MR is already "scanned" at creation time. Logistics staff
        # only need to verify on hand-over instead of re-typing the IMEI.
        # The custom_serial_no / custom_scanned_qty fields are owned by
        # ch_erp15 (Material Request Item) and are mirrored into Stock
        # Entry Detail.serial_no on fulfilment.
        imei = (self.imei_serial or "").strip()
        mr = frappe.get_doc({
            "doctype": "Material Request",
            "material_request_type": "Material Transfer",
            "company": self.company,
            "transaction_date": nowdate(),
            "schedule_date": add_days(nowdate(), 1),
            "set_warehouse": target_wh,
            "custom_buyback_order": self.name,
            "items": [
                {
                    "item_code": self.item,
                    "qty": 1,
                    "schedule_date": add_days(nowdate(), 1),
                    "warehouse": target_wh,
                    "from_warehouse": source_wh,
                    "uom": frappe.db.get_value("Item", self.item, "stock_uom"),
                    "stock_uom": frappe.db.get_value("Item", self.item, "stock_uom"),
                    "custom_serial_no": imei,
                    "custom_scanned_qty": 1 if imei else 0,
                    "description": (
                        f"Pickup of bought-back device {imei or self.item} "
                        f"from {source_wh} → {target_wh} (Buyback Order {self.name})"
                    ),
                },
            ],
        })
        mr.insert(ignore_permissions=True)
        mr.flags.ignore_permissions = True
        mr.submit()

        self._notify_pickup_role(mr.name, source_wh, target_wh, settings)
        return mr.name

    @frappe.whitelist()
    def create_pickup_request_now(self):
        """User-initiated pickup MR creation (Logistics redesign Phase 1).

        Called from the "Create Pickup Transfer Request" button on the
        Buyback Order form. Bypasses ``Buyback Settings.auto_create_pickup_request``
        (which now defaults to OFF) but otherwise reuses the same idempotent
        logic, so accidentally clicking twice will not create duplicates.

        Returns the Material Request name, or None when nothing was created
        (e.g. order not yet Paid, item not stock, MR already exists).
        """
        if self.status != "Paid":
            frappe.throw(
                _("Pickup Transfer Request can only be raised after the Buyback Order is Paid."),
                title=_("Buyback Order: Not Paid"),
            )
        if not self.stock_entry:
            frappe.throw(
                _("Buyback Stock Entry must exist before raising a pickup. Mark order Paid first."),
                title=_("Buyback Order: No Stock Entry"),
            )
        mr_name = self._create_pickup_request(force=True)
        if mr_name:
            frappe.msgprint(
                _("Pickup Material Request {0} created.").format(
                    frappe.utils.get_link_to_form("Material Request", mr_name)
                ),
                indicator="green",
                alert=True,
            )
        else:
            frappe.msgprint(
                _("No pickup request was created. A request may already exist, "
                  "the item may not be a stock item, or the Buyback Bin warehouse "
                  "is not configured in Buyback Settings."),
                indicator="orange",
                alert=True,
            )
        return mr_name

    def _notify_pickup_role(self, mr_name, source_wh, target_wh, settings):
        """Create ToDo + Notification Log for users with the pickup role."""
        role = getattr(settings, "pickup_notify_role", None) or "Stock Manager"
        users = []
        # Prefer role × store scope intersection so branch alerts don't
        # notify unrelated users from other stores.
        try:
            from ch_erp15.ch_erp15.notification_router import get_scoped_users
            users = get_scoped_users([role], store=self.store)
        except Exception:
            users = frappe.get_all(
                "Has Role",
                filters={"role": role, "parenttype": "User"},
                pluck="parent",
            )
        # Filter to enabled, non-system users
        users = [
            u for u in set(users)
            if u not in ("Administrator", "Guest")
            and frappe.db.get_value("User", u, "enabled")
        ]
        if not users:
            return

        subject = _("Buyback Pickup Required: {0}").format(self.name)
        message = _(
            "Buyback Order {0} is paid. Please pick up device {1} from "
            "{2} and deliver to {3}. Material Request: {4}"
        ).format(
            self.name,
            self.imei_serial or self.item,
            source_wh,
            target_wh,
            mr_name,
        )

        for user in users:
            try:
                # ToDo for the action queue
                todo = frappe.get_doc({
                    "doctype": "ToDo",
                    "allocated_to": user,
                    "reference_type": "Material Request",
                    "reference_name": mr_name,
                    "description": message,
                    "priority": "Medium",
                })
                todo.insert(ignore_permissions=True)

                # Notification Log for the bell-icon feed
                notif = frappe.get_doc({
                    "doctype": "Notification Log",
                    "for_user": user,
                    "type": "Alert",
                    "document_type": "Material Request",
                    "document_name": mr_name,
                    "subject": subject,
                    "email_content": message,
                })
                notif.insert(ignore_permissions=True)
            except Exception:
                frappe.log_error(
                    title=f"Buyback pickup notify failed for {user} / {self.name}"
                )
