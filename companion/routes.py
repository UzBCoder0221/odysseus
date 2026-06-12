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
import re

import json
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from core.middleware import require_admin
from src.auth_helpers import get_current_user

from companion import pairing as _pairing


def token_owner(request: Request) -> str | None:
    """The real owner to attribute a request to, for read-scoping.

    Cookie sessions resolve to the logged-in username via get_current_user.
    Bearer-token callers come through as the sandboxed pseudo-user "api"; their
    real owner is stamped on request.state.api_token_owner by the auth
    middleware. Returns None when no owner can be resolved.
    """
    if getattr(request.state, "api_token", False):
        return getattr(request.state, "api_token_owner", None)
    return get_current_user(request)


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

            system_prompt = (
                "You are a calm, supportive companion. Keep your responses warm, "
                "brief (1-3 sentences), and conversational. Never be overly "
                "cheerful. Feel like a quiet, understanding presence."
            ) + pattern_context
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

        user_prompt = (
            f"User state: mood {mood}/10, energy {energy}/10, slept {sleep_hours}h. "
            f"Conditions: {conditions}. Time of day: {time_of_day}."
        )

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
        """Get the companion profile for the current user."""
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
            return {
                "display_name": profile.display_name or "",
                "timezone": profile.timezone or "UTC",
                "conditions": json.loads(profile.conditions or "[]"),
                "energy_pattern": profile.energy_pattern or "Variable",
                "ideal_sleep_hours": profile.ideal_sleep_hours or 8,
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
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    @router.get("/patterns")
    def get_patterns(request: Request):
        """Detect and return patterns from check-in and task data."""
        owner = token_owner(request)
        return detect_patterns(owner)

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
