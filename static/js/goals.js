/**
 * Goals Module — manual CRUD for goals and milestones.
 * Stage 8A: no AI, no scheduling, no automation. Just persistence + UI.
 */

import { makeWindowDraggable } from './windowDrag.js';

const API_BASE = window.location.origin;
let _open = false;
let _goals = [];
let _viewingGoalId = null;  // non-null when viewing a single goal's detail

// ─── API helpers ──────────────────────────────────────────────────────────

async function _api(path, opts = {}) {
  const res = await fetch(`${API_BASE}/api/goals${path}`, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Goals API ${res.status}: ${text}`);
  }
  return res.json();
}

// ─── Render ───────────────────────────────────────────────────────────────

function _renderList(goals) {
  const body = document.getElementById('goals-body');
  if (!body) return;
  if (!goals || goals.length === 0) {
    body.innerHTML = `<div class="goals-empty" style="padding:24px;text-align:center;opacity:0.5;">No goals yet. Click <strong>+ New Goal</strong> to start.</div>`;
    return;
  }
  body.innerHTML = goals.map(g => `
    <div class="goals-card" data-action="open-goal" data-goal-id="${g.id}" style="background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px 16px;margin-bottom:8px;cursor:pointer;transition:box-shadow .15s;position:relative;" title="Click to view details and milestones">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;">
        <div style="flex:1;min-width:0;">
          <div style="font-weight:600;margin-bottom:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${_esc(g.title)}</div>
          <div style="font-size:0.85em;opacity:0.6;display:flex;gap:10px;flex-wrap:wrap;">
            <span class="goals-status-badge goals-status-${_esc(g.status)}">${g.status}</span>
            ${g.target_date ? `<span>🎯 ${g.target_date}</span>` : ''}
            <span>${g.milestone_count > 0 ? `${g.milestones_done}/${g.milestone_count} milestones` : '0 milestones'}</span>
            <span style="opacity:0.4;margin-left:auto;">▶</span>
          </div>
        </div>
        <div style="display:flex;gap:4px;flex-shrink:0;margin-left:8px;">
          <button data-action="add-milestone-list" data-goal-id="${g.id}" style="background:var(--accent);color:var(--accent-text,#fff);border:none;border-radius:4px;cursor:pointer;padding:2px 8px;font-size:12px;" title="Add milestone">+ MS</button>
          <button data-action="edit-goal-list" data-goal-id="${g.id}" style="background:none;border:1px solid var(--border);border-radius:4px;cursor:pointer;padding:2px 8px;font-size:12px;color:var(--text);" title="Edit goal">Edit</button>
          <button data-action="delete-goal" data-goal-id="${g.id}" style="background:none;border:none;color:var(--text);opacity:0.35;cursor:pointer;padding:2px 6px;font-size:14px;" title="Delete goal">✕</button>
        </div>
      </div>
    </div>
  `).join('');
}

function _renderDetail(goal, milestones, researchCfg, researchResults) {
  const body = document.getElementById('goals-body');
  if (!body) return;
  const msRows = milestones.map((m, i) => `
    <div class="milestone-row" data-ms-id="${m.id}" style="background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:10px 14px;margin-bottom:6px;">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
        <div style="flex:1;min-width:0;">
          <div style="display:flex;align-items:center;gap:8px;">
            <input type="checkbox" data-action="toggle-milestone" data-ms-id="${m.id}" ${m.status === 'completed' ? 'checked' : ''} style="cursor:pointer;">
            <span style="${m.status === 'completed' ? 'text-decoration:line-through;opacity:0.5;' : ''}font-weight:500;">${_esc(m.title)}</span>
          </div>
          ${m.description ? `<div style="font-size:0.85em;opacity:0.6;margin-top:4px;margin-left:28px;">${_esc(m.description)}</div>` : ''}
          <div style="font-size:0.8em;opacity:0.5;margin-top:4px;margin-left:28px;display:flex;gap:10px;">
            <span class="goals-status-badge goals-status-${_esc(m.status)}">${m.status}</span>
            ${m.target_date ? `<span>🎯 ${_esc(m.target_date)}</span>` : ''}
          </div>
        </div>
        <div style="display:flex;gap:4px;flex-shrink:0;">
          <button data-action="edit-milestone" data-ms-id="${m.id}" style="background:none;border:1px solid var(--border);border-radius:4px;cursor:pointer;padding:2px 8px;font-size:12px;">Edit</button>
          <button data-action="delete-milestone" data-ms-id="${m.id}" style="background:none;border:1px solid var(--border);border-radius:4px;cursor:pointer;padding:2px 8px;font-size:12px;color:var(--danger,#c44);">✕</button>
        </div>
      </div>
    </div>
  `).join('') || `<div style="padding:16px;text-align:center;opacity:0.5;">No milestones yet.</div>`;

  // ── Research section ─────────────────────────────────────────────
  const rc = researchCfg || {};
  const enabled = rc.enabled ? 'checked' : '';
  const cfgHtml = `
    <div id="goals-research-section" style="margin-top:16px;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
        <h4 style="margin:0;font-size:1em;">🔬 Background Research</h4>
        <div style="display:flex;gap:6px;align-items:center;">
          <label style="font-size:12px;opacity:0.7;">Enabled</label>
          <input type="checkbox" data-action="research-toggle" ${enabled} style="cursor:pointer;">
        </div>
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:8px;font-size:12px;">
        <select data-action="research-trigger" style="padding:4px 8px;border-radius:4px;border:1px solid var(--border);background:var(--input-bg,#333);color:var(--text,#eee);font-size:12px;">
          <option value="manual" ${rc.trigger_mode === 'manual' ? 'selected' : ''}>Manual</option>
          <option value="scheduled" ${rc.trigger_mode === 'scheduled' ? 'selected' : ''}>Scheduled</option>
          <option value="idle" ${rc.trigger_mode === 'idle' ? 'selected' : ''}>Idle</option>
        </select>
        <input type="time" data-action="research-sched-time" value="${rc.scheduled_time || ''}" placeholder="HH:MM" style="padding:4px 8px;border-radius:4px;border:1px solid var(--border);background:var(--input-bg,#333);color:var(--text,#eee);font-size:12px;${rc.trigger_mode !== 'scheduled' ? 'display:none;' : ''}">
        <input type="number" data-action="research-idle-min" value="${rc.idle_threshold_minutes || 30}" min="5" max="1440" placeholder="Idle min" style="padding:4px 8px;border-radius:4px;border:1px solid var(--border);background:var(--input-bg,#333);color:var(--text,#eee);font-size:12px;width:80px;${rc.trigger_mode !== 'idle' ? 'display:none;' : ''}">
        <select data-action="research-depth" style="padding:4px 8px;border-radius:4px;border:1px solid var(--border);background:var(--input-bg,#333);color:var(--text,#eee);font-size:12px;">
          <option value="light" ${rc.depth === 'light' ? 'selected' : ''}>Light (~60s)</option>
          <option value="moderate" ${rc.depth === 'moderate' || !rc.depth ? 'selected' : ''}>Moderate (~2m)</option>
        </select>
        <select data-action="research-digest" style="padding:4px 8px;border-radius:4px;border:1px solid var(--border);background:var(--input-bg,#333);color:var(--text,#eee);font-size:12px;">
          <option value="manual" ${rc.digest_frequency === 'manual' || !rc.digest_frequency ? 'selected' : ''}>No digest</option>
          <option value="daily" ${rc.digest_frequency === 'daily' ? 'selected' : ''}>Daily digest</option>
          <option value="weekly" ${rc.digest_frequency === 'weekly' ? 'selected' : ''}>Weekly digest</option>
        </select>
        <button data-action="research-save-config" style="background:var(--accent);color:var(--accent-text,#fff);border:none;border-radius:4px;cursor:pointer;padding:4px 10px;font-size:12px;">Save Config</button>
      </div>
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;">
        <button data-action="run-research-now" style="background:var(--accent);color:var(--accent-text,#fff);border:none;border-radius:4px;cursor:pointer;padding:6px 14px;font-size:13px;font-weight:600;">► Run Research Now</button>
        <span id="research-status" style="font-size:11px;opacity:0.5;"></span>
      </div>
      ${_renderResearchResults(researchResults, goal.id)}
    </div>
  `;

  body.innerHTML = `
    <div style="margin-bottom:12px;">
      <button data-action="back-to-list" style="background:none;border:none;cursor:pointer;opacity:0.6;padding:4px 0;">← Back to goals</button>
    </div>
    <div class="goal-detail-header" style="background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:12px;">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;">
        <div style="flex:1;">
          <h3 style="margin:0 0 6px 0;font-size:1.2em;">${_esc(goal.title)}</h3>
          ${goal.description ? `<p style="margin:0 0 8px 0;opacity:0.7;font-size:0.9em;">${_esc(goal.description)}</p>` : ''}
          <div style="display:flex;gap:12px;font-size:0.85em;opacity:0.6;flex-wrap:wrap;">
            <span class="goals-status-badge goals-status-${_esc(goal.status)}">${goal.status}</span>
            ${goal.target_date ? `<span>🎯 ${goal.target_date}</span>` : ''}
            <span>📅 Created ${_fmtDate(goal.created_at)}</span>
            ${goal.updated_at ? `<span>✏️ Updated ${_fmtDate(goal.updated_at)}</span>` : ''}
          </div>
        </div>
        <div style="display:flex;gap:6px;flex-shrink:0;">
          <button data-action="edit-goal" style="background:var(--accent);color:var(--accent-text,#fff);border:none;border-radius:6px;cursor:pointer;padding:6px 14px;font-size:13px;">Edit</button>
          <button data-action="ai-suggest-milestones" style="background:none;border:1px solid var(--border);border-radius:6px;cursor:pointer;padding:6px 14px;font-size:13px;color:var(--accent);">AI: Suggest Milestones</button>
        </div>
      </div>
      <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;">
        ${['active','paused','completed','abandoned'].map(s => `
          <button data-action="set-status" data-status="${s}" style="background:${s === goal.status ? 'var(--accent)' : 'var(--surface)'};color:${s === goal.status ? 'var(--accent-text,#fff)' : 'var(--text)'};border:1px solid var(--border);border-radius:12px;cursor:pointer;padding:3px 12px;font-size:12px;opacity:${s === goal.status ? '1' : '0.7'};">${s}</button>
        `).join('')}
      </div>
    </div>

    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
      <h4 style="margin:0;font-size:1em;">Milestones</h4>
      <button data-action="add-milestone" style="background:var(--accent);color:var(--accent-text,#fff);border:none;border-radius:6px;cursor:pointer;padding:4px 12px;font-size:13px;">+ Add Milestone</button>
    </div>
    ${msRows}
    ${cfgHtml}
  `;
}

function _renderResearchResults(results, goalId) {
  if (!results || results.length === 0) {
    return '<div style="font-size:12px;opacity:0.5;margin-top:4px;">No research results yet.</div>';
  }
  const accepted = results.filter(r => r.status === 'accepted');
  const pending = results.filter(r => r.status === 'pending');
  const discarded = results.filter(r => r.status === 'discarded');
  let html = '<div style="margin-top:8px;border-top:1px solid var(--border);padding-top:8px;">';
  html += '<div style="font-size:12px;font-weight:600;margin-bottom:6px;">Results</div>';

  for (const group of [
    { label: 'Pending Review', items: pending, icon: '⏳' },
    { label: 'Accepted', items: accepted, icon: '✅' },
    { label: 'Discarded', items: discarded, icon: '🗑️' },
  ]) {
    if (group.items.length === 0) continue;
    html += `<div style="margin-bottom:6px;"><span style="font-size:11px;opacity:0.6;">${group.icon} ${group.label} (${group.items.length})</span></div>`;
    for (const r of group.items) {
      const preview = (r.summary || '').slice(0, 200);
      const sources = r.source_notes ? JSON.parse(r.source_notes).join(', ') : '';
      html += `<div class="research-result-row" data-result-id="${r.id}" style="background:var(--input-bg,#333);border:1px solid var(--border,#444);border-radius:6px;padding:8px 10px;margin-bottom:6px;">`;
      html += `<div style="font-size:11px;opacity:0.7;margin-bottom:4px;">📄 ${_fmtDate(r.created_at)}</div>`;
      html += `<div style="font-size:12px;line-height:1.4;margin-bottom:4px;">${_esc(preview)}${preview.length >= 200 ? '...' : ''}</div>`;
      if (sources) html += `<div style="font-size:10px;opacity:0.4;margin-bottom:4px;">🔗 ${_esc(sources.slice(0, 200))}</div>`;
      if (r.status === 'pending') {
        html += '<div style="display:flex;gap:6px;margin-top:4px;">';
        html += '<button data-action="accept-research" data-result-id="' + r.id + '" style="background:var(--accent,#5b9);color:var(--accent-text,#fff);border:none;border-radius:4px;cursor:pointer;padding:3px 10px;font-size:11px;">✓ Accept</button>';
        html += '<button data-action="discard-research" data-result-id="' + r.id + '" style="background:none;border:1px solid var(--border,#444);border-radius:4px;cursor:pointer;padding:3px 10px;font-size:11px;color:var(--text);">✕ Discard</button>';
        html += '</div>';
      }
      html += '</div>';
    }
  }
  html += '</div>';
  return html;
}

// ─── Modal creation ───────────────────────────────────────────────────────

function _buildModal() {
  if (document.getElementById('goals-modal')) return;
  const modal = document.createElement('div');
  modal.className = 'modal';
  modal.id = 'goals-modal';
  modal.innerHTML = `
    <div class="modal-content" style="max-width:640px;width:90vw;">
      <div class="modal-header">
        <h4 style="margin:0;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px;"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg> Goals</h4>
        <span style="flex:1"></span>
        <span id="goals-header-action" style="margin-right:8px;display:flex;gap:6px;align-items:center;">
          <button id="goals-pending-btn" data-action="view-pending-research" style="background:none;border:1px solid var(--border);border-radius:6px;cursor:pointer;padding:4px 10px;font-size:12px;display:none;">🔬 Pending <span id="pending-research-count">0</span></button>
          <button id="goals-new-btn" style="background:var(--accent);color:var(--accent-text,#fff);border:none;border-radius:6px;cursor:pointer;padding:4px 12px;font-size:13px;">+ New Goal</button>
        </span>
        <button class="close-btn" id="goals-close">✖</button>
      </div>
      <div class="modal-body" id="goals-body" style="overflow-y:auto;max-height:70vh;padding:12px 16px;">
        <div style="padding:24px;text-align:center;opacity:0.5;">Loading...</div>
      </div>
    </div>
  `;
  document.body.appendChild(modal);

  // Make the modal draggable by its header — same pattern as every other tool modal.
  {
    const content = modal.querySelector('.modal-content');
    const header = modal.querySelector('.modal-header');
    if (content && header) {
      makeWindowDraggable(modal, { content, header });
    }
  }

  modal.addEventListener('click', e => {
    if (e.target === modal) _close();
  });
  document.getElementById('goals-close').addEventListener('click', _close);
  document.getElementById('goals-new-btn').addEventListener('click', () => {
    _showGoalForm(null);
  });
}

function _close() {
  _open = false;
  _viewingGoalId = null;
  const modal = document.getElementById('goals-modal');
  if (modal) modal.remove();
  if (_escHandler) {
    document.removeEventListener('keydown', _escHandler);
    _escHandler = null;
  }
}

// ─── Event delegation ────────────────────────────────────────────────
// Replaces fragile inline onclick="goalsModule.*()" with data-action
// attributes on rendered elements and a single delegated click handler.
// This is the same pattern used by companion.js (§ taskList delegation).

function _setupClickDelegation() {
  const body = document.getElementById('goals-body');
  if (!body) return;
  body.addEventListener('click', async e => {
    const target = e.target.closest('[data-action]');
    if (!target) return;
    const action = target.dataset.action;
    e.stopPropagation();
    try {
      switch (action) {
        case 'open-goal':
          if (target.dataset.goalId) await openGoal(target.dataset.goalId);
          break;
        case 'add-milestone-list':
          if (target.dataset.goalId) await addMilestoneToList(target.dataset.goalId);
          break;
        case 'edit-goal-list':
          if (target.dataset.goalId) await editGoalFromList(target.dataset.goalId);
          break;
        case 'delete-goal':
          if (target.dataset.goalId) await deleteGoal(target.dataset.goalId);
          break;
        case 'back-to-list':
          backToList();
          break;
        case 'edit-goal':
          await editGoal();
          break;
        case 'set-status':
          if (target.dataset.status) await setStatus(target.dataset.status);
          break;
        case 'add-milestone':
          addMilestone();
          break;
        case 'add-milestone-detail':
          addMilestone();
          break;
        case 'edit-milestone':
          if (target.dataset.msId) editMilestone(target.dataset.msId);
          break;
        case 'delete-milestone':
          if (target.dataset.msId) await deleteMilestone(target.dataset.msId);
          break;
        case 'toggle-milestone':
          if (target.dataset.msId) await toggleMilestone(target.dataset.msId, target.checked);
          break;
        case 'ai-suggest-milestones':
          await _showAISuggestions();
          break;
        case 'research-toggle':
          // Instant toggle — auto-saves the enabled state
          await _saveResearchCfg({ enabled: target.checked });
          break;
        case 'research-save-config':
          await _saveResearchCfgFromUI();
          break;
        case 'run-research-now':
          await _runResearchNow();
          break;
        case 'accept-research':
          if (target.dataset.resultId) await _reviewResearch(target.dataset.resultId, 'accepted');
          break;
        case 'discard-research':
          if (target.dataset.resultId) await _reviewResearch(target.dataset.resultId, 'discarded');
          break;
        case 'research-trigger':
          // Show/hide conditional fields when trigger mode changes
          _toggleResearchFields(target.value);
          break;
        case 'view-pending-research':
          await _showPendingResearch();
          break;
        case 'open-goal-from-research':
          if (target.dataset.goalId) await openGoal(target.dataset.goalId);
          break;
      }
    } catch (ex) {
      console.error('Goals click handler:', ex);
    }
  });
}

function _toggleResearchFields(mode) {
  const section = document.getElementById('goals-research-section');
  if (!section) return;
  const schedInput = section.querySelector('[data-action="research-sched-time"]');
  const idleInput = section.querySelector('[data-action="research-idle-min"]');
  if (schedInput) schedInput.style.display = mode === 'scheduled' ? '' : 'none';
  if (idleInput) idleInput.style.display = mode === 'idle' ? '' : 'none';
}

async function _saveResearchCfg(partial) {
  if (!_viewingGoalId) return;
  try {
    await _api('/goals/' + _viewingGoalId + '/research-config', {
      method: 'PATCH',
      body: JSON.stringify(partial),
    });
  } catch (e) {
    console.error('Failed to save research config:', e);
  }
}

async function _saveResearchCfgFromUI() {
  const section = document.getElementById('goals-research-section');
  if (!section || !_viewingGoalId) return;
  const trigger = section.querySelector('[data-action="research-trigger"]')?.value || 'manual';
  const sched = section.querySelector('[data-action="research-sched-time"]')?.value || null;
  const idleMin = parseInt(section.querySelector('[data-action="research-idle-min"]')?.value) || null;
  const depth = section.querySelector('[data-action="research-depth"]')?.value || 'moderate';
  const digest = section.querySelector('[data-action="research-digest"]')?.value || 'manual';
  const enabled = section.querySelector('[data-action="research-toggle"]')?.checked || false;
  try {
    await _api('/goals/' + _viewingGoalId + '/research-config', {
      method: 'PATCH',
      body: JSON.stringify({
        trigger_mode: trigger,
        scheduled_time: sched,
        idle_threshold_minutes: idleMin,
        depth: depth,
        digest_frequency: digest,
        enabled: enabled,
      }),
    });
    const status = document.getElementById('research-status');
    if (status) { status.textContent = 'Config saved ✓'; setTimeout(() => { status.textContent = ''; }, 2000); }
  } catch (e) {
    alert('Failed to save research config: ' + e.message);
  }
}

/** AbortController for run-research-now — aborted on retry/close */
let _researchAbortController = null;

async function _runResearchNow() {
  if (!_viewingGoalId) return;
  const goalId = _viewingGoalId;

  // Show loading overlay
  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.4);z-index:10000;display:flex;align-items:center;justify-content:center;';
  overlay.innerHTML = '<div style="background:var(--surface,#222);border:1px solid var(--border,#444);border-radius:12px;padding:32px;text-align:center;"><div style="font-size:24px;margin-bottom:12px;">🔬</div><div style="font-weight:600;">Running research...</div><div style="font-size:12px;opacity:0.6;margin-top:6px;">Searching the web and synthesizing findings</div><div style="margin-top:12px;"><button class="research-cancel-btn" style="background:none;border:1px solid var(--border,#444);border-radius:6px;cursor:pointer;padding:6px 14px;font-size:12px;">Cancel</button></div></div>';
  document.body.appendChild(overlay);

  // Dismiss on backdrop click or cancel button
  const dismissOverlay = () => { overlay.remove(); };
  overlay.addEventListener('click', e => {
    if (e.target === overlay || e.target.closest('.research-cancel-btn')) {
      if (_researchAbortController) _researchAbortController.abort();
      dismissOverlay();
    }
  });

  // Disable the Run Research Now button
  const runBtn = document.querySelector('[data-action="run-research-now"]');
  if (runBtn) runBtn.disabled = true;

  // Cancel previous in-flight request
  if (_researchAbortController) {
    _researchAbortController.abort();
  }
  _researchAbortController = new AbortController();
  const signal = _researchAbortController.signal;

  try {
    await _api('/goals/' + goalId + '/research/run-now', {
      method: 'POST',
      signal,
    });
    overlay.remove();
    if (runBtn) runBtn.disabled = false;
    // Refresh the goal detail view to show the new result
    await openGoal(goalId);
  } catch (e) {
    if (e.name === 'AbortError') {
      overlay.remove();
      if (runBtn) runBtn.disabled = false;
      return;
    }
    overlay.innerHTML = '<div style="background:var(--surface,#222);border:1px solid var(--border,#444);border-radius:12px;padding:32px;text-align:center;"><div style="font-size:24px;margin-bottom:12px;">❌</div><div style="font-weight:600;color:var(--danger,#c44);">Research Failed</div><div style="font-size:12px;opacity:0.6;margin-top:6px;">' + _esc(e.message) + '</div><button class="research-cancel-btn" style="margin-top:16px;background:none;border:1px solid var(--border,#444);border-radius:6px;cursor:pointer;padding:6px 14px;font-size:13px;">Close</button></div>';
    if (runBtn) runBtn.disabled = false;
  }
}

async function _showPendingResearch() {
  const body = document.getElementById('goals-body');
  if (!body) return;
  try {
    const data = await _api('/research-results/pending');
    const results = data.results || [];
    if (results.length === 0) {
      _fetchAndRender();
      return;
    }
    let html = `
      <div style="margin-bottom:12px;">
        <button data-action="back-to-list" style="background:none;border:none;cursor:pointer;opacity:0.6;padding:4px 0;">← Back to goals</button>
      </div>
      <h4 style="margin:0 0 12px 0;">🔬 Pending Research Review (${results.length})</h4>
    `;
    for (const r of results) {
      const preview = (r.summary || '').slice(0, 300);
      const sources = r.source_notes ? (() => { try { return JSON.parse(r.source_notes).join(', ').slice(0, 200); } catch(e) { return ''; } })() : '';
      html += '<div class="research-result-row" data-result-id="' + r.id + '" style="background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:12px 14px;margin-bottom:10px;">';
      html += '<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:4px;">';
      html += '<div><span style="font-weight:600;font-size:13px;">' + _esc(r.goal_title || r.goal_id) + '</span>';
      html += '<span style="font-size:11px;opacity:0.5;margin-left:8px;">' + _fmtDate(r.created_at) + '</span></div>';
      html += '</div>';
      html += '<div style="font-size:12px;line-height:1.4;margin-bottom:6px;">' + _esc(preview) + (preview.length >= 300 ? '...' : '') + '</div>';
      if (sources) html += '<div style="font-size:10px;opacity:0.4;margin-bottom:6px;">🔗 ' + _esc(sources) + '</div>';
      html += '<div style="display:flex;gap:6px;">';
      html += '<button data-action="accept-research" data-result-id="' + r.id + '" style="background:var(--accent,#5b9);color:var(--accent-text,#fff);border:none;border-radius:4px;cursor:pointer;padding:4px 12px;font-size:12px;">✓ Accept</button>';
      html += '<button data-action="discard-research" data-result-id="' + r.id + '" style="background:none;border:1px solid var(--border,#444);border-radius:4px;cursor:pointer;padding:4px 12px;font-size:12px;color:var(--text);">✕ Discard</button>';
      html += '<button data-action="open-goal-from-research" data-goal-id="' + r.goal_id + '" style="background:none;border:1px solid var(--border,#444);border-radius:4px;cursor:pointer;padding:4px 12px;font-size:12px;color:var(--accent);">View Goal</button>';
      html += '</div></div>';
    }
    body.innerHTML = html;
  } catch (e) {
    console.error('Failed to load pending research:', e);
    _fetchAndRender();
  }
}

async function _reviewResearch(resultId, status) {
  try {
    await _api('/research-results/' + resultId, {
      method: 'PATCH',
      body: JSON.stringify({ status }),
    });
    if (_viewingGoalId) await openGoal(_viewingGoalId);
  } catch (e) {
    alert('Failed to ' + status + ' result: ' + e.message);
  }
}

export function openGoals() {
  if (_open) return;
  _open = true;
  _viewingGoalId = null;
  _buildModal();
  const modal = document.getElementById('goals-modal');
  if (modal) modal.classList.remove('hidden');
  _setupClickDelegation();
  _fetchAndRender();

  _escHandler = e => { if (e.key === 'Escape') _close(); };
  document.addEventListener('keydown', _escHandler);
}

export function closeGoals() {
  _close();
}

export function isGoalsOpen() {
  return _open;
}

// ─── Data fetching ────────────────────────────────────────────────────────

async function _fetchAndRender() {
  try {
    const [data, pendingData] = await Promise.all([
      _api('/goals'),
      _api('/research-results/pending').catch(() => ({ results: [] })),
    ]);
    _goals = data.goals || [];
    _renderList(_goals);
    // Show pending research badge
    const pendingResults = pendingData.results || [];
    const badge = document.getElementById('pending-research-count');
    const btn = document.getElementById('goals-pending-btn');
    if (badge) badge.textContent = pendingResults.length;
    if (btn) btn.style.display = pendingResults.length > 0 ? '' : 'none';
  } catch (e) {
    console.error('Failed to load goals:', e);
    const body = document.getElementById('goals-body');
    if (body) body.innerHTML = `<div style="padding:24px;text-align:center;color:var(--danger,#c44);">Failed to load goals: ${_esc(e.message)}</div>`;
  }
}

export async function openGoal(goalId) {
  _viewingGoalId = goalId;
  try {
    const [goalData, cfg, results] = await Promise.all([
      _api(`/goals/${goalId}`),
      _api(`/goals/${goalId}/research-config`).catch(() => null),
      _api(`/goals/${goalId}/research-results`).catch(() => ({ results: [] })),
    ]);
    _renderDetail(goalData.goal, goalData.milestones || [], cfg, results.results || []);
  } catch (e) {
    console.error('Failed to load goal:', e);
    const body = document.getElementById('goals-body');
    if (body) body.innerHTML = `<div style="padding:24px;text-align:center;color:var(--danger,#c44);">Failed to load goal: ${_esc(e.message)}</div>`;
  }
}

export function backToList() {
  _viewingGoalId = null;
  _fetchAndRender();
}

// ─── Goal CRUD ────────────────────────────────────────────────────────────

function _showGoalForm(existing) {
  const isEdit = !!existing;
  const title = isEdit ? existing.title : '';
  const desc = isEdit ? (existing.description || '') : '';
  const targetDate = isEdit ? (existing.target_date || '') : '';
  const status = isEdit ? existing.status : 'active';

  // Create a small inline form modal
  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.4);z-index:10000;display:flex;align-items:center;justify-content:center;';
  overlay.innerHTML = `
    <div style="background:var(--surface,#222);border:1px solid var(--border,#444);border-radius:12px;padding:24px;width:420px;max-width:90vw;">
      <h3 style="margin:0 0 16px 0;">${isEdit ? 'Edit Goal' : 'New Goal'}</h3>
      <div style="display:flex;flex-direction:column;gap:10px;">
        <input id="goals-form-title" type="text" placeholder="Goal title" value="${_esc(title)}" style="padding:8px 12px;border-radius:6px;border:1px solid var(--border,#444);background:var(--input-bg,#333);color:var(--text,#eee);font-size:14px;" maxlength="200">
        <textarea id="goals-form-desc" placeholder="Description (optional)" style="padding:8px 12px;border-radius:6px;border:1px solid var(--border,#444);background:var(--input-bg,#333);color:var(--text,#eee);font-size:14px;resize:vertical;min-height:60px;">${_esc(desc)}</textarea>
        <div style="display:flex;gap:8px;">
          <input id="goals-form-date" type="date" value="${_esc(targetDate)}" style="flex:1;padding:8px 12px;border-radius:6px;border:1px solid var(--border,#444);background:var(--input-bg,#333);color:var(--text,#eee);font-size:14px;">
          <select id="goals-form-status" style="padding:8px 12px;border-radius:6px;border:1px solid var(--border,#444);background:var(--input-bg,#333);color:var(--text,#eee);font-size:14px;">
            <option value="active" ${status === 'active' ? 'selected' : ''}>Active</option>
            <option value="paused" ${status === 'paused' ? 'selected' : ''}>Paused</option>
            <option value="completed" ${status === 'completed' ? 'selected' : ''}>Completed</option>
            <option value="abandoned" ${status === 'abandoned' ? 'selected' : ''}>Abandoned</option>
          </select>
        </div>
        <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:8px;">
          <button id="goals-form-cancel" style="padding:8px 16px;border-radius:6px;border:1px solid var(--border,#444);background:transparent;color:var(--text,#eee);cursor:pointer;">Cancel</button>
          <button id="goals-form-save" style="padding:8px 16px;border-radius:6px;border:none;background:var(--accent,#5b9);color:var(--accent-text,#fff);cursor:pointer;">${isEdit ? 'Save' : 'Create'}</button>
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);

  const titleInput = overlay.querySelector('#goals-form-title');
  const descInput = overlay.querySelector('#goals-form-desc');
  const dateInput = overlay.querySelector('#goals-form-date');
  const statusInput = overlay.querySelector('#goals-form-status');

  overlay.querySelector('#goals-form-cancel').addEventListener('click', () => overlay.remove());
  overlay.querySelector('#goals-form-save').addEventListener('click', async () => {
    const payload = {
      title: titleInput.value.trim() || 'Untitled',
      description: descInput.value.trim() || null,
      target_date: dateInput.value || null,
      status: statusInput.value,
    };
    try {
      if (isEdit) {
        await _api(`/goals/${existing.id}`, {
          method: 'PATCH',
          body: JSON.stringify(payload),
        });
      } else {
        await _api('/goals', {
          method: 'POST',
          body: JSON.stringify(payload),
        });
      }
      overlay.remove();
      if (_viewingGoalId && isEdit) {
        await openGoal(_viewingGoalId);
      } else {
        await _fetchAndRender();
      }
    } catch (e) {
      alert('Error: ' + e.message);
    }
  });

  // Focus and select title
  setTimeout(() => titleInput && titleInput.focus(), 100);
}

