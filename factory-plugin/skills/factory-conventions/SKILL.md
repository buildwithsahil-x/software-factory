---
name: factory-conventions
description: Conventions every Software Factory agent follows - how to report progress, where to write files, and what to do when a guardrail blocks an action. Use at the start of any factory task.
---

# Factory conventions

## Reporting
- Call the `report_progress` tool at each meaningful milestone (started, key step done, finished).
  One short, plain sentence. The operator watches these live on the dashboard.
- Finish with a short summary: what you did, what files you created or changed, anything left open.

## Files
- Work only inside your current working directory. Use relative paths.
- Do not touch files outside it, and do not read secrets (`.env`, credentials).

## Guardrails
- Some actions are blocked by policy: Azure changes, `terraform apply/plan`, cluster changes,
  pushing to or merging into `dev`, `main` or `infra`, force pushes.
- If an action is blocked, do not retry it in another form. Say what you wanted to do and why,
  then continue with what you are allowed to do.
