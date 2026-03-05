"""
Custom exception hierarchy for the Buyback app.
Pattern: HRMS uses custom exceptions subclassing frappe.ValidationError;
India Compliance keeps them in exceptions.py at app root.

Usage:
    from buyback.exceptions import BuybackValidationError
    frappe.throw(_("msg"), exc=BuybackValidationError)
"""

import frappe


class BuybackError(frappe.ValidationError):
    """Base exception for all buyback-related errors."""

    pass


class BuybackValidationError(BuybackError):
    """Raised when a buyback document fails validation."""

    pass


class BuybackPricingError(BuybackError):
    """Raised when pricing calculation fails or yields invalid results."""

    pass


class BuybackStatusError(BuybackError):
    """Raised when a status transition is invalid."""

    pass


class BuybackPermissionError(frappe.PermissionError):
    """Raised when user lacks permission for a buyback operation."""

    pass


class BuybackOTPError(BuybackError):
    """Raised when OTP verification fails."""

    pass
