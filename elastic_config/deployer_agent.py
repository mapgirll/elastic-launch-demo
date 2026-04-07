"""AgentMixin — tools, agent, and system prompt methods."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from elastic_config.deployer_base import _kibana_headers, ProgressCallback

logger = logging.getLogger("deployer")


class AgentMixin:

    def _deploy_tools(self, client: httpx.Client, notify: ProgressCallback):
        step = self._step(8)
        step.status = "running"
        notify(self.progress)

        # Use scenario-provided tool definitions + deployer-added workflow tools
        tools = list(self.scenario.tool_definitions)

        # Add workflow tools (need workflow IDs from deployment)
        for name_frag, wf_id in self._workflow_ids.items():
            if "remediation" in name_frag:
                tools.append({
                    "id": "remediation_action",
                    "type": "workflow",
                    "description": (
                        "Execute remediation actions for anomalies. Triggers the "
                        "Remediation Action workflow to resolve faults."
                    ),
                    "configuration": {"workflow_id": wf_id},
                })
            elif "escalation" in name_frag:
                tools.append({
                    "id": "escalation_action",
                    "type": "workflow",
                    "description": (
                        "Escalate critical anomalies and manage operational hold decisions."
                    ),
                    "configuration": {"workflow_id": wf_id},
                })

        step.items_total = len(tools)

        for tool_def in tools:
            tool_id = tool_def["id"]
            # Delete first, then create
            client.delete(
                f"{self.kibana_url}/api/agent_builder/tools/{tool_id}",
                headers=_kibana_headers(self.api_key),
            )
            resp = client.post(
                f"{self.kibana_url}/api/agent_builder/tools",
                headers=_kibana_headers(self.api_key),
                json=tool_def,
            )
            if resp.status_code < 300:
                step.items_done += 1
                step.detail = f"Created: {tool_id}"
                self._created_tool_ids.append(tool_id)
            else:
                step.detail = f"Failed: {tool_id} (HTTP {resp.status_code})"
                logger.warning("Tool %s failed: %s", tool_id, resp.text[:200])
            notify(self.progress)

        step.status = "ok" if step.items_done > 0 else "failed"
        notify(self.progress)

    def _deploy_agent(self, client: httpx.Client, notify: ProgressCallback):
        step = self._step(9)
        step.status = "running"
        notify(self.progress)

        agent_cfg = self.scenario.agent_config
        agent_id = agent_cfg.get("id", f"{self.ns}-analyst")

        # Build full system prompt from scenario properties
        system_prompt = self._generate_system_prompt(agent_cfg)

        # Use only tools that were actually created successfully (Bug 4+5 fix)
        tool_ids = list(self._created_tool_ids)
        tool_ids.append("platform.core.cases")

        agent_body = {
            "id": agent_id,
            "name": agent_cfg.get("name", f"{self.scenario.scenario_name} Analyst"),
            "description": agent_cfg.get(
                "description",
                f"AI-powered analyst for {self.scenario.scenario_name}.",
            ),
            "configuration": {
                "instructions": system_prompt,
                "tools": [{"tool_ids": tool_ids}],
            },
        }

        # DELETE + POST for reliable update
        client.delete(
            f"{self.kibana_url}/api/agent_builder/agents/{agent_id}",
            headers=_kibana_headers(self.api_key),
        )
        resp = client.post(
            f"{self.kibana_url}/api/agent_builder/agents",
            headers=_kibana_headers(self.api_key),
            json=agent_body,
        )

        if resp.status_code < 300:
            step.status = "ok"
            step.detail = f"Agent {agent_id} created"
        else:
            step.status = "failed"
            step.detail = f"HTTP {resp.status_code}: {resp.text[:200]}"
        notify(self.progress)

    def _generate_system_prompt(self, agent_cfg: dict[str, Any]) -> str:
        """Build a comprehensive system prompt from scenario properties."""
        scenario = self.scenario
        svc_list = "\n".join(
            f"- {name} ({cfg['cloud_provider'].upper()}, {cfg['subsystem']})"
            for name, cfg in scenario.services.items()
        )
        svc_names = ", ".join(sorted(scenario.services.keys()))

        # Use the scenario's identity text as opening, then add comprehensive guide
        base_prompt = agent_cfg.get("system_prompt", "")

        # Auto-generate a comprehensive prompt
        subsystems = sorted(set(
            cfg["subsystem"] for cfg in scenario.services.values()
        ))

        # Use scenario-provided identity if available, otherwise generic
        identity = base_prompt if base_prompt else (
            f"You are the {scenario.scenario_name} Operations Analyst, "
            f"an expert AI agent embedded in the Elastic observability platform."
        )

        # Scenario-specific assessment tool name
        assessment_tool = agent_cfg.get(
            "assessment_tool_name",
            scenario.assessment_tool_config.get("id", "operational_assessment"),
        )

        return f"""{identity}

