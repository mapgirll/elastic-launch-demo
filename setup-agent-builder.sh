#!/usr/bin/env bash
#
# setup-agent-builder.sh — Deploy NOVA-7 Agent, Tools & Knowledge Base via Kibana API
#
# Uses the Kibana Agent Builder REST API to create the AI agent and its tools,
# and indexes knowledge base documents into Elasticsearch.
#
# Correct Agent Builder API schema:
#   Tool:  {id, type, description, configuration}
#   Agent: {id, name, description, configuration: {instructions, tools: [{tool_ids: [...]}]}}
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
for var in KIBANA_URL ELASTIC_URL ELASTIC_API_KEY; do
    if [[ -z "${!var:-}" ]]; then
        echo "ERROR: $var is not set. Check your .env file."
        exit 1
    fi
done

KIBANA_URL="${KIBANA_URL%/}"
ELASTIC_URL="${ELASTIC_URL%/}"

# ── Helpers ───────────────────────────────────────────────────────────────────
log_info()  { echo "[INFO]  $*"; }
log_ok()    { echo "[OK]    $*"; }
log_warn()  { echo "[WARN]  $*"; }
log_error() { echo "[ERROR] $*"; }

# Kibana API helper — adds kbn-xsrf header
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

# Elasticsearch API helper
es_request() {
    local method="$1" path="$2" body="${3:-}"

    local curl_args=(
        -s -w "\n%{http_code}"
        -X "$method"
        "${ELASTIC_URL}${path}"
        -H "Content-Type: application/json"
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
log_info "NOVA-7 Agent Builder Setup"
log_info "=========================================="
log_info "Kibana:  ${KIBANA_URL}"
log_info "Elastic: ${ELASTIC_URL}"
echo ""

# ── Step 1: Create Tools (transformed to Agent Builder API schema) ────────────
log_info "--- Creating Agent Builder Tools ---"
log_info "Using correct API schema: {id, type, description, configuration}"

tool_count=0
tool_fail=0

create_tool() {
    local tool_id="$1"
    local tool_body="$2"
    log_info "Creating/updating tool: ${tool_id}"
    # Delete first to ensure type changes take effect (PUT can't change type)
    kb_request DELETE "/api/agent_builder/tools/${tool_id}" > /dev/null 2>&1 || true
    if kb_request POST "/api/agent_builder/tools" "$tool_body" > /dev/null 2>&1; then
        log_ok "Tool ${tool_id} created."
        tool_count=$((tool_count + 1))
    else
        # PUT schema: only description + configuration (no id, no type)
        local update_body
        update_body=$(python3 -c "
import json, sys
body = json.loads(sys.argv[1])
print(json.dumps({k: v for k, v in body.items() if k in ('description', 'configuration')}))
" "$tool_body" 2>/dev/null)
        if kb_request PUT "/api/agent_builder/tools/${tool_id}" "$update_body" > /dev/null 2>&1; then
            log_ok "Tool ${tool_id} updated."
            tool_count=$((tool_count + 1))
        else
            log_warn "Failed to create/update ${tool_id}."
            tool_fail=$((tool_fail + 1))
        fi
    fi
}

create_tool "search_telemetry_logs" '{
  "id": "search_telemetry_logs",
  "type": "index_search",
  "description": "Search NOVA-7 telemetry logs for anomaly investigation. Searches the logs data stream which contains all OTLP telemetry. Supports filtering by error type (in body.text), sensor type, vehicle section, service name, severity, channel, and cloud provider. Primary investigation tool for root cause analysis.",
  "configuration": {
    "pattern": "logs,logs.*"
  }
}'

create_tool "search_subsystem_health" '{
  "id": "search_subsystem_health",
  "type": "esql",
  "description": "Query health status of NOVA-7 services by aggregating recent telemetry. Returns error counts, warning counts, and overall health status for each of the 9 services (fuel-system, navigation, comms-array, mission-control, range-safety, ground-systems, payload-monitor, sensor-validator, telemetry-relay). Log message field: body.text (never use 'body' alone).",
  "configuration": {
    "query": "FROM logs,logs.* | WHERE @timestamp > NOW() - 15 MINUTES | STATS error_count = COUNT(*) WHERE severity_text == \"ERROR\", warn_count = COUNT(*) WHERE severity_text == \"WARN\", total = COUNT(*) BY service.name | SORT error_count DESC",
    "params": {}
  }
}'

create_tool "check_service_status" '{
  "id": "check_service_status",
  "type": "esql",
  "description": "Check operational status of individual NOVA-7 microservices. Returns error counts, warning counts, and log totals for each of the 9 services (fuel-system, navigation, comms-array, mission-control, range-safety, ground-systems, payload-monitor, sensor-validator, telemetry-relay). Log message field: body.text (never use 'body' alone).",
  "configuration": {
    "query": "FROM logs,logs.* | WHERE @timestamp > NOW() - 15 MINUTES | STATS error_count = COUNT(*) WHERE severity_text == \"ERROR\", warn_count = COUNT(*) WHERE severity_text == \"WARN\", total = COUNT(*) BY service.name | SORT error_count DESC",
    "params": {}
  }
}'

