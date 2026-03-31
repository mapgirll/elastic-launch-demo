# NOVA-7 Elastic Launch Demo — Learnings & API Reference

## Kibana Agent Builder API (Serverless 9.4.0)

### Tool Schema
```
POST /api/agent_builder/tools
{
  "id": "<tool-id>",
  "type": "index_search" | "esql" | "workflow",
  "description": "...",
  "configuration": {
    // for index_search:
    "pattern": "logs-*"           // NOT "index" — must be "pattern"
    // for esql:
    "query": "FROM logs-* | ...",
    "params": {}                  // REQUIRED even if empty
    // for esql with parameters:
    "query": "FROM logs,logs.* | WHERE body.text LIKE ?error_type | LIMIT 50",
    "params": {
      "error_type": {
        "description": "Wildcard pattern, e.g. *FuelPressureException*",
        "type": "string",
        "optional": false
      }
    }
  }
}
```

**Tool types:**
- `index_search` — searches an Elasticsearch index; requires `configuration.pattern`
- `esql` — runs an ES|QL query; requires `configuration.query` and `configuration.params`
- `workflow` — invokes a Kibana workflow; requires `configuration.workflow_id`

**Gotchas:**
- `index_search` requires `configuration.pattern`, NOT `configuration.index` — returns 400 otherwise
- `esql` requires `configuration.params: {}` even when empty — returns 400 otherwise
- `workflow` requires `configuration.workflow_id` — discover via `POST /api/workflows/search`
- No `name` field on tools — use `id` and `description` only

### Parameterized ES|QL Tools
ES|QL tools support named parameters so the LLM only provides values, not query structure.

**Parameter syntax in queries:**
- `?param_name` — for **values** (strings, numbers). The LLM provides the value; it's substituted safely.
- `??param_name` — for **identifiers** (field names, function names). Use when the LLM chooses which field to query.
- Ref: https://www.elastic.co/docs/reference/query-languages/esql/esql-rest#esql-rest-params
- Ref: https://github.com/elastic/elasticsearch/pull/122459 (double `??` for identifiers)

**Parameter schema in `configuration.params`:**
```json
"params": {
  "param_name": {
    "description": "Shown to LLM as parameter description",
    "type": "string",
    "optional": false
  }
}
```
- `type`: `"string"` (other types TBD)
- `optional`: `false` = required, `true` = optional
- Agent Builder auto-generates a JSON Schema from params and exposes it to the LLM

**CRITICAL: LIKE wildcards with parameters:**
- `CONCAT("*", ?param, "*")` does NOT work — returns validation error
- Instead use `LIKE ?param` and tell the LLM in the description to include wildcards: `"e.g. *FuelPressureException*"`
- `== ?param` works directly for exact string matching (no wildcards needed)

### Updating Existing Tools (PUT)
POST fails if the tool already exists. Use PUT to update:
```
PUT /api/agent_builder/tools/{tool_id}
{
  "description": "...",
  "configuration": { ... }
}
```
**CRITICAL**: PUT body must NOT include `id` or `type` — returns 400 if present.
Only `description` and `configuration` are accepted.

### ES|QL Syntax for Agent Tools
- `COUNT_IF()` does NOT exist — use `COUNT(*) WHERE condition`
- Example: `STATS errors = COUNT(*) WHERE severity_text == "ERROR", total = COUNT(*) BY field`
- Always test via `POST /_query` before deploying to tool configuration

### Agent Schema
```
POST /api/agent_builder/agents
{
  "id": "<agent-id>",
  "name": "...",
  "description": "...",
  "configuration": {
    "instructions": "...",           // System prompt goes here — NOT top-level "system_prompt"
    "tools": [
      { "tool_ids": ["tool1", "tool2", ...] }
    ]
  }
}
```

**Gotchas:**
- `id` is REQUIRED — returns 400 if missing
- System prompt is `configuration.instructions`, NOT `system_prompt` at top level
- Tools must be `configuration.tools: [{tool_ids: [...]}]` (array of objects with tool_ids array)
- If agent already exists, POST returns error; use PUT with agent ID to update
- **CRITICAL: Agent PUT truncates instructions** — The PUT endpoint for agents does NOT reliably update `configuration.instructions` (system prompt). The instructions may be silently truncated or ignored. **Workaround:** DELETE the agent first (`DELETE /api/agent_builder/agents/{agent_id}`), then POST to recreate it fresh. This is the only reliable way to update the system prompt.
- **Verification after deploy:** Always verify the deployed agent's instructions length and content via `GET /api/agent_builder/agents`. Compare against expected content to catch silent truncation.

### Validation
```
GET /api/agent_builder/agents    — List all agents
GET /api/agent_builder/tools     — List all tools (includes readonly defaults; filter by readonly=false)
```

---

## Kibana Saved Objects API (Serverless 9.4.0)

### Available Endpoints
| Endpoint | Status |
|----------|--------|
| `POST /api/saved_objects/_import` | Works |
| `POST /api/saved_objects/_export` | Works |
| `GET /api/saved_objects/_find` | **DISABLED** on serverless |
| `GET /api/saved_objects/<type>/<id>` | **DISABLED** on serverless |
| `DELETE /api/saved_objects/<type>/<id>` | **DISABLED** on serverless |

### Dashboard Import with Lens Panels — CRITICAL FIX

**Problem:** Importing dashboards with by-value Lens panels (embeddableConfig.attributes) causes HTTP 500 on Kibana serverless 9.4.0.

**Root Cause:** Without migration version fields, Kibana cannot determine which migrations to run on the embedded Lens state and crashes instead of gracefully handling it.

**Fix:** Add these two fields to the dashboard saved object:
```json
{
  "attributes": { ... },
  "id": "my-dashboard",
  "type": "dashboard",
  "coreMigrationVersion": "8.8.0",
  "typeMigrationVersion": "10.3.0",
  "references": [],
  "managed": false
}
```

**Key facts:**
- `coreMigrationVersion: "8.8.0"` — required for all saved objects on serverless
- `typeMigrationVersion: "10.3.0"` — required for dashboard type specifically
- Without these, ANY `embeddableConfig.attributes` (even `{title: "X"}`) causes 500
- Bare Lens panels (no embeddableConfig.attributes) import fine without migration versions
- Empty dashboards (no panels) import fine without migration versions
- `compatibilityMode=true` does NOT fix the issue — still 500

### Lens Panel Reference Format

