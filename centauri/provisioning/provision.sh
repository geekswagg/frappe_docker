#!/bin/bash
# Centauri ERPNext group provisioning — idempotent, code-as-config.
#
# Runs each module in centauri/provisioning/modules/ in numeric order by streaming
# lib/_lib.py + the module into `bench console` inside the backend container. A module
# is considered successful only if it prints `PROVISION_OK <name>` (bench console does
# not reliably exit non-zero on a Python exception, so the sentinel is the source of
# truth). Safe to re-run — modules are idempotent.
#
# Usage:
#   ./provision.sh                 # run all modules in order (after hrms pre-step)
#   ./provision.sh --only 40       # run just modules/40_*.py
#   ./provision.sh --from 30       # run modules/30_*.py onward
#   ./provision.sh --dry-run       # print the plan, execute nothing
#   ./provision.sh --skip-hrms     # skip the HRMS install/migrate pre-step
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LIB="$SCRIPT_DIR/lib/_lib.py"
MODULES_DIR="$SCRIPT_DIR/modules"

# Load local overrides if present (gitignored); otherwise module defaults apply.
[ -f "$SCRIPT_DIR/provision.env" ] && . "$SCRIPT_DIR/provision.env"

SITE="${SITE:-erp.comwenga.com}"
PROJECT="${COMPOSE_PROJECT:-centauri}"
COMPOSE_DIR="${COMPOSE_DIR:-/opt/centauri/frappe_docker}"
PAYROLL_BACKEND="${PAYROLL_BACKEND:-hrms}"

ONLY=""; FROM=""; DRY_RUN=0; SKIP_HRMS=0
while [ $# -gt 0 ]; do
  case "$1" in
    --only) ONLY="$2"; shift 2 ;;
    --from) FROM="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --skip-hrms) SKIP_HRMS=1; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown flag: $1" >&2; exit 2 ;;
  esac
done

log() { echo "$(date -Iseconds) [provision] $*"; }

dc() { docker compose -p "$PROJECT" -f "$COMPOSE_DIR/compose.yaml" "$@"; }

# Forward provision.env knobs into the container so modules' CFG picks them up.
FORWARD_KEYS=(
  SITE DEFAULT_CURRENCY COUNTRY
  GROUP_COMPANY GROUP_ABBR CC_COMPANY CC_ABBR GKT_COMPANY GKT_ABBR TBI_COMPANY TBI_ABBR
  PAYROLL_BACKEND SAMPLE_DATA
  PAYE_PERSONAL_RELIEF SHIF_RATE SHIF_MIN HOUSING_LEVY_RATE
  NSSF_RATE NSSF_TIER1_LIMIT NSSF_TIER2_LIMIT NITA_AMOUNT
  RATE_SOFTWARE_ENGINEERING RATE_ARCHITECTURE RATE_PROJECT_MANAGEMENT SECONDMENT_BILLING_FREQUENCY
  API_USER_CRM API_USER_FINANCE API_USER_INTEGRATIONS ALLOW_CORS_ORIGIN
)
EXEC_ENV=()
for k in "${FORWARD_KEYS[@]}"; do
  [ -n "${!k:-}" ] && EXEC_ENV+=( -e "$k=${!k}" )
done

# ── HRMS pre-step (only when PAYROLL_BACKEND=hrms) ───────────────────────────
hrms_prestep() {
  [ "$PAYROLL_BACKEND" = "hrms" ] || { log "PAYROLL_BACKEND=$PAYROLL_BACKEND — skipping HRMS pre-step"; return 0; }
  [ "$SKIP_HRMS" -eq 1 ] && { log "--skip-hrms — skipping HRMS pre-step"; return 0; }
  log "Ensuring hrms app is installed on $SITE"
  if dc exec -T backend bench --site "$SITE" list-apps 2>/dev/null | grep -qx hrms; then
    log "hrms already installed"
  else
    log "Installing hrms (requires the custom image built by build-image.sh)"
    dc exec -T backend bench --site "$SITE" install-app hrms
  fi
  log "Running migrate"
  dc exec -T backend bench --site "$SITE" migrate
}

# ── Build the ordered module list, applying --only / --from ──────────────────
mapfile -t ALL_MODULES < <(ls "$MODULES_DIR"/[0-9]*.py | sort)
RUN_MODULES=()
for m in "${ALL_MODULES[@]}"; do
  num="$(basename "$m" | cut -d_ -f1)"
  # 10# forces base-10 so zero-padded prefixes (05, 08, 09) compare numerically.
  [ -n "$ONLY" ] && [ "$((10#$num))" -ne "$((10#$ONLY))" ] && continue
  [ -n "$FROM" ] && [ "$((10#$num))" -lt "$((10#$FROM))" ] && continue
  RUN_MODULES+=( "$m" )
done

if [ "$DRY_RUN" -eq 1 ]; then
  log "DRY RUN — plan only"
  [ "$PAYROLL_BACKEND" = "hrms" ] && [ "$SKIP_HRMS" -eq 0 ] && echo "  pre: install-app hrms (if missing) + migrate"
  for m in "${RUN_MODULES[@]}"; do echo "  run: $(basename "$m")"; done
  exit 0
fi

# ── Run ──────────────────────────────────────────────────────────────────────
hrms_prestep

failed=0
for m in "${RUN_MODULES[@]}"; do
  label="$(basename "$m" .py)"
  log "── ${label} ─────────────────────────────────────────────"
  set +e
  output="$(cat "$LIB" "$m" | dc exec -T "${EXEC_ENV[@]}" backend bench --site "$SITE" console 2>&1)"
  set -e
  echo "$output"
  if echo "$output" | grep -q "PROVISION_OK ${label}"; then
    log "✓ ${label}"
  else
    log "✗ ${label} — sentinel not found; treating as FAILED"
    failed=1
    break
  fi
done

if [ "$failed" -ne 0 ]; then
  log "Provisioning halted on failure. Fix and re-run (idempotent) with --from <NN>."
  exit 1
fi
log "=== Provisioning complete. Re-run any time; it is idempotent. ==="