create_tool "search_known_anomalies" '{
  "id": "search_known_anomalies",
  "type": "index_search",
  "description": "Search the NOVA-7 knowledge base for previously documented anomalies, failure patterns, and resolution procedures. Contains RCA guides for all 20 fault channels including thermal calibration, fuel pressure, oxidizer flow, GPS multipath, IMU sync, star tracker, S-band/X-band/UHF comms, payload thermal/vibration, relay latency/corruption, power bus, weather, hydraulics, validation pipeline, calibration epoch, FTS, and range safety tracking.",
  "configuration": {
    "pattern": "nova7-knowledge-base"
  }
}'

create_tool "trace_anomaly_propagation" '{
  "id": "trace_anomaly_propagation",
  "type": "esql",
  "description": "Trace the propagation path of anomalies across NOVA-7 services. Shows which services have errors and warnings over time to identify cascade chains and the temporal order of fault propagation. Log message field: body.text (never use 'body' alone).",
  "configuration": {
    "query": "FROM logs,logs.* | WHERE @timestamp > NOW() - 15 MINUTES AND severity_text IN (\"ERROR\", \"WARN\") | STATS error_count = COUNT(*) WHERE severity_text == \"ERROR\", warn_count = COUNT(*) WHERE severity_text == \"WARN\" BY service.name | SORT error_count DESC",
    "params": {}
  }
}'

create_tool "launch_safety_assessment" '{
  "id": "launch_safety_assessment",
  "type": "esql",
  "description": "Comprehensive launch safety assessment. Evaluates all critical NOVA-7 services against launch commit criteria. Checks for active errors including FTS anomalies (FTSCheckException), range safety tracking losses (TrackingLossException), and cascade warnings across services. Returns GO/NO-GO data. Log message field: body.text (never use 'body' alone).",
  "configuration": {
    "query": "FROM logs,logs.* | WHERE @timestamp > NOW() - 15 MINUTES AND severity_text IN (\"ERROR\", \"WARN\") | STATS error_count = COUNT(*) WHERE severity_text == \"ERROR\", warn_count = COUNT(*) WHERE severity_text == \"WARN\" BY service.name | SORT error_count DESC",
    "params": {}
  }
}'

create_tool "esql_telemetry_query" '{
  "id": "esql_telemetry_query",
  "type": "esql",
  "description": "Run custom ES|QL queries against NOVA-7 telemetry logs for advanced analytics, threshold validation, and anomaly detection. CRITICAL: The log message column is body.text (NOT body — using 'body' alone causes Unknown column errors). Available fields: @timestamp, body.text (log message), severity_text (INFO/WARN/ERROR), service.name (9 services), log.level. Use body.text LIKE \"*ErrorType*\" to match specific error types. IMPORTANT: Always use FROM logs,logs.* to include all sub-streams. Example: FROM logs,logs.* | WHERE body.text LIKE \"*FuelPressureException*\" AND severity_text == \"ERROR\" | STATS count = COUNT(*)",
  "configuration": {
    "query": "FROM logs,logs.* | WHERE @timestamp > NOW() - 15 MINUTES | LIMIT 25",
    "params": {}
  }
}'

