# 80 — API integration layer (the hybrid's REST surface). Creates scoped integration
# users, generates API key/secret per user (idempotently — only when missing), enables
# CORS, and registers a disabled sample outbound Webhook. Runs LAST so keys reflect the
# finished configuration.
#
# Generated credentials are printed as `APIKEY <user> <key>:<secret>` lines. The secret
# is shown ONCE by Frappe — capture these and store them in Azure Key Vault (see README).

USERS = [
    ("crm",          "API", "CRM",          ["Sales User", "Sales Master Manager"]),
    ("finance",      "API", "Finance",      ["Accounts User"]),
    ("integrations", "API", "Integrations", ["Sales User", "Purchase User", "Accounts User",
                                             "Stock User", "Item Manager"]),
]


def _ensure_user(email, first, last, roles):
    get_or_create("User", {"email": email},
                  {"first_name": first, "last_name": last,
                   "send_welcome_email": 0, "user_type": "System User", "enabled": 1})
    user = frappe.get_doc("User", email)
    for r in roles:
        ensure_child(user, "roles", ["role"], {"role": r})
    user.save(ignore_permissions=True)
    return user


def _ensure_keys(email):
    if frappe.db.get_value("User", email, "api_key"):
        print(f"APIKEY {email} (already set — rotate via UI if needed)")
        return
    from frappe.core.doctype.user.user import generate_keys
    res = generate_keys(email)
    api_key = frappe.db.get_value("User", email, "api_key")
    secret = res.get("api_secret") if isinstance(res, dict) else res
    print(f"APIKEY {email} {api_key}:{secret}")


def _enable_cors():
    try:
        from frappe.installer import update_site_config
        update_site_config("allow_cors", CFG["ALLOW_CORS_ORIGIN"])
        log(f"CORS allow_cors set to {CFG['ALLOW_CORS_ORIGIN']}")
    except Exception as e:
        log(f"CORS config skipped: {e}")


def _sample_webhook():
    try:
        if frappe.db.exists("Webhook", {"webhook_doctype": "Sales Invoice",
                                        "webhook_docevent": "on_submit"}):
            log("sample webhook already exists; skipping")
            return
        wh = frappe.new_doc("Webhook")
        wh.update({
            "webhook_doctype": "Sales Invoice",
            "webhook_docevent": "on_submit",
            "request_url": "https://integrations.comwenga.com/webhooks/erpnext",
            "request_method": "POST",
            "request_structure": "JSON",
            "enabled": 0,  # disabled — enable once the receiver exists
        })
        wh.insert(ignore_permissions=True)
        log("created disabled sample Webhook (Sales Invoice on_submit)")
    except Exception as e:
        log(f"sample webhook skipped: {e}")


def _main():
    for key, first, last, roles in USERS:
        email = CFG["API_USERS"][key]
        _ensure_user(email, first, last, roles)
        _ensure_keys(email)
    _enable_cors()
    _sample_webhook()


run_module(_main, "80_integration_users")
