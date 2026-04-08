# Troubleshooting

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

### Related docs

- [AGENTS.MD](../AGENTS.MD) — Kibana Alerting API, `systemActions` vs legacy `actions`, and workflow `event.*` context.