For by-value Lens panels inside panelsJSON, use this reference name format:
```
"indexpattern-datasource-layer-<layerId>"
```

For exported dashboards with by-reference panels, Kibana uses:
```
"<panelIndex>:<panelRefName>"   (e.g., "p1:panel_p1")
```

### Verification (Serverless-Compatible)

Use `_export` instead of `_find` or `_get`:
```bash
POST /api/saved_objects/_export
{"objects": [{"type": "dashboard", "id": "my-dashboard-id"}], "includeReferencesDeep": false}
```

---

## Elasticsearch API Notes

### Bulk Indexing
```
POST /_bulk?refresh=true
{"index":{"_index":"my-index","_id":"doc-id"}}
{"field":"value"}
```
- Use `?refresh=true` to make docs immediately searchable
- Content-Type must be `application/x-ndjson`

### Index Creation
- On serverless, creating an index that already exists returns an error (not idempotent)
- Wrap in error handling: `|| log_warn "may already exist"`

---

## OTLP Telemetry Patterns

### Data Stream Routing
Resource attributes control data stream routing:
```python
"data_stream.type": "logs"      # → logs-*
"data_stream.dataset": "generic" # → *-generic.*
"data_stream.namespace": "default" # → *-*.default
```

**CRITICAL — Host Metrics & Infrastructure UI:**
Do NOT set `data_stream.*` attributes on host metrics. The Elastic OTLP endpoint
auto-routes based on the scope name (hostmetricsreceiver scraper names):
- With `data_stream.dataset="generic"` → `metrics-generic.otel-default` (Infrastructure UI ignores this)
- Without `data_stream.*` → `metrics-hostmetricsreceiver.otel-default` (Infrastructure UI finds this)

The Elastic OTLP endpoint recognizes the scraper scope names and routes accordingly.
This is the same behavior as the real OTel Collector — it doesn't set `data_stream.*` either.

### Trace Span Kinds
```python
SPAN_KIND_INTERNAL = 1
SPAN_KIND_SERVER = 2
SPAN_KIND_CLIENT = 3
```

### Trace Status Codes
```python
STATUS_CODE_OK = 1
STATUS_CODE_ERROR = 2
```

### Host Metrics — Infrastructure UI
Must use exact OTel Collector scraper scope names for Elastic Infrastructure UI recognition:
```
github.com/open-telemetry/opentelemetry-collector-contrib/receiver/hostmetricsreceiver/internal/scraper/<name>scraper
```
Where `<name>` is: load, cpu, memory, disk, filesystem, network, processes

Required resource attributes (from Elastic System OTel Assets guide):
- `host.name`, `host.id`, `host.arch`, `host.ip`, `host.mac`
- `host.cpu.vendor.id`, `host.cpu.family`, `host.cpu.model.id`, `host.cpu.model.name`, `host.cpu.stepping`, `host.cpu.cache.l2.size`
- `os.description`, `os.type`

Required metrics (must be enabled explicitly on upstream Collector):
- `system.cpu.utilization`, `system.cpu.logical.count`, `system.memory.utilization`
- All others enabled by default: `system.cpu.time`, `system.cpu.load_average.*`, `system.memory.usage`, `system.disk.io`, `system.network.io`, etc.

---

## Kibana Workflows API

### Route Migration (9.4.0+)

The Workflows API paths changed in 9.4.0. The deployer (`elastic_config/deployer.py`) uses
helper methods `_wf_create`, `_wf_search`, and `_wf_delete` that try the new path first and
fall back to the old path on 404/405, supporting both versions simultaneously.

| Operation | Old path (pre-9.4) | New path (9.4+) |
| --------- | ------------------ | --------------- |
| Create single | `POST /api/workflows` body `{"yaml":"..."}` | `POST /api/workflows/workflow` |
| Bulk create | `POST /api/workflows/_bulk_create` | `POST /api/workflows` body `{"workflows":[...]}` |
| Search/list | `POST /api/workflows/search` body `{"page":1,"size":100}` | `GET /api/workflows` |
| Get by ID | `GET /api/workflows/{id}` | `GET /api/workflows/workflow/{id}` |
| Update | `PUT /api/workflows/{id}` | `PUT /api/workflows/workflow/{id}` |
| Delete | `DELETE /api/workflows/{id}` | `DELETE /api/workflows/workflow/{id}` |
| Run | `POST /api/workflows/{id}/run` | `POST /api/workflows/workflow/{id}/run` |
| Execution status | `GET /api/workflowExecutions/{id}` | `GET /api/workflows/executions/{id}` |

**CRITICAL (9.4.0 breaking change):** `POST /api/workflows` is now the **bulk create** endpoint
expecting `{"workflows": [...]}`. Sending `{"yaml": "..."}` to this path returns:
`{"statusCode":400,"error":"Bad Request","message":"[{\"expected\":\"array\",\"path\":[\"workflows\"]}]"}`
Use `POST /api/workflows/workflow` for single workflow creation on 9.4+.

### Endpoints (legacy reference)

```
POST /api/workflows/workflow     — Create single (9.4+)
POST /api/workflows              — Create single (pre-9.4) / Bulk create (9.4+)
POST /api/workflows/search       — List/search (pre-9.4)
GET  /api/workflows              — List/search (9.4+)
DELETE /api/workflows/workflow/{id} — Delete (9.4+)
DELETE /api/workflows/{id}       — Delete (pre-9.4)
POST /api/workflows/workflow/{id}/run — Run (9.4+)
GET  /api/workflowExecutions/{id} — Execution status (pre-9.4)
```

### CRITICAL: Required Header
All workflow endpoints require `x-elastic-internal-origin: kibana` header.
Without it, returns 400: "exists but is not available with the current configuration".
This header is needed for pre-GA internal Kibana APIs.

### Workflow YAML Schema (workflow-schema-strict-with-dynamic-connectors.json)

**Required top-level fields:**
| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Human-readable workflow name |
| `steps` | Yes | Array of step objects (at least 1) |
| `version` | No | String, e.g. `"1"` |
| `description` | No | Purpose/explanation |
| `triggers` | No | Array of trigger objects (defaults to manual) |
| `inputs` | No | Array of `{name, type, required, default, description}` |
| `consts` | No | Constants referenced via `{{ consts.key }}` |
| `tags` | No | Array of strings for categorization |
| `outputs` | No | Workflow output definitions |
| `settings` | No | Timeout, retries, log level |
| `enabled` | No | Boolean |

