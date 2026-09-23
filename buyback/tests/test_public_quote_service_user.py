# Copyright (c) 2026, Buyback and contributors
# For license information, please see license.txt
"""The one identity swap the public quote portal is allowed to keep.

``submit_public_quote_request`` is reachable by Guest. It inserts a Buyback
Assessment as a configured service user, so the record carries a real owner
rather than "Guest" -- and it has to swap the session to do it, because
``set_user_and_timestamp`` overwrites ``owner`` from ``frappe.session.user`` on
every insert (frappe/model/document.py). Setting ``doc.owner`` beforehand does
not survive.

That the caller is Guest is not what makes this safe. What makes it safe is
that the identity is resolved from configuration and re-validated on every
call, and that it fails closed when it cannot be: an unset setting, a disabled
user, a Website User, or a user who does not actually hold ``create`` on
Buyback Assessment all refuse the request rather than falling back to anything.

These tests exist because the go-live review requires a retained elevation to
prove it cannot be abused. Each one removes a single property and asserts the
endpoint shuts rather than degrades.
"""

from __future__ import annotations

import unittest

import frappe

SETTING = "public_quote_service_user"


def _settings_has_field() -> bool:
    try:
        return frappe.get_meta("Buyback Settings").has_field(SETTING)
    except Exception:
        return False


class TestPublicQuoteServiceUserFailsClosed(unittest.TestCase):
    """Strip one property at a time; the endpoint must refuse, never fall back."""

    @classmethod
    def setUpClass(cls):
        if not _settings_has_field():
            raise unittest.SkipTest(
                f"Buyback Settings has no {SETTING} field on this site")

    def setUp(self):
        from buyback.utils import get_buyback_setting_value

        self.original = get_buyback_setting_value(SETTING, "") or ""

    def tearDown(self):
        frappe.db.rollback()
        frappe.clear_cache(doctype="Buyback Settings")

    def _set(self, value):
        frappe.db.set_value("Buyback Settings", "Buyback Settings", SETTING, value)
        frappe.clear_cache(doctype="Buyback Settings")

    def _resolve(self):
        from buyback.public_portal_api import _public_quote_service_user

        return _public_quote_service_user()

    # ── the properties, one at a time ────────────────────────────────────

    def test_an_unset_service_user_refuses_the_request(self):
        """The important one: no configuration must not mean no check.

        A fallback to Guest would insert unattributable records; a fallback to
        Administrator would hand an anonymous caller the one identity that
        clears every tier of CH User Scope.
        """
        self._set("")
        with self.assertRaises(frappe.ValidationError):
            self._resolve()

    def test_a_service_user_that_does_not_exist_refuses(self):
        self._set("no-such-user@example.invalid")
        with self.assertRaises(frappe.ValidationError):
            self._resolve()

    def test_a_disabled_service_user_refuses(self):
        user = self._make_user(enabled=0, user_type="System User", grant=True)
        self._set(user)
        with self.assertRaises(frappe.ValidationError):
            self._resolve()

    def test_a_website_user_refuses(self):
        """A Website User cannot be the intake identity even if enabled."""
        user = self._make_user(enabled=1, user_type="Website User", grant=True)
        self._set(user)
        with self.assertRaises(frappe.ValidationError):
            self._resolve()

    def test_a_user_without_create_rights_refuses(self):
        """Being named in the setting is not itself an entitlement."""
        user = self._make_user(enabled=1, user_type="System User", grant=False)
        self._set(user)
        with self.assertRaises(frappe.ValidationError):
            self._resolve()

    def test_a_fully_configured_service_user_is_accepted(self):
        """The negative tests are only meaningful if the positive one passes.

        Without this, every assertion above would still hold if _resolve()
        simply always threw.
        """
        user = self._make_user(enabled=1, user_type="System User", grant=True)
        self._set(user)
        self.assertEqual(self._resolve(), user)

    # ── fixture ──────────────────────────────────────────────────────────

    def _make_user(self, *, enabled: int, user_type: str, grant: bool) -> str:
        email = f"_bbtest-{user_type.replace(' ', '').lower()}-{enabled}-{int(grant)}@ch-tests.local"
        if frappe.db.exists("User", email):
            frappe.db.set_value("User", email,
                                {"enabled": enabled, "user_type": user_type})
        else:
            doc = frappe.new_doc("User")
            doc.email = email
            doc.first_name = "BuybackServiceProbe"
            doc.enabled = 1           # insert enabled; User.validate rejects some
            doc.user_type = user_type  # combinations on a disabled insert
            doc.send_welcome_email = 0
            doc.flags.ignore_permissions = True
            doc.insert(ignore_permissions=True)
            frappe.db.set_value("User", email,
                                {"enabled": enabled, "user_type": user_type})

        role = self._granting_role()
        existing = {r.role for r in frappe.get_all(
            "Has Role", filters={"parent": email}, fields=["role"])}
        if grant and role and role not in existing:
            doc = frappe.get_doc("User", email)
            doc.append("roles", {"role": role})
            doc.flags.ignore_permissions = True
            doc.save(ignore_permissions=True)
        if not grant:
            frappe.db.delete("Has Role", {"parent": email})

        # Last, deliberately. User.validate promotes anyone holding a role back
        # to "System User", so setting user_type before the role change silently
        # undid it -- the Website User case was passing as a System User and the
        # test was asserting nothing.
        frappe.db.set_value("User", email,
                            {"enabled": enabled, "user_type": user_type})
        frappe.clear_cache(user=email)
        self.assertEqual(
            frappe.db.get_value("User", email, "user_type"), user_type,
            "fixture failed to pin user_type; the test below would not be "
            "testing what it claims")
        return email

    def _granting_role(self) -> str | None:
        """A role that really carries `create` on Buyback Assessment here.

        Read from the live permission rows rather than hard-coded: Custom
        DocPerm replaces a doctype's own perms on this bench, so guessing a
        role name would make the positive test pass or fail for the wrong
        reason.
        """
        for doctype in ("Custom DocPerm", "DocPerm"):
            rows = frappe.get_all(
                doctype,
                filters={"parent": "Buyback Assessment", "create": 1, "permlevel": 0},
                fields=["role"], limit_page_length=20)
            for row in rows:
                if row.role not in ("System Manager", "Administrator"):
                    return row.role
        return None


