"""PlatformMixin — platform settings configuration methods."""

from __future__ import annotations

import httpx

from elastic_config.deployer_base import _kibana_headers, ProgressCallback


class PlatformMixin:

    def _configure_platform_settings(self, client: httpx.Client, notify: ProgressCallback):
        """Enable wired streams, significant events, and agent builder."""
        step = self._step(4)
        step.status = "running"
        notify(self.progress)

        configured = []
        errors = []

        # 1. Enable wired streams (always disable/re-enable to ensure clean state)
        try:
            # Always cycle — ensures wired logs stream and UI reflects enabled state
            client.post(
                f"{self.kibana_url}/api/streams/_disable",
                headers=_kibana_headers(self.api_key),
                json={},
            )
            resp = client.post(
                f"{self.kibana_url}/api/streams/_enable",
                headers=_kibana_headers(self.api_key),
                json={},
            )
            if resp.status_code < 300:
                configured.append("wired streams")
            else:
                errors.append(f"wired streams (HTTP {resp.status_code})")
        except Exception as exc:
            errors.append(f"wired streams ({exc})")

        # 2. Enable significant events
        try:
            resp = client.post(
                f"{self.kibana_url}/internal/kibana/settings",
                headers=_kibana_headers(self.api_key),
                json={"changes": {"observability:streamsEnableSignificantEvents": True}},
            )
            if resp.status_code < 300:
                configured.append("significant events")
            else:
                errors.append(f"significant events (HTTP {resp.status_code})")
        except Exception as exc:
            errors.append(f"significant events ({exc})")

        # 3. Enable agent builder as preferred chat experience
        try:
            resp = client.post(
                f"{self.kibana_url}/internal/kibana/settings",
                headers=_kibana_headers(self.api_key),
                json={"changes": {"aiAssistant:preferredChatExperience": "agent"}},
            )
            if resp.status_code < 300:
                configured.append("agent builder")
            else:
                errors.append(f"agent builder (HTTP {resp.status_code})")
        except Exception as exc:
            errors.append(f"agent builder ({exc})")

        # 4. Enable workflows UI
        try:
            resp = client.post(
                f"{self.kibana_url}/internal/kibana/settings",
                headers=_kibana_headers(self.api_key),
                json={"changes": {"workflows:ui:enabled": True}},
            )
            if resp.status_code < 300:
                configured.append("workflows UI")
            else:
                errors.append(f"workflows UI (HTTP {resp.status_code})")
        except Exception as exc:
            errors.append(f"workflows UI ({exc})")

        if configured:
            step.status = "ok"
            step.detail = f"Enabled: {', '.join(configured)}"
            if errors:
                step.detail += f"; failed: {', '.join(errors)}"
        else:
            step.status = "failed"
            step.detail = f"Failed: {', '.join(errors)}"

        notify(self.progress)
