# Troubleshooting

## systemd: `Unknown key 'StartLimitIntervalSec' in section [Service]`

**Cause:** On some systemd versions, `StartLimitIntervalSec` and `StartLimitBurst` are **`[Unit]`** settings, not `[Service]`.

**Fix:** Move those lines into the `[Unit]` block above `[Service]`, then `sudo systemctl daemon-reload`.

---

## AUTO_DEPLOY never runs

Startup multi-deploy defaults to **`gcp`, `financial`, `banking`** in **`app/config.py`** (`_DEFAULT_AUTO_DEPLOY_SCENARIO_IDS`). It does **not** depend on `.env` unless you set **`AUTO_DEPLOY_SCENARIOS`** to override or **`AUTO_DEPLOY_SCENARIOS=0`** to turn it off.

If you still see **`parsed_ids=[]`** in **`[elastic-launch-demo] startup:`**, you have an env var forcing empty (e.g. `AUTO_DEPLOY_SCENARIOS=0` or a stray empty assignment in **`EnvironmentFile=`**).

If **`DEMO_*`** fails under **`User=www-data`**, fix **`EnvironmentFile=`** / permissions — that is separate from the hard-coded auto-deploy list.

---

## Journal shows only `uvicorn.error`, not AUTO_DEPLOY / deploy lines

**Cause:** Uvicorn’s logging defaults can leave the root logger at WARNING, so app loggers (e.g. `nova7`, `elastic_config.deployer`) never reach journald.

**Fix:** The app calls `_ensure_app_logs_visible()` at startup (lifespan) to raise the root level and attach a stderr handler if needed. After upgrading to a build that includes that change, restart the service and check:

```bash
sudo journalctl -u elastic-launch-demo.service -n 80 --no-pager
```

You should see **`[elastic-launch-demo] startup:`** with **`parsed_ids=['gcp', 'financial', 'banking']`** (unless overridden) and **`AUTO_DEPLOY starting thread for: …`**.

---

## Kibana alert rules and `system-connector-.workflows`

### `Object type ".workflows" is not registered` (rule editor)

**What it is:** `system-connector-.workflows` is a **system action**, not a normal Action Connector saved object. If the workflow runner is placed in the rule’s **`actions`** array (with `group`, `frequency`, etc.), the rule editor tries to resolve it like a connector type `.workflows`, which is not registered that way. Background execution may still work; the failure is usually in the UI when opening or editing the rule.

**What we do in this repo:** The deployer creates rules with **`actions: []`** and a **`systemActions`** entry whose `id` is `system-connector-.workflows`, with the same `params` (`subAction` / `subActionParams` with `workflowId` and `inputs`). That keeps the workflow on the system-actions path, which the rule UI knows how to render.

**After changing this behavior:** Redeploy the scenario (or at least re-run the alerting step) so existing rules are recreated with the correct shape.

---

### `POST /api/alerting/rule` rejects `systemActions`

**Symptom:** Logs show HTTP 400 with a message like `[request body.systemActions]: definition for this key is missing`.

**Cause:** Some Kibana builds do not include `systemActions` on the create-rule API schema, even though newer stacks expect it for the workflow system action.

**What we do:** The deployer tries **`systemActions` first**. If the API returns 400 in a way that indicates an unsupported `systemActions` key, it **retries the same rule** using the **legacy** payload (`actions` with `group` / `frequency` / `system-connector-.workflows`). After a successful retry, it uses the **legacy shape for the rest of that deployment** so you do not pay two requests per rule.

**Force legacy only (skip `systemActions`):** set before deploy:

```bash
export ELASTIC_ALERT_LEGACY_WORKFLOW_ACTION=1
```

Rules should still be created, but on stacks that required legacy you may still see the **“.workflows” is not registered** issue in the rule UI when editing those rules.

---

## Debian host: locked credentials and uvicorn

- **Connection form still visible with locked env:** see [DEBIAN_LOCKED_CREDENTIALS.md — Troubleshooting](DEBIAN_LOCKED_CREDENTIALS.md#troubleshooting) (`curl` public-config, systemd `EnvironmentFile`, restart).
- **Uvicorn `illegal request line` / TLS bytes (`\x16\x03\x01`):** HTTPS client hitting plain HTTP — use `http://` to the app or terminate TLS in Nginx and `proxy_pass http://127.0.0.1:…`.

---

### Related docs

- [AGENTS.MD](../AGENTS.MD) — Kibana Alerting API, `systemActions` vs legacy `actions`, and workflow `event.*` context.
- [DEBIAN_LOCKED_CREDENTIALS.md](DEBIAN_LOCKED_CREDENTIALS.md) — Server-side `DEMO_*` credentials on Debian/systemd.
