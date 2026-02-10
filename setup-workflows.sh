#!/usr/bin/env bash
#
# setup-workflows.sh — Deploy NOVA-7 workflows via Kibana Workflows API.
#
# Reads YAML workflow files from elastic-config/workflows/ and POSTs them
# to POST /api/workflows with the YAML content as a string.
#
# API reference (undocumented, GA planned for 9.4):
#   POST /api/workflows          — Create workflow (body: {"yaml": "..."})
#   PUT  /api/workflows/{id}     — Update workflow
#   POST /api/workflows/search   — List/search workflows
#   GET  /api/workflows/{id}     — Get workflow by ID
#   POST /api/workflows/{id}/run — Run a workflow
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

kb_request() {
    local method="$1" path="$2" body="${3:-}"

    local curl_args=(
        -s -w "\n%{http_code}"
        -X "$method"
        "${KIBANA_URL}${path}"
        -H "Content-Type: application/json"
        -H "kbn-xsrf: true"
        -H "x-elastic-internal-origin: kibana"
        -H "Authorization: ApiKey ${ELASTIC_API_KEY}"
    )

    if [[ -n "$body" ]]; then
        curl_args+=(-d "$body")
    fi

    local response
    response=$(curl "${curl_args[@]}")
    local http_code
    http_code=$(echo "$response" | tail -1)
    local response_body
    response_body=$(echo "$response" | sed '$d')

    if [[ "$http_code" -ge 200 && "$http_code" -lt 300 ]]; then
        echo "$response_body"
        return 0
    else
        log_error "HTTP $http_code on $method $path: $(echo "$response_body" | head -c 300)"
        return 1
    fi
}

echo ""
log_info "=========================================="
log_info "NOVA-7 Workflow Deployment"
log_info "=========================================="
log_info "Kibana: ${KIBANA_URL}"
echo ""

# ── Clean existing NOVA-7 workflows ──────────────────────────────────────────
log_info "--- Cleaning existing NOVA-7 workflows ---"

wf_deleted=0
if existing_out=$(kb_request POST "/api/workflows/search" '{"page":1,"size":100}' 2>/dev/null); then
    nova7_wf_ids=$(echo "$existing_out" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    items = data if isinstance(data, list) else data.get('results', data.get('items', data.get('data', [])))
    for item in items:
        if 'NOVA-7' in item.get('name', ''):
            print(item['id'])
except:
    pass
" 2>/dev/null || true)

    while IFS= read -r wid; do
        if [[ -n "$wid" ]]; then
            if kb_request DELETE "/api/workflows/${wid}" > /dev/null 2>&1; then
                wf_deleted=$((wf_deleted + 1))
            fi
        fi
    done <<< "$nova7_wf_ids"
fi

if [[ "$wf_deleted" -gt 0 ]]; then
    log_ok "Deleted $wf_deleted existing NOVA-7 workflow(s)."
else
    log_info "No existing NOVA-7 workflows to clean."
fi
echo ""

# ── Deploy workflows ─────────────────────────────────────────────────────────
log_info "--- Deploying Workflows ---"

WORKFLOW_DIR="$SCRIPT_DIR/elastic-config/workflows"
workflow_count=0
workflow_fail=0

for yaml_file in "$WORKFLOW_DIR"/*.yaml; do
    if [[ ! -f "$yaml_file" ]]; then
        log_warn "No workflow YAML files found in $WORKFLOW_DIR/"
        break
    fi

    workflow_name=$(basename "$yaml_file" .yaml)

    # Extract the display name from the YAML (the 'name:' field)
    display_name=$(python3 -c "
import re
with open('$yaml_file') as f:
    for line in f:
        m = re.match(r'^name:\s*(.+)', line)
        if m:
            print(m.group(1).strip())
            break
" 2>/dev/null || echo "$workflow_name")

    log_info "Creating workflow: ${display_name}"

    # Build JSON body with YAML content as a string
    json_body=$(python3 -c "
import json, sys
with open('$yaml_file', 'r') as f:
    yaml_content = f.read()
print(json.dumps({'yaml': yaml_content}))
" 2>/dev/null)

    if [[ -z "$json_body" ]]; then
        log_error "Failed to read ${workflow_name}.yaml"
        workflow_fail=$((workflow_fail + 1))
        continue
    fi

    if result=$(kb_request POST "/api/workflows" "$json_body" 2>&1); then
        log_ok "Workflow ${display_name} created."
        workflow_count=$((workflow_count + 1))
    else
        log_warn "Failed to create workflow ${display_name}."
        workflow_fail=$((workflow_fail + 1))
    fi
done

echo ""
log_info "Workflows: ${workflow_count} deployed, ${workflow_fail} failed."

# ── Verify ────────────────────────────────────────────────────────────────────
echo ""
log_info "--- Verification ---"
if workflows_out=$(kb_request POST "/api/workflows/search" '{"page":1,"size":100}' 2>/dev/null); then
    wf_count=$(echo "$workflows_out" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    items = data if isinstance(data, list) else data.get('results', data.get('items', data.get('data', [])))
    print(len(items))
except:
    print('0')
" 2>/dev/null || echo "0")
    log_ok "Workflows in Kibana: $wf_count"
else
    log_warn "Could not verify workflows (API may not be available)."
fi

echo ""
log_info "=========================================="
log_info "Workflow deployment complete."
log_info "=========================================="

# ── Cascade: re-run dependent scripts (workflow IDs may have changed) ────────
# Skip when called from setup-all.sh (which handles ordering itself)
if [[ -z "${NOVA7_CALLED_FROM_SETUP_ALL:-}" ]]; then
    echo ""
    log_info "Workflow IDs may have changed — cascading to dependent scripts..."
    echo ""

    if [[ -f "$SCRIPT_DIR/setup-agent-builder.sh" ]]; then
        log_info "Re-running setup-agent-builder.sh (workflow tool references)..."
        bash "$SCRIPT_DIR/setup-agent-builder.sh"
        echo ""
    fi

    if [[ -f "$SCRIPT_DIR/setup-alerting.sh" ]]; then
        log_info "Re-running setup-alerting.sh (alert workflow actions)..."
        bash "$SCRIPT_DIR/setup-alerting.sh"
        echo ""
    fi

    log_ok "Cascade complete — agent tools and alert rules updated with new workflow IDs."
fi
