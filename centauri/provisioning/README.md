# Centauri Provisioning — ERPNext group configuration as code

Idempotent, version-controlled configuration for the Omwenga Holdings group on
`erp.comwenga.com`. Replaces click-ops with re-runnable scripts so the entire group
setup — companies, statutory accounts, intercompany wiring, Microsoft & IP catalogs,
secondment billing, TBI payroll, and API integration users — can be rebuilt from scratch
and **survives any database teardown**.

## How it works

Each module in `modules/` is a Python script run server-side through the Frappe ORM. The
orchestrator streams `lib/_lib.py` + the module into `bench console` inside the backend
container:

```bash
cat lib/_lib.py modules/10_companies.py \
  | docker compose -p centauri exec -T backend bench --site erp.comwenga.com console
```

`bench console` does not reliably exit non-zero on a Python error, so every module prints
`PROVISION_OK <name>` on success and the orchestrator treats its absence as a failure.

**Idempotency contract:** existence-checked inserts (never blind), existing docs mutated
only when a value actually changed, one commit per module, sample transactions guarded by
a deterministic key. Safe to re-run — a second run creates nothing new.

## Prerequisites

1. The stack is up and the site `erp.comwenga.com` exists (Phase 1 of the runbook).
2. For real payroll (`PAYROLL_BACKEND=hrms`, the default), the **custom image with hrms**
   is built and in use:
   ```bash
   bash ../scripts/build-image.sh          # builds comwenga/erpnext-hrms:v16 from apps.json
   # set CUSTOM_IMAGE / CUSTOM_TAG / PULL_POLICY in ../../.env  (already in the template)
   # recreate the stack so containers use the new image
   ```
   `provision.sh` runs `install-app hrms` + `migrate` automatically as a pre-step.
   To skip payroll entirely for now, set `PAYROLL_BACKEND=journal` (no image rebuild).

## Quick start

```bash
cp provision.env.template provision.env     # gitignored; tune rates / usernames / SAMPLE_DATA
nano provision.env

./provision.sh --dry-run                    # print the ordered plan, execute nothing
./provision.sh                              # run everything (idempotent)
./provision.sh                              # run again — proves idempotency (no new docs)
```

Useful flags: `--only 40` (one module), `--from 30` (resume from a module), `--skip-hrms`
(skip the install/migrate pre-step), `--dry-run`.

## Modules (run in numeric order; the number is the dependency order)

| Module | Creates |
|---|---|
| `05_fiscal_year` | Calendar fiscal years 2025 + 2026, enabled |
| `10_companies` | OHL (group) + Centauri / Giktek Ventures / Techno Brain Incubator |
| `15_accounts_tax` | Kenya statutory accounts (PAYE/SHIF/NSSF/Housing/NITA/VAT), intercompany + payroll accounts |
| `20_intercompany` | Internal Customers (GKT, TBI) + internal Supplier (CC) for inter-company invoicing |
| `30_item_groups_pricelists` | Item Group tree + KES Selling/Buying price lists + brands |
| `40_microsoft_catalog` | Microsoft licences (resale) + professional/managed services + prices |
| `45_ip_products` | Centauri IP products/subscriptions + intercompany royalty item |
| `50_secondment_billing` | Activity Types, engagement Projects, labour item, sample Timesheet→Invoice |
| `60_payroll_tbi` | hrms: Income Tax Slab, Salary Components/Structure (+ sample Employee); or journal sim |
| `70_dimensions` | Accounting Dimensions: Revenue Stream, GTM Channel (+ seed values) |
| `80_integration_users` | Scoped API users + generated keys, CORS, sample webhook |

## Configuration (`provision.env`)

All knobs (site, company names/abbrs, `PAYROLL_BACKEND`, Kenya statutory rates, secondment
hourly rates, `SAMPLE_DATA`, API usernames, CORS origin) live in `provision.env`. The
orchestrator forwards them into the container; every key also has a safe default in
`lib/_lib.py` (`CFG`), so modules run correctly even with no env file.

`SAMPLE_DATA` controls demonstration transactions: `masters` (none), `draft` (created but
not posted — default, teardown-clean), `submit` (posted to the ledger).

## API keys

`80_integration_users` prints generated credentials as `APIKEY <user> <key>:<secret>`
lines. **The secret is shown only once.** Capture them and store in Azure Key Vault
(the same secrets store used by `centauri/scripts/backup.sh`). Re-running the module does
**not** rotate existing keys.

## Statutory rates — verify before payroll go-live

`60_payroll_tbi` encodes current Kenya rates (SHIF 2.75%, Housing Levy 1.5%, NSSF 6%,
NITA 50, PAYE bands via Income Tax Slab) as a faithful simulation, not tax advice. Confirm
against current law and adjust `provision.env` before running real payroll.

## Verification

After a run, expect one `PROVISION_OK` line per module. Spot-check in the UI: four
companies under OHL; statutory accounts under each `2300 Duties and Taxes`; internal
customers/suppliers; Microsoft + IP item groups populated; a draft intercompany Sales
Invoice; the `TBI Standard KES` salary structure; three API users with keys; the two
Accounting Dimensions. Then **re-run `./provision.sh`** — it should report success with no
newly created documents.
