"""
Goals & Milestones API — /api/goals.

Owner resolution follows the companion route pattern (token_owner) which
checks BOTH cookie sessions (get_current_user) AND bearer tokens
(request.state.api_token_owner). This is the correct pattern — the older
note_routes and task_routes only check get_current_user, which misses
bearer-token callers and is the source of the known ownership bug.

When AUTH_ENABLED=false, token_owner returns "" (empty string = default
owner), matching require_user() in auth_helpers.
"""

import json
import os
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Query

from core.database import SessionLocal, Goal, Milestone, CompanionTask, GoalResearchConfig, GoalResearchResult
from core.models import ChatMessage
from src.auth_helpers import get_current_user

_log = logging.getLogger(__name__)


def token_owner(request: Request) -> str | None:
    """Resolve the real owner for read/write scoping.

    Cookie sessions -> get_current_user (the logged-in username).
    Bearer-token callers -> request.state.api_token_owner (the sandbox owner).
    When AUTH_ENABLED=false -> return "" (empty = default owner), same as
    require_user() in auth_helpers.
    """
    if getattr(request.state, "api_token", False):
        return getattr(request.state, "api_token_owner", None)
    u = get_current_user(request)
    if u is not None:
        return u
    if os.getenv("AUTH_ENABLED", "true").lower() == "false":
        return ""
    return None


# ─── LLM resolution (module-level so both API routes and research helper can reuse) ──


def _resolve_llm_config(owner=None):
    """Resolve (url, model, headers) for AI calls.

    1. Try the default_endpoint_id from settings (current behaviour).
    2. If that fails, fall back to the first enabled endpoint that
       has chat-capable models — this makes the feature work whether
       the default points to a local or Docker endpoint, as long as
       at least one enabled endpoint exists.
    Mirrors _resolve_companion_endpoint from companion/routes.py."""
    import json as _json
    try:
        from src.settings import load_settings
        settings = load_settings()
    except Exception:
        settings = {}
    ep_id = settings.get("default_endpoint_id", "")
    from core.database import SessionLocal, ModelEndpoint
    from src.endpoint_resolver import resolve_endpoint_runtime, build_chat_url, build_headers, _first_chat_model

    def _from_ep(ep):
        """Given a ModelEndpoint row, return (url, model, headers)."""
        base, api_key = resolve_endpoint_runtime(ep, owner=owner)
        url = build_chat_url(base)
        headers = build_headers(api_key, base)
        models = _json.loads(ep.cached_models) if ep.cached_models else []
        _NON_CHAT_EXTRA = ("embed",)
        chat_models = [m for m in models if not any(p in str(m).lower() for p in _NON_CHAT_EXTRA)]
        model = _first_chat_model(chat_models) or _first_chat_model(models) or ""
        if not model:
            return None, None, None
        return url, model, headers

    def _try_endpoint(q):
        ep = q.first()
        if not ep:
            return None, None, None
        try:
            return _from_ep(ep)
        except Exception:
            return None, None, None

    db = SessionLocal()
    try:
        if ep_id:
            q = db.query(ModelEndpoint).filter(
                ModelEndpoint.id == ep_id,
                ModelEndpoint.is_enabled == True
            )
            if owner:
                from src.auth_helpers import owner_filter
                q = owner_filter(q, ModelEndpoint, owner)
            url, model, headers = _try_endpoint(q)
            if url and model:
                return url, model, headers

        q2 = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
        if owner:
            from src.auth_helpers import owner_filter
            q2 = owner_filter(q2, ModelEndpoint, owner)
        for ep in q2.all():
            try:
                url, model, headers = _from_ep(ep)
                if url and model:
                    return url, model, headers
            except Exception:
                continue

        return None, None, None
    except Exception:
        return None, None, None
    finally:
        db.close()


def _parse_json_suggestions(reply: str, max_items: int = 10) -> list[dict]:
    """Defensive JSON parser for AI milestone suggestion responses.

    Handles bare JSON arrays, markdown-fenced arrays, and objects with
    a key containing an array. Returns validated list of
    {title, description, suggested_target_date} dicts.
    """
    import re as _re
    import logging as _logging
    _log = _logging.getLogger(__name__)

    if not reply:
        return []
    text = reply.strip()
    text = _re.sub(r"```[a-z]*\s*\n?", "", text).strip()
    suggestions = None
    # Try direct parse
    try:
        suggestions = json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try extracting JSON array via regex
    if suggestions is None or not isinstance(suggestions, list):
        arr_match = _re.search(r'\[.*\]', text, _re.DOTALL)
        if arr_match:
            try:
                suggestions = json.loads(arr_match.group())
            except (json.JSONDecodeError, TypeError):
                pass
    # Try wrapped in a key like {"milestones": [...]}
    if suggestions is None or not isinstance(suggestions, list):
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                for val in obj.values():
                    if isinstance(val, list):
                        suggestions = val
                        break
        except (json.JSONDecodeError, TypeError):
            pass

    if not suggestions or not isinstance(suggestions, list):
        return []

    validated = []
    for item in suggestions:
        if not isinstance(item, dict):
            continue
        ms_title = item.get("title", "").strip()
        if not ms_title or len(ms_title) > 80:
            continue
        ms_desc = item.get("description", "")
        if not isinstance(ms_desc, str):
            ms_desc = str(ms_desc) if ms_desc else ""
        ms_desc = ms_desc.strip()[:200]
        if not ms_desc:
            _log.warning("Suggestion '%s' missing description", ms_title)
        ms_date = item.get("suggested_target_date")
        if ms_date is not None:
            ms_date = str(ms_date).strip()[:10]
            if ms_date in ("null", "None"):
                ms_date = None
        validated.append({
            "title": ms_title[:80],
            "description": ms_desc or None,
            "suggested_target_date": ms_date,
        })

    return validated[:max_items]


