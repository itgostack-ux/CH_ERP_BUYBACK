from unittest import TestCase
from unittest.mock import patch

from buyback.buyback import alerts


class TestSLAAlertDelivery(TestCase):
    @patch.object(alerts, "send_alert")
    @patch.object(alerts, "_get_alert_recipients", return_value=["manager@example.com"])
    @patch.object(alerts.frappe.db, "get_value", return_value="Store A")
    def test_sla_alert_uses_email_without_blocking_realtime_dialog(
        self, _get_value, _recipients, send_alert
    ):
        alerts.alert_sla_breach(
            "Buyback Order",
            "BBO-TEST-0001",
            "approval_to_payment",
            30,
            15,
        )

        _, kwargs = send_alert.call_args
        self.assertTrue(kwargs["send_email"])
        self.assertFalse(kwargs["send_realtime"])
        self.assertNotIn("send_whatsapp", kwargs)
