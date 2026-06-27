# Centauri provisioning — idempotent helper library.
#
# This file is CONCATENATED ahead of every module and piped into:
#   docker compose -p centauri exec -T backend bench --site erp.comwenga.com console
#
# `bench console` opens a Frappe-initialised REPL (site connected, `frappe` imported)
# and, when stdin is a pipe, executes the whole stream as a script. No app packaging
# and no bind-mount required.
#
# IMPORTANT: `bench console` does not reliably exit non-zero on a Python exception,
# so every module ends with `run_module(_main, "<label>")`, which commits on success
# and prints the sentinel line `PROVISION_OK <label>`. The orchestrator (provision.sh)
# greps stdout for that sentinel and fails the step if it is absent.
#
# Idempotency contract: existence-checked inserts (never blind), existing docs mutated
# only when a value actually changed, one commit per module, submitted sample docs
# guarded by a deterministic key. Safe to re-run; a second run creates nothing new.

import datetime
import os
import frappe


def _f(key, default):
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def _i(key, default):
    try:
        return int(float(os.environ.get(key, default)))
    except (TypeError, ValueError):
        return int(default)


def _s(key, default):
    return os.environ.get(key) or default


# Configuration contract. The orchestrator forwards provision.env values as -e env
# vars on `docker compose exec`; every key has a safe default here, so modules also
# run correctly when invoked directly with no env. Modules read from CFG only.
CFG = {
    "SITE": _s("SITE", "erp.comwenga.com"),
    "CURRENCY": _s("DEFAULT_CURRENCY", "KES"),
    "COUNTRY": _s("COUNTRY", "Kenya"),
    "COMPANIES": {
        "GROUP": (_s("GROUP_COMPANY", "Omwenga Holdings"), _s("GROUP_ABBR", "OHL"), 1, None),
        "CC": (_s("CC_COMPANY", "Centauri Consulting"), _s("CC_ABBR", "CC"), 0, "GROUP"),
        "GKT": (_s("GKT_COMPANY", "Giktek Ventures"), _s("GKT_ABBR", "GKT"), 0, "GROUP"),
        "TBI": (_s("TBI_COMPANY", "Techno Brain Incubator"), _s("TBI_ABBR", "TBI"), 0, "GROUP"),
    },
    "PAYROLL_BACKEND": _s("PAYROLL_BACKEND", "hrms"),
    "SAMPLE_DATA": _s("SAMPLE_DATA", "draft"),
    "STATUTORY": {
        "PAYE_PERSONAL_RELIEF": _f("PAYE_PERSONAL_RELIEF", 2400),
        "SHIF_RATE": _f("SHIF_RATE", 0.0275),
        "SHIF_MIN": _f("SHIF_MIN", 300),
        "HOUSING_LEVY_RATE": _f("HOUSING_LEVY_RATE", 0.015),
        "NSSF_RATE": _f("NSSF_RATE", 0.06),
        "NSSF_TIER1_LIMIT": _f("NSSF_TIER1_LIMIT", 8000),
        "NSSF_TIER2_LIMIT": _f("NSSF_TIER2_LIMIT", 72000),
        "NITA_AMOUNT": _f("NITA_AMOUNT", 50),
    },
    "RATES": {
        "Software Engineering": _f("RATE_SOFTWARE_ENGINEERING", 5000),
        "Architecture": _f("RATE_ARCHITECTURE", 8000),
        "Project Management": _f("RATE_PROJECT_MANAGEMENT", 6000),
    },
    "BILLING_FREQUENCY": _s("SECONDMENT_BILLING_FREQUENCY", "Monthly"),
    "API_USERS": {
        "crm": _s("API_USER_CRM", "api-crm@comwenga.com"),
        "finance": _s("API_USER_FINANCE", "api-finance@comwenga.com"),
        "integrations": _s("API_USER_INTEGRATIONS", "api-integrations@comwenga.com"),
    },
    "ALLOW_CORS_ORIGIN": _s("ALLOW_CORS_ORIGIN", "https://integrations.comwenga.com"),
}


def company(key):
    """Resolve a CFG company key ('GROUP'/'CC'/'GKT'/'TBI') to its legal name."""
    return CFG["COMPANIES"][key][0]


def log(msg):
    print(f"{datetime.datetime.now().isoformat()} [provision] {msg}")


def get_or_create(doctype, filters, defaults=None, child_setup=None, submit=False):
    """Return the existing doc matching `filters`, or insert a new one.

    filters: a name string OR a dict of identifying fields (used both to look up and,
             when creating, as the initial field values).
    defaults: extra fields applied only on first insert.
    child_setup: callable(doc) that appends child rows before insert.
    submit: submit the doc if it is submittable and still a draft.
    """
    name = frappe.db.exists(doctype, filters)
    if name:
        doc = frappe.get_doc(doctype, name)
        log(f"exists   {doctype}: {doc.name}")
    else:
        doc = frappe.new_doc(doctype)
        if isinstance(filters, dict):
            doc.update(filters)
        if defaults:
            doc.update(defaults)
        if child_setup:
            child_setup(doc)
        doc.insert(ignore_permissions=True)
        log(f"created  {doctype}: {doc.name}")
    if submit and getattr(doc, "docstatus", 0) == 0 and frappe.get_meta(doctype).is_submittable:
        doc.submit()
        log(f"submitted {doctype}: {doc.name}")
    return doc