**NOT valid top-level:** `trigger` (singular), `input` (singular), `metadata`.

### Trigger Types
```yaml
triggers:
  - type: manual          # Default if triggers omitted
  - type: scheduled       # Cron/interval
    with:
      every: "5m"         # Simple interval (30s, 5m, 2h, 1d)
  - type: alert           # Fired by alert rules
```

### Input Format
```yaml
inputs:
  - name: my_param
    type: string           # string, number, boolean, array, object
    required: true
    default: "fallback"
    description: "What this does"
```

### Valid Step Types
| Type | Description | Key `with` fields |
|------|-------------|-------------------|
| `console` | Log a message | `message` |
| `http` | HTTP request | `url`, `method`, `headers`, `body`, `timeout` |
| `elasticsearch.search` | ES search | `index`, `size`, `query` (NO `aggs`, `sort`, `_source`, `match_phrase`) |
| `elasticsearch.esql.query` | ES\|QL query | `query`, `format` (use `json`) |
| `elasticsearch.index` | Index a doc | `index`, `document`, `id`, `refresh` |
| `elasticsearch.request` | Generic ES | `method`, `path`, `headers`, `body` |
| `elasticsearch.indices.exists` | Check index | `index` |
| `elasticsearch.indices.create` | Create index | `index`, `mappings` |
| `if` | Conditional | `condition`, `steps`, `else` |
| `foreach` | Loop | `foreach` (expr), `steps` |
| `data.set` | Set variables | `var` (array of key-value pairs) |
| `wait` | Delay | `duration` (e.g. "30s") |
| `ai.agent` | AI agent call | `message` + `agent-id` (step-level) |
| `ai.prompt` | AI prompt | `prompt`, `schema` + `connector-id` |
| `email` | Send email | `to` (array), `subject`, `message` + `connector-id` |
| `slack` | Slack message | `message` + `connector-id` |
| `jira` | Jira ticket | `projectKey`, `summary` + `connector-id` |
| `kibana.request` | Kibana API | `method`, `path`, `body` |

**CRITICAL: `ai.agent` agent-id placement.** The `agent-id` for `ai.agent` steps MUST be
at the **step level** (like `connector-id`), NOT inside `with`. If placed inside `with`,
the workflow engine silently strips it and the agent step may fail or use a wrong default agent.
```yaml
# CORRECT — agent-id at step level
- name: invoke_agent
  type: ai.agent
  agent-id: nova7-launch-anomaly-analyst
  with:
    message: "Perform root cause analysis..."

# WRONG — agent_id inside with (silently stripped!)
- name: invoke_agent
  type: ai.agent
  with:
    agent_id: nova7-launch-anomaly-analyst   # ← STRIPPED BY ENGINE
    message: "..."
```

**NOT valid step types:** `condition`, `branch`, `elasticsearch_query`, `webhook`,
`index` (use `elasticsearch.index`), `script`, `connector`, `agent` (use `ai.agent`),
`parallel`, `wait_then_query`.

### Step Structure
```yaml
- name: step_name          # REQUIRED — unique identifier (NOT "id")
  type: step_type          # REQUIRED
  with:                    # Parameters (NOT "params")
    key: value
  connector-id: "..."      # For connector steps (slack, jira, ai.prompt)
  agent-id: "..."          # For ai.agent steps — MUST be at step level
  condition: "expr"        # Conditional execution
  on-failure:              # Error handling
    retry:
      max-attempts: 3
      delay: "5s"
    continue: true
  timeout: "30s"
```

### CRITICAL: `elasticsearch.search` — Schema Restrictions
The workflow schema for `elasticsearch.search` is extremely restrictive. Only
`index`, `size`, and `query` reliably work. Many common ES features are NOT allowed:
- `sort` → causes `Valid: false` (name shows as "Untitled workflow", `definition: null`)
- `aggs` → causes validation warnings; use `size: 0` + `hits.total.value` for counts
- `_source` → rejected; use small `size` instead of source filtering
- `exists` query → causes `Valid: false`; use `must_not` + `match` as alternative

**Allowed `with` fields for `elasticsearch.search`:** `index`, `size`, `query`.

**Workarounds:**
- **Counting:** `size: 0` query → `{{ steps.X.output.hits.total.value }}`
- **Sorting:** Use wide time ranges (`now-30m`) to compensate for no `sort`
- **Source filtering:** Use small `size` (1-5) to limit payload

### Template Variables
| Context | Syntax | Example |
|---------|--------|---------|
| Inputs | `{{ inputs.name }}` | `{{ inputs.channel }}` |
| Step output | `{{ steps.name.output.path }}` | `{{ steps.search.output.hits.total.value }}` |
| Constants | `{{ consts.key }}` | `{{ consts.api_key }}` |
| Loop item | `{{ foreach.item.field }}` | `{{ foreach.item._source.name }}` |
| Workflow | `{{ workflow.name }}` | Workflow metadata |
| Execution | `{{ execution.id }}` | Execution metadata |

**Expression types:**
- `{{ }}` — inserts as string
- `${{ }}` — preserves data types (arrays, objects)
- Liquid filters supported: `json`, `json_parse`, `base64_encode`, `upcase`, `downcase`, `replace`, `map`, `where`, `sort`, `join`, `filter`

### Debugging Invalid Workflows
When `valid: false`:
1. Name shows as "Untitled workflow" = YAML parsing/schema failure (not just a step error)
2. `definition: null` = schema didn't match at all
3. No error details in API response — must bisect by removing steps
4. Strategy: start with minimal workflow (1 step), add steps back one at a time
5. Common causes: `sort` in es.search, `match_phrase` in es.search, wrong step type names, `params` instead of `with`, `id` instead of `name` on steps

### Example: Minimal Valid Workflow
```yaml
version: "1"
name: My Workflow
triggers:
  - type: manual
inputs:
  - name: query
    type: string
    default: "test"
steps:
  - name: search
    type: elasticsearch.search
    with:
      index: "logs-*"
      size: 10
      query:
        bool:
          filter:
            - range:
                "@timestamp":
                  gte: "now-5m"
  - name: log_result
    type: console
    with:
      message: "Found {{ steps.search.output.hits.total.value }} results"
```

---

## Kibana Streams Significant Events API (Technical Preview, 9.2.0+)

### Endpoints
```
POST /api/streams/_enable                      — Enable Streams (idempotent)
GET  /api/streams                              — List available streams
POST /api/streams/{name}/queries/_bulk         — Bulk create/update/delete queries
GET  /api/streams/{name}/queries               — List queries on a stream
DELETE /api/streams/{name}/queries/{id}         — Delete a single query
```

