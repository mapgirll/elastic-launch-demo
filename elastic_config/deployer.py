"""Scenario deployer — replaces setup-all.sh and sub-scripts with Python.

Deploys a scenario's Elastic config (workflows, agent, tools, KB, significant
events, dashboard, alerting) to an Elastic Cloud environment.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from scenarios.base import BaseScenario

from elastic_config.deployer_base import (
    DeployStep,
    DeployProgress,
    ProgressCallback,
    _kibana_headers,
    _es_headers,
)
from elastic_config.deployer_otlp import OtlpMixin
from elastic_config.deployer_platform import PlatformMixin
from elastic_config.deployer_apm import ApmMixin
from elastic_config.deployer_slos import SloMixin
from elastic_config.deployer_workflows import WorkflowsMixin
from elastic_config.deployer_kb import KbMixin
from elastic_config.deployer_agent import AgentMixin
from elastic_config.deployer_events import EventsMixin
from elastic_config.deployer_views import DataViewsMixin
from elastic_config.deployer_dashboard import DashboardMixin
from elastic_config.deployer_alerting import AlertingMixin

logger = logging.getLogger("deployer")


# ── Main deployer class ────────────────────────────────────────────────────

class ScenarioDeployer(
    OtlpMixin,
    PlatformMixin,
    ApmMixin,
    SloMixin,
    WorkflowsMixin,
    KbMixin,
    AgentMixin,
    EventsMixin,
    DataViewsMixin,
    DashboardMixin,
    AlertingMixin,
):
    """Deploys a scenario's full Elastic configuration."""

    def __init__(
        self,
        scenario: BaseScenario,
        elastic_url: str,
        kibana_url: str,
        api_key: str,
    ):
        self.scenario = scenario
        self.elastic_url = elastic_url.strip().rstrip("/")
        self.kibana_url = kibana_url.strip().rstrip("/")
        self.api_key = api_key.strip()
        self.ns = scenario.namespace
        self.progress = DeployProgress()
        self._workflow_ids: dict[str, str] = {}  # name fragment -> workflow ID
        self._created_tool_ids: list[str] = []   # tools that were actually created

    # ── Public API ─────────────────────────────────────────────────────

    def deploy_all(self, callback: ProgressCallback | None = None) -> DeployProgress:
        """Run the full deployment pipeline.  Returns progress summary."""
        self.progress = DeployProgress(steps=[
            DeployStep("Connectivity check"),           # 0
            DeployStep("Derive Elasticsearch URL"),     # 1
            DeployStep("Derive OTLP endpoint"),         # 2
            DeployStep("Clean up old artifacts"),       # 3
            DeployStep("Configure platform settings"),  # 4
            DeployStep("Generate APM rollup data"),     # 5
            DeployStep("Deploy workflows", items_total=5),  # 6
            DeployStep("Index knowledge base", items_total=20),  # 7
            DeployStep("Deploy AI agent tools", items_total=7),  # 8
            DeployStep("Create AI agent"),              # 9
            DeployStep("Create significant events", items_total=20),  # 10
            DeployStep("Create data views", items_total=5),  # 11
            DeployStep("Import executive dashboard"),   # 12
            DeployStep("Create alert rules", items_total=20),  # 13
            DeployStep("Enable APM anomaly detection"), # 14
            DeployStep("Create SLOs", items_total=3),  # 15
        ])
        _notify = callback or (lambda p: None)
        _notify(self.progress)

        try:
            with httpx.Client(timeout=60.0, verify=True) as client:
                self._check_connectivity(client, _notify)
                self._report_elastic_url_step(_notify)
                self._derive_otlp_step(client, _notify)
                self._cleanup_all_scenarios_step(client, _notify)
                self._configure_platform_settings(client, _notify)
                self._deploy_apm_rollup(client, _notify)
                self._deploy_workflows(client, _notify)
                self._deploy_knowledge_base(client, _notify)
                self._deploy_tools(client, _notify)
                self._deploy_agent(client, _notify)
                self._deploy_significant_events(client, _notify)
                self._deploy_data_views(client, _notify)
                self._deploy_dashboard(client, _notify)
                self._deploy_alerting(client, _notify)
                self._deploy_apm_anomaly_detection(client, _notify)
                self._deploy_slos(client, _notify)
        except Exception as exc:
            self.progress.error = str(exc)
            logger.exception("Deployment failed")

        self.progress.finished = True
        _notify(self.progress)
        return self.progress

    def check_connection(self) -> dict[str, Any]:
        """Quick connectivity test — returns {ok, cluster_name, error}."""
        try:
            with httpx.Client(timeout=15.0, verify=True) as client:
                resp = client.get(
                    f"{self.elastic_url}/",
                    headers=_es_headers(self.api_key),
                )
                if resp.status_code < 300:
                    data = resp.json()
                    return {"ok": True, "cluster_name": data.get("cluster_name", "unknown")}
                return {"ok": False, "error": f"HTTP {resp.status_code}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def detect_existing(self) -> dict[str, Any]:
        """Check if this scenario is already deployed."""
        found = {}
        try:
            with httpx.Client(timeout=15.0, verify=True) as client:
                # Check KB index
                resp = client.head(
                    f"{self.elastic_url}/{self.ns}-knowledge-base",
                    headers=_es_headers(self.api_key),
                )
                found["knowledge_base"] = resp.status_code == 200

                # Check dashboard
                resp = client.post(
                    f"{self.kibana_url}/api/saved_objects/_export",
                    headers=_kibana_headers(self.api_key),
                    json={"objects": [{"type": "dashboard", "id": f"{self.ns}-exec-dashboard"}],
                           "includeReferencesDeep": False},
                )
                found["dashboard"] = resp.status_code < 300

                # Check alert rules
                resp = client.get(
                    f"{self.kibana_url}/api/alerting/rules/_find?per_page=1&filter=alert.attributes.tags:{self.ns}",
                    headers=_kibana_headers(self.api_key),
                )
                if resp.status_code < 300:
                    data = resp.json()
                    found["alert_rules"] = data.get("total", 0)
                else:
                    found["alert_rules"] = 0
        except Exception as exc:
            found["error"] = str(exc)

        found["deployed"] = found.get("knowledge_base", False) or found.get("dashboard", False)
        return found

    def teardown(self) -> dict[str, Any]:
        """Remove scenario-specific resources from Elastic."""
        results = {}
        with httpx.Client(timeout=30.0, verify=True) as client:
            # Delete KB index
            resp = client.delete(
                f"{self.elastic_url}/{self.ns}-knowledge-base",
                headers=_es_headers(self.api_key),
            )
            results["knowledge_base"] = resp.status_code < 300

            # Delete audit indices and remediation queue
            for suffix in ["significant-events-audit", "remediation-audit", "escalation-audit", "remediation-queue", "daily-report-audit"]:
                client.delete(
                    f"{self.elastic_url}/{self.ns}-{suffix}",
                    headers=_es_headers(self.api_key),
                )

            # Delete workflows
            results["workflows_deleted"] = self._cleanup_workflows(client)

            # Delete alert rules
            results["alerts_deleted"] = self._cleanup_alerts(client)

            # Delete agent + tools
            self._cleanup_agent(client)

            # Delete significant events
            self._cleanup_significant_events(client)

            # Delete dashboard
            resp = client.post(
                f"{self.kibana_url}/api/saved_objects/_bulk_delete",
                headers=_kibana_headers(self.api_key),
                json=[{"type": "dashboard", "id": f"{self.ns}-exec-dashboard"}],
            )
            results["dashboard"] = resp.status_code < 300

            # Delete APM ML jobs and datafeeds
            self._cleanup_apm_ml(client)
            results["apm_ml_cleaned"] = True

            # Delete SLOs
            results["slos_deleted"] = self._cleanup_slos(client)

            # Delete cases created by workflows
            results["cases_deleted"] = self._cleanup_cases(client, self.ns)

        return results

    def teardown_with_progress(self, callback: ProgressCallback | None = None) -> DeployProgress:
        """Remove scenario resources with staged progress reporting."""
        progress = DeployProgress(steps=[
            DeployStep("Stop generators"),              # 0
            DeployStep("Delete workflows"),             # 1
            DeployStep("Delete alert rules"),           # 2
            DeployStep("Delete significant events"),    # 3
            DeployStep("Delete AI agent & tools"),      # 4
            DeployStep("Delete knowledge base"),        # 5
            DeployStep("Delete audit indices"),         # 6
            DeployStep("Delete dashboard"),             # 7
            DeployStep("Delete APM ML jobs"),           # 8
            DeployStep("Delete APM rollup data"),       # 9
            DeployStep("Delete SLOs"),                  # 10
            DeployStep("Delete cases"),                 # 11
        ])
        _notify = callback or (lambda p: None)
        _notify(progress)

        # Step 0: generators — caller stops them before invoking this method
        progress.steps[0].status = "ok"
        progress.steps[0].detail = "Generators stopped"
        _notify(progress)

        try:
            with httpx.Client(timeout=30.0, verify=True) as client:
                # Step 1: Delete workflows
                step = progress.steps[1]
                step.status = "running"
                _notify(progress)
                try:
                    deleted = self._cleanup_workflows(client)
                    step.status = "ok"
                    step.detail = f"Deleted {deleted} workflows"
                except Exception as exc:
                    step.status = "failed"
                    step.detail = str(exc)
                _notify(progress)

                # Step 2: Delete alert rules
                step = progress.steps[2]
                step.status = "running"
                _notify(progress)
                try:
                    deleted = self._cleanup_alerts(client)
                    step.status = "ok"
                    step.detail = f"Deleted {deleted} alert rules"
                except Exception as exc:
                    step.status = "failed"
                    step.detail = str(exc)
                _notify(progress)

                # Step 3: Delete significant events
                step = progress.steps[3]
                step.status = "running"
                _notify(progress)
                try:
                    self._cleanup_significant_events(client)
                    step.status = "ok"
                    step.detail = "Stream queries removed"
                except Exception as exc:
                    step.status = "failed"
                    step.detail = str(exc)
                _notify(progress)

                # Step 4: Delete agent + tools
                step = progress.steps[4]
                step.status = "running"
                _notify(progress)
                try:
                    self._cleanup_agent(client)
                    step.status = "ok"
                    step.detail = "Agent and tools removed"
                except Exception as exc:
                    step.status = "failed"
                    step.detail = str(exc)
                _notify(progress)

                # Step 5: Delete knowledge base
                step = progress.steps[5]
                step.status = "running"
                _notify(progress)
                try:
                    resp = client.delete(
                        f"{self.elastic_url}/{self.ns}-knowledge-base",
                        headers=_es_headers(self.api_key),
                    )
                    step.status = "ok"
                    step.detail = "KB index deleted" if resp.status_code < 300 else "KB index not found"
                except Exception as exc:
                    step.status = "failed"
                    step.detail = str(exc)
                _notify(progress)

                # Step 6: Delete audit indices
                step = progress.steps[6]
                step.status = "running"
                _notify(progress)
                try:
                    deleted = 0
                    for suffix in ["significant-events-audit", "remediation-audit", "escalation-audit", "remediation-queue", "daily-report-audit"]:
                        r = client.delete(
                            f"{self.elastic_url}/{self.ns}-{suffix}",
                            headers=_es_headers(self.api_key),
                        )
                        if r.status_code < 300:
                            deleted += 1
                    step.status = "ok"
                    step.detail = f"Deleted {deleted} audit indices"
                except Exception as exc:
                    step.status = "failed"
                    step.detail = str(exc)
                _notify(progress)

                # Step 7: Delete dashboard
                step = progress.steps[7]
                step.status = "running"
                _notify(progress)
                try:
                    resp = client.post(
                        f"{self.kibana_url}/api/saved_objects/_bulk_delete",
                        headers=_kibana_headers(self.api_key),
                        json=[{"type": "dashboard", "id": f"{self.ns}-exec-dashboard"}],
                    )
                    step.status = "ok"
                    step.detail = "Dashboard deleted" if resp.status_code < 300 else "Dashboard not found"
                except Exception as exc:
                    step.status = "failed"
                    step.detail = str(exc)
                _notify(progress)

                # Step 8: Delete APM ML jobs
                step = progress.steps[8]
                step.status = "running"
                _notify(progress)
                try:
                    self._cleanup_apm_ml(client)
                    step.status = "ok"
                    step.detail = "APM ML jobs and datafeeds removed"
                except Exception as exc:
                    step.status = "failed"
                    step.detail = str(exc)
                _notify(progress)

                # Step 9: Delete APM rollup data streams
                step = progress.steps[9]
                step.status = "running"
                _notify(progress)
                try:
                    deleted_ds = 0
                    for ds_pattern in [
                        "metrics-transaction.1m.otel-*",
                        "metrics-service_destination.1m.otel-*",
                        "metrics-service_summary.1m.otel-*",
                    ]:
                        resp = client.get(
                            f"{self.elastic_url}/_data_stream/{ds_pattern}",
                            headers=_es_headers(self.api_key),
                        )
                        if resp.status_code < 300:
                            for ds in resp.json().get("data_streams", []):
                                ds_name = ds.get("name", "")
                                if ds_name:
                                    r = client.delete(
                                        f"{self.elastic_url}/_data_stream/{ds_name}",
                                        headers=_es_headers(self.api_key),
                                    )
                                    if r.status_code < 300:
                                        deleted_ds += 1
                    step.status = "ok"
                    step.detail = f"Deleted {deleted_ds} APM rollup data streams"
                except Exception as exc:
                    step.status = "failed"
                    step.detail = str(exc)
                _notify(progress)

                # Step 10: Delete SLOs
                step = progress.steps[10]
                step.status = "running"
                _notify(progress)
                try:
                    deleted_slos = self._cleanup_slos(client)
                    step.status = "ok"
                    step.detail = f"Deleted {deleted_slos} SLOs"
                except Exception as exc:
                    step.status = "failed"
                    step.detail = str(exc)
                _notify(progress)

                # Step 11: Delete cases
                step = progress.steps[11]
                step.status = "running"
                _notify(progress)
                try:
                    deleted_cases = self._cleanup_cases(client, self.ns)
                    step.status = "ok"
                    step.detail = f"Deleted {deleted_cases} cases"
                except Exception as exc:
                    step.status = "failed"
                    step.detail = str(exc)
                _notify(progress)

        except Exception as exc:
            progress.error = str(exc)
            logger.exception("Teardown failed")

        progress.finished = True
        _notify(progress)
        return progress

    # ── Cases cleanup ─────────────────────────────────────────────────

    def _cleanup_cases(self, client: httpx.Client, ns: str) -> int:
        """Delete all Kibana cases tagged with the given namespace."""
        deleted = 0
        page = 1
        while True:
            resp = client.get(
                f"{self.kibana_url}/api/cases/_find",
                headers=_kibana_headers(self.api_key),
                params={"tags": ns, "perPage": 100, "page": page},
            )
            if resp.status_code >= 300:
                break
            data = resp.json()
            cases = data.get("cases", [])
            if not cases:
                break
            ids = [c["id"] for c in cases if c.get("id")]
            if ids:
                r = client.delete(
                    f"{self.kibana_url}/api/cases",
                    headers=_kibana_headers(self.api_key),
                    params=[("ids", cid) for cid in ids],
                )
                if r.status_code < 300:
                    deleted += len(ids)
            if len(cases) < 100:
                break
            page += 1
        return deleted

    # ── Step implementations ───────────────────────────────────────────

    def _step(self, idx: int) -> DeployStep:
        return self.progress.steps[idx]

    def _check_connectivity(self, client: httpx.Client, notify: ProgressCallback):
        step = self._step(0)
        step.status = "running"
        notify(self.progress)

        # Elasticsearch
        resp = client.get(f"{self.elastic_url}/", headers=_es_headers(self.api_key))
        if resp.status_code >= 300:
            step.status = "failed"
            step.detail = f"Elasticsearch unreachable (HTTP {resp.status_code})"
            raise RuntimeError(step.detail)

        # Kibana
        resp = client.get(f"{self.kibana_url}/api/status", headers=_kibana_headers(self.api_key))
        if resp.status_code >= 300:
            step.detail = f"Kibana may be unavailable (HTTP {resp.status_code}), continuing..."
        else:
            step.detail = "ES + Kibana reachable"

        step.status = "ok"
        notify(self.progress)

    def _report_elastic_url_step(self, notify: ProgressCallback):
        step = self._step(1)
        step.status = "ok"
        step.detail = f"ES: {self.elastic_url}"
        notify(self.progress)

    # ── Cross-scenario cleanup ────────────────────────────────────────

    def _cleanup_all_scenarios_step(self, client: httpx.Client, notify: ProgressCallback):
        """Deploy step: clean up artifacts from ALL known scenarios."""
        step = self._step(3)
        step.status = "running"
        notify(self.progress)

        try:
            deleted = self._cleanup_all_scenarios(client)
            step.status = "ok"
            step.detail = f"Cleaned {deleted} artifacts"
        except Exception as exc:
            step.status = "ok"  # non-fatal — continue deploying
            step.detail = f"Partial cleanup: {exc}"
            logger.warning("Cleanup error (non-fatal): %s", exc)
        notify(self.progress)

    def _cleanup_all_scenarios(self, client: httpx.Client) -> int:
        """Delete artifacts for ALL known scenarios (not just the current one)."""
        from scenarios import get_scenario, list_scenarios

        all_scenarios = list_scenarios()
        deleted = 0

        # Collect all namespaces, scenario names, and agent IDs
        all_namespaces = []
        all_scenario_names = []
        all_agent_ids = []
        for s_meta in all_scenarios:
            try:
                s = get_scenario(s_meta["id"])
                all_namespaces.append(s.namespace)
                all_scenario_names.append(s.scenario_name)
                agent_id = s.agent_config.get("id", f"{s.namespace}-analyst")
                all_agent_ids.append(agent_id)
            except Exception:
                pass

        # Delete alert rules tagged with ANY known namespace
        for ns in all_namespaces:
            try:
                for page in range(1, 11):
                    resp = client.get(
                        f"{self.kibana_url}/api/alerting/rules/_find?per_page=100&page={page}&filter=alert.attributes.tags:{ns}",
                        headers=_kibana_headers(self.api_key),
                    )
                    if resp.status_code >= 300:
                        break
                    rules = resp.json().get("data", [])
                    if not rules:
                        break
                    for rule in rules:
                        rule_id = rule.get("id", "")
                        if rule_id:
                            client.delete(
                                f"{self.kibana_url}/api/alerting/rule/{rule_id}",
                                headers=_kibana_headers(self.api_key),
                            )
                            deleted += 1
            except Exception:
                pass

        # Delete stream queries with ANY known namespace prefix
        try:
            resp = client.get(
                f"{self.kibana_url}/api/streams/logs.otel/queries",
                headers=_kibana_headers(self.api_key),
            )
            if resp.status_code < 300:
                data = resp.json()
                queries = data if isinstance(data, list) else data.get("queries", [])
                for q in queries:
                    qid = q.get("id", "")
                    for ns in all_namespaces:
                        if qid.startswith(f"{ns}-se-"):
                            client.delete(
                                f"{self.kibana_url}/api/streams/logs.otel/queries/{qid}",
                                headers=_kibana_headers(self.api_key),
                            )
                            deleted += 1
                            break
        except Exception:
            pass

        # Delete workflows matching ANY known scenario name
        try:
            items = self._wf_search(client)
            for item in items:
                wf_name = item.get("name", "")
                for sn in all_scenario_names:
                    if sn in wf_name:
                        wf_id = item.get("id", "")
                        if wf_id:
                            self._wf_delete(client, wf_id)
                            deleted += 1
                        break
        except Exception:
            pass

        # Delete ALL known agent IDs
        for agent_id in all_agent_ids:
            try:
                r = client.delete(
                    f"{self.kibana_url}/api/agent_builder/agents/{agent_id}",
                    headers=_kibana_headers(self.api_key),
                )
                if r.status_code < 300:
                    deleted += 1
            except Exception:
                pass

        # Delete ALL known tool IDs (shared + scenario-specific)
        all_tool_ids = {
            "search_error_logs", "search_subsystem_health", "search_service_logs",
            "search_known_anomalies", "trace_anomaly_propagation",
            "browse_recent_errors", "remediation_action", "escalation_action",
        }
        # Add each scenario's assessment tool ID
        for s_meta in all_scenarios:
            try:
                s = get_scenario(s_meta["id"])
                all_tool_ids.add(s.assessment_tool_config["id"])
            except Exception:
                pass
        for tool_id in all_tool_ids:
            try:
                client.delete(
                    f"{self.kibana_url}/api/agent_builder/tools/{tool_id}",
                    headers=_kibana_headers(self.api_key),
                )
            except Exception:
                pass

        # Delete ALL known dashboards
        for ns in all_namespaces:
            try:
                r = client.post(
                    f"{self.kibana_url}/api/saved_objects/_bulk_delete",
                    headers=_kibana_headers(self.api_key),
                    json=[{"type": "dashboard", "id": f"{ns}-exec-dashboard"}],
                )
                if r.status_code < 300:
                    deleted += 1
            except Exception:
                pass

        # Delete ALL known KB indices and audit indices
        for ns in all_namespaces:
            for suffix in [
                "knowledge-base",
                "significant-events-audit",
                "remediation-audit",
                "escalation-audit",
                "remediation-queue",
                "daily-report-audit",
            ]:
                try:
                    r = client.delete(
                        f"{self.elastic_url}/{ns}-{suffix}",
                        headers=_es_headers(self.api_key),
                    )
                    if r.status_code < 300:
                        deleted += 1
                except Exception:
                    pass

        # Reset OTLP metric data streams so TSDB mappings are recreated fresh.
        # This ensures new metric fields (added to generators after the data stream
        # was first created) are included in the mapping.  The OTLP integration
        # recreates these automatically once generators start sending data.
        for ds_pattern in [
            "metrics-*.otel-*",
            "traces-*.otel-*",
        ]:
            try:
                resp = client.get(
                    f"{self.elastic_url}/_data_stream/{ds_pattern}",
                    headers=_es_headers(self.api_key),
                )
                if resp.status_code < 300:
                    streams = resp.json().get("data_streams", [])
                    for ds in streams:
                        ds_name = ds.get("name", "")
                        if ds_name:
                            r = client.delete(
                                f"{self.elastic_url}/_data_stream/{ds_name}",
                                headers=_es_headers(self.api_key),
                            )
                            if r.status_code < 300:
                                deleted += 1
                                logger.info("Deleted data stream %s for mapping refresh", ds_name)
            except Exception as exc:
                logger.warning("Data stream cleanup error (non-fatal): %s", exc)

        # Delete APM ML jobs for all namespaces
        for ns in all_namespaces:
            try:
                self._cleanup_apm_ml(client, ns)
            except Exception:
                pass

        # Delete cases for all namespaces
        for ns in all_namespaces:
            try:
                deleted += self._cleanup_cases(client, ns)
            except Exception:
                pass

        logger.info("Cleaned up %d artifacts across all scenarios", deleted)
        return deleted

    @classmethod
    def cleanup_all(cls, elastic_url: str, kibana_url: str, api_key: str) -> dict[str, Any]:
        """Class method: clean up ALL scenario artifacts without needing a specific scenario."""
        from scenarios import get_scenario, list_scenarios

        all_scenarios = list_scenarios()
        if not all_scenarios:
            return {"ok": True, "deleted": 0}

        # Use the first scenario just to get a deployer instance
        first = get_scenario(all_scenarios[0]["id"])
        deployer = cls(first, elastic_url, kibana_url, api_key)

        try:
            with httpx.Client(timeout=60.0, verify=True) as client:
                deleted = deployer._cleanup_all_scenarios(client)
            return {"ok": True, "deleted": deleted}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
