# Mission: Fix goal-linked chat session bugs

## M1: Bug 1 — Wrong model (nomic-embed-text instead of qwen2.5:7b) | status: completed
### T1.1: Fix model resolution in endpoint_resolver.py | agent:Worker
- [x] S1.1.1: Added "embed" to _NON_CHAT_MODEL tuple in src/endpoint_resolver.py
- [x] S1.1.2: Syntax verified (python -m py_compile passes)
- [x] S1.1.3: API verified — new goal session uses model "qwen2.5:7b" ✓

## M2: Bug 2 — Blank new session instead of goal-linked one | status: completed
### T2.1: Fix primer persistence + navigation reliability | agent:Worker
- [x] S2.1.1: Changed new_sess.add_message() to session_manager.add_message(new_sid, ...) in both goal and milestone endpoints
- [x] S2.1.2: Syntax verified (python -m py_compile passes)
- [x] S2.1.3: API verified — goal primer present in session history ✓

## M3: Verify Both Fixes End-to-End | status: completed
### T3.1: Full system verification | agent:Reviewer
- [x] S3.1.1: Bug 1: New goal session model is "qwen2.5:7b" (not nomic-embed-text) ✓
- [x] S3.1.2: Bug 2: Goal context primer correctly present in session history ✓
- [x] S3.1.3: AI replied "Your goal is to test the model filter as a senior engineer" — demonstrates full pipeline working

## Summary
| Bug | Root Cause | Fix | Verification |
|-----|-----------|-----|-------------|
| 1: Wrong model | _NON_CHAT_MODEL missing "embed" | Added "embed" to tuple | API: model=qwen2.5:7b |
| 2: Blank session | session.add_message used singleton | session_manager.add_message explicit | API: primer present + AI reply |
