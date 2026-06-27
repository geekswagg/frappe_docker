# 05 — Fiscal Years (calendar year). Fiscal years are global, shared by all companies.
# Ensures 2025 and 2026 exist and are enabled, fixing the earlier disabled / Jan-5 mess
# permanently in code so a rebuild never reproduces it.


def _main():
    years = [
        ("2025", "2025-01-01", "2025-12-31"),
        ("2026", "2026-01-01", "2026-12-31"),
    ]
    for year, start, end in years:
        get_or_create(
            "Fiscal Year",
            {"year": year},
            {"year_start_date": start, "year_end_date": end, "disabled": 0},
        )
        # Repair an existing-but-wrong/disabled record (e.g. the earlier 05-01 start).
        ensure_value("Fiscal Year", year, {
            "year_start_date": start,
            "year_end_date": end,
            "disabled": 0,
        })


run_module(_main, "05_fiscal_year")