export async function editGoal() {
  // Fetch fresh goal data from the API
  if (!_viewingGoalId) return;
  try {
    const data = await _api(`/goals/${_viewingGoalId}`);
    _showGoalForm(data.goal);
  } catch (e) {
    alert('Failed to load goal: ' + e.message);
  }
}

export async function editGoalFromList(goalId) {
  // Edit a goal directly from the list view (no need to open detail first)
  try {
    const data = await _api(`/goals/${goalId}`);
    _showGoalForm(data.goal);
  } catch (e) {
    alert('Failed to load goal: ' + e.message);
  }
}

export async function setStatus(status) {
  if (!_viewingGoalId) return;
  try {
    await _api(`/goals/${_viewingGoalId}`, {
      method: 'PATCH',
      body: JSON.stringify({ status }),
    });
    await openGoal(_viewingGoalId);
  } catch (e) {
    alert('Error: ' + e.message);
  }
}

export async function deleteGoal(goalId) {
  if (!confirm('Delete this goal and all its milestones?')) return;
  try {
    await _api(`/goals/${goalId}`, { method: 'DELETE' });
    if (_viewingGoalId === goalId) _viewingGoalId = null;
    await _fetchAndRender();
  } catch (e) {
    alert('Error: ' + e.message);
  }
}

