# 15 — Statutory tax accounts, intercompany control accounts, and payroll accounts.
# Applied to ALL four companies so the chart of accounts is consistent across the group
# (matches what was added by hand in the UI; recreates it on a fresh database).
#
# ensure_account() matches by NAME first, so template-provided accounts (e.g. VAT) are
# never duplicated, and it drops the number if the CoA template already occupies it.


def _main():
    companies = [company(k) for k in ("GROUP", "CC", "GKT", "TBI")]

    for co in companies:
        # ── Statutory liabilities under "Duties and Taxes" (Kenya) ──
        duties = ["Duties and Taxes"]
        ensure_account(co, "VAT", account_type="Tax", number=2315, parent_names=duties)
        ensure_account(co, "PAYE Payable", account_type="Tax", number=2320, parent_names=duties)
        ensure_account(co, "SHIF Payable", account_type="Tax", number=2330, parent_names=duties)
        ensure_account(co, "NSSF Payable", account_type="Tax", number=2340, parent_names=duties)
        ensure_account(co, "Housing Levy Payable", account_type="Tax", number=2360, parent_names=duties)
        ensure_account(co, "NITA Payable", account_type="Tax", number=2370, parent_names=duties)

        # ── Intercompany control accounts ──
        ensure_account(
            co, "Intercompany Receivable", account_type="Receivable", number=1190,
            parent_names=["Accounts Receivable", "Current Assets", "Application of Funds (Assets)"],
        )
        ensure_account(
            co, "Intercompany Payable", account_type="Payable", number=2190,
            parent_names=["Accounts Payable", "Current Liabilities", "Source of Funds (Liabilities)"],
        )

        # ── Payroll expense accounts (earnings + employer statutory cost) ──
        expense_parents = ["Direct Expenses", "Indirect Expenses", "Expenses"]
        ensure_account(co, "Salaries and Wages", account_type="", number=5110, parent_names=expense_parents)
        ensure_account(co, "Staff Statutory Contributions", account_type="", number=5120, parent_names=expense_parents)


run_module(_main, "15_accounts_tax")
