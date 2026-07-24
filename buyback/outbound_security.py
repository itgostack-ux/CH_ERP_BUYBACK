"""Outbound URL validation for configured Buyback integrations."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

import frappe
import requests
from frappe import _


def _allowed_hosts(raw_hosts) -> set[str]:
	hosts = {
		entry.strip().lower().rstrip(".")
		for entry in str(raw_hosts or "").replace(",", "\n").splitlines()
		if entry.strip()
	}
	if not hosts:
		frappe.throw(_("Configure at least one WhatsApp Allowed Host."))
	for host in hosts:
		if (
			"://" in host
			or "/" in host
			or "@" in host
			or ":" in host
			or any(character.isspace() for character in host)
		):
			frappe.throw(_("WhatsApp Allowed Hosts contains an invalid hostname."))
		try:
			ipaddress.ip_address(host)
		except ValueError:
			pass
		else:
			frappe.throw(_("IP literals are not allowed for WhatsApp webhooks."))
	return hosts


def validate_whatsapp_webhook_url(endpoint, raw_allowed_hosts) -> str:
	endpoint = str(endpoint or "").strip()
	if not endpoint or "\\" in endpoint or any(character.isspace() for character in endpoint):
		frappe.throw(_("WhatsApp Webhook URL is invalid."))
	try:
		parsed = urlsplit(endpoint)
		port = parsed.port
	except ValueError:
		frappe.throw(_("WhatsApp Webhook URL is invalid."))
	hostname = (parsed.hostname or "").lower().rstrip(".")
	if (
		parsed.scheme.lower() != "https"
		or not hostname
		or parsed.username
		or parsed.password
		or parsed.fragment
		or port not in (None, 443)
	):
		frappe.throw(
			_("WhatsApp Webhook URL must use HTTPS on port 443 without embedded credentials.")
		)
	if hostname not in _allowed_hosts(raw_allowed_hosts):
		frappe.throw(_("WhatsApp webhook host {0} is not allowlisted.").format(hostname))
	try:
		addresses = {
			row[4][0]
			for row in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
		}
	except (socket.gaierror, OSError):
		frappe.throw(_("WhatsApp webhook host could not be resolved safely."))
	if not addresses:
		frappe.throw(_("WhatsApp webhook host did not resolve to an address."))
	for address in addresses:
		try:
			parsed_address = ipaddress.ip_address(address)
		except ValueError:
			frappe.throw(_("WhatsApp webhook resolved to an invalid address."))
		if not parsed_address.is_global:
			frappe.throw(
				_("WhatsApp webhook cannot target a private, loopback, or link-local address."),
				frappe.PermissionError,
			)
	return endpoint


def post_whatsapp_webhook(endpoint, raw_allowed_hosts, payload, *, timeout=10):
	endpoint = validate_whatsapp_webhook_url(endpoint, raw_allowed_hosts)
	response = requests.post(
		endpoint,
		json=payload,
		timeout=max(1, min(int(timeout or 10), 30)),
		allow_redirects=False,
	)
	if 300 <= response.status_code < 400:
		frappe.throw(_("WhatsApp webhook redirects are not permitted."))
	response.raise_for_status()
	return response
