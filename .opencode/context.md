# Project Context

## Environment
- Language: Python 3.12
- Runtime: FastAPI + uvicorn (localhost:8000)
- Build: N/A (Python, no build step)
- Test: pytest (tests/ directory)
- Package Manager: pip (requirements.txt)

## Stage 8C Progress — ALL 28 TASKS COMPLETE

### Verified Complete
- **M1 — Data Model**: GoalResearchConfig + GoalResearchResult tables, `_migrate_stage8c_research()` in database.py
- **M2 — Trigger Engine**: `_research_scan_loop` in task_scheduler.py (60s poll, scheduled/idle/manual modes, 30-min anti-flood). Uses `threading.Thread(daemon=True) + .join(timeout=120)` for bounded LLM execution.
- **M3 — API Endpoints**: 6 endpoints all verified via curl (GET/PATCH config, POST run-now, GET results-per-goal, GET global-pending, PATCH accept/discard)
- **M4 — Frontend**: 270 lines added to goals.js (969 total) — research config controls (trigger mode, schedule, idle threshold, depth, digest, enabled), "Run Research Now" button with loading overlay + AbortController, accept/discard per result card, pending research badge + review queue in goals header
- **M5 — Email Digest**: `_send_research_digests()` in task_scheduler.py — groups pending results by owner, checks digest_frequency (daily/weekly), builds HTML email, sends via `_send_smtp_message()` using default EmailAccount. In-memory tracking of last send time.
- **M6 — Verification**: End-to-end manual tested (research run → pending → accept/discard → queue management → config last_run_at). Goal CRUD (14 goals) and AI decompose (5 suggestions) non-regression verified.

### Bug Fixes Applied
- `_perform_research` LLM call: replaced `asyncio.run(llm_call_async(...))` with `requests.post(url, json=payload, headers=hdrs, timeout=90)` — daemon threads cannot safely run nested async event loops. Research now completes in ~60s (was hanging 5min+).
- `_resolve_llm_config` extracted from nested function to module level in goals/routes.py for reuse by both API routes and `_perform_research`.
- Inner `try:` block in `_research_scan_loop` (line 561) removed — had no matching `except`/`finally`, causing `SyntaxError`. Outer `try/finally` handles cleanup.
- `_send_research_digests` email call fixed: `_send_smtp_message` expects a cfg `dict` first arg (with keys `smtp_host`, `smtp_port`, `smtp_user`, `smtp_password`, `from_address`), not individual positional args.
- `/api/goals` added to timeout-exempt prefixes in app.py (45s timeout bypass for decompose endpoint).
- **Accept/discard 404 fix**: Frontend `_reviewResearch()` in goals.js line 481 called `_api('/goals/research-results/' + resultId)` → resolving to `/api/goals/goals/research-results/{id}` (double `/goals`). Backend route is `@router.patch("/research-results/{result_id}")` on router with prefix `/api/goals` → `/api/goals/research-results/{id}` (single `/goals`). Changed frontend to `_api('/research-results/' + resultId)`. Verified: old path returns 404, new path returns 200.

## Key Files Modified
- `core/database.py`: GoalResearchConfig + GoalResearchResult models + migration
- `goals/routes.py`: 6 research endpoints + `_perform_research()` + `_resolve_llm_config` (module-level)
- `src/task_scheduler.py`: `_research_scan_loop` + `_send_research_digests` + startup/shutdown wiring
- `static/js/goals.js`: Research config UI, Run Research Now, accept/discard, pending queue (~270 lines added)
- `app.py`: `/api/goals` timeout exemption

## Key Behavioral Rules
- Research output NEVER writes to milestones, tasks — only pending GoalResearchResult rows
- User manually accepts/discards results (same pattern as AI milestone suggestions in Stage 8B)
- LLM synthesis uses `requests.post()` inside daemon thread (not asyncio.run — avoids event loop conflicts)
- Research is opt-in per goal (enabled=false by default, created lazily on first PATCH)
- trigger_mode supports: "manual" (API only), "scheduled" (HH:MM match), "idle" (session last_accessed)
- Email digest uses in-memory tracking (resets on server restart), grouped by owner, respects digest_frequency
- `_send_smtp_message(cfg: dict, from_addr, recipients, message, timeout=30)` — cfg keys: smtp_host, smtp_port, smtp_user, smtp_password

## Verification Summary
- Research runs produce coherent LLM-synthesized summaries with cited sources
- Accept sets status="accepted" + reviewed_at
- Discard removes from pending queue
- last_run_at updates on config after each successful run
- 30-min anti-flood between auto-runs
- Stage 8B patterns (AbortController, global dismiss, button disable) carried forward
- 28/28 todos complete — all Stage 8C tasks [x]
