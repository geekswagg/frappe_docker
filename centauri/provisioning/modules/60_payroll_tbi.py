# 60 — TBI payroll (hrms). Real Kenyan statutory payroll with verifiable formulas.
#
# Employee deductions on the payslip: NSSF (6%, capped at the Tier-II ceiling),
# SHIF (2.75%, floored at 300), Housing Levy (1.5%), PAYE (monthly bands with the
# personal-relief credit). PAYE is computed by formula via two statistical helper
# components so the math stays readable:
#   Taxable Pay (TP)        = gross_pay - NSSF
#   PAYE Before Relief (PBR)= monthly band tax on TP
#   PAYE                    = max(PBR - personal_relief, 0)
#
# Employer statutory costs (NITA, employer NSSF/Housing matches) are NOT employee
# deductions — book them via Journal Entry to the 2300/5120 accounts (out of slip scope).
#
# Rates come from CFG["STATUTORY"]; verify against current Kenyan law before go-live.
# Formulas are evaluated by hrms at salary-slip time, not during provisioning.

S = CFG["STATUTORY"]
STRUCTURE = "TBI Standard KES"

# ── Statutory formulas (hrms salary-slip expressions) ────────────────────────
NSSF_F = (f"(gross_pay if gross_pay <= {int(S['NSSF_TIER2_LIMIT'])} "
          f"else {int(S['NSSF_TIER2_LIMIT'])}) * {S['NSSF_RATE']}")
SHIF_F = (f"(gross_pay * {S['SHIF_RATE']} if gross_pay * {S['SHIF_RATE']} >= {S['SHIF_MIN']} "
          f"else {S['SHIF_MIN']})")
HOUSING_F = f"gross_pay * {S['HOUSING_LEVY_RATE']}"
TP_F = "gross_pay - NSSF"                      # taxable pay = gross - employee NSSF
PBR_F = ("(TP*0.10 if TP<=24000 else "
         "2400+(TP-24000)*0.25 if TP<=32333 else "
         "4483.25+(TP-32333)*0.30 if TP<=500000 else "
         "144783.35+(TP-500000)*0.325 if TP<=800000 else "
         "242283.35+(TP-800000)*0.35)")
_R = S["PAYE_PERSONAL_RELIEF"]
PAYE_F = f"(PBR - {_R} if PBR > {_R} else 0)"


def _hr_settings():
    """Unblock Employee creation by setting a naming system if none is configured.

    Read the STORED value (not the in-memory default, which masks an unsaved blank),
    then persist. 'Full Name' avoids any naming-series dependency.
    """
    try:
        stored = frappe.db.get_single_value("HR Settings", "emp_created_by")
        if stored:
            return
        hs = frappe.get_single("HR Settings")
        hs.emp_created_by = "Full Name"
        hs.save(ignore_permissions=True)
        frappe.db.commit()
        frappe.clear_cache(doctype="HR Settings")
        log("HR Settings: emp_created_by = Full Name")
    except Exception as e:
        log(f"HR Settings step skipped: {e}")


def _component(name, abbr, ctype, account_name=None, tbi=None, **extra):
    """Idempotently ensure a Salary Component with the exact desired config."""
    # Deductions are % of gross_pay (already payment-days-prorated), so they must NOT
    # depend on payment days too — hrms rejects that as double-proration.
    fields = {"type": ctype, "salary_component_abbr": abbr,
              "depends_on_payment_days": 1 if ctype == "Earning" else 0}
    fields.update(extra)
    get_or_create("Salary Component", {"salary_component": name}, dict(fields))
    ensure_value("Salary Component", name, fields)   # enforce config on existing too
    if account_name and tbi:
        doc = frappe.get_doc("Salary Component", name)
        acc = find_account(tbi, account_name=account_name)
        if acc:
            fld = ("default_account"
                   if frappe.get_meta("Salary Component Account").has_field("default_account")
                   else "account")
            ensure_child(doc, "accounts", ["company"], {"company": tbi, fld: acc})
            doc.save(ignore_permissions=True)


