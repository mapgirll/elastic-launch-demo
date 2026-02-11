#!/usr/bin/env bash
#
# setup-exec-dashboard.sh — Import NOVA-7 Executive Dashboard via Kibana
#                            Saved Objects Import API.
#
# Uses multipart NDJSON upload to:
#   POST <KIBANA>/api/saved_objects/_import?overwrite=true
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Load environment ──────────────────────────────────────────────────────────
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

# ── Validate environment ─────────────────────────────────────────────────────
for var in KIBANA_URL ELASTIC_API_KEY; do
    if [[ -z "${!var:-}" ]]; then
        echo "ERROR: $var is not set. Check your .env file."
        exit 1
    fi
done

KIBANA_URL="${KIBANA_URL%/}"

# ── Helpers ───────────────────────────────────────────────────────────────────
log_info()  { echo "[INFO]  $*"; }
log_ok()    { echo "[OK]    $*"; }
log_warn()  { echo "[WARN]  $*"; }
log_error() { echo "[ERROR] $*"; }

echo ""
log_info "=========================================="
log_info "NOVA-7 Executive Dashboard Import"
log_info "=========================================="
log_info "Kibana: ${KIBANA_URL}"
echo ""

# ── Ensure data view exists ──────────────────────────────────────────────────
log_info "Ensuring 'logs*' data view exists..."

# Create or update the logs* data view (does NOT touch the managed logs-* view)
dv_response=$(curl -s -w "\n%{http_code}" \
    -X POST "${KIBANA_URL}/api/data_views/data_view" \
    -H "Content-Type: application/json" \
    -H "kbn-xsrf: true" \
    -H "Authorization: ApiKey ${ELASTIC_API_KEY}" \
    -d '{
  "data_view": {
    "id": "logs*",
    "title": "logs*",
    "name": "NOVA-7 Logs",
    "timeFieldName": "@timestamp"
  },
  "override": true
}')

dv_code=$(echo "$dv_response" | tail -1)
if [[ "$dv_code" -ge 200 && "$dv_code" -lt 300 ]]; then
    log_ok "Data view 'logs*' created/updated."
else
    log_warn "Could not create data view (HTTP $dv_code). Dashboard may not load correctly."
fi

# ── Ensure traces-* data view exists ─────────────────────────────────────────
log_info "Ensuring 'traces-*' data view exists..."

# Create or update the traces-* data view
tr_response=$(curl -s -w "\n%{http_code}" \
    -X POST "${KIBANA_URL}/api/data_views/data_view" \
    -H "Content-Type: application/json" \
    -H "kbn-xsrf: true" \
    -H "Authorization: ApiKey ${ELASTIC_API_KEY}" \
    -d '{
  "data_view": {
    "id": "traces-*",
    "title": "traces-*",
    "name": "NOVA-7 Traces",
    "timeFieldName": "@timestamp"
  },
  "override": true
}')

tr_code=$(echo "$tr_response" | tail -1)
if [[ "$tr_code" -ge 200 && "$tr_code" -lt 300 ]]; then
    log_ok "Data view 'traces-*' created/updated."
else
    log_warn "Could not create traces data view (HTTP $tr_code). APM panels may not load."
fi

echo ""

# ── Import dashboard NDJSON ───────────────────────────────────────────────────
NDJSON_FILE="$SCRIPT_DIR/elastic-config/dashboards/exec-dashboard.ndjson"

if [[ ! -f "$NDJSON_FILE" ]]; then
    log_error "Dashboard NDJSON not found: $NDJSON_FILE"
    exit 1
fi

log_info "Importing executive dashboard via Saved Objects API..."

response=$(curl -s -w "\n%{http_code}" \
    -X POST "${KIBANA_URL}/api/saved_objects/_import?overwrite=true" \
    -H "kbn-xsrf: true" \
    -H "Authorization: ApiKey ${ELASTIC_API_KEY}" \
    -F "file=@${NDJSON_FILE}")

http_code=$(echo "$response" | tail -1)
response_body=$(echo "$response" | sed '$d')

if [[ "$http_code" -ge 200 && "$http_code" -lt 300 ]]; then
    # Parse the response for success/error counts
    success_count=$(echo "$response_body" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data.get('successCount', 0))
except:
    print('?')
" 2>/dev/null)

    errors=$(echo "$response_body" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    errs = data.get('errors', [])
    if errs:
        for e in errs:
            print(f\"  - {e.get('id','?')}: {e.get('error',{}).get('type','unknown')}\")
    else:
        print('  None')
except:
    print('  Could not parse')
" 2>/dev/null)

    log_ok "Dashboard import completed (HTTP $http_code)."
    log_info "Objects imported: ${success_count}"
    log_info "Errors:"
    echo "$errors"
else
    log_error "Dashboard import failed (HTTP $http_code)."
    log_error "Response: $(echo "$response_body" | head -c 500)"
    echo ""
    log_info "Fallback: Import manually via Kibana UI."
    log_info "1. Open: ${KIBANA_URL}/app/management/kibana/objects"
    log_info "2. Click 'Import' and select: $NDJSON_FILE"
    log_info "3. Enable 'Automatically overwrite conflicts'"
    log_info "4. Click 'Import'"
fi

echo ""

# ── Verify dashboard exists (serverless-compatible: uses _export, not _get) ───
log_info "Verifying dashboard via Saved Objects Export API..."
verify_response=$(curl -s -w "\n%{http_code}" \
    -X POST "${KIBANA_URL}/api/saved_objects/_export" \
    -H "kbn-xsrf: true" \
    -H "Content-Type: application/json" \
    -H "Authorization: ApiKey ${ELASTIC_API_KEY}" \
    -d '{"objects":[{"type":"dashboard","id":"nova7-exec-dashboard"}],"includeReferencesDeep":false}')

verify_code=$(echo "$verify_response" | tail -1)

if [[ "$verify_code" -ge 200 && "$verify_code" -lt 300 ]]; then
    log_ok "Dashboard 'NOVA-7 Executive Dashboard' verified."
    log_info "View at: ${KIBANA_URL}/app/dashboards#/view/nova7-exec-dashboard"
else
    log_warn "Could not verify dashboard (HTTP $verify_code). It may still be importing."
fi

echo ""
log_info "=========================================="
log_info "Dashboard import complete."
log_info "=========================================="