### CRITICAL: Required Headers
All Streams endpoints require both:
- `kbn-xsrf: true`
- `x-elastic-internal-origin: kibana`

### Query Schema — `esql` format (current, 9.4.0+)

```json
{
  "operations": [
    {
      "index": {
        "id": "my-query-id",
        "title": "Human-readable title",
        "description": "",
        "esql": {
          "query": "FROM logs.otel,logs.otel.* METADATA _id, _source | WHERE body.text LIKE \"*ERROR_CODE*\" AND severity_text == \"ERROR\""
        }
      }
    }
  ]
}
```

**Gotchas:**

- `description` is **REQUIRED** as of 9.4.0 — omitting it returns 400 `invalid_union` ZodError; an empty string `""` is acceptable
- The `esql` field (object with `query` string) is the current supported format — use `FROM logs.otel,logs.otel.*` for OTLP log streams
- The `kql` field was used in earlier versions; `esql` is now preferred for semantic searchability
- The `filter` DSL (`and`/`or`/`eq` combinators) from the API docs does **NOT** work
- `evidence` and `feature` fields are optional metadata (they get stored but are not the detection mechanism)
- Delete operations in bulk: `{"delete": {"id": "query-id"}}`
- IDs should be prefixed for easy cleanup (e.g., `nova7-se-ch01`)

### KQL Query Format
KQL inside the `kql.query` field uses standard Kibana Query Language:
```
attributes.error.type: "ThermalCalibrationException" AND attributes.sensor.type: "thermal" AND attributes.vehicle_section: "engine_bay"
```

**Field mapping note:** OTLP log record attributes (set as `"error.type"` in code) become
`attributes.error.type` in the Elasticsearch index after OTLP ingestion.

### Stream Name Discovery
- `GET /api/streams` returns available streams
- For OTLP logs, the stream name is `logs.otel`
- Stream must contain data for queries to match

### Verification
```bash
GET /api/streams/{name}/queries
# Response: {"queries": [{id, title, kql: {query: "..."}, ...}, ...]}
```
Count queries with your prefix to verify deployment.

---

## Kibana Lens Panel Format (9.4.0) — By-Value formBased

When creating Lens by-value panels (embedded in panelsJSON), the format must exactly match what Kibana 9.4 expects. Export a working built-in [OTel] dashboard panel to see the reference format.

### Layer Structure (inside `datasourceStates.formBased.layers`)
```json
{
  "<layer-uuid>": {
    "columnOrder": ["<col-uuid-1>", "<col-uuid-2>"],
    "columns": { ... },
    "ignoreGlobalFilters": false,
    "incompleteColumns": {},
    "sampling": 1
  }
}
```
**All three extra fields** (`ignoreGlobalFilters`, `incompleteColumns`, `sampling`) are **required** — without them, Kibana throws `Cannot read properties of undefined (reading 'columnOrder')`.

### State Structure
```json
{
  "adHocDataViews": {},
  "datasourceStates": { "formBased": { "layers": { ... } } },
  "filters": [],
  "internalReferences": [],
  "query": { "language": "kuery", "query": "" },
  "visualization": { ... }
}
```

### Panel Wrapper
```json
{
  "embeddableConfig": {
    "attributes": {
      "description": null,
      "references": [...],
      "savedObjectId": null,
      "state": { ... },
      "title": "",
      "type": "lens",
      "visualizationType": "lnsXY"
    },
    "enhancements": {},
    "syncColors": false,
    "syncCursor": true,
    "syncTooltips": false
  },
  "gridData": { "h": 12, "i": "p1", "w": 24, "x": 0, "y": 0 },
  "panelIndex": "p1",
  "title": "Panel Title",
  "type": "lens"
}
```

### Count Column `sourceField`
Use `"sourceField": "___records___"` (NOT `"Records"`) for count operations.

### lnsXY Visualization — Required Fields
All lnsXY panels MUST include `legend` and `valueLabels`:
```json
{
  "legend": {"isVisible": true, "position": "right"},
  "valueLabels": "hide",
  "preferredSeriesType": "area_stacked",
  "layers": [...]
}
```
Without `legend.isVisible`, Kibana throws `Cannot read properties of undefined (reading 'isVisible')` and the panel silently shows no data.

### lnsDatatable Visualization — Required Fields
Datatable columns need `isTransposed` and `isMetric`, plus `paging` and `sorting`:
```json
{
  "columns": [
    {"columnId": "...", "isTransposed": false, "isMetric": false},
    {"columnId": "...", "isTransposed": false, "isMetric": true}
  ],
  "layerId": "...",
  "layerType": "data",
  "paging": {"enabled": true, "size": 10},
  "sorting": null
}
```

### lnsHeatmap Visualization — Include Legend
Heatmap panels should include `"legend": {"isVisible": true, "position": "right"}`.

### terms Column — Include Bucket Params
Terms columns should include `missingBucket` and `otherBucket` params:
```json
"params": {
  "size": 10,
  "orderDirection": "desc",
  "orderBy": {"columnId": "...", "type": "column"},
  "missingBucket": false,
  "otherBucket": false
}
```

### Panel Attributes — Avoid null Fields
The working OTel panels do NOT include `"description": null` or `"savedObjectId": null`. Omit these entirely rather than setting to null.

### Generator Script
Use `elastic_config/dashboards/generate_exec_dashboard.py` to regenerate the NDJSON — it produces correct Kibana 9.4 format with random UUIDs for layer/column IDs.

---

## OTLP Field Mapping Reference

When OTLP data is ingested by Elastic, field paths differ from what you set in code:

| Set in OTLP Code | Actual ES Field Path | Notes |
|-------------------|---------------------|-------|
| Log attribute `error.type` | `attributes.error.type` | Log attributes → `attributes.*` |
| Log attribute `system.subsystem` | `attributes.system.subsystem` | |
| Log attribute `chaos.channel` | `attributes.chaos.channel` | |
| Resource attr `service.name` | `resource.attributes.service.name` | Resource attrs → `resource.attributes.*` |
| Resource attr `cloud.provider` | `resource.attributes.cloud.provider` | |
| Severity | `severity_text` | NOT `severityText` or `log.level` |
| Log body | `body` | NOT `message` |
| Data stream dataset | `data_stream.dataset` includes `.otel` | e.g., `nginx.access.otel` not `nginx.access` |

