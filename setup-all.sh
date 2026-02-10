#!/usr/bin/env bash
#
# setup-all.sh — Master orchestrator for NOVA-7 Elastic Launch Demo.
#
# Runs all setup scripts in order:
#   1. Validate connectivity (ES + Kibana health checks)
#   2. Workflow deployment (must run before Agent Builder — provides workflow IDs)
#   3. Agent Builder setup (agent, tools, knowledge base)
#   4. Significant Events (Streams queries)
#   5. Dashboard import
#   6. Alerting rules
#   7. Print summary
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Load environment ──────────────────────────────────────────────────────────
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

# ── Helpers ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
log_ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

echo ""
echo "============================================================"
echo "   NOVA-7 Elastic Launch Demo — Full Setup"
echo "============================================================"
echo ""

# ── Pre-flight checks ────────────────────────────────────────────────────────
log_info "Pre-flight checks..."

# Check required env vars
missing=0
for var in ELASTIC_URL ELASTIC_API_KEY KIBANA_URL OTLP_ENDPOINT OTLP_API_KEY; do
    if [[ -z "${!var:-}" ]]; then
        log_error "$var is not set."
        missing=1
    else
        log_ok "$var is configured."
    fi
done

if [[ "$missing" -eq 1 ]]; then
    log_error "Missing required environment variables. Check your .env file."
    exit 1
fi

ELASTIC_URL="${ELASTIC_URL%/}"
KIBANA_URL="${KIBANA_URL%/}"

echo ""

# ── Step 1: Connectivity checks ──────────────────────────────────────────────
log_info "Step 1: Connectivity checks..."

# Elasticsearch
es_code=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "Authorization: ApiKey ${ELASTIC_API_KEY}" \
    "${ELASTIC_URL}/" 2>/dev/null || echo "000")

if [[ "$es_code" -ge 200 && "$es_code" -lt 300 ]]; then
    log_ok "Elasticsearch reachable (HTTP $es_code)."
else
    log_error "Elasticsearch unreachable at ${ELASTIC_URL} (HTTP $es_code)."
    exit 1
fi

# Kibana
kb_code=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "Authorization: ApiKey ${ELASTIC_API_KEY}" \
    "${KIBANA_URL}/api/status" 2>/dev/null || echo "000")

if [[ "$kb_code" -ge 200 && "$kb_code" -lt 300 ]]; then
    log_ok "Kibana reachable (HTTP $kb_code)."
else
    log_warn "Kibana may not be reachable at ${KIBANA_URL} (HTTP $kb_code). Continuing..."
fi

echo ""

# ── Step 2: Workflow Deployment (must run before Agent Builder) ────────────────
log_info "Step 2: Workflow deployment (must run before Agent Builder for workflow IDs)..."
echo ""

# Tell setup-workflows.sh not to cascade into agent-builder/alerting (we handle ordering here)
export NOVA7_CALLED_FROM_SETUP_ALL=1

if [[ -x "$SCRIPT_DIR/setup-workflows.sh" ]]; then
    bash "$SCRIPT_DIR/setup-workflows.sh"
else
    chmod +x "$SCRIPT_DIR/setup-workflows.sh"
    bash "$SCRIPT_DIR/setup-workflows.sh"
fi

echo ""

# ── Step 3: Agent Builder Setup ───────────────────────────────────────────────
log_info "Step 3: Agent Builder setup (agent, tools, knowledge base)..."
echo ""

if [[ -x "$SCRIPT_DIR/setup-agent-builder.sh" ]]; then
    bash "$SCRIPT_DIR/setup-agent-builder.sh"
else
    chmod +x "$SCRIPT_DIR/setup-agent-builder.sh"
    bash "$SCRIPT_DIR/setup-agent-builder.sh"
fi

echo ""

# ── Step 4: Significant Events (Streams Queries) ─────────────────────────────
log_info "Step 4: Significant Events (Streams queries)..."
echo ""

if [[ -x "$SCRIPT_DIR/setup-significant-events.sh" ]]; then
    bash "$SCRIPT_DIR/setup-significant-events.sh"
else
    chmod +x "$SCRIPT_DIR/setup-significant-events.sh"
    bash "$SCRIPT_DIR/setup-significant-events.sh"
fi

echo ""

# ── Step 5: Dashboard Import ─────────────────────────────────────────────────
log_info "Step 5: Executive dashboard import..."
echo ""

if [[ -x "$SCRIPT_DIR/setup-exec-dashboard.sh" ]]; then
    bash "$SCRIPT_DIR/setup-exec-dashboard.sh"
else
    chmod +x "$SCRIPT_DIR/setup-exec-dashboard.sh"
    bash "$SCRIPT_DIR/setup-exec-dashboard.sh"
fi

echo ""

# ── Step 6: Alerting Rules ────────────────────────────────────────────────────
log_info "Step 6: Alerting rules (webhook connector + alert rules)..."
echo ""

if [[ -x "$SCRIPT_DIR/setup-alerting.sh" ]]; then
    bash "$SCRIPT_DIR/setup-alerting.sh"
else
    chmod +x "$SCRIPT_DIR/setup-alerting.sh"
    bash "$SCRIPT_DIR/setup-alerting.sh"
fi

echo ""

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo "   Setup Complete!"
echo "============================================================"
echo ""
log_info "Resources deployed:"
echo "  - Workflows:    3 operational workflows"
echo "  - AI Agent:     NOVA-7 Launch Anomaly Analyst"
echo "  - Tools:        7 agent tools (incl. remediation_action as workflow type)"
echo "  - Knowledge:    5 knowledge base documents"
echo "  - Sig Events:   20 Streams query definitions"
echo "  - Dashboard:    NOVA-7 Executive Dashboard"
echo "  - Alerting:     20 alert rules + webhook connector"
echo ""
log_info "URLs:"
echo "  - Kibana:       ${KIBANA_URL}"
echo "  - Dashboard:    ${KIBANA_URL}/app/dashboards#/view/nova7-exec-dashboard"
echo "  - Agent Builder: ${KIBANA_URL}/app/agent_builder"
echo "  - Elasticsearch: ${ELASTIC_URL}"
echo ""
log_info "Log generators are integrated into the app and start automatically."
echo "  Traces, host metrics, nginx logs, and MySQL logs flow continuously."
echo "  Check generator status: curl localhost/api/status | jq .generators"
echo ""
log_info "For standalone/ad-hoc generator use:"
echo "  python3 -m log_generators.trace_generator"
echo "  python3 -m log_generators.host_metrics_generator"
echo "  python3 -m log_generators.nginx_log_generator"
echo "  python3 -m log_generators.mysql_log_generator"
echo ""
log_info "Run validation:"
echo "  ./validate.sh"
echo ""