// ─── Milestone CRUD ───────────────────────────────────────────────────────

function _showMilestoneForm(existing) {
  if (!_viewingGoalId) return;
  const isEdit = !!existing;
  const title = isEdit ? existing.title : '';
  const desc = isEdit ? (existing.description || '') : '';
  const targetDate = isEdit ? (existing.target_date || '') : '';

  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.4);z-index:10000;display:flex;align-items:center;justify-content:center;';
  overlay.innerHTML = `
    <div style="background:var(--surface,#222);border:1px solid var(--border,#444);border-radius:12px;padding:24px;width:420px;max-width:90vw;">
      <h3 style="margin:0 0 16px 0;">${isEdit ? 'Edit Milestone' : 'New Milestone'}</h3>
      <div style="display:flex;flex-direction:column;gap:10px;">
        <input id="ms-form-title" type="text" placeholder="Milestone title" value="${_esc(title)}" style="padding:8px 12px;border-radius:6px;border:1px solid var(--border,#444);background:var(--input-bg,#333);color:var(--text,#eee);font-size:14px;" maxlength="200">
        <textarea id="ms-form-desc" placeholder="Description (optional)" style="padding:8px 12px;border-radius:6px;border:1px solid var(--border,#444);background:var(--input-bg,#333);color:var(--text,#eee);font-size:14px;resize:vertical;min-height:60px;">${_esc(desc)}</textarea>
        <input id="ms-form-date" type="date" value="${_esc(targetDate)}" style="padding:8px 12px;border-radius:6px;border:1px solid var(--border,#444);background:var(--input-bg,#333);color:var(--text,#eee);font-size:14px;">
        <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:8px;">
          <button id="ms-form-cancel" style="padding:8px 16px;border-radius:6px;border:1px solid var(--border,#444);background:transparent;color:var(--text,#eee);cursor:pointer;">Cancel</button>
          <button id="ms-form-save" style="padding:8px 16px;border-radius:6px;border:none;background:var(--accent,#5b9);color:var(--accent-text,#fff);cursor:pointer;">${isEdit ? 'Save' : 'Create'}</button>
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);

  overlay.querySelector('#ms-form-cancel').addEventListener('click', () => overlay.remove());
  overlay.querySelector('#ms-form-save').addEventListener('click', async () => {
    const payload = {
      title: overlay.querySelector('#ms-form-title').value.trim() || 'Untitled',
      description: overlay.querySelector('#ms-form-desc').value.trim() || null,
      target_date: overlay.querySelector('#ms-form-date').value || null,
    };
    try {
      if (isEdit) {
        await _api(`/milestones/${existing.id}`, {
          method: 'PATCH',
          body: JSON.stringify(payload),
        });
      } else {
        await _api(`/goals/${_viewingGoalId}/milestones`, {
          method: 'POST',
          body: JSON.stringify(payload),
        });
      }
      overlay.remove();
      await openGoal(_viewingGoalId);
    } catch (e) {
      alert('Error: ' + e.message);
    }
  });

  setTimeout(() => overlay.querySelector('#ms-form-title').focus(), 100);
}

export function addMilestone() {
  _showMilestoneForm(null);
}

export async function addMilestoneToList(goalId) {
  // Open the goal's detail view, then show the milestone form
  _viewingGoalId = goalId;
  try {
    const data = await _api(`/goals/${goalId}`);
    _renderDetail(data.goal, data.milestones || []);
    // Show the milestone form after a brief delay so the DOM is updated
    setTimeout(() => _showMilestoneForm(null), 100);
  } catch (e) {
    alert('Failed to load goal: ' + e.message);
  }
}

export function editMilestone(msId) {
  // We need the current goal data — fetch it fresh
  (async () => {
    const data = await _api(`/goals/${_viewingGoalId}`);
    const ms = (data.milestones || []).find(m => m.id === msId);
    if (ms) _showMilestoneForm(ms);
  })();
}

export async function deleteMilestone(msId) {
  if (!confirm('Delete this milestone? Linked tasks will be unlinked (not deleted).')) return;
  try {
    await _api(`/milestones/${msId}`, { method: 'DELETE' });
    await openGoal(_viewingGoalId);
  } catch (e) {
    alert('Error: ' + e.message);
  }
}

export async function toggleMilestone(msId, checked) {
  try {
    await _api(`/milestones/${msId}`, {
      method: 'PATCH',
      body: JSON.stringify({ status: checked ? 'completed' : 'pending' }),
    });
    // Refresh the detail view
    await openGoal(_viewingGoalId);
  } catch (e) {
    alert('Error: ' + e.message);
  }
}

// ─── AI Suggest Milestones ──────────────────────────────────────────────

/** AbortController for the current /decompose fetch — aborted on retry. */
let _decomposeAbortController = null;

async function _showAISuggestions() {
  if (!_viewingGoalId) return;
  const goalId = _viewingGoalId;

  // ── Build loading overlay immediately ──
  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.4);z-index:10000;display:flex;align-items:center;justify-content:center;';
  overlay.innerHTML = '<div style="background:var(--surface,#222);border:1px solid var(--border,#444);border-radius:12px;padding:32px;text-align:center;"><div style="font-size:24px;margin-bottom:12px;">⏳</div><div style="font-weight:600;">Generating suggestions...</div><div style="font-size:12px;opacity:0.6;margin-top:6px;">Asking the AI to break your goal into milestones</div></div>';
  document.body.appendChild(overlay);

  // ── Dismiss handler — registered immediately, covers ALL states ──
  // (X button in error/empty/success overlays + backdrop click on any state)
  overlay.addEventListener('click', e => {
    if (e.target.closest('.ai-suggestion-cancel') || e.target === overlay) {
      overlay.remove();
    }
  });

  // Disable the trigger button
  const triggerBtn = document.querySelector('[data-action="ai-suggest-milestones"]');
  if (triggerBtn) triggerBtn.disabled = true;

  // ── Cancel previous in-flight request (avoids orphaned duplicates) ──
  if (_decomposeAbortController) {
    _decomposeAbortController.abort();
  }
  _decomposeAbortController = new AbortController();
  const signal = _decomposeAbortController.signal;

  let suggestions;
  try {
    const res = await _api('/goals/' + goalId + '/decompose', {
      method: 'POST',
      signal,
    });
    if (res.error) {
      overlay.innerHTML = '<div style="background:var(--surface,#222);border:1px solid var(--border,#444);border-radius:12px;padding:32px;text-align:center;"><div style="font-size:24px;margin-bottom:12px;">❌</div><div style="font-weight:600;color:var(--danger,#c44);">AI Error</div><div style="font-size:12px;opacity:0.6;margin-top:6px;">' + _esc(res.error) + '</div><button class="ai-suggestion-cancel" style="margin-top:16px;background:none;border:1px solid var(--border,#444);border-radius:6px;cursor:pointer;padding:6px 14px;font-size:13px;">Close</button></div>';
      if (triggerBtn) triggerBtn.disabled = false;
      return;
    }
    suggestions = res.suggestions || [];
  } catch (e) {
    // Silent abort — previous request was intentionally cancelled, not a real error
    if (e.name === 'AbortError') {
      overlay.remove();
      return;
    }
    overlay.innerHTML = '<div style="background:var(--surface,#222);border:1px solid var(--border,#444);border-radius:12px;padding:32px;text-align:center;"><div style="font-size:24px;margin-bottom:12px;">❌</div><div style="font-weight:600;color:var(--danger,#c44);">Request Failed</div><div style="font-size:12px;opacity:0.6;margin-top:6px;">' + _esc(e.message) + '</div><button class="ai-suggestion-cancel" style="margin-top:16px;background:none;border:1px solid var(--border,#444);border-radius:6px;cursor:pointer;padding:6px 14px;font-size:13px;">Close</button></div>';
    if (triggerBtn) triggerBtn.disabled = false;
    return;
  }

  if (triggerBtn) triggerBtn.disabled = false;

  if (suggestions.length === 0) {
    overlay.innerHTML = '<div style="background:var(--surface,#222);border:1px solid var(--border,#444);border-radius:12px;padding:32px;text-align:center;"><div style="font-size:24px;margin-bottom:12px;">📭</div><div style="font-weight:600;">No Suggestions</div><div style="font-size:12px;opacity:0.6;margin-top:6px;">AI returned no milestone suggestions.</div><button class="ai-suggestion-cancel" style="margin-top:16px;background:none;border:1px solid var(--border,#444);border-radius:6px;cursor:pointer;padding:6px 14px;font-size:13px;">Close</button></div>';
    return;
  }

  // ── Build editable suggestion list ──
  const today = new Date().toISOString().slice(0, 10);
  let html = '<div style="background:var(--surface,#222);border:1px solid var(--border,#444);border-radius:12px;padding:20px;width:500px;max-width:90vw;max-height:85vh;overflow-y:auto;">';
  html += '<h4 style="margin:0 0 4px 0;">AI-Suggested Milestones</h4>';
  html += '<p style="margin:0 0 16px 0;font-size:12px;opacity:0.6;">Review and edit. Click <strong>Add Selected</strong> to create the checked milestones.</p>';
  html += '<div id="ai-suggestions-list">';
  suggestions.forEach((s, i) => {
    // Validate date — reject past or invalid dates, leave blank
    let dateVal = (s.suggested_target_date || '').trim();
    if (dateVal) {
      // Must be YYYY-MM-DD and in the future
      if (!/^\d{4}-\d{2}-\d{2}$/.test(dateVal) || dateVal <= today) {
        dateVal = '';
        console.warn('Decompose suggestion #' + i + ' had invalid/past date, overriding to blank:', s.suggested_target_date);
      }
    }
    // Ensure description is non-empty for display
    const desc = (s.description || '').trim();
    html += '<div class="ai-suggestion-item" data-idx="' + i + '" style="background:var(--input-bg,#333);border:1px solid var(--border,#444);border-radius:6px;padding:10px 12px;margin-bottom:8px;">';
    html += '<div style="display:flex;align-items:flex-start;gap:8px;">';
    // Checkbox: no hover transform, box-sizing reserved
    html += '<input type="checkbox" class="ai-suggestion-check" data-idx="' + i + '" checked style="margin-top:3px;cursor:pointer;width:14px;height:14px;flex-shrink:0;box-sizing:border-box;border:1px solid transparent;">';
    html += '<div style="flex:1;min-width:0;">';
    html += '<input class="ai-suggestion-title" data-idx="' + i + '" value="' + _esc(s.title) + '" maxlength="80" style="width:100%;background:transparent;border:1px solid transparent;border-radius:4px;padding:2px 4px;font-weight:600;font-size:13px;color:var(--text,#eee);outline:none;box-sizing:border-box;" onfocus="this.style.borderColor=\'var(--border,#444)\'" onblur="this.style.borderColor=\'transparent\'">';
    html += '<input class="ai-suggestion-desc" data-idx="' + i + '" value="' + _esc(desc || '') + '" maxlength="200" placeholder="Description (required) — explain why this milestone matters" style="width:100%;background:transparent;border:1px solid transparent;border-radius:4px;padding:2px 4px;font-size:11px;opacity:0.7;color:var(--text,#eee);outline:none;margin-top:2px;box-sizing:border-box;" onfocus="this.style.borderColor=\'var(--border,#444)\'" onblur="this.style.borderColor=\'transparent\'">';
    html += '<input class="ai-suggestion-date" data-idx="' + i + '" type="date" value="' + dateVal + '" style="margin-top:4px;background:transparent;border:1px solid transparent;border-radius:4px;padding:2px 4px;font-size:11px;opacity:0.6;color:var(--text,#eee);outline:none;cursor:text;box-sizing:border-box;" onfocus="this.style.borderColor=\'var(--border,#444)\'" onblur="this.style.borderColor=\'transparent\'">';
    html += '</div>';
    html += '<button class="ai-suggestion-remove" data-idx="' + i + '" style="background:none;border:none;cursor:pointer;color:var(--danger,#c44);font-size:14px;padding:2px;line-height:1;">✕</button>';
    html += '</div></div>';
  });
  html += '</div>';
  html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-top:12px;">';
  html += '<span style="font-size:11px;opacity:0.5;" id="ai-suggestion-count">' + suggestions.length + ' suggestion(s)</span>';
  html += '<div style="display:flex;gap:8px;">';
  html += '<button class="ai-suggestion-cancel" style="background:none;border:1px solid var(--border,#444);border-radius:6px;cursor:pointer;padding:6px 14px;font-size:13px;">Cancel</button>';
  html += '<button class="ai-suggestion-add" style="background:var(--accent,#5b9);color:var(--accent-text,#fff);border:none;border-radius:6px;cursor:pointer;padding:6px 14px;font-size:13px;font-weight:600;">Add Selected</button>';
  html += '</div></div></div>';
  overlay.innerHTML = html;

  // ── Success-specific handlers (dismiss is already handled by global listener) ──
  overlay.addEventListener('click', async e => {
    if (e.target.closest('.ai-suggestion-cancel') || e.target === overlay) {
      return; // covered by global dismiss listener
    }

    if (e.target.closest('.ai-suggestion-add')) {
      const items = overlay.querySelectorAll('.ai-suggestion-item');
      const selected = [];
      items.forEach(item => {
        const cb = item.querySelector('.ai-suggestion-check');
        if (!cb || !cb.checked) return;
        const titleInput = item.querySelector('.ai-suggestion-title');
        const descInput = item.querySelector('.ai-suggestion-desc');
        const dateInput = item.querySelector('.ai-suggestion-date');
        const title = (titleInput?.value || '').trim();
        if (!title) return;
        selected.push({
          title: title.slice(0, 200),
          description: (descInput?.value || '').trim() || null,
          target_date: dateInput?.value || null,
        });
      });

      if (selected.length === 0) { alert('No milestones selected.'); return; }

      const btn = e.target.closest('.ai-suggestion-add');
      if (btn) btn.disabled = true;

      let created = 0;
      for (const ms of selected) {
        try {
          await _api('/goals/' + goalId + '/milestones', {
            method: 'POST',
            body: JSON.stringify(ms),
          });
          created++;
        } catch (err) {
          console.error('Failed to create milestone:', err);
        }
      }

      overlay.remove();
      if (created > 0) {
        alert('Created ' + created + ' milestone(s)!');
        await openGoal(goalId);
      } else {
        alert('Failed to create milestones.');
      }
      return;
    }

    if (e.target.closest('.ai-suggestion-remove')) {
      const item = e.target.closest('.ai-suggestion-item');
      if (item) item.remove();
      const count = overlay.querySelector('#ai-suggestion-count');
      const remaining = overlay.querySelectorAll('.ai-suggestion-item').length;
      if (count) count.textContent = remaining + ' suggestion(s)';
    }
  });
}

// ─── Helpers ──────────────────────────────────────────────────────────────

function _esc(s) {
  if (typeof s !== 'string') return s ?? '';
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function _fmtDate(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    return d.toLocaleDateString();
  } catch (_) { return iso; }
}

// ─── Export ───────────────────────────────────────────────────────────────

const goalsModule = { openGoals, closeGoals, isGoalsOpen, openGoal, backToList, editGoal, editGoalFromList, setStatus, deleteGoal, addMilestone, addMilestoneToList, editMilestone, deleteMilestone, toggleMilestone, _runResearchNow, _reviewResearch, _showPendingResearch };
export default goalsModule;
window.goalsModule = goalsModule;
