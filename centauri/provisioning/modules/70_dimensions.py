# 70 — Accounting Dimensions for slicing the P&L by how value is created and sold.
# Creates two lightweight custom doctypes (Revenue Stream, GTM Channel) and registers
# each as an Accounting Dimension, so every transaction can be tagged. Each dimension is
# best-effort: registering a dimension alters all transaction tables, so a failure on one
# must not abort the whole provisioning run.

DIMENSIONS = {
    "Revenue Stream": ["Microsoft Resale", "Professional Services", "Managed Services",
                       "IP Products", "Secondment"],
    "GTM Channel": ["Direct", "Partner", "Microsoft Marketplace", "Referral"],
}


def _ensure_dim_doctype(name):
    if frappe.db.exists("DocType", name):
        return
    frappe.get_doc({
        "doctype": "DocType", "name": name, "module": "Accounts", "custom": 1,
        "naming_rule": "By fieldname", "autoname": "field:title", "track_changes": 1,
        "fields": [
            {"fieldname": "title", "label": "Title", "fieldtype": "Data",
             "reqd": 1, "unique": 1, "in_list_view": 1},
            {"fieldname": "disabled", "label": "Disabled", "fieldtype": "Check"},
        ],
        "permissions": [
            {"role": "System Manager", "read": 1, "write": 1, "create": 1, "delete": 1},
            {"role": "Accounts Manager", "read": 1, "write": 1, "create": 1},
        ],
    }).insert(ignore_permissions=True)
    log(f"created custom DocType: {name}")


def _main():
    for dim, values in DIMENSIONS.items():
        try:
            _ensure_dim_doctype(dim)
            get_or_create("Accounting Dimension", {"document_type": dim},
                          {"label": dim, "disabled": 0})
            for v in values:
                if not frappe.db.exists(dim, v):
                    frappe.get_doc({"doctype": dim, "title": v}).insert(ignore_permissions=True)
            frappe.db.commit()
            log(f"dimension ready: {dim}")
        except Exception as e:
            frappe.db.rollback()
            log(f"dimension skipped ({dim}): {e}")


run_module(_main, "70_dimensions")
