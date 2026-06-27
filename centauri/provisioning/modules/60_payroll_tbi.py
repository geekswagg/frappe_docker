# 60 — TBI payroll. Two backends, chosen by PAYROLL_BACKEND:
#   hrms    -> real Income Tax Slab + Salary Components + Salary Structure (+ sample
#              Employee/Assignment gated by SAMPLE_DATA). Requires the custom image
#              with the hrms app (provision.sh installs it in the pre-step).
#   journal -> a best-effort sample monthly Journal Entry that splits a salary into the
#              statutory liabilities, for environments that can't rebuild the image.
#
# Kenya statutory rates come from CFG["STATUTORY"] (set in provision.env). Exact PAYE
# bands / NSSF cap are an explicit verify-before-go-live item; the formulas below are a
# faithful simulation, not tax advice.

S = CFG["STATUTORY"]


def _map_account(doc, company, account_name):
    """Attach a Salary Component -> account mapping if the account exists."""
    acc = find_account(company, account_name=account_name)
    if acc:
        ensure_child(doc, "accounts", ["company"], {"company": company, "account": acc})
        doc.save(ignore_permissions=True)


def _hrms(tbi):
    if not frappe.db.exists("DocType", "Salary Component"):
        raise RuntimeError(
            "hrms app not installed — build the custom image (centauri/scripts/build-image.sh), "
            "set CUSTOM_IMAGE/CUSTOM_TAG/PULL_POLICY in .env, recreate the stack, then re-run. "
            "Or set PAYROLL_BACKEND=journal.")

    # ── Income Tax Slab (annualised Kenya PAYE) ──
    slab = get_or_create(
        "Income Tax Slab", {"name": "Kenya PAYE"},
        {
            "company": tbi, "currency": CFG["CURRENCY"], "effective_from": "2026-01-01",
            "allow_tax_exemption": 1,
            # Proxy for the KES 2,400/month personal relief — refine to a tax credit if needed.
            "standard_tax_exemption_amount": S["PAYE_PERSONAL_RELIEF"] * 12,
        },
    )
    if not slab.get("slabs"):
        for frm, to, pct in [
            (0, 288000, 10), (288000, 388000, 25), (388000, 6000000, 30),
            (6000000, 9600000, 32.5), (9600000, 0, 35),
        ]:
            slab.append("slabs", {"from_amount": frm, "to_amount": to, "percent_deduction": pct})
        slab.save(ignore_permissions=True)
    if slab.docstatus == 0:
        slab.submit()

    # ── Salary Components ──
    earnings = [
        ("Basic", "B", 1, "base"),
        ("House Rent Allowance", "HRA", 1, "base * 0.15"),
        ("Transport Allowance", "TA", 0, None),
    ]
    for name, abbr, formula_based, formula in earnings:
        d = {"type": "Earning", "salary_component_abbr": abbr, "depends_on_payment_days": 1}
        if formula_based:
            d.update({"amount_based_on_formula": 1, "formula": formula})
        c = get_or_create("Salary Component", {"salary_component": name}, d)
        _map_account(c, tbi, "Salaries and Wages")

    deductions = [
        ("PAYE", "PAYE", None, "PAYE Payable", {"variable_based_on_taxable_salary": 1}),
        ("SHIF", "SHIF", f"gross_pay * {S['SHIF_RATE']}", "SHIF Payable", {}),
        ("NSSF", "NSSF", f"gross_pay * {S['NSSF_RATE']}", "NSSF Payable", {}),
        ("Housing Levy", "HL", f"gross_pay * {S['HOUSING_LEVY_RATE']}", "Housing Levy Payable", {}),
        ("NITA", "NITA", None, "NITA Payable", {}),
    ]
    for name, abbr, formula, acct, extra in deductions:
        d = {"type": "Deduction", "salary_component_abbr": abbr}
        if formula:
            d.update({"amount_based_on_formula": 1, "formula": formula})
        if name == "NITA":
            d["amount"] = S["NITA_AMOUNT"]
        d.update(extra)
        c = get_or_create("Salary Component", {"salary_component": name}, d)
        _map_account(c, tbi, acct)

    # ── Salary Structure ──
    ss = get_or_create(
        "Salary Structure", {"name": "TBI Standard KES"},
        {"company": tbi, "currency": CFG["CURRENCY"], "payroll_frequency": "Monthly",
         "is_active": "Yes"},
    )
    if ss.docstatus == 0:
        ss.set("earnings", [])
        ss.set("deductions", [])
        ss.append("earnings", {"salary_component": "Basic", "amount_based_on_formula": 1, "formula": "base"})
        ss.append("earnings", {"salary_component": "House Rent Allowance", "amount_based_on_formula": 1, "formula": "base * 0.15"})
        ss.append("earnings", {"salary_component": "Transport Allowance", "amount": 5000})
        ss.append("deductions", {"salary_component": "PAYE"})
        ss.append("deductions", {"salary_component": "SHIF", "amount_based_on_formula": 1, "formula": f"gross_pay * {S['SHIF_RATE']}"})
        ss.append("deductions", {"salary_component": "NSSF", "amount_based_on_formula": 1, "formula": f"gross_pay * {S['NSSF_RATE']}"})
        ss.append("deductions", {"salary_component": "Housing Levy", "amount_based_on_formula": 1, "formula": f"gross_pay * {S['HOUSING_LEVY_RATE']}"})
        ss.append("deductions", {"salary_component": "NITA", "amount": S["NITA_AMOUNT"]})
        ss.save(ignore_permissions=True)
        ss.submit()

    # ── Sample employee + assignment (gated) ──
    if CFG["SAMPLE_DATA"] in ("draft", "submit"):
        try:
            _sample_employee_assignment(tbi)
        except Exception as e:
            log(f"sample payroll skipped: {e}")
    else:
        log("SAMPLE_DATA=masters — skipping sample employee/assignment")


