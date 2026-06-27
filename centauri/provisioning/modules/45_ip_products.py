# 45 — Centauri IP products layer. Centauri's own software, modelled two ways
# (per the user's decision):
#   1. Sellable products/subscriptions licensed to external clients.
#   2. IP licensed to GKT/TBI for an intercompany royalty (the IP-ROYALTY item is what
#      Centauri bills the subsidiaries — see 50_secondment_billing for the billing flow).
# These are PLACEHOLDER products — rename/extend with your real IP once defined.

# Sellable IP: item_code, name, group, sell KES
IP_PRODUCTS = [
    ("IP-PLATFORM",   "Centauri Platform — Enterprise Licence (annual)", "Software Licenses", 1500000),
    ("IP-PLATFORM-SB","Centauri Platform — SMB Licence (annual)",        "Software Licenses", 450000),
    ("IP-MODULE-A",   "Centauri Module A — Subscription (monthly)",      "Subscriptions",     45000),
    ("IP-MODULE-B",   "Centauri Module B — Subscription (monthly)",      "Subscriptions",     65000),
]

# Intercompany royalty item (CC bills GKT/TBI to use Centauri IP).
ROYALTY = ("IP-ROYALTY", "Centauri IP Royalty (per period)", "Software Licenses", 100000)


def _main():
    # Brand must exist before items link to it.
    get_or_create("Brand", {"brand": "Centauri"}, {})

    for code, name, group, sell in IP_PRODUCTS:
        ensure_item(code, name, group, is_sales=1, is_purchase=0, brand="Centauri")
        ensure_item_price(code, "Centauri Selling", sell)

    code, name, group, sell = ROYALTY
    ensure_item(code, name, group, is_sales=1, is_purchase=1, brand="Centauri")
    ensure_item_price(code, "Centauri Selling", sell)


run_module(_main, "45_ip_products")
