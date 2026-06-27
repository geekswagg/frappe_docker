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
    # frappe.conf.allow_cors accepts "*" or a LIST of origins. Support both, and a
    # comma-separated ALLOW_CORS_ORIGIN. NOTE: site_config is read at worker startup,
    # so the backend must be restarted for this to take effect.
    try:
        from frappe.installer import update_site_config
        raw = (CFG["ALLOW_CORS_ORIGIN"] or "").strip()
        value = "*" if raw == "*" else [o.strip() for o in raw.split(",") if o.strip()]
        update_site_config("allow_cors", value)
        log(f"CORS allow_cors = {value} — restart backend to apply "
            "(docker compose -p centauri restart backend)")
    except Exception as e:
        log(f"CORS config skipped: {e}")


def _main():
    for key, first, last, roles in USERS:
        email = CFG["API_USERS"][key]
        _ensure_user(email, first, last, roles)
        _ensure_keys(email)
    _enable_cors()
    log("Webhooks: configure per integration in the UI (Settings > Webhook) "
        "once you have a receiver URL.")


run_module(_main, "80_integration_users")
