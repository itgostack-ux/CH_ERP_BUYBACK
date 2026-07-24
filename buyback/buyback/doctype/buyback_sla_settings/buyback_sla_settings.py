# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from buyback.outbound_security import validate_whatsapp_webhook_url

class BuybackSLASettings(Document):
    def validate(self):
        if self.enable_whatsapp_alerts:
            if not self.whatsapp_webhook_url:
                frappe.throw(_("WhatsApp Webhook URL is required when alerts are enabled."))
            validate_whatsapp_webhook_url(
                self.whatsapp_webhook_url,
                self.whatsapp_allowed_hosts,
            )
