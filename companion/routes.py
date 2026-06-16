"""Companion bridge — /api/companion/*.

A thin, additive layer so a LAN client (e.g. a phone) can discover what a server
offers and pair to it, without duplicating any LLM logic.

Auth is enforced globally by AuthMiddleware (app.py), so reaching a handler here
means the caller is authenticated by either a cookie session or a Bearer `ody_`
API token. The read endpoints (ping/info/models) accept either; the pairing
endpoints are admin-cookie only.

Pairing CSRF posture: minting happens ONLY on POST. The session cookie is
SameSite=Lax (routes/auth_routes.py), which a browser does not send on a
cross-site POST, so an admin's cookie can't be used by a malicious page to mint
a token -- the same protection the existing POST /api/tokens relies on. Minting
on a GET would be unsafe (Lax cookies ride top-level GET navigations), so GET
/pair only renders a form.
"""

import html
import io
import json
import os
import re
import uuid
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response

from core.middleware import require_admin
from src.auth_helpers import get_current_user

from companion import pairing as _pairing


# Tracks the last user who made an authenticated cookie-session request,
# so the sysinfo POST endpoint can save incoming data under that profile
# even when the collector uses a different owner id (e.g. "auto").
_last_active_username: str | None = None


def _record_active_user(username: str | None) -> None:
    global _last_active_username
    if username:
        _last_active_username = username


def get_last_active_user() -> str | None:
    return _last_active_username


def token_owner(request: Request) -> str | None:
    """The real owner to attribute a request to, for read-scoping.

    Cookie sessions resolve to the logged-in username via get_current_user.
    Bearer-token callers come through as the sandboxed pseudo-user "api"; their
    real owner is stamped on request.state.api_token_owner by the auth
    middleware. Returns None when no owner can be resolved.
    """
    if getattr(request.state, "api_token", False):
        return getattr(request.state, "api_token_owner", None)
    owner = get_current_user(request)
    _record_active_user(owner)
    return owner


def owner_can_see(row_owner, owner) -> bool:
    """Owner-scope rule for read endpoints.

    A caller sees a row when it is their own, or when it is a legacy null-owner
    ("shared") row. A caller must NEVER see another owner's row. Mirrors the
    `owner_filter` rule used elsewhere, expressed as a pure predicate so it can
    be tested directly and used as a defensive in-Python check alongside the
    SQL filter.
    """
    return row_owner is None or row_owner == owner


def mint_pairing_token(owner: str, invalidate=None) -> tuple[str, str]:
    """Mint a pairing token AND invalidate the auth middleware's in-memory token
    cache, so the new token is accepted on the very next request without a server
    restart. Returns (token_id, raw_token); the raw token is shown once.

    `invalidate` is the app's request.app.state.invalidate_token_cache callable
    (passed in so this stays a pure, testable unit).
    """
    token_id, raw_token = _pairing.mint_token(owner)
    if callable(invalidate):
        invalidate()
    return token_id, raw_token


def detect_patterns(owner):
    """Detect patterns from check-in and task data over the last 14-21 days.

    Returns a structured dict with mood/sleep/task trends, or
    {"insufficient_data": True} if fewer than 5 check-ins exist.
    """
    from core.database import SessionLocal, CompanionCheckin, CompanionTask
    from datetime import date, timedelta
    from collections import Counter

    if not owner:
        return {"insufficient_data": True}

    db = SessionLocal()
    try:
        today = date.today()

        # Count all check-ins for the user
        total_checkins = db.query(CompanionCheckin).filter(
            CompanionCheckin.owner == owner
        ).count()
        if total_checkins < 5:
            return {"insufficient_data": True}

        # Fetch last 21 days of check-ins
        cutoff_21 = (today - timedelta(days=21)).isoformat()
        rows = db.query(CompanionCheckin).filter(
            CompanionCheckin.owner == owner,
            CompanionCheckin.date >= cutoff_21
        ).order_by(CompanionCheckin.date).all()

        checkins_by_date = {r.date: r for r in rows}

        def checkins_in_range(start_offset, end_offset):
            start = (today - timedelta(days=start_offset)).isoformat()
            end = (today - timedelta(days=end_offset)).isoformat()
            return [r for d, r in sorted(checkins_by_date.items()) if start <= d <= end]

        result = {}

        # ── Mood ──
        ch7 = checkins_in_range(6, 0)
        moods_7 = [c.mood for c in ch7 if c.mood is not None]
        if len(moods_7) >= 3:
            result["avg_mood_7d"] = round(sum(moods_7) / len(moods_7), 1)

        ch_prev7 = checkins_in_range(13, 7)
        prev_moods = [c.mood for c in ch_prev7 if c.mood is not None]
        if len(prev_moods) >= 3:
            result["avg_mood_prev_7d"] = round(sum(prev_moods) / len(prev_moods), 1)

        current_avg = result.get("avg_mood_7d")
        prev_avg = result.get("avg_mood_prev_7d")
        if current_avg is not None and prev_avg is not None and (len(moods_7) + len(prev_moods)) >= 5:
            diff = current_avg - prev_avg
            result["mood_trend"] = "declining" if diff < -0.5 else "improving" if diff > 0.5 else "stable"
        elif current_avg is not None and len(moods_7) >= 5:
            first3 = sum(moods_7[:3]) / 3
            last3 = sum(moods_7[-3:]) / 3
            diff = last3 - first3
            result["mood_trend"] = "declining" if diff < -0.5 else "improving" if diff > 0.5 else "stable"

        # ── Low mood days of week ──
        low_days = Counter()
        for r in rows:
            if r.mood is not None and r.mood < 4:
                try:
                    d = date.fromisoformat(r.date)
                    low_days[d.strftime("%A")] += 1
                except ValueError:
                    pass
        recurring_low = [day for day, count in low_days.items() if count >= 2]
        if recurring_low:
            result["low_mood_days_of_week"] = recurring_low

        # ── Sleep ──
        sleeps_7 = [c.sleep_hours for c in ch7 if c.sleep_hours is not None and c.sleep_hours > 0]
        if len(sleeps_7) >= 3:
            result["avg_sleep_7d"] = round(sum(sleeps_7) / len(sleeps_7), 1)

        prev_sleeps = [c.sleep_hours for c in ch_prev7 if c.sleep_hours is not None and c.sleep_hours > 0]
        prev_sleep_avg = round(sum(prev_sleeps) / len(prev_sleeps), 1) if len(prev_sleeps) >= 3 else None
        current_sleep_avg = result.get("avg_sleep_7d")
        if current_sleep_avg is not None and prev_sleep_avg is not None and (len(sleeps_7) + len(prev_sleeps)) >= 5:
            sleep_diff = current_sleep_avg - prev_sleep_avg
            result["sleep_trend"] = "declining" if sleep_diff < -0.5 else "improving" if sleep_diff > 0.5 else "stable"

        # ── Task completion rate (last 7 days) ──
        task_start = (today - timedelta(days=7)).isoformat()
        tasks_7 = db.query(CompanionTask).filter(
            CompanionTask.owner == owner,
            CompanionTask.date >= task_start
        ).all()
        if tasks_7:
            total = len(tasks_7)
            completed = sum(1 for t in tasks_7 if t.status == "done")
            if total > 0:
                result["task_completion_rate_7d"] = round(completed / total, 2)

        # ── Common carry-over tasks ──
        carry_cutoff = (today - timedelta(days=21)).isoformat()
        carry_over_tasks = db.query(CompanionTask).filter(
            CompanionTask.owner == owner,
            CompanionTask.date >= carry_cutoff,
            CompanionTask.carried_over == True
        ).all()
        carry_titles = Counter(t.title for t in carry_over_tasks)
        common = [title for title, count in carry_titles.items() if count >= 3]
        if common:
            result["common_carry_over_tasks"] = common

        # ── Check-in streak ──
        streak = 0
        for i in range(60):
            d = (today - timedelta(days=i)).isoformat()
            if d in checkins_by_date:
                streak += 1
            else:
                break
        if streak > 0:
            result["checkin_streak_days"] = streak

        # ── EOD rating trend ──
        eod_ratings_7 = [c.eod_rating for c in ch7 if c.eod_rating is not None]
        if len(eod_ratings_7) >= 3:
            first3_eod = sum(eod_ratings_7[:3]) / 3
            last3_eod = sum(eod_ratings_7[-3:]) / 3
            eod_diff = last3_eod - first3_eod
            result["eod_rating_trend"] = "declining" if eod_diff < -0.5 else "improving" if eod_diff > 0.5 else "stable"

        return result
    finally:
        db.close()


