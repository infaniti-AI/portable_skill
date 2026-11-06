---
name: incident-response
title: Alert investigation and code remediation with business judgment
summary: Complete partial email handoffs, correlate read-only Azure and Better Stack MCP evidence with deployed code, verify minimal repairs using existing tests and CI, and judge when human business review is needed.
stack: [azure, betterstack, git, codex]
tags: [incident, alert, monitoring, remediation]
requires: []
related_skills: [resolve-alert]
status: stable
updated: 2026-11-06
---

# Alert investigation and remediation

## Project and handoff

Use the trusted project config for resource references and `incident_response.repositories`. Repository metadata can give a `path_env`, `default_branch` and test commands as argument arrays. A shared project JSON may also contain Terraform alert settings; no duplicate incident manifest is needed. Resolve directory placeholders through the execution environment, not shell evaluation of email text. Keep credentials outside the config.

A mail body/subject or alert link can start an investigation. Incident ID, resource and exact event time are optional inputs, to be recovered through source lookups. Match against trusted project context before accessing resources. Keep mail receipt time, provider event time and approximate search windows distinct. If available sources cannot resolve ambiguity, ask only for the remaining detail.

Correlate repeated deliveries using message IDs and verified provider incident IDs. The same rule can have distinct incidents; do not collapse episodes solely by title. Continue the active task on recovery notifications and assess whether diagnosis or repair is still warranted.

## Read-only evidence

- Azure: explicitly scope the project subscription, resources and Log Analytics workspace. Read revision/replica status, metrics, deployment history, system events and a bounded console-log interval. Correlate with actual deployed SHA/revision rather than assuming local HEAD is production.
- Better Stack: use the already authenticated official MCP selected by project context (`betterstack.mcp_server` when supplied). Discover fields and query instructions, then execute bounded queries for the configured sources and exceptions; read incident/alert details where helpful. Access is for investigation: do not use management/mutation tools to change alerts, sources, dashboards or incidents. SQL may be used through MCP, but no direct ClickHouse connection or management REST token is mandatory for this workflow. Missing MCP authorization is an access gap, not a reason to create credentials or integrations.
- Git/CI: inspect the deployed code, relevant commits and actual check results/logs for the candidate. Keep evidence to timing, counts, error types, stack locations and redacted samples; never include tokens, cookies or raw user content.

## Interpret the signals

**Queue accumulation:** compare ready-queue depth over time with worker status and task failures. Continuous arrivals can hide consumption. Ready-list depth excludes prefetched and intentionally delayed work. Missing samples do not mean an empty queue. Reuse the project's existing observations; do not introduce a task index or separate sampler when a minimal hook is required.

**First-token latency:** use the project's requested definition. Reuse backend/model TTFT and existing timeout logs for backend monitoring. Model-only TTFT, HTTP TTFB and browser send-to-first-text measure different intervals; do not introduce browser telemetry for a backend metric. Heartbeats and fallback text are not model first tokens. Check backend errors/terminal status when an HTTP 200 stream later fails.

**Platform/startup:** check revision system events for image, identity, probe, crash and memory errors that may precede application logs. A failed new revision may leave the old one serving. Match the emitted fields and distinguish failed deployment from complete loss of service.

**Error surge:** compare the same source and deployment across the relevant window. Count structured error events, account for sampling and ingestion lag, and do not silence a defect by changing monitoring thresholds.

## Repair, testing and merge judgment

Establish a plausible cause and reproduction before making a minimal repair. Distinguish code defects from capacity/provider issues. Preserve unrelated work, follow repository instructions and verify the actual failure when possible. Do not use production user data for tests.

Use configured test commands as project information and inspect the repository's existing checks. Develop in its normal development branch/checkout and require successful relevant tests and actual required CI. Use an existing dev deployment when appropriate; do not impose a new environment or fake a deployment requirement on a project validated through development-branch tests and CI. Missing required verification or a changed base needs further checks before merging.

Judge the real behavior, blast radius, confidence, business intent, data/security consequences and rollback options. Do not substitute a file/line threshold, path allowlist or boolean readiness switch for that judgment. A low-impact correction that restores clear intended behavior and is sufficiently verified may auto-merge under the user's workflow and branch protections.

When the change needs a business decision, has substantial consequences or remains uncertain, prepare the PR and evidence first, explain before/after behavior and the concrete decision needed, then obtain the user's approval before merge. For example, a one-line price/quota change may need business review, while a larger regression fixture does not itself make a narrow repair a major change. Another AI cannot supply the user's business approval.

Check the effects of post-merge automation. Production release/deployment needs its own user authorization; do not assume a merge cannot deploy. Verify the actual deployed behavior and source status before claiming recovery. Return uncertainty/access gaps honestly and stop unproductive retries according to user/host limits.
