"""EventsMixin — significant events deploy and cleanup methods."""

from __future__ import annotations

import logging

import httpx

from elastic_config.deployer_base import _kibana_headers, ProgressCallback

logger = logging.getLogger("deployer")


class EventsMixin:

    def _deploy_significant_events(self, client: httpx.Client, notify: ProgressCallback):
        step = self._step(10)
        step.status = "running"
        notify(self.progress)

        # Clean existing queries (streams already enabled in _configure_platform_settings)
        self._cleanup_significant_events(client)

        # Build bulk operations
        operations = []
        registry = self.scenario.channel_registry
        for ch_num, ch_data in sorted(registry.items()):
            num_str = f"{int(ch_num):02d}"
            error_type = ch_data["error_type"]
            esql_query = f'FROM logs.otel,logs.otel.* METADATA _id, _source | WHERE body.text LIKE "*{error_type}*" AND severity_text == "ERROR"'
            operations.append({
                "index": {
                    "id": f"{self.ns}-se-ch{num_str}",
                    "title": f"Channel {num_str}: {ch_data['name']}",
                    "description": f"{ch_data.get('subsystem', 'system')} — {error_type}",
                    "esql": {"query": esql_query},
                }
            })

        step.items_total = len(operations)

        if operations:
            resp = client.post(
                f"{self.kibana_url}/api/streams/logs.otel/queries/_bulk",
                headers=_kibana_headers(self.api_key),
                json={"operations": operations},
            )
            if resp.status_code < 300:
                step.items_done = len(operations)
                step.detail = f"Created {len(operations)} stream queries"
            else:
                logger.warning("Significant events bulk create failed: %s", resp.text[:500])
                step.detail = f"Bulk create failed (HTTP {resp.status_code})"

        step.status = "ok" if step.items_done > 0 else "failed"
        notify(self.progress)

    def _cleanup_significant_events(self, client: httpx.Client):
        """Delete stream queries for this namespace."""
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
                    if qid.startswith(f"{self.ns}-se-"):
                        client.delete(
                            f"{self.kibana_url}/api/streams/logs.otel/queries/{qid}",
                            headers=_kibana_headers(self.api_key),
                        )
        except Exception:
            pass