def _perform_research(goal_id: str, owner: str, title: str, description: str,
                      depth: str = "moderate") -> dict:
    """Run research for a goal: web search + LLM synthesis.

    Uses threading.Thread(daemon=True) + .join(timeout) so the caller can
    enforce a bounded wall-clock limit — never ThreadPoolExecutor context
    manager (which does not enforce timeouts on the caller side).

    Returns the newly created GoalResearchResult as a dict.
    Never writes to milestones, tasks, or any other table.
    """
    import threading as _threading
    import uuid as _uuid

    result_holder = []

    def _worker():
        """Run inside a daemon thread — bounded by .join(timeout)."""
        try:
            _log.info("Research worker started for goal %s", goal_id)

            # 1. Web search
            from services.search.core import comprehensive_web_search
            query = f"{title}: {description[:200]}" if description else title
            search_out = comprehensive_web_search(query, max_pages=3, return_sources=True)
            if isinstance(search_out, tuple):
                raw_text, sources = search_out
            else:
                raw_text, sources = search_out, []
            source_notes = json.dumps([s.get("url", "") for s in (sources or [])][:10]) if sources else None
            _log.info("Research search done for goal %s: %d chars, %d sources",
                      goal_id, len(raw_text), len(sources))

            # 2. LLM synthesis — use requests.post directly (avoid asyncio.run
            # in a daemon thread, which can hang when httpx event loops conflict)
            url, model, headers = _resolve_llm_config(owner)
            if not url or not model:
                summary = raw_text[:5000]
            else:
                system_prompt = (
                    "You are a research analyst. Synthesize the following web search results "
                    "into a concise research brief for the user's goal. "
                    "Focus on relevant trends, best practices, potential pitfalls, "
                    "and actionable insights. Write 3-5 paragraphs. "
                    "Do NOT mention that you are an AI or that you searched the web — "
                    "just present the information."
                )
                user_prompt = (
                    f"Goal: {title}\nDescription: {description}\n\n"
                    f"Search results:\n{raw_text[:8000]}\n\n"
                    "Provide a research summary with key findings and recommendations."
                )
                try:
                    import requests as _requests
                    payload = {
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.3,
                        "max_tokens": 1500,
                    }
                    hdrs = {"Content-Type": "application/json"}
                    if headers:
                        hdrs.update(headers)
                    resp = _requests.post(url, json=payload, headers=hdrs, timeout=90)
                    if resp.status_code == 200:
                        data = resp.json()
                        reply = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                        summary = (reply or raw_text[:5000]).strip()
                    else:
                        _log.warning("LLM synthesis HTTP %s for goal %s — raw search fallback",
                                     resp.status_code, goal_id)
                        summary = raw_text[:5000]
                except Exception as e:
                    _log.warning("LLM synthesis failed for goal %s: %s — raw search fallback", goal_id, e)
                    summary = raw_text[:5000]

            # 3. Create result row (NEVER writes to milestones/tasks)
            db = SessionLocal()
            try:
                result = GoalResearchResult(
                    id=str(_uuid.uuid4()),
                    goal_id=goal_id,
                    summary=summary[:10000],
                    source_notes=source_notes[:2000] if source_notes else None,
                    status="pending",
                )
                db.add(result)

                # Update last_run_at on config (create if needed)
                cfg = db.query(GoalResearchConfig).filter(
                    GoalResearchConfig.goal_id == goal_id
                ).first()
                if not cfg:
                    cfg = GoalResearchConfig(
                        id=str(_uuid.uuid4()),
                        goal_id=goal_id,
                        trigger_mode="manual",
                    )
                    db.add(cfg)
                from datetime import datetime as _dt
                cfg.last_run_at = _dt.now(timezone.utc)

                db.commit()
                # Build return dict while session is open
                result_holder.append({
                    "id": result.id,
                    "goal_id": result.goal_id,
                    "summary": result.summary,
                    "source_notes": result.source_notes,
                    "status": result.status,
                    "created_at": result.created_at.isoformat() if result.created_at else None,
                })
                _log.info("Research completed for goal %s: result %s", goal_id, result.id)
            finally:
                db.close()
        except Exception as e:
            _log.exception("Research worker failed for goal %s", goal_id)
            result_holder.append({"error": str(e)})

    # Bounded wall-clock timeout via daemon thread + .join(timeout)
    # "light" depth → 60s, "moderate" → 120s
    timeout_s = 60 if depth == "light" else 120
    t = _threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=timeout_s)

    if not result_holder:
        raise RuntimeError(f"Research timed out after {timeout_s}s")

    if "error" in result_holder[0]:
        raise RuntimeError(result_holder[0]["error"])

    return result_holder[0]