def _sample_employee_assignment(tbi):
    if not frappe.db.exists("Employee", {"employee_name": "Sample TBI Engineer"}):
        emp = frappe.new_doc("Employee")
        emp.update({
            "first_name": "Sample", "last_name": "TBI Engineer",
            "company": tbi, "gender": "Other",
            "date_of_birth": "1995-01-01", "date_of_joining": "2026-01-01",
            "status": "Active",
        })
        emp.insert(ignore_permissions=True)
        log(f"created sample Employee: {emp.name}")
    emp_name = frappe.db.get_value("Employee", {"employee_name": "Sample TBI Engineer"}, "name")

    if not frappe.db.exists("Salary Structure Assignment",
                            {"employee": emp_name, "salary_structure": "TBI Standard KES"}):
        ssa = frappe.new_doc("Salary Structure Assignment")
        ssa.update({
            "employee": emp_name, "salary_structure": "TBI Standard KES",
            "company": tbi, "from_date": "2026-01-01", "base": 150000,
            "income_tax_slab": "Kenya PAYE",
        })
        ssa.insert(ignore_permissions=True)
        ssa.submit()  # no GL impact; required to run payroll
        log(f"created Salary Structure Assignment for {emp_name}")


def _journal(tbi):
    """Best-effort sample monthly payroll Journal Entry (draft)."""
    marker = "SAMPLE-TBI-PAYROLL-2026-06"
    if frappe.db.exists("Journal Entry", {"cheque_no": marker}):
        log("sample payroll JE already exists; skipping")
        return
    gross = 150000.0
    shif = round(gross * S["SHIF_RATE"], 2)
    nssf = round(gross * S["NSSF_RATE"], 2)
    housing = round(gross * S["HOUSING_LEVY_RATE"], 2)
    paye = round(gross * 0.25, 2)  # rough proxy; refine with real bands
    net = round(gross - shif - nssf - housing - paye, 2)

    def acc(name):
        return find_account(tbi, account_name=name)

    je = frappe.new_doc("Journal Entry")
    je.voucher_type = "Journal Entry"
    je.company = tbi
    je.posting_date = "2026-06-30"
    je.cheque_no = marker
    je.cheque_date = "2026-06-30"
    je.user_remark = "Sample TBI payroll (journal simulation)"
    je.append("accounts", {"account": acc("Salaries and Wages"), "debit_in_account_currency": gross})
    for liab, amt in [("PAYE Payable", paye), ("SHIF Payable", shif),
                      ("NSSF Payable", nssf), ("Housing Levy Payable", housing)]:
        je.append("accounts", {"account": acc(liab), "credit_in_account_currency": amt})
    # Net pay credited to the default payable/bank-clearing — use Creditors if present.
    net_acc = acc("Creditors") or acc("Accounts Payable")
    je.append("accounts", {"account": net_acc, "credit_in_account_currency": net})
    je.insert(ignore_permissions=True)
    log(f"created sample payroll Journal Entry (draft): {je.name}")


def _main():
    tbi = company("TBI")
    backend = CFG["PAYROLL_BACKEND"]
    if backend == "hrms":
        _hrms(tbi)
    elif backend == "journal":
        if CFG["SAMPLE_DATA"] in ("draft", "submit"):
            try:
                _journal(tbi)
            except Exception as e:
                log(f"journal sample skipped: {e}")
        else:
            log("journal backend + SAMPLE_DATA=masters — nothing to create")
    else:
        raise RuntimeError(f"Unknown PAYROLL_BACKEND={backend} (expected hrms|journal)")


run_module(_main, "60_payroll_tbi")