### CRITICAL: Passthrough Mapping on `logs` Stream

The `attributes` field on the `logs` data stream uses **passthrough** mapping type.
This means individual attribute keys (e.g., `attributes.error.type`, `attributes.chaos.channel`)
are stored in `_source` but are **NOT indexed** — they cannot be searched, filtered, or
aggregated with term/match/range queries or ES|QL WHERE clauses.

**Indexed (queryable) fields on `logs`:**
| Field | Type | Values |
|-------|------|--------|
| `body.text` | text | Log message body — error type names appear here |
| `severity_text` | keyword | `INFO`, `WARN`, `ERROR` |
| `log.level` | keyword | Same as `severity_text` |
| `service.name` | keyword | 9 services (e.g., `fuel-system`, `navigation`) |
| `resource.attributes.service.name` | keyword | Same as `service.name` |
| `event_name` | keyword | Top-level field; contains JSON with callback_url/user_email |
| `@timestamp` | date | Event timestamp |

**NOT indexed (source-only) fields:** `attributes.*`, `resource.attributes.cloud.*`,
`resource.attributes.host.*` (except `host.name`).

**Consequence:** All queries (KQL, ES query DSL, ES|QL) that need to filter by error type
must use `body.text` instead of `attributes.error.type`. Examples:
- **KQL:** `body.text: "FuelPressureException" AND severity_text: "ERROR"`
- **ES query DSL:** `{"match_phrase": {"body.text": "FuelPressureException"}}`
- **ES|QL:** `WHERE body.text LIKE "*FuelPressureException*" AND severity_text == "ERROR"`

This affects: significant event queries, alert rules, workflow ES search steps, and agent tools.

### Required Host Metric Resource Attributes
These must be set for OTel dashboards to fully work:
```
host.name, host.id, host.arch, host.type, host.image.id
host.cpu.model.name, host.cpu.vendor.id, host.cpu.family
host.cpu.model.id, host.cpu.stepping, host.cpu.cache.l2.size
host.ip (array), host.mac (array)
os.type, os.description
cloud.provider, cloud.platform, cloud.region, cloud.availability_zone
```

### Required Host Metrics (for OTel dashboard panels)
Beyond the defaults (`system.cpu.time`, `system.memory.usage`, `system.disk.io`, `system.network.io`), these are needed:
- `system.disk.io_time` — disk IO time cumulative counter (per device + direction)
- `system.network.packets` — packet counts (per device + direction)
- `system.network.dropped` — dropped packet counts
- `system.network.errors` — error counts
- `system.processes.created` — cumulative process creation count

---

## Kibana Data Views

The OTel dashboards expect these data views to exist:
- `logs-*` — for log dashboards and Exec Dashboard
- `metrics-*` — for host/system metrics dashboards

Create via:
```bash
POST /api/data_views/data_view
{"data_view": {"title": "metrics-*", "name": "metrics-*", "timeFieldName": "@timestamp"}}
```

---

## Kibana Alerting API

### Alert Rules
```
POST /api/alerting/rule
{
  "name": "Rule Name",
  "rule_type_id": ".es-query",
  "consumer": "alerts",
  "schedule": { "interval": "30s" },
  "params": {
    "searchType": "esQuery",
    "index": ["logs-*"],
    "esQuery": "{ \"query\": { ... } }",
    "timeField": "@timestamp",
    "timeWindowSize": 1,
    "timeWindowUnit": "m",
    "threshold": [0],
    "thresholdComparator": ">"
  },
  "actions": [{ "group": "query matched", "id": "<connector-id>", "params": {...} }],
  "tags": ["nova7"]
}
```

### Native Workflows Connector (Preferred)
Use the built-in `.workflows` system action to trigger workflows directly from alert rules.
No need to create a webhook connector — the system connector ID is `system-connector-.workflows`.

```json
"actions": [{
  "group": "query matched",
  "id": "system-connector-.workflows",
  "params": {
    "subAction": "run",
    "subActionParams": {
      "workflowId": "<workflow-id>",
      "inputs": {
        "channel": 1,
        "error_type": "SomeException",
        "subsystem": "propulsion",
        "severity": "high"
      }
    }
  },
  "frequency": {
    "summary": false,
    "notify_when": "onActiveAlert",
    "throttle": null
  }
}]
```

**CRITICAL:**
- `.workflows` is a **system action type** — you CANNOT create a connector for it (`POST /api/actions/connector` returns "System action creation is forbidden")
- The workflow MUST have `type: alert` in its `triggers` list for the Workflows connector to appear in the UI
- `subAction` must be `"run"` — any other value is rejected
- **`subActionParams.inputs` resolves BLANK in workflow steps** — values passed via `inputs` in the alert action are NOT accessible as `{{ inputs.X }}` in the workflow. They resolve to empty strings in ALL step types (search, console, email, http). Use `event.*` namespace instead (e.g., `event.rule.tags[1]`, `event.rule.name`)
- Alert context is available via `event.*` namespace: `event.rule.name`, `event.rule.tags[N]`, `event.alerts[0].kibana.alert.*`, `event.spaceId`
- Encode key data in alert rule `tags` for easy workflow access: `tags: ["nova7", "FuelPressureException"]` → `{{ event.rule.tags[1] }}`

### Webhook Connector (Legacy)
```
POST /api/actions/connector
{
  "name": "Connector Name",
  "connector_type_id": ".webhook",
  "config": { "url": "...", "method": "post", "headers": {...} },
  "secrets": {}
}
```

---

## General Lessons

