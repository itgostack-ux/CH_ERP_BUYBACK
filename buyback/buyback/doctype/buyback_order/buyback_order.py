import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime, flt, nowdate, add_days, cint

from buyback.exceptions import BuybackStatusError
from buyback.utils import log_audit


class BuybackOrder(Document):
    def before_insert(self):
        """Auto-assign sequential integer ID using advisory lock."""
        frappe.db.sql("SELECT GET_LOCK('buyback_order_id', 10)")
        try:
            last = frappe.db.sql(
                "SELECT MAX(order_id) FROM `tabBuyback Order`"
            )[0][0] or 0
            self.order_id = last + 1
        finally:
            frappe.db.sql("SELECT RELEASE_LOCK('buyback_order_id')")

        self.status = "Draft"

        # Generate approval token for customer-facing page
        if not self.approval_token:
            self.approval_token = frappe.generate_hash(length=32)

    def validate(self):
        self._populate_item_hierarchy()
        self._link_assessment()
        self._calculate_price_variance()
        self._calculate_exchange_totals()
        self._calculate_payment_totals()
        self._check_approval_requirement()
        self._validate_kyc_for_otp_stage()

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
        status = self.workflow_state or self.status

        # Safety net: create JE + SE if somehow on_submit missed it
        if status == "Paid" and not self.journal_entry and not self.stock_entry:
            self._create_accounting_entries()

        if status == "Closed":
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
            self.status = "Awaiting Approval" if self.requires_approval else "Approved"
        log_audit("Order Created", "Buyback Order", self.name,
                  new_value={"final_price": self.final_price, "status": self.status})

        # Update Serial No status
        from buyback.serial_no_utils import update_serial_buyback_status
        if self.status == "Paid":
            # Workflow brought us directly to Paid — create accounting entries
            self._create_accounting_entries()
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
        self.status = "Cancelled"
        log_audit("Order Cancelled", "Buyback Order", self.name,
                  new_value={"status": "Cancelled"})

    def _cancel_linked_entry(self, doctype, name):
        """Cancel a linked JE or SE if it exists and is submitted."""
        if not name:
            return
        try:
            doc = frappe.get_doc(doctype, name)
            if doc.docstatus == 1:
                doc.cancel()
        except Exception:
            frappe.log_error(
                f"Failed to cancel {doctype} {name} for {self.name}",
                "Buyback Order Cancel",
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

    def _check_approval_requirement(self):
        """Set requires_approval flag based on settings."""
        threshold = frappe.db.get_single_value(
            "Buyback Settings", "require_manager_approval_above"
        ) or 0
        if flt(self.final_price) > flt(threshold):
            self.requires_approval = 1

    def approve(self, remarks=None):
        """Manager approves the order."""
        if self.status != "Awaiting Approval":
            frappe.throw(
                _("Can only approve orders in 'Awaiting Approval' status."),
                exc=BuybackStatusError,
            )
        self.status = "Approved"
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
        if self.status != "Awaiting Approval":
            frappe.throw(
                _("Can only reject orders in 'Awaiting Approval' status."),
                exc=BuybackStatusError,
            )
        self.status = "Rejected"
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
        self.customer_approved = 1
        self.customer_approved_at = now_datetime()
        self.customer_approval_method = method
        self.status = "Customer Approved"
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
            frappe.throw(_("Invalid settlement type: {0}").format(settlement_type))

        self.settlement_type = settlement_type
        if settlement_type == "Exchange":
            if not new_item:
                frappe.throw(_("New device item is required for exchange."))
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
        """Send OTP for customer verification."""
        from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog

        if self.status not in ("Approved", "Awaiting OTP"):
            frappe.throw(
                _("OTP can only be sent after approval."),
                exc=BuybackStatusError,
            )

        otp_code = CHOTPLog.generate_otp(
            self.mobile_no,
            "Buyback Confirmation",
            reference_doctype="Buyback Order",
            reference_name=self.name,
        )
        self.status = "Awaiting OTP"
        self.save()
        log_audit("OTP Sent", "Buyback Order", self.name)
        return otp_code

    def verify_otp(self, otp_code):
        """Verify customer OTP."""
        from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog

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
            self.otp_verified = 1
            self.otp_verified_at = now_datetime()
            self.status = "OTP Verified"
            self.save()
            log_audit("OTP Verified", "Buyback Order", self.name)

        return result

    def mark_ready_to_pay(self):
        """Move to payment stage."""
        if self.status != "OTP Verified":
            frappe.throw(
                _("Must verify OTP before proceeding to payment."),
                exc=BuybackStatusError,
            )
        self.status = "Ready to Pay"
        self.save()

    def mark_paid(self):
        """Mark as fully paid."""
        if self.payment_status != "Paid":
            frappe.throw(
                _("Total paid must equal final price."),
                exc=BuybackStatusError,
            )
        self.status = "Paid"
        self.save()
        log_audit("Payment Made", "Buyback Order", self.name,
                  new_value={"total_paid": self.total_paid})

    def close(self):
        """Close the order after payment."""
        if self.status != "Paid":
            frappe.throw(
                _("Can only close paid orders."),
                exc=BuybackStatusError,
            )
        self.status = "Closed"
        self.save()
        log_audit("Order Closed", "Buyback Order", self.name)

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
        except Exception:
            frappe.log_error(
                title=f"KYC Sync Error: {self.name} → {self.customer}",
                message=frappe.get_traceback(),
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
            except (ImportError, Exception):
                pass  # ch_item_master not installed or error — non-critical
        except Exception:
            frappe.log_error(
                title=f"Customer Activity Update Error: {self.name}",
                message=frappe.get_traceback(),
            )

    def _create_accounting_entries(self):
        """Create JE + SE atomically — called when status transitions to Paid."""
        _prev_user = frappe.session.user
        try:
            frappe.set_user("Administrator")
            self._create_journal_entry()
            self._create_stock_entry()
            # Persist the JE/SE links via db.set_value (avoid recursive save)
            frappe.db.set_value("Buyback Order", self.name, {
                "journal_entry": self.journal_entry,
                "stock_entry": self.stock_entry,
            }, update_modified=False)
        except Exception:
            frappe.log_error(
                title=_("Buyback Order {0}: JE/SE creation failed").format(self.name),
            )
            raise
        finally:
            frappe.set_user(_prev_user)

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
        settings = frappe.get_single("Buyback Settings")
        expense_account = settings.buyback_expense_account
        if not expense_account:
            frappe.logger("buyback").warning(
                f"No buyback_expense_account configured — skipping JE for {self.name}"
            )
            return

        company = self.company or settings.default_company
        if not company:
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
        je.insert(ignore_permissions=True)
        je.flags.ignore_permissions = True
        je.submit()
        self.journal_entry = je.name

    def _create_stock_entry(self):
        """
        Create a Stock Entry (Material Receipt) for the received buyback device.
        The device enters the buyback warehouse as used inventory.
        """
        settings = frappe.get_single("Buyback Settings")

        # Determine target warehouse: store IS the warehouse now (no indirection)
        target_warehouse = self.store if self.store else None
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