class TestTheSwapRestoresIdentity(unittest.TestCase):
    """Whatever happens around the insert, the session must come back."""

    def tearDown(self):
        frappe.db.rollback()

    def test_the_portal_restores_the_caller_in_a_finally(self):
        """Pinned on the source: the insert is the only thing inside the swap.

        Driving the whole Guest endpoint would need a live OTP, an eligible
        item and a price list; this asserts the structural property that makes
        the swap recoverable, which is what the elevation review turns on.
        """
        import ast
        import inspect
        import textwrap

        from buyback import public_portal_api

        src = inspect.getsource(public_portal_api.submit_public_quote_request)
        tree = ast.parse(textwrap.dedent(src))

        tries = [n for n in ast.walk(tree) if isinstance(n, ast.Try) and n.finalbody]
        restoring = []
        for node in tries:
            fin = "\n".join(
                ast.get_source_segment(textwrap.dedent(src), s) or "" for s in node.finalbody)
            if "set_user" in fin:
                restoring.append(node)

        self.assertTrue(
            restoring,
            "submit_public_quote_request switches identity without restoring it "
            "in a finally; an exception would leave the request running as the "
            "service user")

        for node in restoring:
            body = "\n".join(
                ast.get_source_segment(textwrap.dedent(src), s) or "" for s in node.body)
            self.assertNotIn(
                "frappe.sendmail", body,
                "work beyond the insert happens while impersonating the service "
                "user; keep the swap as narrow as the reason for it")