# Discover the "NOVA-7 Remediation Action" workflow ID for the workflow tool
log_info "Discovering Remediation Action workflow ID..."
REMEDIATION_WF_ID=""
if wf_search=$(kb_request POST "/api/workflows/search" '{"page":1,"size":100}' 2>/dev/null); then
    REMEDIATION_WF_ID=$(echo "$wf_search" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    items = data if isinstance(data, list) else data.get('results', data.get('items', data.get('data', [])))
    for item in items:
        if 'Remediation Action' in item.get('name', ''):
            print(item['id'])
            break
except:
    pass
" 2>/dev/null || true)
fi

if [[ -z "$REMEDIATION_WF_ID" ]]; then
    log_warn "Could not find Remediation Action workflow. Ensure setup-workflows.sh ran first."
    log_warn "Falling back to placeholder workflow_id — update manually after workflow deployment."
    REMEDIATION_WF_ID="WORKFLOW_ID_NOT_FOUND"
else
    log_ok "Found Remediation Action workflow: ${REMEDIATION_WF_ID}"
fi

create_tool "remediation_action" '{
  "id": "remediation_action",
  "type": "workflow",
  "description": "Execute remediation actions for NOVA-7 anomalies. Triggers the Remediation Action workflow which searches for the specific error, extracts the callback URL, resolves the fault channel, captures before/after error counts, and logs to the audit trail. Inputs: error_type (e.g. FuelPressureException), channel (1-20), action_type (recalibrate_sensor, restart_service, failover_service, isolate_subsystem), target_service, justification, dry_run.",
  "configuration": {
    "workflow_id": "'"${REMEDIATION_WF_ID}"'"
  }
}'

# Discover the "NOVA-7 Escalation and Hold Management" workflow ID
log_info "Discovering Escalation workflow ID..."
ESCALATION_WF_ID=""
if [[ -n "$wf_search" ]]; then
    ESCALATION_WF_ID=$(echo "$wf_search" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    items = data if isinstance(data, list) else data.get('results', data.get('items', data.get('data', [])))
    for item in items:
        if 'Escalation' in item.get('name', ''):
            print(item['id'])
            break
except:
    pass
" 2>/dev/null || true)
fi

if [[ -z "$ESCALATION_WF_ID" ]]; then
    log_warn "Could not find Escalation workflow. Ensure setup-workflows.sh ran first."
    ESCALATION_WF_ID="WORKFLOW_ID_NOT_FOUND"
else
    log_ok "Found Escalation workflow: ${ESCALATION_WF_ID}"
fi

create_tool "escalation_action" '{
  "id": "escalation_action",
  "type": "workflow",
  "description": "Escalate critical anomalies and manage launch hold decisions. Triggers the Escalation and Hold Management workflow. Supports four actions: escalate (flag for launch director attention), request_hold (stop countdown), resolve (mark anomaly resolved), request_resume (restart countdown). Inputs: action (escalate, request_hold, resolve, request_resume), channel (1-20), severity (ADVISORY, CAUTION, WARNING, CRITICAL), justification, hold_id (for resolve/resume), investigation_summary (for resolve).",
  "configuration": {
    "workflow_id": "'"${ESCALATION_WF_ID}"'"
  }
}'

log_info "Tools: ${tool_count} created, ${tool_fail} failed."
echo ""

# ── Step 2: Create Agent ─────────────────────────────────────────────────────
log_info "--- Creating Agent Builder Agent ---"

AGENT_FILE="$SCRIPT_DIR/elastic-config/agent/launch-anomaly-agent.json"
if [[ ! -f "$AGENT_FILE" ]]; then
    log_error "Agent config not found: $AGENT_FILE"
    exit 1
fi

# Transform the application-level agent config to Agent Builder API format
# API schema: {id, name, description, configuration: {instructions, tools: [{tool_ids: [...]}]}}
# The system prompt maps to configuration.instructions (per Elastic docs)
AGENT_BODY=$(python3 -c "
import json
with open('$AGENT_FILE') as f:
    config = json.load(f)
agent = {
    'id': config.get('agent_id', 'nova7-launch-anomaly-analyst'),
    'name': config['name'],
    'description': config['description'],
    'configuration': {
        'instructions': config['system_prompt'],
        'tools': [
            {'tool_ids': config['tools']}
        ]
    }
}
print(json.dumps(agent))
")

# DELETE + POST to reliably update (PUT truncates instructions)
AGENT_ID=$(python3 -c "import json; print(json.load(open('$AGENT_FILE')).get('agent_id','nova7-launch-anomaly-analyst'))")
log_info "Deleting existing agent (if any): ${AGENT_ID}"
kb_request DELETE "/api/agent_builder/agents/${AGENT_ID}" > /dev/null 2>&1 || true

log_info "Creating agent: NOVA-7 Launch Anomaly Analyst"
if kb_request POST "/api/agent_builder/agents" "$AGENT_BODY" > /dev/null 2>&1; then
    log_ok "Agent created successfully (with full instructions/system prompt)."
else
    log_warn "Failed to create agent."
    log_info "Try via Agent Builder UI: ${KIBANA_URL}/app/agent_builder"
fi
echo ""

# ── Step 3: Index Knowledge Base Documents ────────────────────────────────────
log_info "--- Indexing Knowledge Base Documents ---"

KB_INDEX="nova7-knowledge-base"

# Create the knowledge base index
KB_INDEX_BODY='{
  "settings": {
    "number_of_shards": 1,
    "number_of_replicas": 1
  },
  "mappings": {
    "properties": {
      "title": { "type": "text", "fields": { "keyword": { "type": "keyword" } } },
      "content": { "type": "text" },
      "category": { "type": "keyword" },
      "tags": { "type": "keyword" },
      "mission_id": { "type": "keyword" },
      "created_at": { "type": "date" },
      "channel_number": { "type": "integer" },
      "error_type": { "type": "keyword" },
      "sensor_type": { "type": "keyword" },
      "vehicle_section": { "type": "keyword" },
      "subsystem": { "type": "keyword" },
      "affected_services": { "type": "keyword" },
      "severity_levels": { "type": "keyword" }
    }
  }
}'

