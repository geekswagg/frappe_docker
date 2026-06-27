# 20 — Intercompany setup. Creates the internal Customers/Suppliers that ERPNext's
# inter-company invoicing uses, so Centauri (CC) can bill engineering labour and IP
# royalties to Giktek (GKT) and Techno Brain Incubator (TBI), with the paired
# Purchase Invoice booked in the receiving company.
#
# Direction of value: CC SELLS to GKT/TBI. So we need:
#   - internal CUSTOMERS representing GKT and TBI (CC invoices them)
#   - an internal SUPPLIER representing CC      (GKT/TBI receive its invoices)


def _first_non_group(doctype, name_field="name"):
    rows = frappe.get_all(doctype, filters={"is_group": 0}, pluck="name", limit=1)
    return rows[0] if rows else None


def _customer_group():
    return (frappe.db.get_single_value("Selling Settings", "customer_group")
            or _first_non_group("Customer Group") or "All Customer Groups")


def _territory():
    return (frappe.db.get_single_value("Selling Settings", "territory")
            or _first_non_group("Territory") or "All Territories")


def _supplier_group():
    return (frappe.db.get_single_value("Buying Settings", "supplier_group")
            or _first_non_group("Supplier Group") or "All Supplier Groups")


def _main():
    cc = company("CC")
    gkt = company("GKT")
    tbi = company("TBI")
    cg, terr, sg = _customer_group(), _territory(), _supplier_group()

    # ── Internal customers: GKT and TBI, billable by CC ──
    # ERPNext validates internal parties on insert, so the "allowed to transact with"
    # rows must be present BEFORE insert — populate them via child_setup, then top up
    # idempotently for already-existing records.
    for represented in (gkt, tbi):
        cust = get_or_create(
            "Customer",
            {"customer_name": f"{represented} (Internal)"},
            {
                "customer_type": "Company",
                "customer_group": cg,
                "territory": terr,
                "is_internal_customer": 1,
                "represents_company": represented,
            },
            child_setup=lambda doc: doc.append("companies", {"company": cc}),
        )
        ensure_child(cust, "companies", ["company"], {"company": cc})
        cust.save(ignore_permissions=True)

    # ── Internal supplier: CC, whose invoices GKT/TBI receive ──
    def _supplier_companies(doc):
        for buyer in (gkt, tbi):
            doc.append("companies", {"company": buyer})

    supp = get_or_create(
        "Supplier",
        {"supplier_name": f"{cc} (Internal)"},
        {
            "supplier_group": sg,
            "supplier_type": "Company",
            "is_internal_supplier": 1,
            "represents_company": cc,
        },
        child_setup=_supplier_companies,
    )
    for buyer in (gkt, tbi):
        ensure_child(supp, "companies", ["company"], {"company": buyer})
    supp.save(ignore_permissions=True)


run_module(_main, "20_intercompany")