1. **Search docs before trial-and-error** — API schemas aren't always intuitive; check official docs and GitHub issues first
2. **Export to learn format** — When import fails, export existing objects to see what Kibana expects (migration versions, field naming)
3. **Serverless != self-managed** — Many Kibana APIs are disabled on serverless; always test against the actual cluster
4. **Migration versions matter** — Kibana saved objects need `coreMigrationVersion` and `typeMigrationVersion` for proper import, especially with complex embedded content like Lens panels
5. **Test incrementally** — When debugging 500 errors, strip down to minimal reproducer and add fields back one at a time
6. **`x-elastic-internal-origin: kibana`** — Required header for pre-GA internal Kibana APIs (Workflows, etc.). Without it, routes return "exists but not available with current configuration"
7. **Bulk indexing large payloads** — Use `--data-binary @file` instead of `-d "$VARIABLE"` to avoid shell "Argument list too long" errors
8. **OTLP field paths are NOT intuitive** — Always verify with `_field_caps` against actual ES data before writing ESQL queries, dashboards, or KQL filters
9. **Lens format is fragile** — Missing a single field like `incompleteColumns: {}` causes cryptic JS errors; always use a generator script or export a working panel as reference
10. **Kibana API from shell** — The `source .env && curl` pattern often fails to load vars when piped; use `export $(grep -E '^[A-Za-z_]' .env | xargs)` or hardcode the URL. Also prefer writing curl output to a temp file, then parsing with a separate Python script, rather than piping inline — multiline heredoc Python inside `$()` is fragile with escaping
11. **Data views must exist before dashboard import** — If a Lens panel references data view ID `logs-*`, that data view must exist in Kibana. Create it via `POST /api/data_views/data_view` before importing the dashboard. After import, users may need to refresh the browser for Kibana to pick up new data view fields
12. **`ai.agent` context window overflow** — The workflow engine passes ALL accumulated step outputs as context to `ai.agent` steps. OTLP log documents are large (passthrough attributes in `_source`). If `elasticsearch.search` steps return full docs, the combined context can exceed the model's max input length: `"Input is too long for requested model"` (error from inference entity `.rainbow-sprinkles-elastic`). **Fix:** Always use `_source` filtering and small `size` in search steps that precede `ai.agent` steps
13. **`ai.agent` agent-id placement** — The `agent-id` for `ai.agent` steps MUST be at the **step level** (like `connector-id`), NOT inside `with`. If placed inside `with` (as `agent_id` or `agentId`), the workflow engine silently strips it
14. **Workflow `data.set` variable access** — Variables set by `data.set` inside an `if` step's nested branches are NOT accessible via the parent step name. `{{ steps.parent_if_step.output.var }}` resolves to empty. Use `{{ inputs.field }}` directly or `{{ steps.nested_set_step.output.var }}` instead
15. **Workflow execution API** — `GET /api/workflowExecutions?workflowId={id}` lists executions. `GET /api/workflowExecutions/{executionId}` gets details (status, error, startedAt, finishedAt). The `history` field on the workflow object only shows completed runs
16. **OTel CPU state names** — Use `wait` not `iowait` for I/O wait CPU state. The OTel standard CPU states are: `user`, `system`, `idle`, `wait`, `nice`, `interrupt`, `softirq`, `steal`. Elastic Infrastructure UI dashboards filter by these exact names
17. **Passthrough mapping on `logs` stream** — OTLP `attributes.*` and most `resource.attributes.*` use passthrough mapping (stored in `_source` but NOT indexed). Only `body.text`, `severity_text`, `service.name`, `resource.attributes.service.name`, `host.name`, `@timestamp` are indexed. Use `body.text LIKE "*ErrorType*"` for ES|QL, `body.text: "ErrorType"` for KQL, and `match`/`match_phrase` on `body.text` for query DSL
18. **AI agent body vs body.text bug** — The AI agent (LLM) will construct ES|QL queries using `body` instead of `body.text`, causing `verification_exception: Unknown column [body]`. To prevent this: (a) add a prominent `## CRITICAL: Field Names` warning block in the agent system prompt, (b) add `body.text (NOT body)` warnings to every ES|QL tool description, (c) add validation in `validate.sh` to check deployed agent instructions and tool descriptions contain `body.text` warnings. This is a recurring issue because `body` seems natural but the OTLP mapping creates `body.text` as the actual indexed text field
19. **Agent PUT does not update instructions reliably** — When updating an existing agent via `PUT /api/agent_builder/agents/{id}`, the `configuration.instructions` field may be silently truncated. Always DELETE + POST to recreate the agent when updating the system prompt
20. **Tool type changes require DELETE before POST** — `PUT /api/agent_builder/tools/{id}` cannot change the `type` field (returns 400 if included). To change a tool's type (e.g., `esql` → `workflow`), you must DELETE the old tool first, then POST the new one. Use a `create_tool` pattern that always deletes before creating
21. **`index_search` tool pattern — use exact stream name** — Set `configuration.pattern` to `"logs"` (the exact data stream name), NOT `"logs*"` or `"logs-*"`. A wildcard pattern like `logs*` causes the agent to search across unrelated indices (nginx, mysql, generic logs) that contain no mission telemetry, wasting tool calls and confusing the LLM with irrelevant results
22. **Alert-triggered workflows use `event.*` namespace** — When a workflow has `type: alert` trigger and is invoked by the `.workflows` system connector, the alert context is available via the `event.*` namespace: `event.rule.name`, `event.rule.tags[0]`, `event.rule.tags[1]`, `event.alerts[0].kibana.alert.*`, `event.spaceId`. This is NOT `inputs.*` — the `event.*` namespace is the raw alert context document
23. **`inputs.*` from alert connector resolves BLANK everywhere** — When an alert rule fires a workflow via the `.workflows` connector with `subActionParams.inputs`, those inputs resolve to **empty strings** in ALL workflow step contexts: `elasticsearch.search` query fields, `console` messages, `email` subject/message/to, `http` body, etc. Do NOT rely on `inputs.*` in alert-triggered workflows. Use `event.*` namespace instead (e.g., `event.rule.tags[1]` for error type, `event.rule.name` for rule name)
24. **Having an `inputs:` schema may block alert context** — If an alert-triggered workflow defines an `inputs:` schema, it may filter/block the raw alert context document. For workflows that only need alert context (not manual inputs), remove the `inputs:` block entirely and use only `event.*` for all data access
25. **Workflow `if` conditions use KQL dot paths, NOT `{{ }}` templates** — The `condition` field in workflow steps uses KQL syntax with literal dot paths as field names. **CORRECT:** `condition: "inputs.severity : critical"`. **WRONG:** `condition: "{{ inputs.severity }} : critical"` (template substitution replaces the value, then KQL interprets the value as a field name → always false). For array access in conditions, use `.0.` instead of `[0]`: `condition: "steps.set_data.output.var.0.email : *@*"`
26. **`elasticsearch.search` schema — `aggs`, `_source`, `sort`, `exists`, `match_phrase` all NOT allowed** — The workflow schema for `elasticsearch.search` steps only allows `index`, `size`, and `query` with basic query types (`match`, `term`, `range`, `bool`). Including `sort` → `Valid: false`. Including `match_phrase` → `Valid: false` (shows as "Untitled workflow"). Including `aggs` → validation warnings. Including `_source` → rejected. Including `exists` query → `Valid: false`. **Preferred workaround:** Use `elasticsearch.esql.query` with `KQL()` function instead — e.g. `FROM logs,logs.* | WHERE KQL("body.text: \"ErrorType\" AND severity_text: \"ERROR\"") | KEEP event_name`. For counts: `| STATS total = COUNT(*)`. Output format: `{columns: [...], values: [[...]]}` — access via `steps.X.output.values[row][col]`
27. **`Elastic-Cloud-SMTP` system email connector** — Pre-configured email connector on Elastic Cloud serverless. Use `connector-id: Elastic-Cloud-SMTP` in email steps. Sends from `noreply@alerts.elastic.co`. Returns `{accepted: [...], rejected: [...], response: "250 2.0.0 Ok: queued"}`
28. **Extracting callback_url from log `event_name`** — `event_name` is a top-level indexed `keyword` field (NOT a passthrough attribute). It contains a JSON string like `{"callback_url": "http://...", "user_email": "user@example.com"}`. Extract using ES|QL: `FROM logs,logs.* | WHERE KQL("body.text: \"ErrorType\"...") | KEEP event_name` → then `data.set` with `json_parse`: `${{ steps.search.output.values[1][0] | json_parse }}`. Access parsed fields via `steps.set_step.output.var[0].event_meta.callback_url`
29. **Parameterized ES|QL tools — `?param` syntax** — Agent Builder `esql` tools support named parameters using ES|QL's native `?param_name` (for values) and `??param_name` (for identifiers/field names). Define params in `configuration.params` with `{description, type, optional}`. The Agent Builder auto-generates a JSON Schema from params for the LLM. **CRITICAL:** `CONCAT("*", ?param, "*")` does NOT pass validation — use `LIKE ?param` and instruct the LLM in the param description to include wildcards (e.g., `"Wrap in asterisks: *FuelPressureException*"`). `== ?param` works for exact string matching. Refs: https://www.elastic.co/docs/reference/query-languages/esql/esql-rest#esql-rest-params , https://github.com/elastic/elasticsearch/pull/122459
30. **Prefer parameterized esql tools over `index_search` for logs** — `index_search` lets the LLM auto-generate ES|QL queries, which consistently fails: uses `message` instead of `body.text`, `FROM logs` instead of `FROM logs,logs.*`, `resource.attributes.service.name` instead of `service.name`, `MATCH()` instead of `LIKE`. Parameterized `esql` tools bake in correct field names and query patterns — the LLM only provides parameter values, eliminating 3-4 wasted tool calls per investigation
29. **`elasticsearch.esql.query` step — preferred for text search in workflows** — Use `elasticsearch.esql.query` instead of `elasticsearch.search` when you need `match_phrase`, `sort`, or other schema-restricted features. Key patterns: (a) Use `FROM logs,logs.*` to include sub-streams. (b) Use `KQL()` function for text matching: `WHERE KQL("body.text: \"ErrorType\" AND severity_text: \"ERROR\"")`. (c) Output is `{columns: [{name, type}], values: [[row1], [row2]]}`. (d) Access values positionally: `steps.X.output.values[0][0]` for first row, first column. (e) Column order matches `KEEP` clause. (f) `format: json` is required in `with` block
30. **Alert rule tags for workflow data passing** — Since `inputs.*` is blank in alert-triggered workflows, encode key data in alert rule tags. Example: `tags: ["nova7", "FuelPressureException"]` → access in workflow as `event.rule.tags[1]`. Keep tags minimal (prefix + error_type) for reliable array index access