log_info "Ensuring knowledge base index: ${KB_INDEX}"
# Delete existing index to ensure fresh mapping with all fields
es_request DELETE "/${KB_INDEX}" > /dev/null 2>&1 && \
    log_info "Deleted existing knowledge base index." || true
es_request PUT "/${KB_INDEX}" "$KB_INDEX_BODY" > /dev/null 2>&1 && \
    log_ok "Knowledge base index created with enhanced schema." || \
    log_warn "Knowledge base index creation failed."

# Build bulk request for all KB docs
kb_count=0
BULK_BODY=""

for kb_file in "$SCRIPT_DIR"/elastic-config/knowledge-base/*.md; do
    if [[ ! -f "$kb_file" ]]; then
        log_warn "No knowledge base docs found."
        break
    fi

    kb_name=$(basename "$kb_file" .md)

    # Use python to safely build JSON for the doc with structured metadata
    DOC_JSON=$(python3 -c "
import json, sys, re

# Channel metadata mapping: filename -> (channel_number, error_type, sensor_type, vehicle_section, subsystem, affected_services, severity_levels)
CHANNEL_MAP = {
    'thermal_calibration': (1, 'ThermalCalibrationException', 'thermal', 'engine_bay', 'propulsion', ['fuel-system', 'sensor-validator'], ['high']),
    'fuel_pressure': (2, 'FuelPressureException', 'pressure', 'fuel_tanks', 'propulsion', ['fuel-system', 'sensor-validator'], ['high']),
    'oxidizer_flow': (3, 'OxidizerFlowException', 'flow_rate', 'engine_bay', 'propulsion', ['fuel-system', 'sensor-validator'], ['high']),
    'gps_multipath': (4, 'GPSMultipathException', 'gps', 'avionics', 'guidance', ['navigation', 'sensor-validator'], ['high']),
    'imu_sync': (5, 'IMUSyncException', 'imu', 'avionics', 'guidance', ['navigation', 'sensor-validator'], ['high']),
    'star_tracker': (6, 'StarTrackerAlignmentException', 'star_tracker', 'avionics', 'guidance', ['navigation', 'sensor-validator'], ['high']),
    'sband_signal': (7, 'SignalDegradationException', 'rf_signal', 'antenna_array', 'communications', ['comms-array', 'sensor-validator'], ['medium']),
    'xband_packet': (8, 'PacketLossException', 'packet_integrity', 'antenna_array', 'communications', ['comms-array', 'sensor-validator'], ['medium']),
    'uhf_antenna': (9, 'AntennaPointingException', 'antenna_position', 'antenna_array', 'communications', ['comms-array', 'sensor-validator'], ['medium']),
    'payload_thermal': (10, 'PayloadThermalException', 'thermal', 'payload_bay', 'payload', ['payload-monitor', 'sensor-validator'], ['medium']),
    'payload_vibration': (11, 'PayloadVibrationException', 'vibration', 'payload_bay', 'payload', ['payload-monitor', 'sensor-validator'], ['medium']),
    'cross_cloud_relay': (12, 'RelayLatencyException', 'network_latency', 'ground_network', 'relay', ['telemetry-relay', 'sensor-validator'], ['medium']),
    'relay_corruption': (13, 'PacketCorruptionException', 'data_integrity', 'ground_network', 'relay', ['telemetry-relay', 'sensor-validator'], ['medium']),
    'power_bus': (14, 'PowerBusFaultException', 'electrical', 'launch_pad', 'ground', ['ground-systems', 'sensor-validator'], ['medium']),
    'weather_gap': (15, 'WeatherDataGapException', 'weather', 'launch_pad', 'ground', ['ground-systems', 'sensor-validator'], ['medium']),
    'hydraulic_pressure': (16, 'HydraulicPressureException', 'hydraulic', 'launch_pad', 'ground', ['ground-systems', 'sensor-validator'], ['medium']),
    'validation_pipeline': (17, 'ValidationPipelineException', 'pipeline_health', 'ground_network', 'validation', ['sensor-validator'], ['medium']),
    'calibration_epoch': (18, 'CalibrationEpochException', 'calibration', 'ground_network', 'validation', ['sensor-validator'], ['medium']),
    'fts_check': (19, 'FTSCheckException', 'safety_system', 'vehicle_wide', 'safety', ['range-safety', 'sensor-validator'], ['critical']),
    'tracking_loss': (20, 'TrackingLossException', 'radar_tracking', 'vehicle_wide', 'safety', ['range-safety', 'sensor-validator'], ['critical']),
}

with open('$kb_file') as f:
    content = f.read()
title = content.split('\n')[0].lstrip('#').strip()
doc = {
    'title': title,
    'content': content,
    'category': 'launch-anomaly',
    'tags': ['nova7', '$kb_name'],
    'mission_id': 'NOVA-7'
}

# Add structured metadata if this file maps to a known channel
meta = CHANNEL_MAP.get('$kb_name')
if meta:
    doc['channel_number'] = meta[0]
    doc['error_type'] = meta[1]
    doc['sensor_type'] = meta[2]
    doc['vehicle_section'] = meta[3]
    doc['subsystem'] = meta[4]
    doc['affected_services'] = meta[5]
    doc['severity_levels'] = meta[6]

print(json.dumps(doc))
")

    BULK_BODY+="{\"index\":{\"_index\":\"${KB_INDEX}\",\"_id\":\"${kb_name}\"}}"$'\n'
    BULK_BODY+="${DOC_JSON}"$'\n'
    kb_count=$((kb_count + 1))
    log_info "Prepared KB doc: ${kb_name}"
done

if [[ -n "$BULK_BODY" ]]; then
    log_info "Sending bulk index request ($kb_count docs)..."
    BULK_FILE=$(mktemp /tmp/nova7-kb-bulk.XXXXXX)
    printf '%s' "$BULK_BODY" > "$BULK_FILE"
    response=$(curl -s -w "\n%{http_code}" -X POST \
        "${ELASTIC_URL}/_bulk?refresh=true" \
        -H "Content-Type: application/x-ndjson" \
        -H "Authorization: ApiKey ${ELASTIC_API_KEY}" \
        --data-binary "@${BULK_FILE}")
    rm -f "$BULK_FILE"
    http_code=$(echo "$response" | tail -1)
    if [[ "$http_code" -ge 200 && "$http_code" -lt 300 ]]; then
        log_ok "Indexed $kb_count knowledge base documents."
    else
        log_error "Bulk index failed (HTTP $http_code)."
    fi
fi
echo ""

# ── Step 4: Validate ─────────────────────────────────────────────────────────
log_info "--- Validation ---"

log_info "Checking agents..."
if agents_out=$(kb_request GET "/api/agent_builder/agents" 2>/dev/null); then
    if echo "$agents_out" | grep -qi "nova"; then
        log_ok "NOVA-7 agent found."
    else
        log_warn "Agent Builder reachable but NOVA-7 agent not confirmed."
    fi
else
    log_warn "Could not verify agents (API may not be available)."
fi

log_info "Checking tools..."
if tools_out=$(kb_request GET "/api/agent_builder/tools" 2>/dev/null); then
    custom_count=$(echo "$tools_out" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    tools = data if isinstance(data, list) else data.get('results', data.get('tools', data.get('data', [])))
    custom = [t for t in tools if not t.get('readonly', False) and not t.get('is_default', False)]
    print(len(custom))
except:
    print('0')
" 2>/dev/null || echo "0")
    log_ok "Custom tools registered: $custom_count"
else
    log_warn "Could not verify tools."
fi

log_info "Checking knowledge base..."
if kb_out=$(es_request POST "/${KB_INDEX}/_count" '{}' 2>/dev/null); then
    doc_count=$(echo "$kb_out" | python3 -c "import json,sys; print(json.load(sys.stdin).get('count',0))" 2>/dev/null || echo "0")
    log_ok "Knowledge base documents: $doc_count"
else
    log_warn "Could not verify knowledge base."
fi

echo ""
log_info "=========================================="
log_info "Agent Builder setup complete."
log_info "=========================================="
