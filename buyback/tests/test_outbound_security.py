from unittest.mock import Mock, patch

import frappe
from frappe.tests import IntegrationTestCase

from buyback.outbound_security import post_whatsapp_webhook, validate_whatsapp_webhook_url


PUBLIC_DNS = [(2, 1, 6, "", ("93.184.216.34", 443))]


class TestBuybackOutboundSecurity(IntegrationTestCase):
	@patch("buyback.outbound_security.socket.getaddrinfo", return_value=PUBLIC_DNS)
	def test_webhook_requires_exact_public_https_host(self, _dns):
		self.assertEqual(
			validate_whatsapp_webhook_url(
				"https://hooks.example.test/whatsapp", "hooks.example.test"
			),
			"https://hooks.example.test/whatsapp",
		)
		for endpoint in (
			"http://hooks.example.test/whatsapp",
			"https://user:pass@hooks.example.test/whatsapp",
			"https://127.0.0.1/whatsapp",
		):
			with self.assertRaises((frappe.ValidationError, frappe.PermissionError)):
				validate_whatsapp_webhook_url(endpoint, "hooks.example.test")

	@patch(
		"buyback.outbound_security.socket.getaddrinfo",
		return_value=[(2, 1, 6, "", ("10.0.0.1", 443))],
	)
	def test_private_webhook_resolution_is_rejected(self, _dns):
		with self.assertRaises(frappe.PermissionError):
			validate_whatsapp_webhook_url(
				"https://hooks.example.test/whatsapp", "hooks.example.test"
			)

	@patch("buyback.outbound_security.socket.getaddrinfo", return_value=PUBLIC_DNS)
	@patch("buyback.outbound_security.requests.post")
	def test_webhook_redirect_is_not_followed(self, post, _dns):
		post.return_value = Mock(status_code=307)
		with self.assertRaises(frappe.ValidationError):
			post_whatsapp_webhook(
				"https://hooks.example.test/whatsapp",
				"hooks.example.test",
				{"text": "test"},
			)
		self.assertFalse(post.call_args.kwargs["allow_redirects"])
