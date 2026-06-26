# Project Context

**Branch**: `stage-8c-research` (= `opencode/mighty-mountain`, same commit)  
**Server**: uvicorn port 7000, AUTH_ENABLED=true, SQLite `data/app.db`  
**Platform**: win32, Python 3.12

## Current State — 4 Fixes (ALL DEPLOYED)

| Fix | Description | Files | Status |
|-----|-------------|-------|--------|
| 1 | Hide system messages from chat UI | `static/js/sessions.js` — role check | ✅ |
| 2 | Dock chip shows "Goals" not "goals-modal" | `static/js/modalManager.js` — _LABELS entry | ✅ |
| 3 | Backfill completed_at for pre-migration milestones | `core/database.py` — migration function | ✅ Verified in DB |
| 4 | Accepted research in chat context | `goals/routes.py` — snapshot appends research | ✅ |

## Last User Action
Compared `stage-8c-research` ↔ `opencode/mighty-mountain` → **same commit (bd9e574)**

## Pending
- Nothing — waiting for next user request
