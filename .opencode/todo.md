# Mission: Stage 8C — Autonomous Background Research + Review Queue

## M1: Data Model — DB tables [COMPLETE]
### T1.1: Add GoalResearchConfig table | core/database.py
- [x] S1.1.1: Define GoalResearchConfig model (goal_id FK, trigger_mode, scheduled_time, idle_threshold_minutes, depth, digest_frequency, enabled, last_run_at)
- [x] S1.1.2: Define GoalResearchResult model (id, goal_id FK, created_at, summary, source_notes, status, reviewed_at)
- [x] S1.1.3: Add migration to create both tables (check for existing, CREATE TABLE IF NOT EXISTS)

## M2: Research Trigger Engine [COMPLETE]
### T2.1: Create research loop in task_scheduler | src/task_scheduler.py
- [x] S2.1.1: Add `_research_loop` async method (60s poll, reuses existing _known_task_owners pattern)
- [x] S2.1.2: Implement scheduled trigger logic (check GoalResearchConfig.scheduled_time against current time, fire once per day)
- [x] S2.1.3: Implement idle trigger logic (check user activity tracking; if exists, compare idle_threshold_minutes)
- [x] S2.1.4: Implement perform_research() — sync function using threading.Thread + .join(timeout) for bounded web search + LLM synthesis
- [x] S2.1.5: Wire perform_research to write GoalResearchResult row + update last_run_at
- [x] S2.1.6: Add _research_loop start/stop lifecycle to task_scheduler's start()/stop()

## M3: API Endpoints | goals/routes.py [COMPLETE]
### T3.1: Research config endpoints
- [x] S3.1.1: GET /goals/{id}/research-config — return config or defaults
- [x] S3.1.2: PATCH /goals/{id}/research-config — update config fields
### T3.2: Research run + results endpoints
- [x] S3.2.1: POST /goals/{id}/research/run-now — manual trigger, sync call with timeout guard
- [x] S3.2.2: GET /goals/{id}/research-results — list results, filterable by status
- [x] S3.2.3: GET /research-results/pending — global pending queue
- [x] S3.2.4: PATCH /research-results/{id} — accept/discard status update

## M4: Frontend | static/js/goals.js [COMPLETE]
### T4.1: Research section on goal detail view
- [x] S4.1.1: Add "Research" section below milestones with config controls (trigger mode, schedule/idle, depth, digest, enabled toggle)
- [x] S4.1.2: Add "Run Research Now" button with loading state (disable button, spinner, no orphaned requests)
- [x] S4.1.3: Loading overlay for research-in-progress (dismissable, same pattern as 8B AI Suggestions)
### T4.2: Research results display + review queue
- [x] S4.2.1: Show research results per goal (summary, source_notes, timestamp, accept/discard buttons)
- [x] S4.2.2: Global pending queue view (list all pending across all goals)
- [x] S4.2.3: Accept/discard action handlers

## M5: Email Digest [COMPLETE]
### T5.1: Wire digest email using existing infra
- [x] S5.1.1: Build digest email content from pending research results
- [x] S5.1.2: Use _send_smtp_message via default EmailAccount per owner
- [x] S5.1.3: Respect digest_frequency (daily/weekly) — skip if already sent today/this-week

## M6: Verification [COMPLETE]
### T6.1: Real manual research run
- [x] S6.1.1: Configure goal for manual trigger, click "Run Research Now", paste research summary
- [x] S6.1.2: Confirm result appears in pending queue and nowhere else (no milestone/task created)
### T6.2: Accept/discard flow
- [x] S6.2.1: Accept one result, discard another, confirm status via GET
### T6.3: Non-regression
- [x] S6.3.1: Confirm Companion planner, Goals CRUD, task-linking, AI milestone suggestions still work
