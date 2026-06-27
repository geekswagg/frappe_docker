# 40 — Centauri's Microsoft partner catalog. Representative SKUs per pillar.
# Licences are modelled as resale items (buying + selling price = margin); services
# are sell-only. Prices are ILLUSTRATIVE KES/unit — adjust to your partner pricing.
# Items are company-agnostic in ERPNext (sellable from any company); Centauri is the
# intended seller.

# Licences (resale): item_code, name, group, buy KES, sell KES
LICENSES = [
    ("M365-BP",   "Microsoft 365 Business Premium",        "Microsoft Licenses", 2400, 3000),
    ("M365-E3",   "Microsoft 365 E3",                       "Microsoft Licenses", 4200, 5200),
    ("M365-E5",   "Microsoft 365 E5",                       "Microsoft Licenses", 7200, 8800),
    ("O365-E1",   "Office 365 E1",                          "Microsoft Licenses", 1400, 1800),
    ("D365-SALES","Dynamics 365 Sales Enterprise",          "Microsoft Licenses", 11000, 13500),
    ("D365-BC",   "Dynamics 365 Business Central Essentials","Microsoft Licenses", 9500, 11500),
    ("PBI-PRO",   "Power BI Pro",                           "Microsoft Licenses", 1500, 1900),
    ("POWERAPPS", "Power Apps per user",                    "Microsoft Licenses", 700, 950),
    ("AZURE-CONS","Azure Consumption (metered, per KES100)", "Microsoft Licenses", 100, 115),
]

# Services (sell-only): item_code, name, group, sell KES
SERVICES = [
    ("SVC-M365-IMPL", "Microsoft 365 Implementation",                "Microsoft Services", 250000),
    ("SVC-AZ-MIGR",   "Azure Migration",                             "Microsoft Services", 600000),
    ("SVC-D365-IMPL", "Dynamics 365 Implementation",                 "Microsoft Services", 900000),
    ("SVC-SECURITY",  "Microsoft Security Assessment (Defender/Sentinel/Entra)", "Microsoft Services", 400000),
    ("MS-M365",       "Managed Microsoft 365 (monthly)",             "Managed Services", 120000),
    ("MS-AZURE",      "Managed Azure (monthly)",                     "Managed Services", 180000),
    ("MS-SOC",        "Managed SOC — Microsoft Sentinel (monthly)",  "Managed Services", 350000),
]


def _main():
    for code, name, group, buy, sell in LICENSES:
        ensure_item(code, name, group, is_sales=1, is_purchase=1, brand="Microsoft")
        ensure_item_price(code, "Centauri Buying", buy)
        ensure_item_price(code, "Centauri Selling", sell)

    for code, name, group, sell in SERVICES:
        ensure_item(code, name, group, is_sales=1, is_purchase=0, brand="Microsoft")
        ensure_item_price(code, "Centauri Selling", sell)


run_module(_main, "40_microsoft_catalog")