def _components(tbi):
    # Earnings
    _component("Basic", "B", "Earning", "Salaries and Wages", tbi,
               amount_based_on_formula=1, formula="base", depends_on_payment_days=1)
    _component("House Rent Allowance", "HRA", "Earning", "Salaries and Wages", tbi,
               amount_based_on_formula=1, formula="base * 0.15")
    _component("Transport Allowance", "TA", "Earning", "Salaries and Wages", tbi)
    # Employee statutory deductions
    _component("NSSF", "NSSF", "Deduction", "NSSF Payable", tbi,
               amount_based_on_formula=1, formula=NSSF_F, variable_based_on_taxable_salary=0)
    # Statistical helpers for PAYE (excluded from totals/net)
    _component("Taxable Pay", "TP", "Deduction", None, None,
               amount_based_on_formula=1, formula=TP_F, statistical_component=1)
    _component("PAYE Before Relief", "PBR", "Deduction", None, None,
               amount_based_on_formula=1, formula=PBR_F, statistical_component=1)
    _component("PAYE", "PAYE", "Deduction", "PAYE Payable", tbi,
               amount_based_on_formula=1, formula=PAYE_F, variable_based_on_taxable_salary=0)
    _component("SHIF", "SHIF", "Deduction", "SHIF Payable", tbi,
               amount_based_on_formula=1, formula=SHIF_F)
    _component("Housing Levy", "HL", "Deduction", "Housing Levy Payable", tbi,
               amount_based_on_formula=1, formula=HOUSING_F)


def _structure(tbi):
    """(Re)build the salary structure. Order matters: NSSF -> TP -> PBR -> PAYE."""
    existing = frappe.db.exists("Salary Structure", STRUCTURE)
    if existing:
        doc = frappe.get_doc("Salary Structure", STRUCTURE)
        if any(r.salary_component == "Taxable Pay" for r in doc.get("deductions")):
            log("Salary Structure already improved; leaving as-is")
            return
        # Pre-payroll rebuild (safe while no salary slips reference it).
        if doc.docstatus == 1:
            doc.cancel()
        frappe.delete_doc("Salary Structure", STRUCTURE, force=1, ignore_permissions=True)
        log("rebuilding Salary Structure with improved statutory formulas")

    def _rows(d):
        d.append("earnings", {"salary_component": "Basic", "amount_based_on_formula": 1, "formula": "base"})
        d.append("earnings", {"salary_component": "House Rent Allowance", "amount_based_on_formula": 1, "formula": "base * 0.15"})
        d.append("earnings", {"salary_component": "Transport Allowance", "amount": 5000})
        d.append("deductions", {"salary_component": "NSSF", "amount_based_on_formula": 1, "formula": NSSF_F, "depends_on_payment_days": 0})
        d.append("deductions", {"salary_component": "Taxable Pay", "amount_based_on_formula": 1, "formula": TP_F, "statistical_component": 1, "depends_on_payment_days": 0})
        d.append("deductions", {"salary_component": "PAYE Before Relief", "amount_based_on_formula": 1, "formula": PBR_F, "statistical_component": 1, "depends_on_payment_days": 0})
        d.append("deductions", {"salary_component": "PAYE", "amount_based_on_formula": 1, "formula": PAYE_F, "depends_on_payment_days": 0})
        d.append("deductions", {"salary_component": "SHIF", "amount_based_on_formula": 1, "formula": SHIF_F, "depends_on_payment_days": 0})
        d.append("deductions", {"salary_component": "Housing Levy", "amount_based_on_formula": 1, "formula": HOUSING_F, "depends_on_payment_days": 0})

    ss = get_or_create("Salary Structure", {"name": STRUCTURE},
                       {"company": tbi, "currency": CFG["CURRENCY"],
                        "payroll_frequency": "Monthly", "is_active": "Yes"},
                       child_setup=_rows)
    if ss.docstatus == 0:
        ss.submit()


def _ensure_holiday_list(tbi):
    """A Holiday List is required to compute a Salary Slip; create one (weekly off
    Sunday) and set it as the company default so payslips can be generated."""
    def _weekly(doc):
        try:
            doc.get_weekly_off_dates()
        except Exception:
            pass
    get_or_create("Holiday List", {"holiday_list_name": "Kenya 2026"},
                  {"from_date": "2026-01-01", "to_date": "2026-12-31", "weekly_off": "Sunday"},
                  child_setup=_weekly)
    if frappe.get_meta("Company").has_field("default_holiday_list"):
        ensure_value("Company", tbi, {"default_holiday_list": "Kenya 2026"})