---

## Deployer Architecture & URL Derivation

### Mixin Pattern and Step Indexing

The deployer is composed of mixins (`SloMixin`, `AlertingMixin`, `DashboardMixin`, etc.), each contributing one or more numbered steps to `DeployProgress`. Steps are referenced by index via `self._step(n)`. Adding a new step at any index requires bumping all `_step()` calls in every file with a higher index.

**Current step index assignments (as of 2026-03-31):**
| Index | Step | File |
|-------|------|------|
| 0 | Connectivity check | `deployer.py` |
| 1 | Derive Elasticsearch URL | `deployer.py` |
| 2 | Derive OTLP endpoint | `deployer_otlp.py` |
| 3 | Clean up old artifacts | `deployer.py` |
| 4 | Configure platform settings | `deployer_platform.py` |
| 5 | Generate APM rollup data | `deployer_apm.py` |
| 6 | Deploy workflows | `deployer_workflows.py` |
| 7 | Index knowledge base | `deployer_kb.py` |
| 8 | Deploy AI agent tools | `deployer_agent.py` |
| 9 | Create AI agent | `deployer_agent.py` |
| 10 | Create significant events | `deployer_events.py` |
| 11 | Create data views | `deployer_views.py` |
| 12 | Import executive dashboard | `deployer_dashboard.py` |
| 13 | Create alert rules | `deployer_alerting.py` |
| 14 | Enable APM anomaly detection | `deployer_apm.py` |
| 15 | Create SLOs | `deployer_slos.py` |

**Gotcha:** When inserting a step, update `_step(n)` calls in every mixin file that uses a higher index. There are typically 9+ files involved.

### Elasticsearch URL Derivation (Three-Strategy Hierarchy)

The function `_derive_elastic_url(kibana_url, api_key, explicit="")` in `app/main.py` implements this priority order:

1. **Explicit** — if the user supplied `ELASTIC_URL` env var or typed one in the UI, use it directly
2. **Fleet API** — `GET {kibana_url}/api/fleet/outputs` (requires auth header `ApiKey {api_key}`). Find the item with `"name": "default"` and use its `hosts[0]`
3. **URL substitution** — replace `.kb.` with `.es.` in the Kibana URL (Elastic Cloud pattern)

```python
def _derive_elastic_url(kibana_url: str, api_key: str, explicit: str = "") -> str:
    if explicit:
        return explicit.strip().rstrip("/")
    try:
        resp = client.get(f"{kibana_url}/api/fleet/outputs",
            headers={"Authorization": f"ApiKey {api_key}", "kbn-xsrf": "true"})
        if resp.status_code == 200:
            for item in resp.json().get("items", []):
                if item.get("name") == "default":
                    hosts = item.get("hosts", [])
                    if hosts:
                        return hosts[0].rstrip("/")
    except Exception as exc:
        logger.warning("Fleet API ES URL derivation failed: %s", exc)
    if ".kb." in kibana_url:
        return kibana_url.replace(".kb.", ".es.")
    return ""
```