def _resolve_companion_endpoint(owner=None):
    """Resolve (url, model, headers) for companion AI calls.

    Reads default_endpoint_id from settings, queries the ModelEndpoint,
    and always picks the first chat-capable model from its cached_models
    list — ignoring any embedding/TTS/utility model that may be stored in
    default_model.
    """
    import json as _json
    try:
        from src.settings import load_settings
        settings = load_settings()
    except Exception:
        settings = {}
    ep_id = settings.get("default_endpoint_id", "")
    if not ep_id:
        return None, None, None

    from core.database import SessionLocal, ModelEndpoint
    from src.endpoint_resolver import resolve_endpoint_runtime, build_chat_url, build_headers, _first_chat_model

    db = SessionLocal()
    try:
        q = db.query(ModelEndpoint).filter(
            ModelEndpoint.id == ep_id,
            ModelEndpoint.is_enabled == True
        )
        if owner:
            from src.auth_helpers import owner_filter
            q = owner_filter(q, ModelEndpoint, owner)
        ep = q.first()
        if not ep:
            return None, None, None

        base, api_key = resolve_endpoint_runtime(ep, owner=owner)
        url = build_chat_url(base)
        headers = build_headers(api_key, base)

        models = _json.loads(ep.cached_models) if ep.cached_models else []
        _NON_CHAT_EXTRA = ("embed",)
        chat_models = [
            m for m in models
            if not any(p in str(m).lower() for p in _NON_CHAT_EXTRA)
        ]
        model = _first_chat_model(chat_models) or _first_chat_model(models) or ""
        if not model:
            return None, None, None

        return url, model, headers
    except Exception:
        return None, None, None
    finally:
        db.close()


def get_current_lifestyle_context(owner):
    """Determine current/next schedule blocks based on today's day type.
    
    Returns dict with current_block and next_block (each may be None).
    """
    from datetime import datetime, date
    from core.database import SessionLocal, CompanionLifestyle
    import json

    try:
        db = SessionLocal()
        row = db.query(CompanionLifestyle).filter(
            CompanionLifestyle.owner == owner
        ).first()
        if not row:
            return {"current_block": None, "next_block": None}
        db.close()
    except Exception:
        return {"current_block": None, "next_block": None}
    finally:
        try:
            db.close()
        except Exception:
            pass

    # Determine weekday vs weekend
    today = date.today()
    is_weekend = today.weekday() >= 5  # 5=Sat, 6=Sun
    schedule_raw = row.weekend_schedule if is_weekend else row.weekday_schedule
    if not schedule_raw:
        return {"current_block": None, "next_block": None}
    try:
        blocks = json.loads(schedule_raw)
    except (ValueError, TypeError):
        return {"current_block": None, "next_block": None}
    if not blocks:
        return {"current_block": None, "next_block": None}

    # Sort by start time
    blocks.sort(key=lambda b: b.get("start", "00:00"))

    now = datetime.now()
    current_min = now.hour * 60 + now.minute

    current_block = None
    next_block = None

    for b in blocks:
        start_str = b.get("start", "")
        end_str = b.get("end", "")
        if not start_str or not end_str:
            continue
        try:
            start_parts = start_str.split(":")
            end_parts = end_str.split(":")
            start_min = int(start_parts[0]) * 60 + int(start_parts[1])
            end_min = int(end_parts[0]) * 60 + int(end_parts[1])
        except (ValueError, IndexError):
            continue

        # Handle overnight blocks (end < start, e.g. sleep 23:00-07:00)
        if end_min < start_min:
            # If current time >= start OR current time < end, we're in it
            if current_min >= start_min or current_min < end_min:
                current_block = {"label": b.get("label", ""), "type": b.get("type", ""), "ends_at": end_str}
                break
        else:
            if start_min <= current_min < end_min:
                current_block = {"label": b.get("label", ""), "type": b.get("type", ""), "ends_at": end_str}
                break

        # Track first upcoming block (after current time)
        if not next_block and start_min > current_min and (end_min > start_min or True):
            next_block = {"label": b.get("label", ""), "type": b.get("type", ""), "starts_at": start_str}
            # Don't break — keep looking for current block first
            if not current_block and next_block:
                pass

    # If no current block found but we have a next_block, keep it
    # If no next block found yet and no current block, pick the first
    if not current_block and not next_block and blocks:
        # Tomorrow's first block
        pass

    return {"current_block": current_block, "next_block": next_block}


# In-memory store for the latest extracted fact — used by the "Noted" toast polling
# endpoint. Set by extract_companion_memory_from_chat and _extract_eod_memory_facts.
_latest_companion_fact: dict | None = None

def _set_latest_companion_fact(fact: dict) -> None:
    global _latest_companion_fact
    _latest_companion_fact = fact

def _get_latest_companion_fact() -> dict | None:
    return _latest_companion_fact


async def extract_companion_memory_from_chat(owner, message, memory_manager=None):
    """Fire-and-forget extraction of a personal fact from a main chat message.
    Skips short messages, calls companion endpoint with a focused prompt,
    saves to the Brain's MemoryManager if a fact is found.
    Returns the saved fact dict or None — caller may use for UI indicator.
    """
    if not message or len(message.strip()) < 15:
        return None
    if not owner:
        return None

    url, model, headers = _resolve_companion_endpoint(owner)
    if not url or not model:
        import logging
        logging.getLogger("companion").debug(
            "[memory-extract] no endpoint for owner=%s (url=%s model=%s)", owner, url, model
        )
        return None

    from src.llm_core import llm_call_async
    import json, uuid as _uuid

    system_prompt = (
        "You extract personal facts from user messages. Respond ONLY with a JSON "
        'object {"content": "...", "category": "...", "is_sensitive": true|false} '
        "or the word null if nothing to extract.\n\n"
        "Categories: milestone, life_event, relationship, loss, preference, health, emotional_moment, other.\n"
        "is_sensitive=true for: deaths, grief, serious illness, trauma, mental health crises.\n\n"
        "Examples:\n"
        'User: "i have got 2nd place in local running tournament"\n'
        'Assistant: {"content": "User placed 2nd in a local running tournament", "category": "milestone", "is_sensitive": false}\n\n'
        'User: "my dad passed away when I was young"\n'
        'Assistant: {"content": "User father passed away when they were young", "category": "loss", "is_sensitive": true}\n\n'
        'User: "started learning guitar last week"\n'
        'Assistant: {"content": "User started learning guitar last week", "category": "life_event", "is_sensitive": false}\n\n'
        'User: "what is 15% of 200"\n'
        'Assistant: null\n\n'
        "Extract milestones, achievements, life events, relationships, losses, health "
        "changes, emotional moments. Skip trivial queries (math, weather, greetings)."
    )

    try:
        reply = await llm_call_async(url, model, [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message.strip()}
        ], headers=headers, temperature=0.3, max_tokens=300)
    except Exception as e:
        import logging
        logging.getLogger("companion").debug("[memory-extract] LLM call failed: %s", e)
        return None

    if not reply:
        return None

    text = reply.strip()
    # Remove markdown fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(l for l in lines if not l.startswith("```"))

    if text.strip() == "null" or not text.strip():
        return None

    # Try JSON parse; if fails, attempt regex extraction of first JSON object
    try:
        fact = json.loads(text)
    except (ValueError, TypeError):
        import re as _re
        _brace_match = _re.search(r'\{[^{}]*\}', text, _re.DOTALL)
        if _brace_match:
            try:
                fact = json.loads(_brace_match.group())
            except (ValueError, TypeError):
                import logging
                logging.getLogger("companion").warning(
                    "[memory-extract] unparseable response: %.200s", text
                )
                return None
        else:
            import logging
            logging.getLogger("companion").warning(
                "[memory-extract] unparseable response: %.200s", text
            )
            return None

    if not isinstance(fact, dict) or "content" not in fact:
        return None

    content = str(fact.get("content", ""))[:150]
    if not content or len(content) < 5:
        return None
    category = str(fact.get("category", "other"))[:30]
    if category not in ("life_event", "relationship", "loss", "milestone", "preference",
                        "health", "emotional_moment", "other"):
        category = "other"
    is_sensitive = bool(fact.get("is_sensitive", False))
    source_excerpt = str(fact.get("source_excerpt", ""))[:200]

    # Save into the Brain's MemoryManager
    if memory_manager is None:
        from src.memory import MemoryManager
        from src.constants import DATA_DIR
        memory_manager = MemoryManager(DATA_DIR)

    try:
        fact_id = str(_uuid.uuid4())
        entry = memory_manager.add_entry(content, source="auto", category=category, owner=owner)
        entry["id"] = fact_id
        entry["is_sensitive"] = is_sensitive
        if source_excerpt:
            entry["source_excerpt"] = source_excerpt
        all_entries = memory_manager.load_all()
        all_entries.append(entry)
        memory_manager.save(all_entries)
        import logging
        logging.getLogger("companion").info(
            "[memory-extract] saved fact id=%s cat=%s content=%.80s", fact_id, category, content
        )
        # Store latest extracted fact for "Noted" toast polling
        _set_latest_companion_fact({
            "id": fact_id, "content": content, "category": category,
            "is_sensitive": is_sensitive, "source": "realtime",
        })
        return {"id": fact_id, "content": content, "category": category,
                "is_sensitive": is_sensitive, "source": "realtime"}
    except Exception as exc:
        import logging
        logging.getLogger("companion").debug("[memory-extract] save failed: %s", exc)
        return None


