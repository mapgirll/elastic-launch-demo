#!/usr/bin/env bash
#
# setup-alerting.sh — Create NOVA-7 alert rules using native Workflows connector
# targeting the Significant Event Notification workflow.
#
# Creates:
#   1. 20 alert rules (one per fault channel) using .es-query rule type
#   2. Each rule uses the built-in system-connector-.workflows action
#
# API reference:
#   POST /api/alerting/rule              — Create alert rule
#   GET  /api/alerting/rules/_find       — List alert rules
#   DELETE /api/alerting/rule/{id}       — Delete alert rule
#   POST /api/workflows/search           — Search workflows
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
        log_error "HTTP $http_code on $method $path: $(echo "$response_body" | head -c 500)"
        return 1
    fi
}

echo ""
log_info "=========================================="
log_info "NOVA-7 Alerting Rules Setup"
log_info "=========================================="
log_info "Kibana: ${KIBANA_URL}"
echo ""

# ── Step 1: Discover workflow ID ──────────────────────────────────────────────
log_info "--- Step 1: Discover Significant Event Notification workflow ---"

WORKFLOW_ID=""

if wf_search=$(kb_request POST "/api/workflows/search" '{"page":1,"size":100}' 2>&1); then
    WORKFLOW_ID=$(echo "$wf_search" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    items = data if isinstance(data, list) else data.get('results', data.get('items', data.get('data', [])))
    for item in items:
        name = item.get('name', '')
        if 'Significant Event Notification' in name:
            print(item['id'])
            break
except:
    pass
" 2>/dev/null || true)
fi

if [[ -z "$WORKFLOW_ID" ]]; then
    log_error "Could not find 'Significant Event Notification' workflow."
    log_info "Make sure setup-workflows.sh has been run first."
    exit 1
fi

log_ok "Workflow ID: ${WORKFLOW_ID}"
echo ""

# ── Step 2: Clean old NOVA-7 webhook connectors ──────────────────────────────
log_info "--- Step 2: Clean old NOVA-7 webhook connectors ---"

old_deleted=0
if connectors_out=$(kb_request GET "/api/actions/connectors" 2>/dev/null); then
    old_connector_ids=$(echo "$connectors_out" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for c in data:
        if 'NOVA-7' in c.get('name', '') and c.get('connector_type_id') == '.webhook':
            print(c['id'])
except:
    pass
" 2>/dev/null || true)

    while IFS= read -r cid; do
        if [[ -n "$cid" ]]; then
            if kb_request DELETE "/api/actions/connector/${cid}" > /dev/null 2>&1; then
                old_deleted=$((old_deleted + 1))
            fi
        fi
    done <<< "$old_connector_ids"
fi

if [[ "$old_deleted" -gt 0 ]]; then
    log_ok "Deleted $old_deleted old NOVA-7 webhook connector(s)."
else
    log_info "No old NOVA-7 webhook connectors to clean."
fi

log_ok "Using native Workflows system connector: system-connector-.workflows"
echo ""

# ── Step 3: Clean existing nova7-alert-* rules ───────────────────────────────
log_info "--- Step 3: Clean existing nova7-alert-* rules ---"

deleted=0
page=1

while true; do
    rules_out=$(kb_request GET "/api/alerting/rules/_find?per_page=100&page=${page}&filter=alert.attributes.tags:nova7" 2>/dev/null) || break

    rule_ids=$(echo "$rules_out" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    rules = data.get('data', [])
    for r in rules:
        name = r.get('name', '')
        if name.startswith('NOVA-7 CH'):
            print(r['id'])
except:
    pass
" 2>/dev/null || true)

    if [[ -z "$rule_ids" ]]; then
        break
    fi

    while IFS= read -r rid; do
        if [[ -n "$rid" ]]; then
            if kb_request DELETE "/api/alerting/rule/${rid}" > /dev/null 2>&1; then
                deleted=$((deleted + 1))
            fi
        fi
    done <<< "$rule_ids"

    page=$((page + 1))
    # Safety: don't loop forever
    if [[ "$page" -gt 10 ]]; then
        break
    fi
done

if [[ "$deleted" -gt 0 ]]; then
    log_ok "Deleted $deleted existing NOVA-7 alert rules."
else
    log_info "No existing NOVA-7 alert rules to clean."
fi
echo ""

# ── Step 4: Create 20 alert rules ────────────────────────────────────────────
log_info "--- Step 4: Create alert rules (native Workflows connector) ---"

# Channel data: NUM|NAME|SUBSYSTEM|ERROR_TYPE|SENSOR_TYPE|VEHICLE_SECTION
CHANNELS=(
    "01|Thermal Calibration Drift|propulsion|ThermalCalibrationException|thermal|engine_bay"
    "02|Fuel Pressure Anomaly|propulsion|FuelPressureException|pressure|fuel_tanks"
    "03|Oxidizer Flow Rate Deviation|propulsion|OxidizerFlowException|flow_rate|engine_bay"
    "04|GPS Multipath Interference|guidance|GPSMultipathException|gps|avionics"
    "05|IMU Synchronization Loss|guidance|IMUSyncException|imu|avionics"
    "06|Star Tracker Alignment Fault|guidance|StarTrackerAlignmentException|star_tracker|avionics"
    "07|S-Band Signal Degradation|communications|SignalDegradationException|rf_signal|antenna_array"
    "08|X-Band Packet Loss|communications|PacketLossException|packet_integrity|antenna_array"
    "09|UHF Antenna Pointing Error|communications|AntennaPointingException|antenna_position|antenna_array"
    "10|Payload Thermal Excursion|payload|PayloadThermalException|thermal|payload_bay"
    "11|Payload Vibration Anomaly|payload|PayloadVibrationException|vibration|payload_bay"
    "12|Cross-Cloud Relay Latency|relay|RelayLatencyException|network_latency|ground_network"
    "13|Relay Packet Corruption|relay|PacketCorruptionException|data_integrity|ground_network"
    "14|Ground Power Bus Fault|ground|PowerBusFaultException|electrical|launch_pad"
    "15|Weather Station Data Gap|ground|WeatherDataGapException|weather|launch_pad"
    "16|Pad Hydraulic Pressure Loss|ground|HydraulicPressureException|hydraulic|launch_pad"
    "17|Sensor Validation Pipeline Stall|validation|ValidationPipelineException|pipeline_health|ground_network"
    "18|Calibration Epoch Mismatch|validation|CalibrationEpochException|calibration|ground_network"
    "19|FTS Check Failure|safety|FTSCheckException|safety_system|vehicle_wide"
    "20|Range Safety Tracking Loss|safety|TrackingLossException|radar_tracking|vehicle_wide"
)

created=0
failed=0

for ch in "${CHANNELS[@]}"; do
    IFS='|' read -r num name subsystem error_type sensor_type vehicle_section <<< "$ch"

    # Determine severity
    case "$num" in
        19|20) severity="critical" ;;
        01|02|03|04|05|06) severity="high" ;;
        *) severity="medium" ;;
    esac

    rule_name="NOVA-7 CH${num}: ${name}"
    log_info "Creating rule: ${rule_name} [${severity}]"

    # Build the rule JSON with Python for safe escaping
    rule_json=$(python3 -c "
import json

num = '${num}'
name = '''${name}'''
subsystem = '${subsystem}'
error_type = '${error_type}'
sensor_type = '${sensor_type}'
vehicle_section = '${vehicle_section}'
severity = '${severity}'
workflow_id = '${WORKFLOW_ID}'
channel_int = int(num)

es_query = json.dumps({
    'query': {
        'bool': {
            'filter': [
                {'range': {'@timestamp': {'gte': 'now-1m'}}},
                {'match_phrase': {'body.text': error_type}},
                {'term': {'severity_text': 'ERROR'}}
            ]
        }
    }
})

rule = {
    'name': f'NOVA-7 CH{num}: {name}',
    'rule_type_id': '.es-query',
    'consumer': 'alerts',
    'tags': ['nova7', error_type],
    'schedule': {'interval': '1m'},
    'params': {
        'searchType': 'esQuery',
        'esQuery': es_query,
        'index': ['logs*'],
        'timeField': '@timestamp',
        'threshold': [0],
        'thresholdComparator': '>',
        'size': 100,
        'timeWindowSize': 1,
        'timeWindowUnit': 'm'
    },
    'actions': [{
        'group': 'query matched',
        'id': 'system-connector-.workflows',
        'frequency': {
            'summary': False,
            'notify_when': 'onActiveAlert',
            'throttle': None
        },
        'params': {
            'subAction': 'run',
            'subActionParams': {
                'workflowId': workflow_id,
                'inputs': {
                    'channel': channel_int,
                    'error_type': error_type,
                    'subsystem': subsystem,
                    'severity': severity
                }
            }
        }
    }]
}

print(json.dumps(rule))
")

    if kb_request POST "/api/alerting/rule" "$rule_json" > /dev/null 2>&1; then
        log_ok "  Created: ${rule_name}"
        created=$((created + 1))
    else
        log_warn "  Failed: ${rule_name}"
        failed=$((failed + 1))
    fi
done

echo ""
log_info "Rules created: ${created}, failed: ${failed}"
echo ""

# ── Step 5: Verify ───────────────────────────────────────────────────────────
log_info "--- Step 5: Verify ---"

if verify_out=$(kb_request GET "/api/alerting/rules/_find?per_page=100&filter=alert.attributes.tags:nova7" 2>/dev/null); then
    rule_count=$(echo "$verify_out" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    rules = data.get('data', [])
    count = sum(1 for r in rules if r.get('name', '').startswith('NOVA-7 CH'))
    print(count)
except:
    print('0')
" 2>/dev/null || echo "0")

    # Also verify connector type
    wf_action_count=$(echo "$verify_out" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    rules = data.get('data', [])
    count = 0
    for r in rules:
        if r.get('name', '').startswith('NOVA-7 CH'):
            for a in r.get('actions', []):
                if a.get('connector_type_id') == '.workflows':
                    count += 1
                    break
    print(count)
except:
    print('0')
" 2>/dev/null || echo "0")

    if [[ "$rule_count" -ge 20 ]]; then
        log_ok "Verified: $rule_count NOVA-7 alert rules found."
    elif [[ "$rule_count" -gt 0 ]]; then
        log_warn "Only $rule_count NOVA-7 alert rules found (expected 20)."
    else
        log_warn "No NOVA-7 alert rules found in verification."
    fi

    if [[ "$wf_action_count" -ge 20 ]]; then
        log_ok "Verified: $wf_action_count rules use native .workflows connector."
    elif [[ "$wf_action_count" -gt 0 ]]; then
        log_warn "Only $wf_action_count rules use .workflows connector (expected 20)."
    else
        log_warn "No rules using .workflows connector found."
    fi
else
    log_warn "Could not verify alert rules."
fi

echo ""
log_info "=========================================="
log_info "NOVA-7 Alerting setup complete."
log_info "  Connector: system-connector-.workflows (native)"
log_info "  Workflow:  ${WORKFLOW_ID}"
log_info "  Rules:     ${created} created"
log_info "=========================================="
