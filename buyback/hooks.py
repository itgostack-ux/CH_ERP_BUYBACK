app_name = "buyback"
app_title = "BuyBack"
app_publisher = "Abiraj"
app_description = "BuyBack"
app_email = "abiraj@gostack.in"
app_license = "mit"

boot_session = "buyback.boot.boot_session"

required_apps = ["frappe/erpnext", "AbirJ1/ch_item_master", "ch_payments"]

add_to_apps_screen = [
	{
		"name": "buyback",
		"logo": "/assets/buyback/icon.svg",
		"title": "BuyBack",
		"route": "/BuyBack",
	}
]

app_include_css = "/assets/buyback/css/buyback.css"
app_include_js = "/assets/buyback/js/buyback.js"
web_include_css = "/assets/buyback/css/buyback.css"

after_install = "buyback.install.after_install"
after_migrate = [
    "buyback.custom_fields.setup_custom_fields",
    "buyback.install.sync_default_settings",
    "buyback.install.create_reporting_indexes",
    "buyback.install.seed_grade_master",
    "buyback.print_setup.ensure_print_formats",
    "buyback.setup_workspace.setup",
]

before_uninstall = "buyback.uninstall.before_uninstall"

doc_events = {
    "Buyback Assessment": {
        "after_insert": "buyback.doc_events.on_assessment_created",
    },
    "Buyback Inspection": {
        "on_update": "buyback.doc_events.on_inspection_update",
    },
    "Buyback Order": {
        "on_update": [
            "buyback.buyback.doc_event_hooks.on_buyback_order_update",
            "buyback.buyback.whatsapp_notifications.on_buyback_order_whatsapp",
        ],
    },
    # Prevent cross-customer exchange credit misuse on every SI save/submit
    "Sales Invoice": {
        "validate": "buyback.exchange_hooks.validate_exchange_order_customer_match",
    },
    # Close the loop: an approved 'Buyback Price Override' exception writes the
    # approved price back to its referenced Buyback Order (that order only).
    "CH Exception Request": {
        "on_submit": "buyback.exception_hooks.apply_approved_buyback_price_override",
        "on_update_after_submit": "buyback.exception_hooks.apply_approved_buyback_price_override",
    },
}

scheduler_events = {
	"daily": [
		"buyback.tasks.expire_assessments",
		"buyback.tasks.daily_buyback_summary",
		"buyback.buyback.alerts.check_daily_alerts",
	],
	"hourly": [
		"buyback.tasks.expire_otps",
	],
	"cron": {
		"*/5 * * * *": [
			"buyback.buyback.sla_engine.evaluate_all_slas",
		],
	},
}

override_doctype_dashboards = {
	"Customer": "buyback.overrides.dashboard_overrides.get_dashboard_for_customer",
	"Item": "buyback.overrides.dashboard_overrides.get_dashboard_for_item",
}

ignore_links_on_delete = ["Buyback Audit Log", "Buyback SLA Log"]

fixtures = [
    {"doctype": "Custom DocPerm", "filters": [["parent", "in", [
        "Buyback Price Master", "Grade Master",
        "Buyback Inspection", "Buyback Order",
        "Buyback Assessment",
        "Buyback Exchange Order", "Buyback Audit Log",
        "Buyback Question Bank", "Buyback Checklist Template",
        "Buyback Pricing Rule", "Buyback Settings",
        "Buyback SLA Settings",
    ]]]},
    {"doctype": "Client Script", "filters": [["module", "=", "BuyBack"]]},
    {"doctype": "Server Script", "filters": [["module", "=", "BuyBack"]]},
    {"doctype": "Workflow", "filters": [["document_type", "in", [
        "Buyback Order", "Buyback Exchange Order", "Buyback Assessment",
    ]]]},
    {"doctype": "Workflow State", "filters": [["name", "in", [
        "Draft", "Awaiting Approval", "Approved", "Rejected",
        "Awaiting Customer Approval", "Customer Approved",
        "Awaiting OTP", "OTP Verified", "Ready to Pay", "Paid", "Closed",
        "Cancelled", "New Device Delivered", "Awaiting Pickup",
        "Old Device Received", "Inspected", "Settled",
        "Quoted", "Accepted", "Expired", "In Progress", "Completed",
        "Submitted", "Quote Generated",
    ]]]},
    {"doctype": "Workflow Action Master", "filters": [["name", "in", [
        "Approve", "Reject", "Send for Approval", "Send OTP",
        "Verify OTP", "Mark Ready to Pay", "Mark Paid", "Close",
        "Cancel", "Deliver New Device", "Receive Old Device",
        "Inspect Old Device", "Settle", "Submit", "Reopen",
        "Customer Approve", "Generate Quote",
    ]]]},
]