def _drop_old_slab():
    """PAYE is now formula-based; remove the unused 'Kenya PAYE' Income Tax Slab if present."""
    name = frappe.db.exists("Income Tax Slab", "Kenya PAYE")
    if not name:
        return
    try:
        doc = frappe.get_doc("Income Tax Slab", name)
        if doc.docstatus == 1:
            doc.cancel()
        frappe.delete_doc("Income Tax Slab", name, force=1, ignore_permissions=True)
        log("removed unused Income Tax Slab 'Kenya PAYE'")
    except Exception as e:
        log(f"could not remove old Income Tax Slab (harmless): {e}")


def _compute(base):
    """Formula-derived monthly breakdown for a given base — mirrors the structure."""
    gross = base + base * 0.15 + 5000.0          # Basic + HRA(15%) + Transport(5000)
    nssf = round(min(gross, S["NSSF_TIER2_LIMIT"]) * S["NSSF_RATE"], 2)
    shif = round(max(gross * S["SHIF_RATE"], S["SHIF_MIN"]), 2)
    housing = round(gross * S["HOUSING_LEVY_RATE"], 2)
    tp = gross - nssf
    if tp <= 24000:
        pbr = tp * 0.10
    elif tp <= 32333:
        pbr = 2400 + (tp - 24000) * 0.25
    elif tp <= 500000:
        pbr = 4483.25 + (tp - 32333) * 0.30
    elif tp <= 800000:
        pbr = 144783.35 + (tp - 500000) * 0.325
    else:
        pbr = 242283.35 + (tp - 800000) * 0.35
    paye = round(max(pbr - S["PAYE_PERSONAL_RELIEF"], 0), 2)
    ded = round(nssf + shif + housing + paye, 2)
    return {"gross": round(gross, 2), "nssf": nssf, "shif": shif, "housing": housing,
            "paye": paye, "deductions": ded, "net": round(gross - ded, 2)}


def _assign_holiday_list(tbi, emp_name):
    """Newer hrms resolves holidays via a 'Holiday List Assignment' doctype. Create one
    schema-adaptively (only setting fields that exist) so the live slip can compute."""
    if not frappe.db.exists("DocType", "Holiday List Assignment"):
        return
    try:
        if frappe.db.exists("Holiday List Assignment", {"holiday_list": "Kenya 2026"}):
            return
        meta = frappe.get_meta("Holiday List Assignment")

        def setf(f, v):
            if meta.has_field(f):
                doc.set(f, v)

        doc = frappe.new_doc("Holiday List Assignment")
        setf("holiday_list", "Kenya 2026")
        setf("company", tbi)
        setf("employee", emp_name)
        setf("applicable_for", "Company")
        for f in ("from_date", "applicable_from", "effective_from", "start_date", "date"):
            setf(f, "2026-01-01")
        doc.insert(ignore_permissions=True)
        if meta.is_submittable:
            doc.submit()
        log("created Holiday List Assignment (Kenya 2026)")
    except Exception as e:
        log(f"holiday list assignment skipped: {e}")