**CRITICAL:** Use `logger.warning()` in the except block — `except Exception: pass` silently swallows Fleet API failures and makes derivation failures impossible to diagnose.

### OTLP Endpoint Derivation

`_derive_otlp_endpoint(client)` in `elastic_config/deployer.py` tries three strategies in order:

1. **`OTLP_ENDPOINT` env var** — if set explicitly, used as-is; derivation is skipped entirely
2. **Cluster settings API** — `GET {elastic_url}/_cluster/settings?include_defaults=true`, extracts `persistent.cluster.metadata.display_name` (falls back to `defaults.cluster.name`), converts `"Name (ID)"` → `"Name-ID"`, combines with the cloud suffix from the ES URL → `https://{alias}.ingest.{suffix}:443`
3. **URL substitution** — replace `.es.` with `.ingest.` in the ES URL, append `:443` if missing

Each candidate from strategies 2 and 3 is verified with `_verify_otlp_candidate(client, url)`:
```
POST {url}/v1/logs  body: {"resourceLogs": []}
```
Returns the first candidate that responds with HTTP 200. If both probes fail, returns `None`.

**Fallback in auto-deploy** — If the deployer returns an empty `otlp_endpoint` (probes failed),
`app/main.py` uses a pre-computed fallback derived from `ELASTIC_URL` at startup (`.es.` → `.ingest.:443`) without a live probe. Priority chain in auto-deploy:
```
OTLP_ENDPOINT env var  →  deployer result (probe-verified)  →  pre-computed fallback  →  "" (silent drop)
```

**CRITICAL:** `OTLP_ENDPOINT` previously defaulted to `"http://otel-collector:4318"` in `config.py`,
which was always truthy and blocked the deployer-derived endpoint. It now defaults to `""`.

**CRITICAL:** `OTLPClient._send()` silently returns if `self.endpoint` is empty (no exception, no log).
This prevents "Request URL is missing an 'http://' or 'https://' protocol" errors when the endpoint
hasn't been configured yet. Telemetry is dropped until `reconfigure()` is called.

### Auto-Deploy from Environment Variables

If `KIBANA_URL` and `ELASTIC_API_KEY` are set in `.env` (or as container env vars), the app
automatically deploys `ACTIVE_SCENARIO` on startup without requiring UI interaction.

**Supported env vars:**

| Variable | Required | Notes |
| -------- | -------- | ----- |
| `KIBANA_URL` | Yes | Triggers auto-deploy when set alongside `ELASTIC_API_KEY` |
| `ELASTIC_API_KEY` | Yes | Used for all Elastic API calls |
| `ACTIVE_SCENARIO` | No | Defaults to `space` |
| `ELASTIC_URL` | No | Auto-derived from `KIBANA_URL` (`.kb.` → `.es.`) if not set |
| `OTLP_ENDPOINT` | No | Auto-derived from ES URL if not set (see OTLP derivation above) |
| `OPERATOR_EMAIL` | No | Pre-fills the email field in the Chaos UI (fallback when `X-Forwarded-User` header is absent) |

**Progress visibility:**

- Each deployer step is logged at INFO level: `[auto-deploy:{scenario}] {step} — {status}`
- The selector UI (`/`) detects an in-progress auto-deploy via `GET /api/setup/auto-deploy`
  and automatically shows the progress panel without requiring the user to click Launch
- The `/api/setup/auto-deploy` endpoint returns `{"in_progress": bool, "deployment_id": str|null}`

**start.sh:** Uses `command -v python3` to resolve the active venv's Python rather than
`/usr/bin/python3`. Do not use `sudo` — it resets PATH and loses the venv. If root is needed
for port binding, use `sudo env PATH="$PATH" "$PYTHON" -m uvicorn ...` instead.

### User Email Detection (`/api/user/info`)

Priority order for auto-populating the operator email in the Chaos UI:

1. `X-Forwarded-User` request header (set by reverse proxies — Okta SSO, OAuth2 Proxy, etc.)
2. `OPERATOR_EMAIL` environment variable (useful in Instruqt, k8s, or any environment without SSO)

```python
email = request.headers.get("X-Forwarded-User", "") or os.environ.get("OPERATOR_EMAIL", "")
```

### Kibana Authentication Verification

**CRITICAL:** Do NOT use `GET /api/status` to test Kibana connectivity with an API key — it is unauthenticated and will return 200 even with a wrong or missing API key.

Use an auth-required endpoint instead:
```
GET {kibana_url}/api/spaces/space
headers: {"Authorization": "ApiKey {api_key}", "kbn-xsrf": "true"}
```
Returns 200 with valid credentials, 401/403 with bad API key.

### Test Connection Endpoint Pattern

`POST /api/setup/test-connection` tests each component independently and returns per-component results:

```json
{
  "kibana_ok": true,
  "kibana_error": null,
  "es_ok": true,
  "es_error": null,
  "elastic_url": "https://...",
  "otlp_ok": false,
  "otlp_error": "HTTP 401",
  "otlp_endpoint": "https://..."
}
```

The UI uses `kibana_ok`, `es_ok`, `otlp_ok` to set ✅/❌ indicators per field. A combined status string reads `Kibana: OK | Elasticsearch: OK | OTLP: Not verified`. Message color is red if ANY component failed (`anyFail = kibana_ok === false || es_ok === false || otlp_ok === false`).

### UI Checkmark Clearing Rules

The `clearConnChecks()` JS function must be wired to `oninput` on ALL four credential fields:
- Kibana URL (`oninput="deriveUrls(); clearConnChecks()"`)
- API Key (`oninput="clearConnChecks()"`)
- Elasticsearch URL (`oninput="clearConnChecks()"`)
- OTLP Endpoint (`oninput="clearConnChecks()"`)

Missing `oninput` on the advanced fields (ES URL, OTLP) causes stale ✅/❌ to persist after the user edits those fields.

### Field Repopulation on Test

When the test returns a derived `elastic_url` or `otlp_endpoint`, repopulate empty fields OR fields the user hasn't manually edited:
```javascript
const esField = document.getElementById('es-url');
if (data.elastic_url && (!esField.value || !esField.dataset.manual))
    esField.value = data.elastic_url;
```
Use `dataset.manual = "1"` (set on manual edits) to distinguish user-typed values from auto-derived ones. Without the `!esField.value` guard, an empty field never repopulates if it previously had `dataset.manual` set.