async def _extract_eod_memory_facts(owner, eod_done, eod_blocked, eod_tomorrow, memory_manager=None):
    """Fire-and-forget EOD batch extraction. Gathers up to 3 facts from
    EOD reflection text and saves them into the Brain's MemoryManager.
    """
    if not owner:
        return

    url, model, headers = _resolve_companion_endpoint(owner)
    if not url or not model:
        return

    from src.llm_core import llm_call_async
    import json, uuid as _uuid

    prompt = (
        "You are a memory extraction assistant focused on emotionally or personally significant moments.\n\n"
        "Based on the user's end-of-day reflection:\n"
        f"Done: '{eod_done}' | Blocked: '{eod_blocked}' | Tomorrow: '{eod_tomorrow}'\n\n"
        "Extract up to 3 NEW personal facts worth remembering long-term (life events, relationships, "
        "milestones, health notes, emotional moments) that aren't just routine task completion.\n\n"
        "IGNORE (these are already handled by the general memory system):\n"
        "- Identity facts (name, job, location)\n"
        "- Generic preferences ('I like pizza')\n"
        "- Routine task completion updates\n\n"
        "Respond with ONLY a JSON array (can be empty []):\n"
        '[{"content": "short factual statement, max 150 chars, third person", '
        '"category": "life_event|relationship|loss|milestone|preference|health|emotional_moment|other", '
        '"is_sensitive": true|false}]\n\n'
        "is_sensitive should be true for: deaths, grief, serious illness, trauma, mental health crises.\n\n"
        "No markdown, no explanation."
    )

    try:
        reply = await llm_call_async(url, model, [
            {"role": "user", "content": prompt}
        ], headers=headers, temperature=0.3, max_tokens=500)
    except Exception:
        return

    if not reply:
        return

    text = reply.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(l for l in lines if not l.startswith("```"))

    try:
        facts = json.loads(text)
    except (ValueError, TypeError):
        return

    if not isinstance(facts, list):
        return

    if memory_manager is None:
        from src.memory import MemoryManager
        from src.constants import DATA_DIR
        memory_manager = MemoryManager(DATA_DIR)

    try:
        existing = memory_manager.load(owner) or []
        existing_texts = [e.get("text", "").lower() for e in existing]

        for fact in facts:
            if not isinstance(fact, dict) or "content" not in fact:
                continue
            content = str(fact.get("content", ""))[:150].strip()
            if not content or len(content) < 5:
                continue
            category = str(fact.get("category", "other"))[:30]
            if category not in ("life_event", "relationship", "loss", "milestone",
                                "preference", "health", "emotional_moment", "other"):
                category = "other"
            is_sensitive = bool(fact.get("is_sensitive", False))

            # Simple deduplication: skip if similar content already exists
            content_lower = content.lower()
            is_dup = False
            for existing_text in existing_texts:
                if content_lower == existing_text:
                    is_dup = True
                    break
                words = set(content_lower.split())
                ex_words = set(existing_text.split())
                if len(words) > 3 and len(ex_words) > 3:
                    overlap = len(words & ex_words) / max(len(words), len(ex_words))
                    if overlap > 0.6:
                        is_dup = True
                        break
            if is_dup:
                continue

            entry = memory_manager.add_entry(content, source="auto", category=category, owner=owner)
            entry["id"] = str(_uuid.uuid4())
            entry["is_sensitive"] = is_sensitive
            all_entries = memory_manager.load_all()
            all_entries.append(entry)
            memory_manager.save(all_entries)
    except Exception:
        pass


def get_relevant_memory_context(owner, limit=5):
    """Return relevant personal facts from the Brain's MemoryManager for prompt injection.
    Returns a formatted string or empty string if no facts exist.
    Sensitive facts get an extra caution instruction.
    """
    if not owner:
        return ""
    from src.memory import MemoryManager
    from src.constants import DATA_DIR
    mm = MemoryManager(DATA_DIR)
    try:
        entries = mm.load(owner)
        if not entries:
            return ""
        # Sort by timestamp descending
        entries = sorted(entries, key=lambda e: e.get("timestamp", 0), reverse=True)[:limit]
        lines = []
        has_sensitive = False
        for e in entries:
            text = e.get("text", "")
            if not text:
                continue
            if e.get("is_sensitive"):
                has_sensitive = True
                lines.append(f"- {text} (sensitive — handle with care)")
            else:
                lines.append(f"- {text}")
        if not lines:
            return ""
        result = (
            "\n\nRelevant context about this person (reference ONLY if naturally relevant, "
            "never list these out or interrogate the user about them):\n"
            + "\n".join(lines)
        )
        if has_sensitive:
            result += (
                "\n\nSome of the above is marked sensitive. If relevant, handle with care; "
                "do not bring it up casually or repeatedly. Never use it to make assumptions "
                "about current mood unless the user themselves brings up something related."
            )
        return result
    except Exception:
        return ""


def setup_companion_routes() -> APIRouter:
    router = APIRouter(prefix="/api/companion", tags=["companion"])

    @router.get("/ping")
    def ping(request: Request):
        """Cheap, auth-validated health check. A 200 with ok=true confirms the
        host/port and credential are valid; middleware returns 401 otherwise."""
        from core.constants import APP_VERSION
        return {
            "ok": True,
            "name": "odysseus",
            "version": APP_VERSION,
            "auth": "token" if getattr(request.state, "api_token", False) else "session",
        }

    @router.get("/info")
    def info(request: Request):
        """Server identity + coarse capability flags. `owner` is the caller's own
        identity (the token's owner for bearer callers)."""
        from core.constants import APP_VERSION
        return {
            "name": "odysseus",
            "version": APP_VERSION,
            "owner": token_owner(request),
            "capabilities": {"chat": True, "streaming": True},
        }

    @router.get("/models")
    def models(request: Request):
        """LLM model endpoints the CALLER can use.

        The stock /api/models route scopes to get_current_user, which for a
        bearer token is the sandboxed pseudo-user "api" (owns nothing). Here we
        scope to the token's real owner instead, plus legacy null-owner shared
        rows -- the same rule as owner_filter. Read-only; never returns api_key
        material.
        """
        import json as _json

        from core.database import SessionLocal, ModelEndpoint
        from src.endpoint_resolver import build_chat_url

        owner = token_owner(request)
        out = []
        db = SessionLocal()
        try:
            q = db.query(ModelEndpoint).filter(
                ModelEndpoint.is_enabled == True,  # noqa: E712
                (ModelEndpoint.model_type == "llm") | (ModelEndpoint.model_type == None),  # noqa: E711
            )
            if owner:
                q = q.filter((ModelEndpoint.owner == owner) | (ModelEndpoint.owner == None))  # noqa: E711
            for ep in q.all():
                if not owner_can_see(ep.owner, owner):
                    continue
                try:
                    model_ids = _json.loads(ep.cached_models) if ep.cached_models else []
                except (ValueError, TypeError):
                    model_ids = []
                try:
                    hidden = set(_json.loads(ep.hidden_models)) if ep.hidden_models else set()
                except (ValueError, TypeError):
                    hidden = set()
                model_ids = [m for m in model_ids if m not in hidden]
                try:
                    chat_url = build_chat_url(ep.base_url)
                except Exception:
                    chat_url = ep.base_url
                out.append({
                    "endpoint_id": ep.id,
                    "name": ep.name,
                    "endpoint_url": chat_url,
                    "models": model_ids,
                    "supports_tools": ep.supports_tools,
                })
        finally:
            db.close()
        return {"endpoints": out}

    @router.get("/pair")
    def pair_page(request: Request):
        """Admin-only pairing page. Renders a form that POSTs to mint a code.

        A GET never mints a credential: SameSite=Lax session cookies ride
        top-level GET navigations, so minting on GET would be triggerable by a
        link or <img> (CSRF). The actual mint is the POST handler below.
        """
        require_admin(request)
        page = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pair a device</title>