def _sample(tbi):
    """Sample employee + assignment + a computed salary slip to demonstrate the math."""
    base = 150000
    b = _compute(base)
    log(f"expected payslip (base {base:,}): gross={b['gross']} NSSF={b['nssf']} "
        f"SHIF={b['shif']} Housing={b['housing']} PAYE={b['paye']} "
        f"deductions={b['deductions']} net={b['net']}")

    if not frappe.db.exists("Employee", {"employee_name": "Sample TBI Engineer"}):
        emp = frappe.new_doc("Employee")
        emp.update({"first_name": "Sample", "last_name": "TBI Engineer", "company": tbi,
                    "employee_number": "TBI-001", "gender": "Male",
                    "date_of_birth": "1995-01-01", "date_of_joining": "2026-01-01",
                    "status": "Active", "holiday_list": "Kenya 2026"})
        emp.insert(ignore_permissions=True)
        log(f"created sample Employee: {emp.name}")
    emp_name = frappe.db.get_value("Employee", {"employee_name": "Sample TBI Engineer"}, "name")
    if frappe.get_meta("Employee").has_field("holiday_list"):
        ensure_value("Employee", emp_name, {"holiday_list": "Kenya 2026"})
    frappe.clear_document_cache("Employee", emp_name)
    frappe.clear_document_cache("Company", tbi)
    _assign_holiday_list(tbi, emp_name)

    if not frappe.db.exists("Salary Structure Assignment",
                            {"employee": emp_name, "salary_structure": STRUCTURE, "docstatus": ["<", 2]}):
        ssa = frappe.new_doc("Salary Structure Assignment")
        ssa.update({"employee": emp_name, "salary_structure": STRUCTURE, "company": tbi,
                    "from_date": "2026-01-01", "base": base})
        ssa.insert(ignore_permissions=True)
        ssa.submit()
        log(f"assigned {STRUCTURE} to {emp_name} (base {base:,})")

    if not frappe.db.exists("Salary Slip", {"employee": emp_name, "start_date": "2026-06-01"}):
        slip = frappe.new_doc("Salary Slip")
        slip.update({"employee": emp_name, "company": tbi, "start_date": "2026-06-01",
                     "end_date": "2026-06-30", "payroll_frequency": "Monthly"})
        slip.insert(ignore_permissions=True)   # triggers full computation
        if CFG["SAMPLE_DATA"] == "submit":
            slip.submit()
        log(f"sample Salary Slip {slip.name}: gross={slip.get('gross_pay')} "
            f"deductions={slip.get('total_deduction')} net={slip.get('net_pay')} ({CFG['SAMPLE_DATA']})")


def _hrms(tbi):
    if not frappe.db.exists("DocType", "Salary Component"):
        raise RuntimeError(
            "hrms app not installed — build the custom image (centauri/scripts/build-image.sh), "
            "set CUSTOM_IMAGE/CUSTOM_TAG/PULL_POLICY in .env, recreate the stack, then re-run. "
            "Or set PAYROLL_BACKEND=journal.")
    _hr_settings()
    _drop_old_slab()
    _components(tbi)
    _structure(tbi)
    _ensure_holiday_list(tbi)
    if CFG["SAMPLE_DATA"] in ("draft", "submit"):
        try:
            _sample(tbi)
        except Exception as e:
            log(f"sample payroll skipped: {e}")
    else:
        log("SAMPLE_DATA=masters — skipping sample employee/slip")


def _journal(tbi):
    """Best-effort sample monthly payroll Journal Entry (draft)."""
    marker = "SAMPLE-TBI-PAYROLL-2026-06"
    if frappe.db.exists("Journal Entry", {"cheque_no": marker}):
        log("sample payroll JE already exists; skipping")
        return
    gross = 150000.0
    nssf = round(min(gross, S["NSSF_TIER2_LIMIT"]) * S["NSSF_RATE"], 2)
    shif = round(max(gross * S["SHIF_RATE"], S["SHIF_MIN"]), 2)
    housing = round(gross * S["HOUSING_LEVY_RATE"], 2)
    tp = gross - nssf
    if tp <= 24000:
        pbr = tp * 0.10
    elif tp <= 32333:
        pbr = 2400 + (tp - 24000) * 0.25
    elif tp <= 500000:
        pbr = 4483.25 + (tp - 32333) * 0.30
    elif tp <= 800000:
        pbr = 144783.35 + (tp - 500000) * 0.325
    else:
        pbr = 242283.35 + (tp - 800000) * 0.35
    paye = round(max(pbr - S["PAYE_PERSONAL_RELIEF"], 0), 2)
    net = round(gross - nssf - shif - housing - paye, 2)

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
    net_acc = acc("Creditors") or acc("Accounts Payable")
    je.append("accounts", {"account": net_acc, "credit_in_account_currency": net})
    je.insert(ignore_permissions=True)
    log(f"created sample payroll Journal Entry (draft): {je.name} "
        f"(gross={gross} paye={paye} nssf={nssf} shif={shif} housing={housing} net={net})")


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
