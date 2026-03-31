"""WorkflowsMixin — workflow deploy and cleanup methods."""

from __future__ import annotations

import json
import logging
import os

import httpx

from elastic_config.deployer_base import _kibana_headers, ProgressCallback

logger = logging.getLogger("deployer")


class WorkflowsMixin:

    def _deploy_workflows(self, client: httpx.Client, notify: ProgressCallback):
        step = self._step(6)
        step.status = "running"
        notify(self.progress)

        # Clean existing workflows for this namespace
        self._cleanup_workflows(client)

        # Generate templated workflows
        workflow_yamls = self._generate_workflow_yamls()
        step.items_total = len(workflow_yamls)

        for name, yaml_content in workflow_yamls.items():
            resp = self._wf_create(client, yaml_content)
            if resp.status_code < 300:
                # Extract workflow ID from response
                try:
                    wf_data = resp.json()
                    wf_id = wf_data.get("id", "")
                    if wf_id:
                        self._workflow_ids[name] = wf_id
                except Exception:
                    pass
                step.items_done += 1
                step.detail = f"Deployed: {name}"
            else:
                step.detail = f"Failed: {name} (HTTP {resp.status_code})"
                logger.warning("Workflow %s deploy failed: %s", name, resp.text[:200])
            notify(self.progress)

        step.status = "ok" if step.items_done > 0 else "failed"
        notify(self.progress)

    def _generate_workflow_yamls(self) -> dict[str, str]:
        """Generate workflow YAMLs templated for this scenario."""
        ns = self.ns
        scenario_name = self.scenario.scenario_name
        agent_cfg = self.scenario.agent_config
        agent_id = agent_cfg.get("id", f"{ns}-analyst")

        # Read template YAMLs from elastic_config/workflows/ and substitute
        wf_dir = os.path.join(os.path.dirname(__file__), "workflows")

        workflows = {}
        if os.path.isdir(wf_dir):
            for fname in sorted(os.listdir(wf_dir)):
                if not fname.endswith(".yaml"):
                    continue
                with open(os.path.join(wf_dir, fname)) as f:
                    yaml_content = f.read()
                # Template substitutions
                yaml_content = yaml_content.replace("__SCENARIO_NAME__", scenario_name)
                yaml_content = yaml_content.replace("__AGENT_ID__", agent_id)
                yaml_content = yaml_content.replace("__NS__", ns)
                key = fname.replace(".yaml", "")
                workflows[key] = yaml_content
        else:
            # Generate minimal workflows inline
            workflows = self._generate_inline_workflows(scenario_name, ns, agent_id)

        return workflows

    def _generate_inline_workflows(
        self, scenario_name: str, ns: str, agent_id: str,
    ) -> dict[str, str]:
        """Fallback: generate minimal workflow YAMLs if templates not found."""
        notification = f"""version: "1"
name: {scenario_name} Significant Event Notification
description: >
  Notify operations team when a significant event is detected.
  Triggered by alert rules — runs AI root cause analysis.

triggers:
  - type: alert

steps:
  - name: count_errors
    type: elasticsearch.esql.query
    with:
      query: >
        FROM logs.otel,logs.otel.*
        | WHERE @timestamp > NOW() - 15 MINUTES AND severity_text == "ERROR"
        | STATS total_errors = COUNT(*)
      format: json

  - name: run_rca
    type: ai.agent
    agent-id: {agent_id}
    create-conversation: true
    with:
      message: >
        Significant event detected: {{{{ event.rule.name }}}}.
        Error type: {{{{ event.rule.tags[1] }}}}.
        Total errors in last 15 minutes: {{{{ steps.count_errors.output.values[0][0] }}}}.
        Perform a root cause analysis only. Do NOT execute any remediation actions.

  - name: create_case
    type: kibana.createCaseDefaultSpace
    with:
      title: "{scenario_name} RCA: {{{{ event.rule.name }}}}"
      description: |
        [View Conversation]({{{{ kibanaUrl }}}}/app/agent_builder/conversations/{{{{ steps.run_rca.output.conversation_id }}}})

        {{{{ steps.run_rca.output.message }}}}
      tags:
        - "{ns}"
        - "{{{{ event.rule.tags[1] }}}}"
      severity: "high"
      owner: "observability"
      settings:
        syncAlerts: false
      connector:
        id: "none"
        name: "none"
        type: ".none"
        fields: null

  - name: audit_log
    type: elasticsearch.index
    with:
      index: "{ns}-significant-events-audit"
      document:
        rule_name: "{{{{ event.rule.name }}}}"
        error_type: "{{{{ event.rule.tags[1] }}}}"
        total_errors: "{{{{ steps.count_errors.output.values[0][0] }}}}"
        rca_case_created: true
      refresh: wait_for
"""

        remediation = f"""version: "1"
name: {scenario_name} Remediation Action
description: >
  Execute remediation actions. Queues a remediation command to an ES
  index for the backend poller to process.

triggers:
  - type: manual

inputs:
  - name: error_type
    type: string
    required: true
  - name: channel
    type: number
    required: true
  - name: action_type
    type: string
    required: true
  - name: target_service
    type: string
    default: ""
  - name: justification
    type: string
    required: true
  - name: dry_run
    type: boolean
    default: true

steps:
  - name: queue_remediation
    type: elasticsearch.index
    with:
      index: "{ns}-remediation-queue"
      document:
        channel: "{{{{ inputs.channel }}}}"
        action_type: "{{{{ inputs.action_type }}}}"
        target_service: "{{{{ inputs.target_service }}}}"
        justification: "{{{{ inputs.justification }}}}"
        dry_run: "{{{{ inputs.dry_run }}}}"
        error_type: "{{{{ inputs.error_type }}}}"
        namespace: "{ns}"
        status: "pending"
        mission_id: "{scenario_name}"
      refresh: wait_for

  - name: log_queued
    type: console
    with:
      message: "Remediation QUEUED for channel {{{{ inputs.channel }}}}. Backend will process shortly."

  - name: find_open_case
    type: kibana.request
    with:
      method: GET
      path: "/api/cases/_find?tags={ns}&tags={{{{ inputs.error_type }}}}&status=open&sortField=createdAt&sortOrder=desc&perPage=1&owner=observability"

  - name: close_case
    type: if
    condition: "steps.find_open_case.output.cases.0.id : *"
    steps:
      - name: update_case_closed
        type: kibana.updateCase
        with:
          cases:
            - id: "{{{{ steps.find_open_case.output.cases[0].id }}}}"
              version: "{{{{ steps.find_open_case.output.cases[0].version }}}}"
              status: "closed"

  - name: log_case_closed
    type: console
    with:
      message: "Case closed for {{{{ inputs.error_type }}}} (channel {{{{ inputs.channel }}}})."

  - name: audit_log
    type: elasticsearch.index
    with:
      index: "{ns}-remediation-audit"
      document:
        channel: "{{{{ inputs.channel }}}}"
        action_type: "{{{{ inputs.action_type }}}}"
        target_service: "{{{{ inputs.target_service }}}}"
        justification: "{{{{ inputs.justification }}}}"
        dry_run: "{{{{ inputs.dry_run }}}}"
        status: "resolved"
        case_closed: true
        mission_id: "{scenario_name}"
      refresh: wait_for
"""

        escalation = f"""version: "1"
name: {scenario_name} Escalation and Hold Management
description: >
  Manage escalation of critical anomalies and operational hold decisions.

triggers:
  - type: manual

inputs:
  - name: action
    type: string
    required: true
  - name: channel
    type: number
    default: 0
  - name: severity
    type: string
    default: "WARNING"
  - name: justification
    type: string
    required: true
  - name: hold_id
    type: string
    default: ""
  - name: investigation_summary
    type: string
    default: ""

steps:
  - name: route_escalate
    type: if
    condition: "inputs.action : escalate"
    steps:
      - name: escalate_log
        type: console
        with:
          message: >
            ESCALATION - Channel {{{{ inputs.channel }}}}.
            Severity: {{{{ inputs.severity }}}}.
            Justification: {{{{ inputs.justification }}}}.

      - name: escalate_audit
        type: elasticsearch.index
        with:
          index: "{ns}-escalation-audit"
          document:
            action: "escalate"
            channel: "{{{{ inputs.channel }}}}"
            severity: "{{{{ inputs.severity }}}}"
            justification: "{{{{ inputs.justification }}}}"
          refresh: wait_for

  - name: route_hold
    type: if
    condition: "inputs.action : request_hold"
    steps:
      - name: hold_safety_check
        type: ai.agent
        agent-id: {agent_id}
        with:
          message: >
            Hold requested for channel {{{{ inputs.channel }}}}
            (severity: {{{{ inputs.severity }}}}). Reason: {{{{ inputs.justification }}}}.
            Perform a rapid safety assessment.

      - name: hold_audit
        type: elasticsearch.index
        with:
          index: "{ns}-escalation-audit"
          document:
            action: "request_hold"
            channel: "{{{{ inputs.channel }}}}"
            severity: "{{{{ inputs.severity }}}}"
            status: "hold_active"
          refresh: wait_for
"""

        return {
            "significant_event_notification": notification,
            "remediation_action": remediation,
            "escalation_hold": escalation,
        }

    def _wf_create(self, client: httpx.Client, yaml_content: str) -> httpx.Response:
        """POST a single workflow. Tries new path first, falls back to old."""
        body = json.dumps({"yaml": yaml_content})
        resp = client.post(
            f"{self.kibana_url}/api/workflows/workflow",
            headers=_kibana_headers(self.api_key),
            content=body,
        )
        if resp.status_code in (404, 405):
            resp = client.post(
                f"{self.kibana_url}/api/workflows",
                headers=_kibana_headers(self.api_key),
                content=body,
            )
        return resp

    def _wf_search(self, client: httpx.Client) -> list:
        """Return all workflow items. Tries new GET path first, falls back to POST search."""
        resp = client.get(
            f"{self.kibana_url}/api/workflows",
            headers=_kibana_headers(self.api_key),
        )
        if resp.status_code in (404, 405):
            resp = client.post(
                f"{self.kibana_url}/api/workflows/search",
                headers=_kibana_headers(self.api_key),
                json={"page": 1, "size": 100},
            )
        if resp.status_code >= 300:
            return []
        data = resp.json()
        return data if isinstance(data, list) else data.get("results", data.get("items", []))

    def _wf_delete(self, client: httpx.Client, wf_id: str) -> httpx.Response:
        """Delete a workflow by ID. Tries new path first, falls back to old."""
        resp = client.delete(
            f"{self.kibana_url}/api/workflows/workflow/{wf_id}",
            headers=_kibana_headers(self.api_key),
        )
        if resp.status_code in (404, 405):
            resp = client.delete(
                f"{self.kibana_url}/api/workflows/{wf_id}",
                headers=_kibana_headers(self.api_key),
            )
        return resp

    def _cleanup_workflows(self, client: httpx.Client) -> int:
        """Delete workflows matching this scenario's name."""
        deleted = 0
        try:
            items = self._wf_search(client)
            scenario_name = self.scenario.scenario_name
            for item in items:
                if scenario_name in item.get("name", "") or f"{self.ns}-" in item.get("name", "").lower():
                    wf_id = item.get("id", "")
                    if wf_id:
                        r = self._wf_delete(client, wf_id)
                        if r.status_code < 300:
                            deleted += 1
        except Exception:
            pass
        return deleted