<style>
  body{font-family:-apple-system,system-ui,sans-serif;max-width:520px;margin:48px auto;padding:0 20px;color:#e8e8e8;background:#16161a}
  .card{background:#1f1f25;border:1px solid #2c2c35;border-radius:14px;padding:28px;text-align:center}
  button{background:#7c9cff;color:#0e0e12;border:none;border-radius:10px;padding:12px 20px;font-size:15px;font-weight:600;cursor:pointer}
</style></head>
<body><div class="card">
  <h2>Pair a device</h2>
  <p>Generate a one-time pairing code (a chat-scoped API token) for a LAN client.</p>
  <form method="POST" action="/api/companion/pair">
    <button type="submit">Generate pairing code</button>
  </form>
  <p style="color:#8a8a96;font-size:12px;margin-top:18px">Admin only. Each code mints a new token, shown once. Manage or revoke under Settings &rarr; API tokens.</p>
</div></body></html>"""
        return HTMLResponse(page)

    @router.post("/pair")
    def pair_create(request: Request):
        """Mint a pairing code. Admin-cookie only; CSRF-safe because the
        SameSite=Lax session cookie is not sent on a cross-site POST (same
        protection as POST /api/tokens). Minting invalidates the token cache so
        the code works immediately, no restart. `?format=json` returns the
        payload for an in-app pairing screen."""
        require_admin(request)
        owner = get_current_user(request)
        invalidate = getattr(request.app.state, "invalidate_token_cache", None)
        token_id, raw_token = mint_pairing_token(owner, invalidate)

        hosts = _pairing.lan_ip_candidates()
        host = hosts[0] if hosts else "127.0.0.1"
        port = request.url.port or _pairing.default_port()
        payload = _pairing.pairing_payload(host, port, raw_token)
        qr = _pairing.pairing_qr_png_data_uri(payload)
        qr_ok = bool(qr and qr.startswith("data:image/png;base64,"))

        if (request.query_params.get("format") or "").lower() == "json":
            return {
                "host": host,
                "port": port,
                "token": raw_token,
                "token_id": token_id,
                "hosts": hosts,
                "payload": payload,
                "qr": qr if qr_ok else None,
            }

        import json as _json
        payload_json = _json.dumps(payload, separators=(",", ":"))
        # Only ever emit a known PNG data-URI into the src; every other value is
        # html.escaped.
        qr_block = (
            f'<img src="{html.escape(qr)}" alt="Pairing QR" width="260" height="260">'
            if qr_ok else "<p><em>QR rendering unavailable -- enter the details manually.</em></p>"
        )
        page = f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pairing code</title>
<style>
  body{{font-family:-apple-system,system-ui,sans-serif;max-width:520px;margin:40px auto;padding:0 20px;color:#e8e8e8;background:#16161a}}
  .card{{background:#1f1f25;border:1px solid #2c2c35;border-radius:14px;padding:24px;text-align:center}}
  code{{background:#0e0e12;padding:2px 6px;border-radius:6px;word-break:break-all}}
  .row{{text-align:left;margin:10px 0;font-size:14px;color:#bdbdc7}}
  .warn{{color:#e0a85e;font-size:13px;margin-top:18px}}
</style></head>
<body><div class="card">
  <h2>Pairing code</h2>
  {qr_block}
  <div class="row"><strong>Host:</strong> <code>{html.escape(host)}</code></div>
  <div class="row"><strong>Port:</strong> <code>{html.escape(str(port))}</code></div>
  <div class="row"><strong>Token:</strong> <code>{html.escape(raw_token)}</code></div>
  <div class="row"><strong>Payload:</strong> <code>{html.escape(payload_json)}</code></div>
  <p class="warn">Shown once. This grants chat access to your Odysseus; revoke it
  in Settings &rarr; API tokens (id <code>{html.escape(token_id)}</code>). The
  device must be on the same network, and the server must bind to your LAN.</p>
</div></body></html>"""
        return HTMLResponse(page)

    @router.post("/message")
    async def companion_message(request: Request):
        """Generate a short AI companion message based on check-in data.
        
        Optionally accepts:
          - mood_delta: int (-10..10) to adjust tone
          - messages: list of {role, content} for conversation history
        
        Uses the user's configured default chat model. Returns a generic
        fallback if no model is configured or the LLM call fails.
        """
        body = await request.json()
        mood = body.get("mood", 5)
        energy = body.get("energy", 5)
        sleep_hours = body.get("sleep", 0)
        conditions = body.get("conditions", [])
        time_of_day = body.get("time_of_day", "")
        mood_delta = body.get("mood_delta")
        messages = body.get("messages")

        url, model, headers = _resolve_companion_endpoint(owner=token_owner(request))
        if not url or not model:
            return {"message": "I'm here when you need me."}

        owner = token_owner(request)

        # ── Profile context helper for message endpoints ──
        def _profile_context_msg():
            ctx = ""
            try:
                from core.database import SessionLocal as _S, CompanionProfile as _CP
                _dbp = _S()
                _prof = _dbp.query(_CP).filter(_CP.owner == owner).first()
                if _prof:
                    parts = []
                    if _prof.mbti_type:
                        parts.append(f"MBTI: {_prof.mbti_type}")
                    if _prof.enneagram_type:
                        parts.append(f"Enneagram: {_prof.enneagram_type}")
                    if _prof.sleep_schedule_start and _prof.sleep_schedule_end:
                        parts.append(f"sleep schedule {_prof.sleep_schedule_start}-{_prof.sleep_schedule_end}")
                    if _prof.additional_conditions:
                        parts.append(f"additional context: {_prof.additional_conditions}")
                    if parts:
                        ctx = "\n\nProfile context (use only if conversation naturally goes there, don't force): " + "; ".join(parts) + "."
                _dbp.close()
            except Exception:
                pass
            return ctx

        if messages:
            # Conversation mode — preserve existing messages, just respond
            pattern_context = ""
            patterns = detect_patterns(owner)
            if not patterns.get("insufficient_data"):
                parts = []
                if patterns.get("avg_sleep_7d") is not None:
                    parts.append(f"sleep this week averaging {patterns['avg_sleep_7d']}h")
                if patterns.get("mood_trend"):
                    parts.append(f"mood has been {patterns['mood_trend']}")
                if patterns.get("checkin_streak_days") and patterns["checkin_streak_days"] >= 3:
                    parts.append(f"they've checked in {patterns['checkin_streak_days']} days straight")
                if parts:
                    pattern_context = (
                        f"\n\nRelevant context (only reference if the conversation naturally goes there; "
                        f"never volunteer unprompted): {'; '.join(parts)}."
                    )

            lifestyle_context = ""
            try:
                ls_ctx = get_current_lifestyle_context(owner)
                cb = ls_ctx.get("current_block")
                nb = ls_ctx.get("next_block")
                if cb and cb["type"] in ("work", "school"):
                    lifestyle_context = f"\n\nThe user is currently at {cb['label']} until {cb['ends_at']}."
                if nb and nb["type"] == "free":
                    lifestyle_context += f"\nThey have free time at {nb['starts_at']} — a good window for tasks."
            except Exception:
                pass

            # ── Memory context (conversation mode) ──
            mem_ctx_conv = ""
            try:
                mem_ctx_conv = get_relevant_memory_context(owner, limit=3)
            except Exception:
                pass

            system_prompt = (
                "You are a calm, supportive companion. Keep your responses warm, "
                "brief (1-3 sentences), and conversational. Never be overly "
                "cheerful. Feel like a quiet, understanding presence."
            ) + pattern_context + _profile_context_msg() + lifestyle_context + mem_ctx_conv
            conversation = [{"role": "system", "content": system_prompt}]
            for msg in messages:
                conversation.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})
            try:
                from src.llm_core import llm_call
                response = llm_call(url, model, conversation, temperature=0.7, max_tokens=120, headers=headers)
                return {"message": response.strip()}
            except Exception as e:
                import logging
                logging.getLogger("companion").error(f"/message chat call failed: {e}", exc_info=True)
                return {"message": "I'm here."}

        tone_modifier = ""
        if mood_delta is not None:
            if mood_delta <= -3:
                tone_modifier = " The user's mood dropped significantly. Be extra gentle and supportive."
            elif mood_delta >= 3:
                tone_modifier = " The user's mood improved noticeably. Be warm and affirming."

        # ── Lightweight pattern context (only if highly relevant) ──
        pattern_hint = ""
        patterns = detect_patterns(owner)
        if not patterns.get("insufficient_data"):
            if patterns.get("mood_trend") == "declining" and mood <= 4:
                pattern_hint = (
                    " (Note: their mood has been trending down this week, "
                    "and today is also low — be especially gentle.)"
                )
            elif (patterns.get("checkin_streak_days") or 0) >= 7:
                pattern_hint = (
                    f" (Note: they've checked in {patterns['checkin_streak_days']} days "
                    f"straight — a very brief nod to consistency is fine if it fits.)"
                )

        system_prompt = (
            "You are a calm, supportive companion. Generate ONE short sentence "
            "(max 20 words) that acknowledges the user's current state. Be warm "
            "but not overly cheerful. Never give advice or ask questions. "
            "Feel like a quiet presence, not a coach." + tone_modifier + pattern_hint
        )

        profile_ctx = _profile_context_msg()
        user_prompt = (
            f"User state: mood {mood}/10, energy {energy}/10, slept {sleep_hours}h. "
            f"Conditions: {conditions}. Time of day: {time_of_day}."
        )
        if profile_ctx:
            user_prompt += profile_ctx

        # ── Lifestyle context (only for work/school blocks) ──
        try:
            ls_ctx = get_current_lifestyle_context(owner)
            cb = ls_ctx.get("current_block")
            if cb and cb["type"] in ("work", "school"):
                user_prompt += f"\n\nThe user is currently at {cb['label']} (until {cb['ends_at']})."
                if energy <= 3:
                    user_prompt += f" Tough to push through a long day at {cb['label']} when energy's low — small wins count."
        except Exception:
            pass

        # ── Memory context (bar message) ──
        try:
            mem_ctx_bar = get_relevant_memory_context(owner, limit=3)
            if mem_ctx_bar:
                user_prompt += mem_ctx_bar
        except Exception:
            pass

        try:
            from src.llm_core import llm_call
            response = llm_call(url, model, [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ], temperature=0.7, max_tokens=60, headers=headers)
            return {"message": response.strip()}
        except Exception as e:
            import logging
            logging.getLogger("companion").error(f"/message AI call failed: {e}", exc_info=True)
            return {"error": "AI call failed", "detail": str(e)}

    @router.get("/profile")
    def get_profile(request: Request):
        """Get the companion profile for the current user.

        If the current owner's profile has no system_info, falls back to
        the first profile in the database that has one (handles the case
        where the collector was run under a different owner id)."""
        from core.database import SessionLocal, CompanionProfile

        owner = token_owner(request)
        if not owner:
            return {}
        db = SessionLocal()
        try:
            profile = db.query(CompanionProfile).filter(
                CompanionProfile.owner == owner
            ).first()
            if not profile:
                return {}

            system_info = json.loads(profile.system_info) if profile.system_info else None
            system_info_updated_at = profile.system_info_updated_at

            if not system_info:
                fallback = db.query(CompanionProfile).filter(
                    CompanionProfile.system_info.isnot(None),
                    CompanionProfile.system_info != ""
                ).first()
                if fallback and fallback.owner != owner:
                    system_info = json.loads(fallback.system_info)
                    system_info_updated_at = fallback.system_info_updated_at

            return {
                "display_name": profile.display_name or "",
                "timezone": profile.timezone or "UTC",
                "conditions": json.loads(profile.conditions or "[]"),
                "energy_pattern": profile.energy_pattern or "Variable",
                "ideal_sleep_hours": profile.ideal_sleep_hours or 8,
                "birthday": profile.birthday,
                "mbti_type": profile.mbti_type,
                "enneagram_type": profile.enneagram_type,
                "additional_conditions": profile.additional_conditions,
                "sleep_schedule_start": profile.sleep_schedule_start,
                "sleep_schedule_end": profile.sleep_schedule_end,
                "system_info": system_info,
                "system_info_updated_at": system_info_updated_at.isoformat() if system_info_updated_at else None,
            }
        finally:
            db.close()

    @router.post("/profile")
    async def upsert_profile(request: Request):
        """Create or update the companion profile."""
        from core.database import SessionLocal, CompanionProfile

        body = await request.json()
        owner = token_owner(request)
        if not owner:
            return {"ok": False}
        db = SessionLocal()
        try:
            profile = db.query(CompanionProfile).filter(
                CompanionProfile.owner == owner
            ).first()
            if not profile:
                profile = CompanionProfile(
                    id=str(uuid.uuid4()),
                    owner=owner,
                )
                db.add(profile)
            profile.display_name = body.get("display_name", "")
            profile.timezone = body.get("timezone", "UTC")
            profile.conditions = json.dumps(body.get("conditions", []))
            profile.energy_pattern = body.get("energy_pattern", "Variable")
            profile.ideal_sleep_hours = body.get("ideal_sleep_hours", 8)
            profile.birthday = body.get("birthday")
            profile.mbti_type = body.get("mbti_type")
            profile.enneagram_type = body.get("enneagram_type")
            profile.additional_conditions = body.get("additional_conditions")
            profile.sleep_schedule_start = body.get("sleep_schedule_start")
            profile.sleep_schedule_end = body.get("sleep_schedule_end")
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    # ── System info ──

    SYSINFO_TOKEN_HEADER = "X-Odysseus-Token"
    SYSINFO_TOKEN_VALUE = "sysinfo"

    @router.post("/sysinfo")
    async def receive_sysinfo(request: Request):
        """Receive system info payload from the collector script.
        Auth-exempt; validated by X-Odysseus-Token: sysinfo header."""
        from core.database import SessionLocal, CompanionProfile

        token = request.headers.get(SYSINFO_TOKEN_HEADER, "")
        if token != SYSINFO_TOKEN_VALUE:
            return JSONResponse(status_code=403, content={"error": "Invalid token"})

        body = await request.json()
        owner = body.get("owner", "").strip()
        system_info = body.get("system_info")
        if not owner or not system_info:
            return JSONResponse(status_code=400, content={"error": "Missing owner or system_info"})

        db = SessionLocal()
        try:
            profile = db.query(CompanionProfile).filter(
                CompanionProfile.owner == owner
            ).first()
            if not profile:
                profile = CompanionProfile(
                    id=str(uuid.uuid4()),
                    owner=owner,
                )
                db.add(profile)
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            profile.system_info = json.dumps(system_info)
            profile.system_info_updated_at = now

            # Also save under the last active user (if different), so the
            # data is visible to whoever is currently logged into the web UI.
            last_user = get_last_active_user()
            if last_user and last_user != owner:
                active = db.query(CompanionProfile).filter(
                    CompanionProfile.owner == last_user
                ).first()
                if not active:
                    active = CompanionProfile(
                        id=str(uuid.uuid4()),
                        owner=last_user,
                    )
                    db.add(active)
                active.system_info = json.dumps(system_info)
                active.system_info_updated_at = now

            db.commit()
            return {"ok": True}
        finally:
            db.close()

    @router.get("/sysinfo")
    def get_sysinfo(request: Request):
        """Return stored system info for the current user.
        Path is auth-exempt, so we manually validate the session cookie."""
        from routes.auth_routes import SESSION_COOKIE

        auth_manager = getattr(request.app.state, "auth_manager", None)
        if not auth_manager:
            return JSONResponse(status_code=503, content={"error": "Auth not configured"})

        token = request.cookies.get(SESSION_COOKIE, "")
        if not auth_manager.validate_token(token):
            return JSONResponse(status_code=401, content={"error": "Not authenticated"})

        owner = auth_manager.get_username_for_token(token)
        if not owner:
            return JSONResponse(status_code=401, content={"error": "Not authenticated"})

        from core.database import SessionLocal, CompanionProfile

        db = SessionLocal()
        try:
            profile = db.query(CompanionProfile).filter(
                CompanionProfile.owner == owner
            ).first()
            if not profile or not profile.system_info:
                return {"system_info": None, "updated_at": None}
            return {
                "system_info": json.loads(profile.system_info),
                "updated_at": profile.system_info_updated_at.isoformat() if profile.system_info_updated_at else None,
            }
        finally:
            db.close()

    _DIST_DIR = os.path.join(os.path.dirname(__file__), "..", "tools", "dist")
    _SOURCE_FILES = ["sysinfo_helpers.py", "sysinfo_collector.py",
                     "sysinfo_collector_silent.py", "BUILD_INSTRUCTIONS.txt"]

    @router.get("/sysinfo/downloads/status")
    def downloads_status(request: Request):
        """Return availability of each download variant."""
        return {
            "exe_normal": os.path.isfile(os.path.join(_DIST_DIR, "OdysseusSysInfo.exe")),
            "exe_silent": os.path.isfile(os.path.join(_DIST_DIR, "OdysseusSysInfo_Silent.exe")),
            "source_bundle": True,
        }

    @router.get("/sysinfo/downloads/exe")
    def download_exe(request: Request, variant: str = ""):
        """Serve a pre-built collector .exe."""
        filename = {"normal": "OdysseusSysInfo.exe",
                    "silent": "OdysseusSysInfo_Silent.exe"}.get(variant)
        if not filename:
            return JSONResponse(status_code=404, content={"error": "Invalid variant"})
        path = os.path.join(_DIST_DIR, filename)
        if not os.path.isfile(path):
            return JSONResponse(
                status_code=404,
                content={"error": "Not built yet — see source download for build instructions"},
            )
        return FileResponse(path, media_type="application/octet-stream",
                            filename=filename,
                            headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @router.get("/sysinfo/downloads/source")
    def download_source(request: Request):
        """Serve a ZIP of the collector source files."""
        tools_dir = os.path.join(os.path.dirname(__file__), "..", "tools")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name in _SOURCE_FILES:
                fpath = os.path.join(tools_dir, name)
                if os.path.isfile(fpath):
                    zf.write(fpath, arcname=name)
        buf.seek(0)
        return Response(content=buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition":
                                 'attachment; filename="odysseus_sysinfo_source.zip"'})


    # ── Pattern suggestion daily cache ──
    _pattern_suggestion_cache: dict = {}

    @router.get("/patterns")
    def get_patterns(request: Request):
        """Detect and return patterns from check-in and task data."""
        owner = token_owner(request)
        return detect_patterns(owner)

    @router.get("/patterns/suggestion")
    def get_pattern_suggestion(request: Request):
        """AI-generated suggestion based on detected patterns (cached daily)."""
        owner = token_owner(request)
        from datetime import date as _d
        cache_key = f"{owner}:{_d.today().isoformat()}"
        cached = getattr(get_pattern_suggestion, "_suggestion_cache", {})
        if cache_key in cached:
            return {"suggestion": cached[cache_key]}

        patterns = detect_patterns(owner)
        if patterns.get("insufficient_data"):
            return {"suggestion": "Complete more check-ins to unlock personalized insights."}

        # Build a compact summary for the AI
        summary_parts = []
        if patterns.get("avg_mood_7d") is not None:
            summary_parts.append(f"avg-mood-7d:{patterns['avg_mood_7d']:.1f}")
        if patterns.get("avg_energy_7d") is not None:
            summary_parts.append(f"avg-energy-7d:{patterns['avg_energy_7d']:.1f}")
        if patterns.get("avg_sleep_7d") is not None:
            summary_parts.append(f"avg-sleep-7d:{patterns['avg_sleep_7d']:.1f}")
        if "best_mood_hour" in patterns:
            summary_parts.append(f"best-mood-hour:{patterns['best_mood_hour']}")
        if patterns.get("mood_trend"):
            summary_parts.append(f"mood-trend:{patterns['mood_trend']}")
        if patterns.get("mood_volatility"):
            summary_parts.append(f"volatility:{patterns['mood_volatility']}")
        if patterns.get("sleep_regularity"):
            summary_parts.append(f"sleep-regularity:{patterns['sleep_regularity']}")
        if (patterns.get("checkin_streak_days") or 0) >= 2:
            summary_parts.append(f"streak:{patterns['checkin_streak_days']}d")
        summary = "; ".join(summary_parts)

        url, model, headers = _resolve_companion_endpoint(owner=owner)
        if not url or not model:
            return {"suggestion": ""}

        system_prompt = (
            "You are a thoughtful insights bot. Based on the user's pattern "
            "summary, suggest ONE simple, actionable tweak they could try today "
            "(1 sentence, max 20 words). Be warm, not clinical."
        )
        try:
            from src.llm_core import llm_call
            suggestion = llm_call(url, model, [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Pattern summary: {summary}"}
            ], temperature=0.7, max_tokens=60, headers=headers).strip()
            if not suggestion:
                suggestion = "Try taking a moment to breathe between tasks."
            cached[cache_key] = suggestion
            get_pattern_suggestion._suggestion_cache = cached
            return {"suggestion": suggestion}
        except Exception:
            return {"suggestion": "Try taking a moment to breathe between tasks."}

    @router.get("/lifestyle")
    def get_lifestyle(request: Request):
        """Get the user's weekday/weekend schedule blocks."""
        from core.database import SessionLocal, CompanionLifestyle
        owner = token_owner(request)
        if not owner:
            return {"weekday_schedule": [], "weekend_schedule": []}
        db = SessionLocal()
        try:
            row = db.query(CompanionLifestyle).filter(
                CompanionLifestyle.owner == owner
            ).first()
            if not row:
                return {"weekday_schedule": [], "weekend_schedule": []}
            return {
                "weekday_schedule": json.loads(row.weekday_schedule) if row.weekday_schedule else [],
                "weekend_schedule": json.loads(row.weekend_schedule) if row.weekend_schedule else [],
            }
        finally:
            db.close()

    @router.post("/lifestyle")
    async def save_lifestyle(request: Request):
        """Save the user's weekday/weekend schedule blocks (full replace)."""
        import uuid as _uuid
        from core.database import SessionLocal, CompanionLifestyle
        body = await request.json()
        owner = token_owner(request)
        if not owner:
            return {"ok": False}
        db = SessionLocal()
        try:
            row = db.query(CompanionLifestyle).filter(
                CompanionLifestyle.owner == owner
            ).first()
            if not row:
                row = CompanionLifestyle(id=_uuid.uuid4().hex, owner=owner)
                db.add(row)
            row.weekday_schedule = json.dumps(body.get("weekday_schedule", []))
            row.weekend_schedule = json.dumps(body.get("weekend_schedule", []))
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    # ── "Noted" toast endpoint (used by companion JS polling) ────────────

    @router.get("/memory/latest")
    def get_latest_memory(request: Request):
        """Return the most recently extracted fact (in-memory), or null."""
        owner = token_owner(request)
        if not owner:
            return {"fact": None}
        fact = _get_latest_companion_fact()
        if fact:
            return {"fact": {"id": fact.get("id"), "content": fact.get("content"),
                             "category": fact.get("category")}}
        return {"fact": None}

    @router.get("/checkins")
    def get_checkins(request: Request):
        """Get all check-ins for the current user."""
        from core.database import SessionLocal, CompanionCheckin

        owner = token_owner(request)
        if not owner:
            return {"entries": {}}
        db = SessionLocal()
        try:
            rows = db.query(CompanionCheckin).filter(
                CompanionCheckin.owner == owner
            ).all()
            entries = {}
            for row in rows:
                entries[row.date] = {
                    "mood": row.mood,
                    "energy": row.energy,
                    "sleep": row.sleep_hours,
                    "text": row.text or "",
                    "message": row.message or "",
                    "briefing": row.briefing or "",
                    "timestamp": row.created_at.isoformat() if row.created_at else "",
                    "mid_mood": row.mid_mood,
                    "mid_energy": row.mid_energy,
                    "mid_feeling": row.mid_feeling or "",
                    "eod_done": row.eod_done or "",
                    "eod_blocked": row.eod_blocked or "",
                    "eod_tomorrow": row.eod_tomorrow or "",
                    "eod_rating": row.eod_rating,
                    "eod_message": row.eod_message or "",
                }
            return {"entries": entries}
        finally:
            db.close()

    @router.post("/checkins")
    async def upsert_checkin(request: Request):
        """Create or update a check-in entry."""
        from core.database import SessionLocal, CompanionCheckin

        body = await request.json()
        owner = token_owner(request)
        if not owner:
            return {"ok": False}
        date = body.get("date", "")
        if not date:
            return {"ok": False}
        db = SessionLocal()
        try:
            entry = db.query(CompanionCheckin).filter(
                CompanionCheckin.owner == owner,
                CompanionCheckin.date == date
            ).first()
            if not entry:
                entry = CompanionCheckin(
                    id=str(uuid.uuid4()),
                    owner=owner,
                    date=date,
                )
                db.add(entry)
            entry.mood = body.get("mood", 5)
            entry.energy = body.get("energy", 5)
            entry.sleep_hours = body.get("sleep", 0)
            entry.text = body.get("text", "")
            entry.message = body.get("message", "")
            briefing = body.get("briefing")
            if briefing is not None:
                entry.briefing = briefing
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    @router.patch("/checkins/today")
    async def patch_today_checkin(request: Request):
        """Partial-update today's check-in. Accepts any subset of:
        mid_mood, mid_energy, mid_feeling, eod_done, eod_blocked,
        eod_tomorrow, eod_rating, eod_message.
        Creates today's row if it does not yet exist.
        """
        from core.database import SessionLocal, CompanionCheckin
        from datetime import date as _date

        body = await request.json()
        owner = token_owner(request)
        if not owner:
            return {"ok": False}
        today_str = _date.today().isoformat()
        db = SessionLocal()
        try:
            entry = db.query(CompanionCheckin).filter(
                CompanionCheckin.owner == owner,
                CompanionCheckin.date == today_str
            ).first()
            if not entry:
                entry = CompanionCheckin(
                    id=str(uuid.uuid4()),
                    owner=owner,
                    date=today_str,
                    mood=5,
                    energy=5,
                    sleep_hours=0,
                )
                db.add(entry)
            for field in ("mid_mood", "mid_energy", "mid_feeling",
                         "eod_done", "eod_blocked", "eod_tomorrow",
                         "eod_rating", "eod_message"):
                if field in body:
                    setattr(entry, field, body[field])
            db.commit()

            # Stage 7D: EOD batch memory extraction (fire-and-forget)
            eod_done_val = body.get("eod_done", "") or ""
            eod_blocked_val = body.get("eod_blocked", "") or ""
            eod_tomorrow_val = body.get("eod_tomorrow", "") or ""
            has_eod_content = any(len(v.strip()) > 10 for v in (eod_done_val, eod_blocked_val, eod_tomorrow_val))
            if has_eod_content and owner:
                import asyncio as _asyncio
                _asyncio.ensure_future(_extract_eod_memory_facts(
                    owner, eod_done_val, eod_blocked_val, eod_tomorrow_val,
                ))

            return {
                "ok": True,
                "entry": {
                    "mid_mood": entry.mid_mood,
                    "mid_energy": entry.mid_energy,
                    "mid_feeling": entry.mid_feeling or "",
                    "eod_done": entry.eod_done or "",
                    "eod_blocked": entry.eod_blocked or "",
                    "eod_tomorrow": entry.eod_tomorrow or "",
                    "eod_rating": entry.eod_rating,
                    "eod_message": entry.eod_message or "",
                }
            }
        finally:
            db.close()

    @router.get("/tasks")
    def get_tasks(request: Request):
        """Get tasks for the current user."""
        from core.database import SessionLocal, CompanionTask

        owner = token_owner(request)
        if not owner:
            return {"tasks": []}
        date = request.query_params.get("date", "")
        db = SessionLocal()
        try:
            q = db.query(CompanionTask).filter(
                CompanionTask.owner == owner
            )
            if date:
                q = q.filter(CompanionTask.date == date)
            rows = q.order_by(CompanionTask.sort_order).all()
            tasks = []
            for row in rows:
                sub = []
                if row.sub_steps:
                    try:
                        sub = json.loads(row.sub_steps)
                    except Exception:
                        sub = []
                tasks.append({
                    "id": row.id,
                    "title": row.title,
                    "estimated_minutes": row.estimated_minutes,
                    "priority": row.priority,
                    "status": row.status,
                    "sort_order": row.sort_order,
                    "date": row.date,
                    "carried_over": row.carried_over or False,
                    "sub_steps": sub,
                    "created_at": row.created_at.isoformat() if row.created_at else "",
                    "completed_at": row.completed_at.isoformat() if row.completed_at else "",
                    "due_time": row.due_time,
                    "reminder_sent_pre": row.reminder_sent_pre or False,
                    "reminder_sent_due": row.reminder_sent_due or False,
                    "started_at": row.started_at.isoformat() if row.started_at else "",
                    "last_progress_check_ts": row.last_progress_check_ts.isoformat() if row.last_progress_check_ts else "",
                })
            return {"tasks": tasks}
        finally:
            db.close()

    @router.post("/tasks")
    async def create_task(request: Request):
        """Create a new task."""
        from core.database import SessionLocal, CompanionTask

        body = await request.json()
        owner = token_owner(request)
        if not owner:
            return {"ok": False}
        db = SessionLocal()
        try:
            max_order = db.query(CompanionTask.sort_order).filter(
                CompanionTask.owner == owner,
                CompanionTask.date == body.get("date", "")
            ).order_by(CompanionTask.sort_order.desc()).first()
            sort_order = (max_order[0] or 0) + 1 if max_order else 0
            task = CompanionTask(
                id=str(uuid.uuid4()),
                owner=owner,
                title=body.get("title", "")[:80],
                estimated_minutes=body.get("estimated_minutes"),
                priority=body.get("priority", "Medium"),
                status="todo",
                sort_order=sort_order,
                date=body.get("date", ""),
                carried_over=body.get("carried_over", False),
                sub_steps=json.dumps(body.get("sub_steps", [])),
                due_time=body.get("due_time"),
            )
            db.add(task)
            db.commit()
            return {"ok": True, "id": task.id}
        finally:
            db.close()

    @router.patch("/tasks/{task_id}")
    async def update_task(task_id: str, request: Request):
        """Update a task (status, title, sub_steps, etc.)."""
        from core.database import SessionLocal, CompanionTask
        from datetime import datetime, timezone

        body = await request.json()
        owner = token_owner(request)
        if not owner:
            return {"ok": False}
        db = SessionLocal()
        try:
            task = db.query(CompanionTask).filter(
                CompanionTask.id == task_id,
                CompanionTask.owner == owner
            ).first()
            if not task:
                return {"ok": False, "error": "not found"}
            if "title" in body:
                task.title = body["title"][:80]
            if "estimated_minutes" in body:
                task.estimated_minutes = body["estimated_minutes"]
            if "priority" in body:
                task.priority = body["priority"]
            if "status" in body:
                task.status = body["status"]
                if body["status"] == "done" and not task.completed_at:
                    task.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                elif body["status"] != "done":
                    task.completed_at = None
            if "sort_order" in body:
                task.sort_order = body["sort_order"]
            if "carried_over" in body:
                task.carried_over = body["carried_over"]
            if "sub_steps" in body:
                task.sub_steps = json.dumps(body["sub_steps"])
            if "due_time" in body:
                task.due_time = body["due_time"]
            if "reminder_sent_pre" in body:
                task.reminder_sent_pre = body["reminder_sent_pre"]
            if "reminder_sent_due" in body:
                task.reminder_sent_due = body["reminder_sent_due"]
            if "last_progress_check_ts" in body:
                val = body["last_progress_check_ts"]
                task.last_progress_check_ts = datetime.fromisoformat(val) if val else None
            if "started_at" in body:
                val = body["started_at"]
                task.started_at = datetime.fromisoformat(val) if val else None
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    @router.post("/briefing")
    async def generate_briefing(request: Request):
        """Generate an AI morning briefing based on check-in data."""
        body = await request.json()
        mood = body.get("mood", 5)
        energy = body.get("energy", 5)
        sleep_hours = body.get("sleep", 0)
        conditions = body.get("conditions", [])
        energy_pattern = body.get("energy_pattern", "Variable")
        pending_tasks = body.get("pending_tasks", "")
        time_of_day = body.get("time_of_day", "")

        url, model, headers = _resolve_companion_endpoint(owner=token_owner(request))
        if not url or not model:
            return {"briefing": "Good morning. Take a moment to breathe and set your intention for the day."}

        condition_note = ""
        if conditions:
            if "adhd" in conditions:
                condition_note += " The user has ADHD. Suggest max 3 concrete priorities. "
            if any(c in ("depression", "anxiety") for c in conditions):
                condition_note += " Be gentle and encourage small wins. "
            if "chronic_fatigue" in conditions:
                condition_note += " The user has chronic fatigue — keep it light. "

        energy_note = ""
        if energy < 4:
            energy_note = " Energy is very low. Suggest a light day, one thing at a time."
        elif energy > 7:
            energy_note = " Energy is high. Encourage tackling harder tasks."

        system_prompt = (
            "You are a warm, supportive morning companion. Generate a SHORT briefing "
            "(4-6 sentences max). Acknowledge how the user is feeling. "
            "End with one grounding sentence."
        )

        user_prompt = (
            f"Time: {time_of_day}. Mood: {mood}/10. Energy: {energy}/10. "
            f"Slept {sleep_hours}h. Pattern: {energy_pattern}.{condition_note}{energy_note}"
        )
        if pending_tasks:
            user_prompt += f" Pending tasks from yesterday: {pending_tasks}"

        # ── Profile context ──
        try:
            from core.database import SessionLocal as _S, CompanionProfile as _CP
            _dbp = _S()
            _prof = _dbp.query(_CP).filter(_CP.owner == token_owner(request)).first()
            if _prof:
                _profile_hints = []
                if _prof.birthday:
                    from datetime import date as _d
                    try:
                        _bd = _d.fromisoformat(_prof.birthday)
                        _today = _d.today()
                        _next_bd = _bd.replace(year=_today.year)
                        if _next_bd < _today:
                            _next_bd = _bd.replace(year=_today.year + 1)
                        _days_until = (_next_bd - _today).days
                        _is_near = _today.month == _bd.month and abs(_today.day - _bd.day) <= 3
                        if _days_until <= 3 or _is_near:
                            _profile_hints.append(f"The user's birthday is within 3 days ({_prof.birthday}). A gentle happy-birthday acknowledgement is fine, but don't overdo it.")
                    except ValueError:
                        pass
                if _prof.sleep_schedule_start and _prof.sleep_schedule_end:
                    _profile_hints.append(f"Usual sleep schedule: {_prof.sleep_schedule_start} to {_prof.sleep_schedule_end}.")
                if _prof.additional_conditions:
                    _profile_hints.append(f"Additional conditions: {_prof.additional_conditions}.")
                if _profile_hints:
                    user_prompt += "\n\nProfile context: " + " ".join(_profile_hints)
            _dbp.close()
        except Exception:
            pass

        # ── Lifestyle context ──
        try:
            owner_ls = token_owner(request)
            ls_ctx = get_current_lifestyle_context(owner_ls)
            if ls_ctx.get("current_block"):
                cb = ls_ctx["current_block"]
                user_prompt += f"\n\nUser is currently at/in: {cb['label']} until {cb['ends_at']}."
                if cb["type"] in ("work", "school"):
                    user_prompt += " Keep task suggestions realistic — they can't start big tasks right now."
            if ls_ctx.get("next_block"):
                nb = ls_ctx["next_block"]
                if nb["type"] == "meal":
                    user_prompt += f" After their current block, they've got {nb['label']} around {nb['starts_at']}."
                elif nb["type"] == "free":
                    user_prompt += f" They have free time coming up at {nb['starts_at']} — a good window for tasks."
        except Exception:
            pass

        # ── Pattern context ──
        owner = token_owner(request)
        patterns = detect_patterns(owner)
        if not patterns.get("insufficient_data"):
            context_hints = []
            if patterns.get("mood_trend") == "declining" and (patterns.get("checkin_streak_days") or 0) >= 3:
                context_hints.append(
                    "Note: user's mood has been trending down over the past week. "
                    "Be gentle, don't ignore it, but don't be heavy about it either — "
                    "maybe one acknowledging sentence."
                )
            if (patterns.get("task_completion_rate_7d") or 1) < 0.4:
                common_tasks = patterns.get("common_carry_over_tasks", [])
                if common_tasks:
                    context_hints.append(
                        f"User has been carrying over similar tasks repeatedly. "
                        f"If relevant, gently suggest breaking down or deprioritizing "
                        f"one of: {common_tasks}"
                    )
            if (patterns.get("checkin_streak_days") or 0) >= 7:
                context_hints.append(
                    f"User has checked in for {patterns['checkin_streak_days']} days straight — "
                    f"if it fits naturally, a brief acknowledgement of consistency is nice "
                    f"(not over the top)."
                )
            if context_hints:
                user_prompt += (
                    "\n\nRelevant context (use only if natural, don't force it): "
                    + " ".join(context_hints)
                )

        # ── Memory context ──
        try:
            mem_ctx = get_relevant_memory_context(token_owner(request), limit=3)
            if mem_ctx:
                user_prompt += mem_ctx
        except Exception:
            pass

        try:
            from src.llm_core import llm_call
            response = llm_call(url, model, [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ], temperature=0.7, max_tokens=200, headers=headers)
            return {"briefing": response.strip()}
        except Exception as e:
            import logging
            logging.getLogger("companion").error(f"/briefing AI call failed: {e}", exc_info=True)
            return {"error": "AI call failed", "detail": str(e)}

    @router.delete("/tasks/{task_id}")
    async def delete_task(task_id: str, request: Request):
        """Delete a task."""
        from core.database import SessionLocal, CompanionTask

        owner = token_owner(request)
        if not owner:
            return {"ok": False}
        db = SessionLocal()
        try:
            task = db.query(CompanionTask).filter(
                CompanionTask.id == task_id,
                CompanionTask.owner == owner
            ).first()
            if not task:
                return {"ok": False, "error": "not found"}
            db.delete(task)
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    @router.post("/tasks/prioritize")
    async def prioritize_tasks(request: Request):
        """AI-powered task prioritization. Returns a JSON array with suggested
        task order and one-line reasoning per task."""
        body = await request.json()
        tasks_in = body.get("tasks", [])
        mood = body.get("mood", 5)
        energy = body.get("energy", 5)
        conditions = body.get("conditions", [])

        url, model, headers = _resolve_companion_endpoint(owner=token_owner(request))
        if not url or not model or not tasks_in:
            return {"suggestions": []}

        tasks_json = "\n".join(
            f'- id={t.get("id","")} title="{t.get("title","")}" [priority: {t.get("priority","Medium")}]'
            + (f" ({t.get('estimated_minutes','')}m)" if t.get("estimated_minutes") else "")
            for t in tasks_in
        )
        system_prompt = (
            "You are a task prioritizer. Return ONLY a JSON array. "
            "Each item must have: "
            '{"id": string, "suggested_order": number, "reason": string}. '
            "The id field must be the EXACT id value sent in the task listing below — echo it back unchanged. "
            "Order tasks by what the user should do first given their current state. "
            "Do not return any other text, no markdown, no explanation outside the JSON."
        )
        user_prompt = (
            f"User state — mood: {mood}/10, energy: {energy}/10, "
            f"conditions: {conditions}.\nTasks:\n{tasks_json}"
        )

        try:
            from src.llm_core import llm_call
            response = llm_call(url, model, [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ], temperature=0.3, max_tokens=500, headers=headers)
        except Exception as e:
            import logging
            logging.getLogger("companion").error(f"/tasks/prioritize AI call failed: {e}", exc_info=True)
            return {"error": "AI call failed", "detail": str(e)}

        try:
            import json as _json
            suggestions = _json.loads(response.strip())
            if not isinstance(suggestions, list):
                return {"suggestions": []}
            return {"suggestions": suggestions}
        except Exception as e:
            return {"error": "AI response parse failed", "detail": str(e)}

    @router.post("/tasks/breakdown")
    async def breakdown_task(request: Request):
        """Break a task title into actionable steps using AI."""
        body = await request.json()
        title = body.get("title", "")

        if not title:
            return {"error": "Missing title"}

        url, model, headers = _resolve_companion_endpoint(owner=token_owner(request))
        if not url or not model:
            return {"error": "No model configured"}

        system_prompt = (
            "Return ONLY a single JSON array of strings. Each string is one concrete action "
            "step, max 10 words. 2 to 4 steps total. "
            "No markdown, no keys, no explanation outside the array. "
            "No markdown fences. No text before or after the array. No newlines between items."
        )
        user_prompt = f"Task: {title}"

        try:
            from src.llm_core import llm_call
            response = llm_call(url, model, [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ], temperature=0.3, max_tokens=300, headers=headers)
        except Exception as e:
            import logging
            logging.getLogger("companion").error(f"/tasks/breakdown AI call failed: {e}", exc_info=True)
            return {"error": "AI call failed", "detail": str(e)}

        try:
            import json as _json
            raw = response.strip()
            text = re.sub(r"```[a-z]*\n?", "", raw).strip()
            match = re.search(r'\[.*?\]', text, re.DOTALL)
            if not match:
                return {"error": "AI response parse failed", "detail": "No JSON array found"}
            steps = _json.loads(match.group())
            if not isinstance(steps, list):
                return {"steps": []}
            return {"steps": steps}
        except Exception as e:
            return {"error": "AI response parse failed", "detail": str(e)}

    return router
