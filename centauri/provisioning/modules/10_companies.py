# 10 — Companies. Codifies the OHL group + CC/GKT/TBI subsidiaries.
# get_or_create matches the companies already made in the UI (no duplicates); on a
# fresh post-teardown database it recreates them identically (the reproducibility goal).
# Order matters: the group must exist before subsidiaries reference it as parent.


def _main():
    order = ["GROUP", "CC", "GKT", "TBI"]
    for key in order:
        name, abbr, is_group, parent_key = CFG["COMPANIES"][key]
        parent = company(parent_key) if parent_key else None

        defaults = {
            "abbr": abbr,
            "default_currency": CFG["CURRENCY"],
            "country": CFG["COUNTRY"],
            "is_group": is_group,
            # Applied only on first insert; ignored for already-existing companies.
            "chart_of_accounts": "Standard with Numbers",
        }
        if parent:
            defaults["parent_company"] = parent

        get_or_create("Company", {"company_name": name}, defaults)

        # Ensure the structural fields are correct even on UI-created companies
        # (e.g. parent_company / is_group that may not have been set by hand).
        fields = {"is_group": is_group}
        if parent:
            fields["parent_company"] = parent
        ensure_value("Company", name, fields)


run_module(_main, "10_companies")