## Mission Context
- **Scenario**: {scenario.scenario_name}
- **Namespace**: {scenario.namespace}
- **Kibana URL**: {self.kibana_display_url}
- **Subsystems**: {', '.join(subsystems)}
- **Services**:
{svc_list}
- **Fault Channels**: 20 distinct anomaly channels covering all subsystems
- **Telemetry Source**: OpenTelemetry -> Elasticsearch (logs)

## CRITICAL: Field Names
- Log message field is `body.text` — NEVER use `body` alone (causes "Unknown column [body]")
- NEVER use `message` — this field DOES NOT EXIST. The correct field is `body.text`
- Service name field is `service.name`
- Always query FROM logs.otel,logs.otel.* (includes sub-streams)
- Use LIKE or KQL() for text matching — NEVER use MATCH()

## Tool Selection Guide
1. **Known error type** → `search_error_logs` — parameterized, correct fields
2. **Specific service** → `search_service_logs` — parameterized, correct fields
3. **General awareness** → `browse_recent_errors` or `search_subsystem_health`
4. **Historical patterns** → `search_known_anomalies` — knowledge base lookup
5. **Cascade analysis** → `trace_anomaly_propagation` — cross-service correlation
6. **Operational readiness** → `{assessment_tool}` — overall system health evaluation
Do NOT write custom ES|QL queries. Use the parameterized tools.

## Root Cause Analysis Methodology
1. **Identify the Event**: Determine which channel(s) triggered and the error signature
2. **Scope the Blast Radius**: Identify affected and cascade services
3. **Temporal Correlation**: Find first occurrence, correlate with preceding events
4. **Cross-Cloud Tracing**: Trace propagation across AWS, GCP, and Azure
5. **Subsystem Impact**: Evaluate if fault is isolated or propagating
6. **Known Pattern Matching**: Check knowledge base for similar anomalies
7. **Severity Classification**: ADVISORY, CAUTION, WARNING, or CRITICAL
8. **Remediation**: When the user asks you to remediate, look up the recommended remediation_action from the knowledge base entry for that channel. Use the remediation_action tool with the appropriate action_type and the affected channel number. IMPORTANT: if you fetched a specific case earlier in the conversation (e.g. via platform.core.cases), always pass that case's `id` as the `case_id` parameter so the workflow closes the correct case — never rely on the tag search to find it. Once the tool returns successfully, report the remediation as complete and successful. Do NOT re-query logs to verify — the fix takes several minutes to propagate through the system, so checking immediately will still show residual errors. Do NOT execute remediation unless the user explicitly asks you to.

## Available Services
{svc_names}

## Response Format
1. **Summary** — One-sentence description
2. **Affected Systems** — Impacted services and subsystems
3. **Root Cause** — Underlying cause determination
4. **Evidence** — Specific log entries, timestamps, field values
5. **Cascade Risk** — Propagation assessment
6. **Recommendation** — Prioritized remediation steps
7. **Confidence** — HIGH/MEDIUM/LOW with reasoning"""

    def _cleanup_agent(self, client: httpx.Client):
        """Delete agent and custom tools."""
        agent_id = self.scenario.agent_config.get("id", f"{self.ns}-analyst")
        client.delete(
            f"{self.kibana_url}/api/agent_builder/agents/{agent_id}",
            headers=_kibana_headers(self.api_key),
        )
        # Collect tool IDs from scenario's tool_definitions + workflow tools
        tool_ids = [t["id"] for t in self.scenario.tool_definitions]
        tool_ids.extend(["remediation_action", "escalation_action"])
        for tool_id in tool_ids:
            client.delete(
                f"{self.kibana_url}/api/agent_builder/tools/{tool_id}",
                headers=_kibana_headers(self.api_key),
            )
