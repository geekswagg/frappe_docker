# 50 — Secondment / labour billing. Centauri's engineers are billed to GKT and TBI via
# Timesheets -> intercompany Sales Invoices (revenue in CC, cost in GKT/TBI). This module
# creates the MASTERS always; sample transactions are best-effort and gated by SAMPLE_DATA
# (draft | submit | masters) so a missing field never blocks the rest of provisioning.

ACTIVITY_TYPES = ["Software Engineering", "Architecture", "Project Management"]


def _ensure_masters(cc, gkt, tbi):
    for at in ACTIVITY_TYPES:
        get_or_create("Activity Type", {"activity_type": at}, {})

    # Billable labour item used on intercompany invoices.
    ensure_item("SVC-LABOUR", "Engineering Labour (per hour)", "Engineering Labour",
                is_sales=1, is_purchase=1)
    ensure_item_price("SVC-LABOUR", "Centauri Selling", CFG["RATES"]["Software Engineering"])

    # One project per secondment engagement, owned by CC.
    get_or_create("Project", {"project_name": "CC → GKT Delivery"},
                  {"company": cc, "status": "Open",
                   "customer": f"{gkt} (Internal)"})
    get_or_create("Project", {"project_name": "CC → TBI Platform Squad"},
                  {"company": cc, "status": "Open",
                   "customer": f"{tbi} (Internal)"})


def _sample_timesheet(cc):
    """Best-effort billable timesheet on the CC->TBI project."""
    project = "CC → TBI Platform Squad"
    if frappe.db.exists("Timesheet", {"parent_project": project}):
        log("sample timesheet already exists; skipping")
        return
    rate = CFG["RATES"]["Software Engineering"]
    ts = frappe.new_doc("Timesheet")
    ts.company = cc
    ts.parent_project = project
    ts.append("time_logs", {
        "activity_type": "Software Engineering",
        "from_time": "2026-06-01 09:00:00",
        "hours": 8,
        "project": project,
        "is_billable": 1,
        "billing_hours": 8,
        "billing_rate": rate,
    })
    ts.insert(ignore_permissions=True)
    if CFG["SAMPLE_DATA"] == "submit":
        ts.submit()
    log(f"created sample Timesheet: {ts.name} ({CFG['SAMPLE_DATA']})")


def _sample_invoice(cc, tbi):
    """Best-effort draft intercompany Sales Invoice (CC -> internal TBI)."""
    marker = "SAMPLE-CC-TBI-LABOUR"
    if frappe.db.exists("Sales Invoice", {"po_no": marker}):
        log("sample intercompany invoice already exists; skipping")
        return
    rate = CFG["RATES"]["Software Engineering"]
    si = frappe.new_doc("Sales Invoice")
    si.company = cc
    si.customer = f"{tbi} (Internal)"
    si.po_no = marker
    si.append("items", {"item_code": "SVC-LABOUR", "qty": 8, "rate": rate})
    si.insert(ignore_permissions=True)
    if CFG["SAMPLE_DATA"] == "submit":
        si.submit()
    log(f"created sample Sales Invoice: {si.name} ({CFG['SAMPLE_DATA']})")


def _main():
    cc, gkt, tbi = company("CC"), company("GKT"), company("TBI")
    _ensure_masters(cc, gkt, tbi)

    if CFG["SAMPLE_DATA"] in ("draft", "submit"):
        for fn, args in ((_sample_timesheet, (cc,)), (_sample_invoice, (cc, tbi))):
            try:
                fn(*args)
            except Exception as e:
                log(f"sample skipped ({fn.__name__}): {e}")
    else:
        log("SAMPLE_DATA=masters — skipping sample transactions")


run_module(_main, "50_secondment_billing")
