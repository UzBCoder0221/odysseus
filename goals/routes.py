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


def setup_goals_routes() -> APIRouter:
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
                m.status = body["status"]
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
        import re as _re
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

        # ── Defensive JSON parse ───────────────────────────────────────────
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
            return {"error": "Could not parse AI response", "suggestions": []}

        # Validate each item — drop malformed entries
        import logging as _logging
        _log = _logging.getLogger(__name__)
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
                _log.warning("Decompose suggestion '%s' missing description — keeping it but frontend will prompt user", ms_title)
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

        return {"suggestions": validated[:10]}

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