def setup_goals_routes(session_manager=None) -> APIRouter:
    router = APIRouter(prefix="/api/goals", tags=["goals"])

    # ─── Goals ──────────────────────────────────────────────────────────

    @router.get("/goals")
    def list_goals(request: Request):
        """List all goals for the authenticated owner, sorted by sort_order."""
        owner = token_owner(request)
        if owner is None:
            return {"goals": []}
        db = SessionLocal()
        try:
            rows = (
                db.query(Goal)
                .filter(Goal.owner == owner)
                .order_by(Goal.sort_order)
                .all()
            )
            results = []
            for g in rows:
                ms_count = db.query(Milestone).filter(Milestone.goal_id == g.id).count()
                ms_done = (
                    db.query(Milestone)
                    .filter(Milestone.goal_id == g.id, Milestone.status == "completed")
                    .count()
                )
                results.append(
                    {
                        "id": g.id,
                        "title": g.title,
                        "description": g.description,
                        "target_date": g.target_date,
                        "status": g.status,
                        "sort_order": g.sort_order,
                        "created_at": g.created_at.isoformat() if g.created_at else "",
                        "updated_at": g.updated_at.isoformat() if g.updated_at else "",
                        "milestone_count": ms_count,
                        "milestones_done": ms_done,
                    }
                )
            return {"goals": results}
        finally:
            db.close()

    @router.post("/goals")
    async def create_goal(request: Request):
        owner = token_owner(request)
        if owner is None:
            raise HTTPException(401, "Not authenticated")
        body = await request.json()
        g = Goal(
            id=str(uuid.uuid4()),
            owner=owner,
            title=(body.get("title", "Untitled") or "")[:200],
            description=body.get("description"),
            target_date=body.get("target_date"),
            status=body.get("status", "active"),
            sort_order=body.get("sort_order", 0),
        )
        db = SessionLocal()
        try:
            db.add(g)
            db.commit()
            return {
                "ok": True,
                "goal": {
                    "id": g.id,
                    "title": g.title,
                    "description": g.description,
                    "target_date": g.target_date,
                    "status": g.status,
                    "sort_order": g.sort_order,
                    "created_at": g.created_at.isoformat() if g.created_at else "",
                },
            }
        finally:
            db.close()

    @router.get("/goals/{goal_id}")
    def get_goal(goal_id: str, request: Request):
        """Get one goal with its milestones nested."""
        owner = token_owner(request)
        if owner is None:
            raise HTTPException(401, "Not authenticated")
        db = SessionLocal()
        try:
            g = db.query(Goal).filter(Goal.id == goal_id, Goal.owner == owner).first()
            if not g:
                raise HTTPException(404, "Goal not found")
            milestones = (
                db.query(Milestone)
                .filter(Milestone.goal_id == g.id)
                .order_by(Milestone.sort_order)
                .all()
            )
            return {
                "goal": _goal_dict(g),
                "milestones": [_milestone_dict(m) for m in milestones],
            }
        finally:
            db.close()

    @router.patch("/goals/{goal_id}")
    async def update_goal(goal_id: str, request: Request):
        """Update a goal (title, description, target_date, status, sort_order)."""
        owner = token_owner(request)
        if owner is None:
            raise HTTPException(401, "Not authenticated")
        body = await request.json()
        db = SessionLocal()
        try:
            g = db.query(Goal).filter(Goal.id == goal_id, Goal.owner == owner).first()
            if not g:
                raise HTTPException(404, "Goal not found")
            if "title" in body:
                g.title = (body["title"] or "")[:200]
            if "description" in body:
                g.description = body["description"]
            if "target_date" in body:
                g.target_date = body["target_date"]
            if "status" in body:
                g.status = body["status"]
            if "sort_order" in body:
                g.sort_order = body["sort_order"]
            g.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
            return {"ok": True, "goal": _goal_dict(g)}
        finally:
            db.close()

    @router.delete("/goals/{goal_id}")
    async def delete_goal(goal_id: str, request: Request):
        """Delete a goal — cascade-deletes milestones, NULLs out
        milestone_id on linked CompanionTask rows (never deletes tasks)."""
        owner = token_owner(request)
        if owner is None:
            raise HTTPException(401, "Not authenticated")
        db = SessionLocal()
        try:
            g = db.query(Goal).filter(Goal.id == goal_id, Goal.owner == owner).first()
            if not g:
                raise HTTPException(404, "Goal not found")

            # NULL out milestone_id on companion tasks linked to this goal's milestones
            milestone_ids = [
                m[0]
                for m in db.query(Milestone.id).filter(Milestone.goal_id == goal_id).all()
            ]
            if milestone_ids:
                (
                    db.query(CompanionTask)
                    .filter(CompanionTask.milestone_id.in_(milestone_ids))
                    .update(
                        {CompanionTask.milestone_id: None},
                        synchronize_session=False,
                    )
                )

            # Delete milestones
            db.query(Milestone).filter(Milestone.goal_id == goal_id).delete(
                synchronize_session=False
            )

            # Delete goal
            db.delete(g)
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    # ─── Milestones ──────────────────────────────────────────────────────

    @router.get("/goals/{goal_id}/milestones")
    def list_milestones(goal_id: str, request: Request):
        """List milestones for a goal."""
        owner = token_owner(request)
        if owner is None:
            raise HTTPException(401, "Not authenticated")
        db = SessionLocal()
        try:
            g = db.query(Goal).filter(Goal.id == goal_id, Goal.owner == owner).first()
            if not g:
                raise HTTPException(404, "Goal not found")
            rows = (
                db.query(Milestone)
                .filter(Milestone.goal_id == goal_id)
                .order_by(Milestone.sort_order)
                .all()
            )
            return {"milestones": [_milestone_dict(m) for m in rows]}
        finally:
            db.close()

    @router.post("/goals/{goal_id}/milestones")
    async def create_milestone(goal_id: str, request: Request):
        """Create a milestone under a goal."""
        owner = token_owner(request)
        if owner is None:
            raise HTTPException(401, "Not authenticated")
        body = await request.json()
        db = SessionLocal()
        try:
            g = db.query(Goal).filter(Goal.id == goal_id, Goal.owner == owner).first()
            if not g:
                raise HTTPException(404, "Goal not found")
            # Auto sort_order: append to end
            max_order = (
                db.query(Milestone.sort_order)
                .filter(Milestone.goal_id == goal_id)
                .order_by(Milestone.sort_order.desc())
                .first()
            )
            sort_order = (max_order[0] or 0) + 1 if max_order else 0
            m = Milestone(
                id=str(uuid.uuid4()),
                goal_id=goal_id,
                title=(body.get("title", "Untitled") or "")[:200],
                description=body.get("description"),
                target_date=body.get("target_date"),
                status=body.get("status", "pending"),
                sort_order=body.get("sort_order", sort_order),
            )
            db.add(m)
            db.commit()
            return {"ok": True, "milestone": _milestone_dict(m)}
        finally:
            db.close()

    @router.patch("/milestones/{milestone_id}")
    async def update_milestone(milestone_id: str, request: Request):
        """Update a milestone."""
        owner = token_owner(request)
        if owner is None:
            raise HTTPException(401, "Not authenticated")
        body = await request.json()
        db = SessionLocal()
        try:
            m = (
                db.query(Milestone)
                .join(Goal, Milestone.goal_id == Goal.id)
                .filter(Milestone.id == milestone_id, Goal.owner == owner)
                .first()
            )
            if not m:
                raise HTTPException(404, "Milestone not found")
            if "title" in body:
                m.title = (body["title"] or "")[:200]
            if "description" in body:
                m.description = body["description"]
            if "target_date" in body:
                m.target_date = body["target_date"]
            if "status" in body:
                new_status = body["status"]
                # Track completion time: set when completed, clear when
                # changed away from completed.
                if new_status == "completed" and m.status != "completed":
                    m.completed_at = datetime.now(timezone.utc)
                elif new_status != "completed" and m.status == "completed":
                    m.completed_at = None
                m.status = new_status
            if "sort_order" in body:
                m.sort_order = body["sort_order"]
            db.commit()
            return {"ok": True, "milestone": _milestone_dict(m)}
        finally:
            db.close()

    @router.delete("/milestones/{milestone_id}")
    async def delete_milestone(milestone_id: str, request: Request):
        """Delete a milestone — NULLs out milestone_id on linked
        CompanionTask rows (never deletes tasks)."""
        owner = token_owner(request)
        if owner is None:
            raise HTTPException(401, "Not authenticated")
        db = SessionLocal()
        try:
            m = (
                db.query(Milestone)
                .join(Goal, Milestone.goal_id == Goal.id)
                .filter(Milestone.id == milestone_id, Goal.owner == owner)
                .first()
            )
            if not m:
                raise HTTPException(404, "Milestone not found")

            # NULL out milestone_id on linked companion tasks
            (
                db.query(CompanionTask)
                .filter(CompanionTask.milestone_id == milestone_id)
                .update({CompanionTask.milestone_id: None}, synchronize_session=False)
            )

            db.delete(m)
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    # ─── AI Decomposition ──────────────────────────────────────────────────

    # (_resolve_llm_config is now a module-level function, see below)

    @router.post("/goals/{goal_id}/decompose")
    async def decompose_goal(goal_id: str, request: Request):
        """Suggest milestones for a goal using AI.

        Returns a JSON array of {title, description, suggested_target_date}
        without writing anything to the database. The frontend shows these
        for review/editing before the user can choose to create them.
        """
        owner = token_owner(request)
        if owner is None:
            raise HTTPException(401, "Not authenticated")
        db = SessionLocal()
        try:
            g = db.query(Goal).filter(Goal.id == goal_id, Goal.owner == owner).first()
            if not g:
                raise HTTPException(404, "Goal not found")
            title = g.title or ""
            description = g.description or ""
        finally:
            db.close()

        url, model, headers = _resolve_llm_config(owner)
        if not url or not model:
            return {"error": "No AI model configured", "suggestions": []}

        system_prompt = (
            "You are a project planner. Break the user's goal into milestones. "
            "Each milestone is a concrete, measurable outcome that marks progress toward the goal. "
            "Return ONLY a JSON array of milestone objects. "
            "No markdown fences, no explanation, no text outside the JSON array.\n\n"
            "Each object MUST have:\n"
            '  "title": short milestone name (max 80 chars)\n'
            '  "description": brief 1-sentence description of the concrete benefit of this milestone (max 200 chars). REQUIRED for every milestone — never blank.\n'
            '  "suggested_target_date": YYYY-MM-DD or null. Only set this for milestones that are genuinely time-sensitive (e.g. a quit date, funding deadline, hard launch date) — leave null for milestones like "Track Progress" or "Review Results" that don\'t have a natural due date. ANY date given MUST be in the future relative to today.\n\n'
            f"Today's date is {datetime.now(timezone.utc).strftime('%Y-%m-%d')}.\n\n"
            "Guidelines:\n"
            "- Choose 3-7 milestones based on the goal's complexity\n"
            "- Do not exceed 10 milestones\n"
            "- Order them in a natural progression (first to last)\n"
            "- Every milestone must have a non-empty description explaining why it matters\n"
            "- Only suggest a target_date for genuinely time-sensitive milestones; prefer null otherwise"
        )
        user_prompt = f"Goal: {title}\nDescription: {description}\n\nGenerate milestones for this goal."

        try:
            from src.llm_core import llm_call_async
            reply = await llm_call_async(url, model, [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ], headers=headers, temperature=0.5, max_tokens=2000)
        except Exception as e:
            return {"error": f"AI call failed: {e}", "suggestions": []}

        if not reply:
            return {"error": "AI returned empty response", "suggestions": []}

        validated = _parse_json_suggestions(reply, max_items=10)
        if not validated:
            return {"error": "Could not parse AI response", "suggestions": []}
        return {"suggestions": validated}

    # ─── Link CompanionTask to Milestone ─────────────────────────────────

    @router.patch("/companion/tasks/{task_id}/link-milestone")
    async def link_milestone(task_id: str, request: Request):
        """Set or clear milestone_id on an existing CompanionTask.

        Body: {"milestone_id": "uuid-string|null"}
        """
        owner = token_owner(request)
        if owner is None:
            raise HTTPException(401, "Not authenticated")
        body = await request.json()
        milestone_id = body.get("milestone_id")  # str or None

        db = SessionLocal()
        try:
            task = (
                db.query(CompanionTask)
                .filter(CompanionTask.id == task_id, CompanionTask.owner == owner)
                .first()
            )
            if not task:
                raise HTTPException(404, "CompanionTask not found")

            if milestone_id is not None:
                # Verify the milestone exists and belongs to the same owner
                ms = (
                    db.query(Milestone)
                    .join(Goal, Milestone.goal_id == Goal.id)
                    .filter(Milestone.id == milestone_id, Goal.owner == owner)
                    .first()
                )
                if not ms:
                    raise HTTPException(404, "Milestone not found")

            task.milestone_id = milestone_id
            db.commit()
            return {"ok": True, "milestone_id": task.milestone_id}
        finally:
            db.close()
    # ─── Research Config ──────────────────────────────────────────────

    @router.get("/goals/{goal_id}/research-config")
    def get_research_config(goal_id: str, request: Request):
        """Get research config for a goal (or defaults if not yet configured)."""
        owner = token_owner(request)
        if owner is None:
            raise HTTPException(401, "Not authenticated")
        db = SessionLocal()
        try:
            g = db.query(Goal).filter(Goal.id == goal_id, Goal.owner == owner).first()
            if not g:
                raise HTTPException(404, "Goal not found")
            cfg = db.query(GoalResearchConfig).filter(GoalResearchConfig.goal_id == goal_id).first()
            if not cfg:
                return {
                    "goal_id": goal_id,
                    "trigger_mode": "manual",
                    "scheduled_time": None,
                    "idle_threshold_minutes": None,
                    "depth": "moderate",
                    "digest_frequency": "manual",
                    "enabled": False,
                    "last_run_at": None,
                }
            return {
                "goal_id": cfg.goal_id,
                "trigger_mode": cfg.trigger_mode,
                "scheduled_time": cfg.scheduled_time,
                "idle_threshold_minutes": cfg.idle_threshold_minutes,
                "depth": cfg.depth,
                "digest_frequency": cfg.digest_frequency,
                "enabled": cfg.enabled,
                "last_run_at": cfg.last_run_at.isoformat() if cfg.last_run_at else None,
            }
        finally:
            db.close()

    @router.patch("/goals/{goal_id}/research-config")
    async def update_research_config(goal_id: str, request: Request):
        """Update research config for a goal. Creates config row if not exists."""
        owner = token_owner(request)
        if owner is None:
            raise HTTPException(401, "Not authenticated")
        body = await request.json()
        db = SessionLocal()
        try:
            g = db.query(Goal).filter(Goal.id == goal_id, Goal.owner == owner).first()
            if not g:
                raise HTTPException(404, "Goal not found")
            cfg = db.query(GoalResearchConfig).filter(GoalResearchConfig.goal_id == goal_id).first()
            if not cfg:
                cfg = GoalResearchConfig(
                    id=str(uuid.uuid4()),
                    goal_id=goal_id,
                )
                db.add(cfg)
            for key in ("trigger_mode", "scheduled_time", "idle_threshold_minutes",
                        "depth", "digest_frequency", "enabled"):
                if key in body:
                    setattr(cfg, key, body[key])
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    # ─── Research Results ──────────────────────────────────────────────

    @router.get("/goals/{goal_id}/research-results")
    def list_research_results(goal_id: str, request: Request,
                              status: Optional[str] = Query(None)):
        """List research results for a goal, optionally filtered by status."""
        owner = token_owner(request)
        if owner is None:
            raise HTTPException(401, "Not authenticated")
        db = SessionLocal()
        try:
            g = db.query(Goal).filter(Goal.id == goal_id, Goal.owner == owner).first()
            if not g:
                raise HTTPException(404, "Goal not found")
            q = db.query(GoalResearchResult).filter(GoalResearchResult.goal_id == goal_id)
            if status:
                q = q.filter(GoalResearchResult.status == status)
            rows = q.order_by(GoalResearchResult.created_at.desc()).all()
            return {
                "results": [
                    {
                        "id": r.id,
                        "goal_id": r.goal_id,
                        "summary": r.summary,
                        "source_notes": r.source_notes,
                        "status": r.status,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                        "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
                    }
                    for r in rows
                ]
            }
        finally:
            db.close()

    @router.get("/research-results/pending")
    def get_pending_research_results(request: Request):
        """List all pending research results across all goals (global review queue)."""
        owner = token_owner(request)
        if owner is None:
            raise HTTPException(401, "Not authenticated")
        db = SessionLocal()
        try:
            rows = (
                db.query(GoalResearchResult, Goal.title)
                .join(Goal, GoalResearchResult.goal_id == Goal.id)
                .filter(GoalResearchResult.status == "pending", Goal.owner == owner)
                .order_by(GoalResearchResult.created_at.desc())
                .all()
            )
            return {
                "results": [
                    {
                        "id": r.id,
                        "goal_id": r.goal_id,
                        "goal_title": goal_title,
                        "summary": r.summary,
                        "source_notes": r.source_notes,
                        "status": r.status,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                    }
                    for r, goal_title in rows
                ]
            }
        finally:
            db.close()

    @router.patch("/research-results/{result_id}")
    async def update_research_result(result_id: str, request: Request):
        """Set status to 'accepted' or 'discarded' for a research result."""
        owner = token_owner(request)
        if owner is None:
            raise HTTPException(401, "Not authenticated")
        body = await request.json()
        status = body.get("status")
        if status not in ("accepted", "discarded"):
            raise HTTPException(400, "status must be 'accepted' or 'discarded'")
        db = SessionLocal()
        try:
            r = (
                db.query(GoalResearchResult)
                .join(Goal, GoalResearchResult.goal_id == Goal.id)
                .filter(GoalResearchResult.id == result_id, Goal.owner == owner)
                .first()
            )
            if not r:
                raise HTTPException(404, "Research result not found")
            r.status = status
            r.reviewed_at = datetime.now(timezone.utc)
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    @router.post("/research-results/{result_id}/convert")
    async def convert_research_to_milestones(result_id: str, request: Request):
        """Convert an accepted research result into milestone suggestions.

        Loads the research summary + goal context, sends to LLM grounded
        in the research findings, returns suggestions in the same shape
        as /decompose ({title, description, suggested_target_date}[]).
        Does NOT write to the database — this is suggest-only.
        """
        owner = token_owner(request)
        if owner is None:
            raise HTTPException(401, "Not authenticated")
        db = SessionLocal()
        try:
            r = (
                db.query(GoalResearchResult)
                .join(Goal, GoalResearchResult.goal_id == Goal.id)
                .filter(GoalResearchResult.id == result_id, Goal.owner == owner)
                .first()
            )
            if not r:
                raise HTTPException(404, "Research result not found")
            # Load parent goal for context
            goal = db.query(Goal).filter(Goal.id == r.goal_id).first()
            goal_title = goal.title if goal else ""
            goal_desc = goal.description if goal else ""
        finally:
            db.close()

        # Only accepted results can be converted
        if r.status != "accepted":
            raise HTTPException(400, "Only accepted research results can be converted to milestones")

        url, model, headers = _resolve_llm_config(owner)
        if not url or not model:
            return {"error": "No AI model configured", "suggestions": []}

        system_prompt = (
            "You are a project planner. Based on the research findings provided, "
            "break the user's goal into concrete, actionable milestones. "
            "Each milestone must be directly supported by or suggested by the research findings. "
            "Return ONLY a JSON array of milestone objects. "
            "No markdown fences, no explanation, no text outside the JSON array.\n\n"
            "Each object MUST have:\n"
            '  "title": short milestone name (max 80 chars)\n'
            '  "description": brief 1-sentence description grounded in the research findings (max 200 chars). REQUIRED for every milestone — never blank.\n'
            '  "suggested_target_date": YYYY-MM-DD or null. Only set this for milestones that are genuinely time-sensitive (e.g. a quit date, funding deadline, hard launch date) — leave null for milestones like "Track Progress" or "Review Results" that don\'t have a natural due date. ANY date given MUST be in the future relative to today.\n\n'
            f"Today's date is {datetime.now(timezone.utc).strftime('%Y-%m-%d')}.\n\n"
            "Guidelines:\n"
            "- Choose 3-7 milestones based on the research complexity\n"
            "- Do not exceed 10 milestones\n"
            "- Order them in a natural progression (first to last)\n"
            "- Every milestone must have a non-empty description grounded in the research\n"
            "- Only suggest a target_date for genuinely time-sensitive milestones; prefer null otherwise\n"
            "- Base your suggestions on what the research actually surfaced, not generic advice"
        )
        user_prompt = (
            f"Goal: {goal_title}\n"
            f"Description: {goal_desc}\n\n"
            f"Research Findings:\n{r.summary}\n\n"
            f"Based on these research findings, what milestones should be created for this goal?"
        )

        import re as _re
        try:
            from src.llm_core import llm_call_async
            reply = await llm_call_async(url, model, [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ], headers=headers, temperature=0.5, max_tokens=2000)
        except Exception as e:
            return {"error": f"AI call failed: {e}", "suggestions": []}

        if not reply:
            return {"error": "AI returned empty response", "suggestions": []}

        validated = _parse_json_suggestions(reply, max_items=10)
        if not validated:
            return {"error": "Could not parse AI response", "suggestions": []}
        return {"suggestions": validated}

    # ─── Manual Research Run ───────────────────────────────────────────

    @router.post("/goals/{goal_id}/research/run-now")
    async def run_research_now(goal_id: str, request: Request):
        """Manually trigger a research run. Returns the created result row."""
        owner = token_owner(request)
        if owner is None:
            raise HTTPException(401, "Not authenticated")
        db = SessionLocal()
        try:
            g = db.query(Goal).filter(Goal.id == goal_id, Goal.owner == owner).first()
            if not g:
                raise HTTPException(404, "Goal not found")
        finally:
            db.close()

        try:
            result = _perform_research(goal_id, owner, g.title, g.description or "")
        except RuntimeError as e:
            raise HTTPException(504, str(e))
        except Exception as e:
            _log.exception("Research run failed")
            raise HTTPException(500, f"Research failed: {e}")
        return result

    # ─── Chat-about endpoints (prime a new chat session with goal/milestone context) ──

    def _milestone_snapshot(goal_id: str, focus_ms_id: str = None) -> str:
        """Build a milestone progress snapshot string.

        Queries all milestones for the goal and formats them with status
        icons and completion timestamps.  If *focus_ms_id* is given, that
        milestone is marked ``← current focus`` and an extra note is
        appended.
        """
        from core.database import Milestone
        _s_db = SessionLocal()
        try:
            rows = (
                _s_db.query(Milestone)
                .filter(Milestone.goal_id == goal_id)
                .order_by(Milestone.sort_order)
                .all()
            )
        finally:
            _s_db.close()

        def _fmt(m):
            st = "✓ Done" if m.status == "completed" else (
                "▶ In progress" if m.status == "in_progress" else "○ Pending"
            )
            done_ts = ""
            if m.status == "completed" and getattr(m, "completed_at", None):
                done_ts = f" (completed {m.completed_at.strftime('%Y-%m-%d %H:%M')})"
            focus = " ← current focus" if m.id == focus_ms_id else ""
            return f"  {st}{done_ts} — {m.title}{focus}"

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        snapshot = (
            f"[Milestone progress — {now_str} UTC]\n"
            + "\n".join(_fmt(m) for m in rows)
        )
        if focus_ms_id:
            snapshot += "\n\nThe user is currently focused on the milestone marked '← current focus'."

        # Append accepted research findings (max 3 most recent)
        from core.database import GoalResearchResult
        try:
            research = (
                _s_db.query(GoalResearchResult)
                .filter(
                    GoalResearchResult.goal_id == goal_id,
                    GoalResearchResult.status == "accepted",
                )
                .order_by(GoalResearchResult.created_at.desc())
                .limit(3)
                .all()
            )
            if research:
                snapshot += "\n\n[Accepted research findings]\n"
                for r in research:
                    truncated = r.summary[:300]
                    ellipsis = "..." if len(r.summary) > 300 else ""
                    snapshot += f"— {truncated}{ellipsis}\n"
        except Exception:
            pass  # non-critical — don't break the snapshot over research

        return snapshot

    def _resolve_chat_endpoint(owner: str):
        """Resolve a chat endpoint (url, model, headers) for a new session.

        Logic:
          1. Look up the user's configured default endpoint (same as session creation).
          2. If the default's model is not an embedding model, use it.
          3. Otherwise, scan all enabled endpoints for the first non-embedding model.
          4. If nothing qualifies, raise 400.
        """
        from src.endpoint_resolver import resolve_endpoint
        # 1. Try the default endpoint (same lookup as session creation)
        url, model, headers = resolve_endpoint("default", owner=owner)
        if url and model and "embed" not in model.lower():
            return url, model, headers or {}

        # 2. Fall back: scan all enabled endpoints for a non-embedding model
        from core.database import SessionLocal, ModelEndpoint
        from src.endpoint_resolver import (
            resolve_endpoint_runtime, build_chat_url, build_headers,
        )
        from src.auth_helpers import owner_filter
        import json as _json

        db = SessionLocal()
        try:
            q = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
            if owner:
                q = owner_filter(q, ModelEndpoint, owner)
            for ep in q.all():
                try:
                    base, api_key = resolve_endpoint_runtime(ep, owner=owner)
                    ep_url = build_chat_url(base)
                    ep_headers = build_headers(api_key, base) or {}
                    cached = _json.loads(ep.cached_models) if ep.cached_models else []
                    for m in cached:
                        if "embed" not in str(m).lower():
                            return ep_url, m, ep_headers
                except Exception:
                    continue
        finally:
            db.close()

        # 3. Nothing qualified
        raise HTTPException(400, "No chat endpoint configured — add one in Settings first")

    @router.post("/goals/{goal_id}/chat")
    async def chat_about_goal(goal_id: str, request: Request):
        """Get or create a chat session primed with this goal's context.

        One chat per goal: reuses existing linked session if available,
        otherwise creates a new one and stores the link on the Goal row.
        """
        owner = token_owner(request)
        if owner is None:
            raise HTTPException(401, "Not authenticated")
        if session_manager is None:
            raise HTTPException(500, "session_manager not configured")
        db = SessionLocal()
        try:
            g = db.query(Goal).filter(Goal.id == goal_id, Goal.owner == owner).first()
            if not g:
                raise HTTPException(404, "Goal not found")
            goal_title = g.title or ""
            goal_desc = g.description or ""

            # Reuse existing linked session if it still exists
            if g.chat_session_id:
                try:
                    session_manager.get_session(g.chat_session_id)
                    # Inject milestone progress snapshot on every "Ask" click
                    snapshot = _milestone_snapshot(goal_id)
                    session_manager.add_message(g.chat_session_id, ChatMessage(
                        role="system", content=snapshot,
                        metadata={"goal_context": goal_id},
                    ))
                    return {"session_id": g.chat_session_id, "name": f"Goal: {goal_title[:50]}"}
                except (KeyError, Exception):
                    pass  # Session was deleted — create a fresh one

            ep_url, ep_model, ep_headers = _resolve_chat_endpoint(owner)
            if not ep_url or not ep_model:
                raise HTTPException(400, "No chat endpoint configured — add one in Settings first")

            new_sid = str(uuid.uuid4())
            new_name = f"Goal: {goal_title[:50]}"
            new_sess = session_manager.create_session(
                session_id=new_sid,
                name=new_name,
                endpoint_url=ep_url,
                model=ep_model,
                rag=False,
                owner=owner,
            )
            if ep_headers:
                new_sess.headers = ep_headers
                session_manager.save_sessions()

            try:
                from src.event_bus import fire_event
                fire_event("session_created", owner)
            except Exception:
                pass

            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            primer = (
                f"[Goal context — {date_str}]\n\n"
                f"The user is working on this goal:\n"
                f"Title: {goal_title}\n"
                f"Description: {goal_desc or '(no description)'}\n\n"
                f"Answer their questions with this context in mind, but otherwise "
                f"behave as a normal helpful assistant. This is not a constrained "
                f"or goal-only conversation — the user may drift off-topic freely. "
                f"Respond conversationally and briefly — no bullet lists, no headers, "
                f"no unsolicited plans. Match the user's energy. If they say hi, say hi "
                f"back in one or two sentences."
            )
            session_manager.add_message(new_sid, ChatMessage(
                role="system",
                content=primer,
                metadata={"goal_context": goal_id},
            ))

            # Inject milestone progress snapshot (no focus marker for goal-level chat)
            snapshot = _milestone_snapshot(goal_id)
            session_manager.add_message(new_sid, ChatMessage(
                role="system", content=snapshot,
                metadata={"goal_context": goal_id},
            ))

            # Persist the link on the Goal row
            g.chat_session_id = new_sid
            db.commit()

            return {"session_id": new_sid, "name": new_name}
        finally:
            db.close()

    @router.post("/goals/{goal_id}/milestones/{milestone_id}/chat")
    async def chat_about_milestone(goal_id: str, milestone_id: str, request: Request):
        """Navigate to the parent goal's linked chat, with milestone context.

        Milestones share the parent goal's single chat session (Bug #2).
        If the goal doesn't have a linked session yet, creates one (same as
        POST /goals/{goal_id}/chat) and returns its session_id.
        """
        owner = token_owner(request)
        if owner is None:
            raise HTTPException(401, "Not authenticated")
        if session_manager is None:
            raise HTTPException(500, "session_manager not configured")
        db = SessionLocal()
        try:
            g = db.query(Goal).filter(Goal.id == goal_id, Goal.owner == owner).first()
            if not g:
                raise HTTPException(404, "Goal not found")
            ms = db.query(Milestone).filter(
                Milestone.id == milestone_id, Milestone.goal_id == goal_id
            ).first()
            if not ms:
                raise HTTPException(404, "Milestone not found")
            goal_title = g.title or ""
            goal_desc = g.description or ""
            ms_title = ms.title or ""
            ms_desc = ms.description or ""

            # Reuse parent goal's linked session
            if g.chat_session_id:
                try:
                    session_manager.get_session(g.chat_session_id)
                    # Inject milestone progress snapshot with current-focus marker
                    snapshot = _milestone_snapshot(goal_id, focus_ms_id=milestone_id)
                    session_manager.add_message(g.chat_session_id, ChatMessage(
                        role="system", content=snapshot,
                        metadata={"goal_context": goal_id, "milestone_context": milestone_id},
                    ))
                    return {"session_id": g.chat_session_id, "name": f"Goal: {goal_title[:50]}"}
                except (KeyError, Exception):
                    pass  # Session was deleted — create a fresh one
        finally:
            db.close()

        # No linked session yet — create a goal session with milestone context
        ep_url, ep_model, ep_headers = _resolve_chat_endpoint(owner)
        if not ep_url or not ep_model:
            raise HTTPException(400, "No chat endpoint configured — add one in Settings first")

        db2 = SessionLocal()
        try:
            g2 = db2.query(Goal).filter(Goal.id == goal_id, Goal.owner == owner).first()
            if not g2:
                raise HTTPException(404, "Goal not found")

            new_sid = str(uuid.uuid4())
            new_name = f"Goal: {goal_title[:50]}"
            new_sess = session_manager.create_session(
                session_id=new_sid,
                name=new_name,
                endpoint_url=ep_url,
                model=ep_model,
                rag=False,
                owner=owner,
            )
            if ep_headers:
                new_sess.headers = ep_headers
                session_manager.save_sessions()

            try:
                from src.event_bus import fire_event
                fire_event("session_created", owner)
            except Exception:
                pass

            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            # Include both goal and milestone context in the primer
            primer = (
                f"[Goal & Milestone context — {date_str}]\n\n"
                f"The user is working on this goal:\n"
                f"Title: {goal_title}\n"
                f"Description: {goal_desc or '(no description)'}\n\n"
                f"This milestone:\n"
                f"Title: {ms_title}\n"
                f"Description: {ms_desc or '(no description)'}\n\n"
                f"Answer their questions with this context in mind, but otherwise "
                f"behave as a normal helpful assistant. This is not a constrained "
                f"or goal-only conversation — the user may drift off-topic freely. "
                f"Respond conversationally and briefly — no bullet lists, no headers, "
                f"no unsolicited plans. Match the user's energy. If they say hi, say hi "
                f"back in one or two sentences."
            )
            session_manager.add_message(new_sid, ChatMessage(
                role="system",
                content=primer,
                metadata={"goal_context": goal_id, "milestone_context": milestone_id},
            ))

            # Inject milestone progress snapshot with current-focus marker
            snapshot = _milestone_snapshot(goal_id, focus_ms_id=milestone_id)
            session_manager.add_message(new_sid, ChatMessage(
                role="system", content=snapshot,
                metadata={"goal_context": goal_id, "milestone_context": milestone_id},
            ))

            # Link to goal
            g2.chat_session_id = new_sid
            db2.commit()

            return {"session_id": new_sid, "name": new_name}
        finally:
            db2.close()

    return router


# ─── Helpers ────────────────────────────────────────────────────────────────


def _goal_dict(g: Goal) -> dict:
    return {
        "id": g.id,
        "title": g.title,
        "description": g.description,
        "target_date": g.target_date,
        "status": g.status,
        "sort_order": g.sort_order,
        "created_at": g.created_at.isoformat() if g.created_at else "",
        "updated_at": g.updated_at.isoformat() if g.updated_at else "",
    }


def _milestone_dict(m: Milestone) -> dict:
    return {
        "id": m.id,
        "goal_id": m.goal_id,
        "title": m.title,
        "description": m.description,
        "target_date": m.target_date,
        "status": m.status,
        "sort_order": m.sort_order,
        "created_at": m.created_at.isoformat() if m.created_at else "",
    }
