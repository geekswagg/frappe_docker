# 30 — Item Group tree and KES price lists. Foundation for the Microsoft catalog (40),
# IP products (45), and engineering labour (50). All pricing is KES (the group currency);
# add USD price lists later if you resell Microsoft licences in USD.

ROOT = "All Item Groups"

GROUPS = [
    # (name, parent, is_group)
    ("Microsoft", ROOT, 1),
    ("Microsoft Licenses", "Microsoft", 0),
    ("Microsoft Services", "Microsoft", 0),
    ("Services", ROOT, 1),
    ("Professional Services", "Services", 0),
    ("Managed Services", "Services", 0),
    ("Engineering Labour", "Services", 0),
    ("IP Products", ROOT, 1),
    ("Software Licenses", "IP Products", 0),
    ("Subscriptions", "IP Products", 0),
]


def _main():
    for name, parent, is_group in GROUPS:
        get_or_create(
            "Item Group",
            {"item_group_name": name},
            {"parent_item_group": parent, "is_group": is_group},
        )

    # KES price lists used by all catalog modules.
    get_or_create("Price List", {"price_list_name": "Centauri Selling"},
                  {"selling": 1, "buying": 0, "currency": CFG["CURRENCY"], "enabled": 1})
    get_or_create("Price List", {"price_list_name": "Centauri Buying"},
                  {"selling": 0, "buying": 1, "currency": CFG["CURRENCY"], "enabled": 1})

    # Microsoft brand for catalog items.
    get_or_create("Brand", {"brand": "Microsoft"}, {})


run_module(_main, "30_item_groups_pricelists")