def ensure_value(doctype, name, fieldmap):
    """Idempotently set fields on an existing doc; saves only if something changed."""
    doc = frappe.get_doc(doctype, name)
    dirty = False
    for k, v in fieldmap.items():
        if doc.get(k) != v:
            doc.set(k, v)
            dirty = True
    if dirty:
        doc.save(ignore_permissions=True)
        log(f"updated  {doctype}: {name} -> {list(fieldmap)}")
    return doc


def ensure_single(doctype, fieldmap):
    """Same as ensure_value but for Single doctypes (e.g. Accounts Settings)."""
    doc = frappe.get_single(doctype)
    dirty = False
    for k, v in fieldmap.items():
        if doc.get(k) != v:
            doc.set(k, v)
            dirty = True
    if dirty:
        doc.save(ignore_permissions=True)
        log(f"updated  {doctype} (single) -> {list(fieldmap)}")
    return doc


def ensure_child(parent, table_field, match_keys, rowdata):
    """Append a child row only when no existing row matches all `match_keys`.

    Returns the (existing or new) row. Caller is responsible for saving `parent`.
    """
    for row in parent.get(table_field) or []:
        if all(row.get(k) == rowdata.get(k) for k in match_keys):
            for k, v in rowdata.items():
                if row.get(k) != v:
                    row.set(k, v)
            return row
    return parent.append(table_field, rowdata)


def find_account(company, number=None, account_name=None):
    """Resolve an Account name within a company by number and/or name. None if absent."""
    filters = {"company": company}
    if number is not None:
        filters["account_number"] = str(number)
    if account_name:
        filters["account_name"] = account_name
    names = frappe.get_all("Account", filters=filters, pluck="name", limit=1)
    return names[0] if names else None


def company_abbr(company):
    return frappe.db.get_value("Company", company, "abbr")


def ensure_account(company, account_name, account_type="", number=None,
                   parent_names=None, is_group=0):
    """Idempotently ensure a leaf/group Account exists in `company`.

    Matches an existing account by name first (so template-provided accounts like VAT
    are never duplicated, regardless of their number). Resolves the parent by trying
    each name in `parent_names` in order. If the requested account number is already
    used by a different account in this company, the account is created WITHOUT a
    number (and a warning logged) rather than crashing — robust against CoA templates
    that already occupy numbers in the same range.
    Returns the account name, or None if no parent could be resolved.
    """
    existing = find_account(company, account_name=account_name)
    if existing:
        return existing
    parent = None
    for pname in (parent_names or []):
        parent = find_account(company, account_name=pname)
        if parent:
            break
    if not parent:
        log(f"WARN parent not found for '{account_name}' in {company} (tried {parent_names}); skipped")
        return None
    doc = frappe.new_doc("Account")
    doc.account_name = account_name
    doc.company = company
    doc.parent_account = parent
    doc.is_group = is_group
    if account_type:
        doc.account_type = account_type
    if number is not None:
        if frappe.db.exists("Account", {"company": company, "account_number": str(number)}):
            log(f"WARN number {number} already used in {company}; creating '{account_name}' without a number")
        else:
            doc.account_number = str(number)
    doc.insert(ignore_permissions=True)
    log(f"created  Account: {doc.name}")
    return doc.name


def ensure_item(item_code, item_name, item_group, is_sales=1, is_purchase=0,
                is_stock=0, uom="Nos", brand=None, description=None):
    """Idempotently ensure an Item (service or resale) exists."""
    defaults = {
        "item_name": item_name,
        "item_group": item_group,
        "stock_uom": uom,
        "is_stock_item": is_stock,
        "is_sales_item": is_sales,
        "is_purchase_item": is_purchase,
    }
    if brand:
        defaults["brand"] = brand
    if description:
        defaults["description"] = description
    return get_or_create("Item", {"item_code": item_code}, defaults)


def ensure_item_price(item_code, price_list, rate):
    """Idempotently ensure an Item Price (in the group currency) for a price list."""
    return get_or_create(
        "Item Price",
        {"item_code": item_code, "price_list": price_list},
        {"price_list_rate": rate, "currency": CFG["CURRENCY"]},
    )


def run_module(fn, label):
    """Wrap a module body: commit + sentinel on success, rollback + non-zero on error."""
    try:
        fn()
        frappe.db.commit()
        print(f"PROVISION_OK {label}")
    except Exception:
        frappe.db.rollback()
        import traceback
        traceback.print_exc()
        print(f"PROVISION_FAIL {label}")
        raise SystemExit(1)
