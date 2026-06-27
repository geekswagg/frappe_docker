#!/bin/bash
# Centauri ERPNext custom image build: frappe + erpnext + hrms (for payroll).
# Adds the HRMS app (Salary Slip, Payroll Entry, statutory deductions) which the
# official frappe/erpnext image does NOT ship. Runtime `bench get-app` is unsupported
# and lost on container recreation — the app MUST be baked into the image.
#
# Run on every node that hosts the stack (Windows desktop primary AND the Azure
# failover VM) so a promoted failover boots with the same apps. Alternatively push
# the resulting tag to a registry (e.g. Azure Container Registry) and pull on each node.
#
# Docs: docs/02-setup/02-build-setup.md, docs/01-getting-started/02-docker-immutability.md
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/centauri/frappe_docker}"
APPS_JSON="${APPS_JSON:-$REPO_DIR/apps.json}"
FRAPPE_PATH="${FRAPPE_PATH:-https://github.com/frappe/frappe}"
FRAPPE_BRANCH="${FRAPPE_BRANCH:-version-16}"
CUSTOM_IMAGE="${CUSTOM_IMAGE:-comwenga/erpnext-hrms}"
CUSTOM_TAG="${CUSTOM_TAG:-v16}"

log() { echo "$(date -Iseconds) [build-image] $*"; }

[ -f "$APPS_JSON" ] || { echo "apps.json not found at $APPS_JSON" >&2; exit 1; }

# CACHE_BUST = hash of apps.json. apps.json is passed as a BuildKit *secret*, so it is
# deliberately excluded from the layer cache key; hashing it into CACHE_BUST is what
# forces a rebuild when the app list changes (see docs/03-production/06-...md#build-cache).
CACHE_BUST="$(sha256sum "$APPS_JSON" | awk '{print $1}')"

log "Building ${CUSTOM_IMAGE}:${CUSTOM_TAG} from $APPS_JSON (frappe ${FRAPPE_BRANCH})"
log "Apps:"; sed 's/^/    /' "$APPS_JSON"

# Requires Docker Engine v23.0+ (BuildKit default) for --secret support.
docker build \
  --no-cache \
  --build-arg=FRAPPE_PATH="$FRAPPE_PATH" \
  --build-arg=FRAPPE_BRANCH="$FRAPPE_BRANCH" \
  --build-arg=CACHE_BUST="$CACHE_BUST" \
  --secret=id=apps_json,src="$APPS_JSON" \
  --tag="${CUSTOM_IMAGE}:${CUSTOM_TAG}" \
  --file="$REPO_DIR/images/layered/Containerfile" \
  "$REPO_DIR"

log "Built ${CUSTOM_IMAGE}:${CUSTOM_TAG}"
cat <<EOF

Next steps:
  1. Set these in $REPO_DIR/.env (already in centauri.env.template):
       CUSTOM_IMAGE=${CUSTOM_IMAGE}
       CUSTOM_TAG=${CUSTOM_TAG}
       PULL_POLICY=missing
  2. Recreate the stack so containers pick up the new image:
       docker compose -p centauri ... up -d
  3. Install + migrate HRMS on the site (idempotent):
       docker compose -p centauri exec -T backend \\
         bench --site erp.comwenga.com list-apps | grep -qx hrms \\
         || docker compose -p centauri exec -T backend \\
              bench --site erp.comwenga.com install-app hrms
       docker compose -p centauri exec -T backend \\
         bench --site erp.comwenga.com migrate
EOF
