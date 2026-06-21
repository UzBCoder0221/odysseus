import Storage from './storage.js';

const PROFILE_KEY = Storage.KEYS.COMPANION_PROFILE;
const LOG_KEY = Storage.KEYS.COMPANION_LOG;
const TASKS_CACHE_KEY = 'odysseus-companion-tasks-cache';
const NUDGE_KEY = 'odysseus-companion-last-nudge-ts';
const CHAT_KEY = 'odysseus-companion-chat-msgs';
const API_BASE = '';

function el(id) { return document.getElementById(id); }

function defaultProfile() {
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  return {
    displayName: '',
    timezone: tz,
    conditions: [],
    energyPattern: 'Variable',
    idealSleepHours: 8,
    birthday: '',
    mbtiType: '',
    enneagramType: '',
    additionalConditions: '',
    sleepScheduleStart: '',
    sleepScheduleEnd: '',
    systemInfo: null,
    systemInfoUpdatedAt: null,
  };
}

function loadProfile() {
  return Storage.getJSON(PROFILE_KEY, defaultProfile());
}

function saveProfile(p) {
  Storage.setJSON(PROFILE_KEY, p);
  fetch(`${API_BASE}/api/companion/profile`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      display_name: p.displayName || '',
      timezone: p.timezone || '',
      conditions: p.conditions || [],
      energy_pattern: p.energyPattern || 'Variable',
      ideal_sleep_hours: p.idealSleepHours || 8,
      birthday: p.birthday || null,
      mbti_type: p.mbtiType || null,
      enneagram_type: p.enneagramType || null,
      additional_conditions: p.additionalConditions || null,
      sleep_schedule_start: p.sleepScheduleStart || null,
      sleep_schedule_end: p.sleepScheduleEnd || null,
    })
  }).catch(() => {});
}

function loadLog() {
  return Storage.getJSON(LOG_KEY, {});
}

function saveLog(log) {
  Storage.setJSON(LOG_KEY, log);
}

function todayKey() {
  return new Date().toISOString().slice(0, 10);
}

function timeOfDay() {
  const h = new Date().getHours();
  if (h < 12) return 'morning';
  if (h < 17) return 'afternoon';
  if (h < 21) return 'evening';
  return 'night';
}

/* ── Task helpers ── */
function loadTasks() {
  return Storage.getJSON(TASKS_CACHE_KEY, []);
}

function saveTasks(tasks) {
  Storage.setJSON(TASKS_CACHE_KEY, tasks);
}

async function fetchTasksFromBackend() {
  try {
    const r = await fetch(`${API_BASE}/api/companion/tasks?date=${todayKey()}`);
    const data = await r.json();
    if (data.tasks) {
      saveTasks(data.tasks);
      return data.tasks;
    }
  } catch (_) {}
  return loadTasks();
}

async function syncTaskToBackend(task) {
  try {
    if (task.id) {
      await fetch(`${API_BASE}/api/companion/tasks/${task.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(task)
      });
      return task.id;
    } else {
      const r = await fetch(`${API_BASE}/api/companion/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...task, date: todayKey() })
      });
      const data = await r.json();
      if (data.id) task.id = data.id;
      return data.id || null;
    }
  } catch (_) { return null; }
}

async function deleteTaskFromBackend(taskId) {
  try {
    await fetch(`${API_BASE}/api/companion/tasks/${taskId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: 'deleted' })
    });
  } catch (_) {}
}

/* ── Companion state bar ── */
function renderBar() {
  const bar = el('companion-bar');
  if (!bar) return;
  const log = loadLog();
  const today = log[todayKey()];
  const profile = loadProfile();
  const tasks = loadTasks();
  const totalTasks = tasks.filter(t => t.status !== 'done').length;
  const doneTasks = tasks.filter(t => t.status === 'done').length;
  const briefingAvailable = today && today.briefing;
  const midAvailable = today && today.mid_mood;
  const eodAvailable = today && today.eod_rating;

  let scoresHtml = '';
  if (today) {
    let extras = '';
    if (midAvailable) extras += `<span class="companion-dot" style="background:${moodColor(today.mid_mood, true)}" title="Mid-mood: ${today.mid_mood}/5"></span>`;
    if (eodAvailable) extras += `<span class="companion-star" title="Day rating: ${today.eod_rating}/5">${'★'.repeat(today.eod_rating)}${'☆'.repeat(5 - today.eod_rating)}</span>`;
    scoresHtml = `
      <div class="companion-bar-scores">
        <span class="companion-dot" style="background:${moodColor(today.mood)}" title="Mood: ${today.mood}/10"></span>
        <span class="companion-dot" style="background:${energyColor(today.energy)}" title="Energy: ${today.energy}/10"></span>
        <span class="companion-sleep" title="Sleep: ${today.sleep}h">${today.sleep}h</span>
        ${briefingAvailable ? '<button class="companion-bar-btn" id="companion-briefing-btn" title="Today\'s Briefing">📋</button>' : ''}
        ${extras}
      </div>
      <div class="companion-bar-message">${esc(today.message || '')}</div>`;
  } else {
    scoresHtml = '<span class="companion-bar-prompt">No check-in yet today</span>';
  }

  const taskInfo = totalTasks > 0 ? `<span class="companion-bar-task-info">${doneTasks}/${totalTasks}</span>` : '';
  const primaryClass = today ? '' : ' primary';

  bar.innerHTML = `
    <div class="companion-bar-inner">
      ${scoresHtml}
      ${taskInfo}
      <button class="companion-bar-checkin-btn${primaryClass}" id="companion-open-btn">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 1 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>
        ${today ? '' : '<span>Check in</span>'}
      </button>
    </div>`;

  el('companion-open-btn')?.addEventListener('click', openPanel);
  el('companion-briefing-btn')?.addEventListener('click', () => { openPanel(); switchTab('today'); });
  renderPanelOpener();
}

/* ── Side panel ── */
function openPanel() {
  const panel = el('companion-panel');
  const overlay = el('companion-overlay');
  if (panel) panel.classList.remove('hidden');
  if (overlay) overlay.classList.remove('hidden');
  populateCheckinForm();
  populateProfileForm();
  loadTodayTab();
  renderSysInfo();
}

function closePanel() {
  const panel = el('companion-panel');
  const overlay = el('companion-overlay');
  if (panel) panel.classList.add('hidden');
  if (overlay) overlay.classList.add('hidden');
  el('companion-chat-card')?.classList.remove('expanded');
}

function switchTab(tab) {
  const panel = el('companion-panel');
  if (!panel) return;
  panel.querySelectorAll('[data-companion-tab]').forEach(b => b.classList.toggle('active', b.dataset.companionTab === tab));
  panel.querySelectorAll('[data-companion-panel]').forEach(p => p.classList.toggle('hidden', p.dataset.companionPanel !== tab));
  if (tab === 'history') updateHistoryTab();
  if (tab === 'today') loadTodayTab();
  if (tab === 'checkin') populateCheckinForm();
  if (tab === 'profile') populateProfileForm();
  if (tab === 'lifestyle') populateLifestyleForm();
}

function populateCheckinForm() {
  const log = loadLog();
  const today = log[todayKey()];
  const moodSlider = el('companion-mood');
  const energySlider = el('companion-energy');
  const sleepInput = el('companion-sleep');
  const textInput = el('companion-text');
  const moodVal = el('companion-mood-val');
  const energyVal = el('companion-energy-val');

  if (today) {
    if (moodSlider) moodSlider.value = today.mood;
    if (energySlider) energySlider.value = today.energy;
    if (sleepInput) sleepInput.value = today.sleep;
    if (textInput) textInput.value = today.text || '';
  } else {
    if (moodSlider) moodSlider.value = 6;
    if (energySlider) energySlider.value = 5;
    if (sleepInput) sleepInput.value = '';
    if (textInput) textInput.value = '';
  }
  if (moodVal) moodVal.textContent = moodSlider ? moodSlider.value : '6';
  if (energyVal) energyVal.textContent = energySlider ? energySlider.value : '5';

  const briefBox = el('companion-briefing-box');
  if (briefBox) {
    if (today && today.briefing) {
      briefBox.innerHTML = `<div class="companion-briefing-card"><div class="companion-briefing-title">Today's Briefing</div><div class="companion-briefing-text">${esc(today.briefing)}</div></div>`;
    } else {
      briefBox.innerHTML = '';
    }
  }
}

async function submitCheckin() {
  const mood = parseInt(el('companion-mood')?.value, 10) || 5;
  const energy = parseInt(el('companion-energy')?.value, 10) || 5;
  const sleep = parseFloat(el('companion-sleep')?.value) || 0;
  const text = (el('companion-text')?.value || '').trim().slice(0, 200);
  const profile = loadProfile();
  const key = todayKey();

  const entry = {
    mood, energy, sleep, text,
    timestamp: new Date().toISOString(),
    message: '',
    briefing: ''
  };

  const log = loadLog();
  const existing = log[key];
  entry.briefing = existing?.briefing || '';

  log[key] = entry;
  saveLog(log);

  const submitBtn = el('companion-submit');
  if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = '...'; }

  try {
    const res = await fetch(`${API_BASE}/api/companion/message`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mood, energy, sleep, conditions: profile.conditions, time_of_day: timeOfDay() })
    });
    const data = await res.json();
    if (data.message) entry.message = data.message;
  } catch (_) {}

  try {
    const briefRes = await fetch(`${API_BASE}/api/companion/briefing`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        mood, energy, sleep,
        conditions: profile.conditions,
        energy_pattern: profile.energyPattern,
        time_of_day: timeOfDay()
      })
    });
    const briefData = await briefRes.json();
    if (briefData.briefing) entry.briefing = briefData.briefing;
  } catch (_) {}

  log[key] = entry;
  saveLog(log);

  try {
    await fetch(`${API_BASE}/api/companion/checkins`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        date: key, mood, energy, sleep, text,
        message: entry.message || '',
        briefing: entry.briefing || ''
      })
    });
  } catch (_) {}

  if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = 'Save'; }
  closePanel();
  renderBar();
}

/* ── History tab ── */
function updateHistoryTab() {
  const container = el('companion-history-list');
  if (!container) return;
  const log = loadLog();
  const entries = Object.entries(log).sort((a, b) => b[0].localeCompare(a[0]));

  let html = '<div class="companion-table-legend">';
  html += '<span><span class="companion-dot" style="background:#e06c75;display:inline-block;width:7px;height:7px;"></span> Low (&lt;4)</span>';
  html += '<span><span class="companion-dot" style="background:#e5c07b;display:inline-block;width:7px;height:7px;"></span> Okay (4–6)</span>';
  html += '<span><span class="companion-dot" style="background:#98c379;display:inline-block;width:7px;height:7px;"></span> Good (&gt;6)</span>';
  html += '</div>';

  html += '<div class="companion-table-header"><span>Date</span><span>Mood</span><span>Energy</span><span>Sleep</span><span>Mid</span><span>EOD</span><span>Note</span></div>';

  if (entries.length === 0) {
    html += '<div class="companion-history-empty">No check-ins yet. Start by checking in!</div>';
  } else if (entries.length === 1) {
    const [date, entry] = entries[0];
    html += renderHistoryRow(date, entry);
    html += '<div class="companion-history-empty" style="padding:12px 0;opacity:0.6;">Keep checking in — patterns appear after a few days</div>';
  } else {
    for (const [date, entry] of entries) {
      html += renderHistoryRow(date, entry);
    }
  }
  container.innerHTML = html;
  updateHistoryDetail();
  updateMoodGrid();
  updatePatternsCard();
}

function renderHistoryRow(date, entry) {
  const md = moodColor(entry.mood);
  const ed = energyColor(entry.energy);
  let midHtml = '';
  if (entry.mid_mood) {
    midHtml = `<span class="companion-dot" style="background:${moodColor(entry.mid_mood, true)}" title="Mid: ${entry.mid_mood}/5"></span>${entry.mid_mood}`;
  }
  let eodHtml = '';
  if (entry.eod_rating) {
    eodHtml = `<span class="companion-star-small">${'★'.repeat(entry.eod_rating)}${'☆'.repeat(5 - entry.eod_rating)}</span>`;
  } else if (entry.eod_done) {
    eodHtml = '<span title="Has reflection">📝</span>';
  }
  return `<div class="companion-history-entry">
    <div class="companion-history-date">${esc(date)}</div>
    <div class="companion-history-dots"><span class="companion-dot" style="background:${md}"></span>${entry.mood}</div>
    <div class="companion-history-dots"><span class="companion-dot" style="background:${ed}"></span>${entry.energy}</div>
    <div class="companion-history-sleep">${entry.sleep}h</div>
    <div class="companion-history-mid">${midHtml}</div>
    <div class="companion-history-eod">${eodHtml}</div>
    <div class="companion-history-text">${esc(entry.text || '')}</div>
  </div>`;
}

function updateHistoryDetail() {
  const container = el('companion-history-detail');
  if (!container) return;
  const log = loadLog();
  const entries = Object.entries(log).sort((a, b) => b[0].localeCompare(a[0]));
  let html = '';
  for (const [date, entry] of entries) {
    const parts = [];
    if (entry.mid_feeling) parts.push(`Mid: ${esc(entry.mid_feeling)}`);
    if (entry.eod_done) parts.push(`Went well: ${esc(entry.eod_done)}`);
    if (entry.eod_blocked) parts.push(`Blocked: ${esc(entry.eod_blocked)}`);
    if (entry.eod_tomorrow) parts.push(`Tomorrow: ${esc(entry.eod_tomorrow)}`);
    if (entry.eod_message) parts.push(`Closure: ${esc(entry.eod_message)}`);
    if (parts.length > 0) {
      html += `<div class="companion-history-detail-row"><span class="companion-history-date" style="flex-shrink:0;">${esc(date)}</span><span>${parts.join(' · ')}</span></div>`;
    }
  }
  if (!html) html = '<div style="padding:8px 0;font-size:11px;opacity:0.5;">No mid-day or EOD reflections yet.</div>';
  container.innerHTML = html;
}

function updateMoodGrid() {
  const grid = el('companion-mood-grid');
  if (!grid) return;
  const log = loadLog();
  const entries = Object.entries(log).sort((a, b) => a[0].localeCompare(b[0]));
  const last7 = entries.slice(-7);

  let html = '<div class="companion-grid-labels">';
  html += '<div class="companion-grid-label" style="font-size:11px;opacity:0.5;margin-bottom:4px;">Score</div>';
  html += '<div class="companion-grid-y-axis">';
  for (let s = 10; s >= 1; s--) {
    html += `<span style="font-size:8px;opacity:0.35;">${s}</span>`;
  }
  html += '</div>';
  html += '</div>';

  html += '<div style="flex:1;">';
  html += '<div class="companion-grid-label" style="font-size:10px;opacity:0.5;margin-bottom:4px;text-align:center;">Last 7 days</div>';
  html += '<div class="companion-grid">';
  for (const [date, entry] of last7) {
    const short = date.slice(5);
    const md = moodColor(entry.mood);
    const ed = energyColor(entry.energy);
    html += `<div class="companion-grid-day">
      <span class="companion-grid-dot" style="background:${ed}" title="Energy: ${entry.energy}"></span>
      <span class="companion-grid-dot" style="background:${md}" title="Mood: ${entry.mood}"></span>
      <span class="companion-grid-date">${esc(short)}</span>
    </div>`;
  }
  html += '</div>';
  html += '<div class="companion-grid-legend" style="display:flex;gap:10px;justify-content:center;margin-top:6px;font-size:10px;opacity:0.5;">';
  html += '<span><span class="companion-dot" style="background:#c678dd;display:inline-block;width:7px;height:7px;"></span> Mood</span>';
  html += '<span><span class="companion-dot" style="background:#61afef;display:inline-block;width:7px;height:7px;"></span> Energy</span>';
  html += '</div>';
  html += '</div>';

  grid.innerHTML = html;
}

/* ── Patterns card ── */
async function updatePatternsCard() {
  const container = el('companion-patterns-content');
  if (!container) return;
  try {
    const res = await fetch(`${API_BASE}/api/companion/patterns`);
    const data = await res.json();
    if (data.insufficient_data) {
      container.innerHTML = '<div class="patterns-insufficient">Check in for a few more days and patterns will start showing up here.</div>';
      return;
    }
    const lines = [];
    if (data.mood_trend) {
      const trendWord = { declining: 'trending down', stable: 'stable', improving: 'improving' }[data.mood_trend] || data.mood_trend;
      lines.push(`Your mood has been ${trendWord} compared to last week.`);
    }
    if (data.checkin_streak_days && data.checkin_streak_days >= 3) {
      lines.push(`You've checked in ${data.checkin_streak_days} days in a row.`);
    }
    if (data.sleep_trend) {
      const sleepWord = { declining: 'trending down', stable: 'stable', improving: 'improving' }[data.sleep_trend] || data.sleep_trend;
      lines.push(`Sleep has been ${sleepWord}.`);
    }
    if (data.common_carry_over_tasks && data.common_carry_over_tasks.length > 0) {
      lines.push(`Tasks you've carried over a few times: ${data.common_carry_over_tasks.join(', ')}.`);
    }
    if (data.low_mood_days_of_week && data.low_mood_days_of_week.length > 0) {
      lines.push(`Low mood tends to happen on: ${data.low_mood_days_of_week.join(', ')}.`);
    }
    container.innerHTML = lines.map(l => `<div class="patterns-line">${esc(l)}</div>`).join('');

    /* Fetch suggestion */
    const sugRes = await fetch(`${API_BASE}/api/companion/patterns/suggestion`);
    const sugData = await sugRes.json();
    const sugEl = el('companion-patterns-suggestion');
    if (sugEl && sugData.suggestion && !sugData.suggestion.includes('Complete more check-ins')) {
      sugEl.innerHTML = `<div class="patterns-suggestion-title">💡 Suggestion</div><div class="patterns-suggestion-text">${esc(sugData.suggestion)}</div>`;
      sugEl.classList.remove('hidden');
    } else if (sugEl) {
      sugEl.classList.add('hidden');
    }
  } catch (_) {
    container.innerHTML = '<div class="patterns-insufficient">Could not load patterns.</div>';
  }
}

/* ── Today tab (briefing + tasks + mid-day + EOD + chat) ── */
function loadTodayTab() {
  const briefContainer = el('companion-briefing-today');
  const log = loadLog();
  const today = log[todayKey()];
  if (briefContainer) {
    if (today && today.briefing) {
      briefContainer.innerHTML = `<div class="companion-briefing-title">Today's Briefing</div><div class="companion-briefing-text">${esc(today.briefing)}</div>`;
      briefContainer.classList.remove('hidden');
    } else {
      briefContainer.classList.add('hidden');
    }
  }
  updateMiddayCard();
  updateEodCard();
  renderTaskList();
}

/* ── Mid-day micro check-in ── */
function updateMiddayCard() {
  const card = el('companion-midday-card');
  if (!card) return;
  const log = loadLog();
  const today = log[todayKey()];
  const h = new Date().getHours();
  const isMiddayTime = h >= 11 && h < 16;
  const alreadyDone = today && today.mid_mood;

  if (!isMiddayTime || alreadyDone) {
    card.classList.add('hidden');
    return;
  }
  card.classList.remove('hidden');

  if (today) {
    const ms = el('companion-mid-mood');
    const es = el('companion-mid-energy');
    if (ms) ms.value = Math.min(5, Math.round(today.mood / 2)) || 3;
    if (es) es.value = Math.min(5, Math.round(today.energy / 2)) || 3;
    if (el('companion-mid-mood-val')) el('companion-mid-mood-val').textContent = ms ? ms.value : 3;
    if (el('companion-mid-energy-val')) el('companion-mid-energy-val').textContent = es ? es.value : 3;
  }
}

async function submitMidday() {
  const mood = parseInt(el('companion-mid-mood')?.value, 10) || 3;
  const energy = parseInt(el('companion-mid-energy')?.value, 10) || 3;
  const feeling = el('companion-feeling-grid')?.querySelector('.companion-feeling-btn.selected')?.dataset.feeling || '';

  const log = loadLog();
  const today = log[todayKey()] || {};
  const moodDelta = today.mood ? Math.round(mood * 2) - today.mood : 0;

  try {
    const res = await fetch(`${API_BASE}/api/companion/checkins/today`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        mid_mood: mood,
        mid_energy: energy,
        mid_feeling: feeling
      })
    });
    const data = await res.json();
    if (data.ok) {
      today.mid_mood = mood;
      today.mid_energy = energy;
      today.mid_feeling = feeling;
      log[todayKey()] = today;
      saveLog(log);

      /* Fetch AI message with mood delta */
      const profile = loadProfile();
      const msgRes = await fetch(`${API_BASE}/api/companion/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mood: Math.round(mood * 2),
          energy: Math.round(energy * 2),
          sleep: today.sleep || 0,
          conditions: profile.conditions,
          time_of_day: timeOfDay(),
          mood_delta: moodDelta
        })
      });
      const msgData = await msgRes.json();
      const msgEl = el('companion-mid-message');
      if (msgEl && msgData.message) {
        msgEl.textContent = msgData.message;
        msgEl.classList.remove('hidden');
      }

      el('companion-mid-submit').textContent = 'Saved ✓';
      setTimeout(() => { el('companion-mid-submit').textContent = 'Reflect'; }, 2000);
      renderBar();
    }
  } catch (_) {}
}

/* ── EOD reflection ── */
function updateEodCard() {
  const card = el('companion-eod-card');
  if (!card) return;
  const log = loadLog();
  const today = log[todayKey()];
  const h = new Date().getHours();
  const isEodTime = h >= 18 || h < 6;
  const alreadyDone = today && today.eod_rating;

  if (!isEodTime || alreadyDone) {
    card.classList.add('hidden');
    return;
  }
  card.classList.remove('hidden');

  if (today && today.eod_done) {
    if (el('companion-eod-done')) el('companion-eod-done').value = today.eod_done;
    if (el('companion-eod-blocked')) el('companion-eod-blocked').value = today.eod_blocked || '';
    if (el('companion-eod-tomorrow')) el('companion-eod-tomorrow').value = today.eod_tomorrow || '';
    if (el('companion-eod-rating')) el('companion-eod-rating').value = today.eod_rating || 3;
    if (el('companion-eod-rating-val')) el('companion-eod-rating-val').textContent = today.eod_rating || 3;
  }
}

async function submitEod() {
  const done = (el('companion-eod-done')?.value || '').trim();
  const blocked = (el('companion-eod-blocked')?.value || '').trim();
  const tomorrow = (el('companion-eod-tomorrow')?.value || '').trim();
  const rating = parseInt(el('companion-eod-rating')?.value, 10) || 3;

  const log = loadLog();
  const today = log[todayKey()] || {};

  try {
    const res = await fetch(`${API_BASE}/api/companion/checkins/today`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        eod_done: done,
        eod_blocked: blocked,
        eod_tomorrow: tomorrow,
        eod_rating: rating
      })
    });
    const data = await res.json();
    if (data.ok) {
      today.eod_done = done;
      today.eod_blocked = blocked;
      today.eod_tomorrow = tomorrow;
      today.eod_rating = rating;
      log[todayKey()] = today;
      saveLog(log);

      /* Generate AI closing message */
      const profile = loadProfile();
      try {
        const msgRes = await fetch(`${API_BASE}/api/companion/message`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            mood: today.mood || 5,
            energy: today.energy || 5,
            sleep: today.sleep || 0,
            conditions: profile.conditions,
            time_of_day: 'night'
          })
        });
        const msgData = await msgRes.json();
        if (msgData.message) {
          today.eod_message = msgData.message;
          log[todayKey()] = today;
          saveLog(log);
          const msgEl = el('companion-eod-message');
          if (msgEl) {
            msgEl.textContent = msgData.message;
            msgEl.classList.remove('hidden');
          }
          /* Also save EOD message to backend */
          await fetch(`${API_BASE}/api/companion/checkins/today`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ eod_message: msgData.message })
          });
        }
      } catch (_) {}

      el('companion-eod-submit').textContent = 'Saved ✓';
      setTimeout(() => { el('companion-eod-submit').textContent = 'Reflect'; }, 2000);
      renderBar();
    }
  } catch (_) {}
}

/* ── Just talk chat ── */
function loadChatMessages() {
  return Storage.getJSON(CHAT_KEY, []);
}

function saveChatMessages(msgs) {
  Storage.setJSON(CHAT_KEY, msgs);
}

function clearChatMessages() {
  Storage.remove(CHAT_KEY);
}

function renderChatLog() {
  const log = el('companion-chat-log');
  if (!log) return;
  const msgs = loadChatMessages();
  if (msgs.length === 0) {
    log.innerHTML = '<div class="companion-chat-empty">Share anything — thoughts, feelings, or just say hi.</div>';
    return;
  }
  log.innerHTML = msgs.slice(-20).map(m => {
    const isUser = m.role === 'user';
    return `<div class="companion-chat-msg ${isUser ? 'chat-msg-user' : 'chat-msg-ai'}">${esc(m.content)}</div>`;
  }).join('');
  log.scrollTop = log.scrollHeight;
}

/* ── Pending state for Just talk chat ── */
let _chatPending = false;
let _lastUserMessage = '';

async function sendChatMessage(retryMsg) {
  const input = el('companion-chat-input');
  const text = retryMsg || (input?.value || '').trim();
  if (!text || _chatPending) return;
  _lastUserMessage = text;
  if (input && !retryMsg) input.value = '';

  _chatPending = true;
  const sendBtn = el('companion-chat-send');
  if (sendBtn) sendBtn.disabled = true;
  if (input) input.disabled = true;

  const msgs = loadChatMessages();
  /* Only add user message if not a retry (already saved) */
  if (!retryMsg) {
    msgs.push({ role: 'user', content: text });
    saveChatMessages(msgs);
    renderChatLog();
  }

  /* Add typing indicator */
  const log = el('companion-chat-log');
  const typingEl = document.createElement('div');
  typingEl.className = 'companion-chat-msg companion-typing';
  typingEl.id = 'companion-typing-indicator';
  typingEl.innerHTML = '<span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span>';
  log.appendChild(typingEl);
  log.scrollTop = log.scrollHeight;

  /* 30s timeout */
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 30000);

  const profile = loadProfile();
  const logData = loadLog();
  const today = logData[todayKey()] || {};

  try {
    const res = await fetch(`${API_BASE}/api/companion/message`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: controller.signal,
      body: JSON.stringify({
        messages: msgs.map(m => ({ role: m.role, content: m.content })),
        mood: today.mood || 5,
        energy: today.energy || 5,
        sleep: today.sleep || 0,
        conditions: profile.conditions,
        time_of_day: timeOfDay()
      })
    });
    const data = await res.json();

    /* Remove typing indicator */
    const ti = document.getElementById('companion-typing-indicator');
    if (ti) ti.remove();

    if (data.message) {
      msgs.push({ role: 'assistant', content: data.message });
      saveChatMessages(msgs);
      renderChatLog();
    } else {
      /* Empty or error response — show fallback */
      msgs.push({ role: 'assistant', content: 'I\'m here.' });
      saveChatMessages(msgs);
      renderChatLog();
    }
  } catch (_) {
    /* Remove typing indicator */
    const ti = document.getElementById('companion-typing-indicator');
    if (ti) ti.remove();

    /* Show error bubble with retry */
    if (log) {
      const errEl = document.createElement('div');
      errEl.className = 'companion-chat-msg chat-msg-error';
      errEl.innerHTML = '<span>⚠️ Something went wrong</span> <button class="chat-retry-btn" data-companion-retry>Retry</button>';
      log.appendChild(errEl);
      log.scrollTop = log.scrollHeight;
    }
  } finally {
    clearTimeout(timeoutId);
    _chatPending = false;
    if (sendBtn) sendBtn.disabled = false;
    if (input) input.disabled = false;
    if (input && !retryMsg) input.focus();
  }
}

/* ── Nudge system ── */
function shouldNudge() {
  const log = loadLog();
  const today = log[todayKey()];
  const h = new Date().getHours();
  /* Only between 08:00 and 21:00 */
  if (h < 8 || h >= 21) return null;
  /* No nudge if no morning check-in (except after 20:00 for EOD) */
  if (!today && h < 20) return null;
  /* Already checked in — check mid-day and EOD */
  if (today) {
    const isMiddayTime = h >= 11 && h < 16;
    if (isMiddayTime && !today.mid_mood) return 'midday';
    if (h >= 18 && !today.eod_rating) return 'eod';
  }
  /* General nudge for afternoon if no check-in */
  if (!today && h < 20) return 'checkin';
  if (!today && h >= 20) return 'eod';
  return null;
}

function getNudgeMessages(type, taskTitle, mealLabel) {
  const nudges = {
    greeting: ['Hey, just checking in. How are you feeling?'],
    midday: ['Quick check: how\'s your day going so far?', 'Mid-day pause — how are you holding up?', 'Halfway through the day — how\'s the energy?'],
    eod: ['Evening reflection time. How was your day?', 'Day\'s winding down — want to reflect?', 'End-of-day pause — what went well?'],
    move: ['Time to stretch those legs. Walk a few steps?', 'Stand up, roll your shoulders, breathe.'],
    drink: ['Sip some water. Your brain will thank you.', 'Hydration check: had water recently?'],
    breathe: ['Close your eyes. Take three slow breaths.', 'Breathe in for 4, hold for 4, out for 4.'],
    task_pre: ['Reminder: "' + (taskTitle || 'a task') + '" is due soon.', 'Heads up — "' + (taskTitle || 'a task') + '" is coming up.'],
    task_due: ['"' + (taskTitle || 'A task') + '" is due now!', 'Time for "' + (taskTitle || 'a task') + '" — it\'s due.'],
    task_progress: ['How\'s "' + (taskTitle || 'your task') + '" going? Need a hand?', 'Still working on "' + (taskTitle || 'that task') + '"?'],
    meal_time: ['Around ' + (mealLabel || 'meal') + ' time — remember to eat something?'],
  };
  return nudges[type] || nudges.greeting;
}

function checkTaskReminders() {
  const tasks = loadTasks();
  const now = new Date();
  const currentMin = now.getHours() * 60 + now.getMinutes();
  let best = null;

  for (const task of tasks) {
    if (task.status === 'done' || !task.due_time) continue;
    const parts = task.due_time.split(':');
    const taskMin = parseInt(parts[0], 10) * 60 + parseInt(parts[1], 10);
    const diff = taskMin - currentMin;

    /* Pre-reminder: 15-20 min before due */
    if (diff >= 15 && diff <= 20 && !task.reminder_sent_pre) {
      if (!best || best.priority < 2) best = { priority: 2, type: 'task_pre', task: task };
    }
    /* Due reminder: within 5 min of due */
    if (diff >= -5 && diff <= 5 && !task.reminder_sent_due) {
      if (!best || best.priority < 3) best = { priority: 3, type: 'task_due', task: task };
    }
    /* Progress check: started but not done, check >30 min ago */
    if (task.status === 'in_progress' && task.started_at) {
      const lastCheck = task.last_progress_check_ts ? new Date(task.last_progress_check_ts).getTime() : 0;
      if (Date.now() - lastCheck > 30 * 60 * 1000) {
        if (!best || best.priority < 1) best = { priority: 1, type: 'task_progress', task: task };
      }
    }
  }
  return best;
}

function checkMealNudge() {
  const data = Storage.getJSON(LIFESTYLE_KEY, { weekday_schedule: [], weekend_schedule: [] });
  const now = new Date();
  const isWeekend = now.getDay() === 0 || now.getDay() === 6;
  const schedule = isWeekend ? data.weekend_schedule : data.weekday_schedule;
  const currentMin = now.getHours() * 60 + now.getMinutes();
  const dateKey = todayKey();
  for (const block of schedule) {
    if (block.type !== 'meal' || !block.start || !block.end) continue;
    const sp = block.start.split(':');
    const ep = block.end.split(':');
    const startMin = parseInt(sp[0]) * 60 + parseInt(sp[1]);
    const endMin = parseInt(ep[0]) * 60 + parseInt(ep[1]);
    /* Within block or up to 15 min after end */
    const inWindow = currentMin >= startMin && currentMin <= endMin + 15;
    if (!inWindow) continue;
    const shownKey = `meal_nudge_shown_${block.id}_${dateKey}`;
    if (localStorage.getItem(shownKey)) continue;
    return { block, shownKey };
  }
  return null;
}

function selectNudgeType() {
  /* Task reminders take priority */
  const taskReminder = checkTaskReminders();
  if (taskReminder) return taskReminder.type;

  /* Meal nudge (lowest priority among reminders, before 3A types) */
  const mealNudge = checkMealNudge();
  if (mealNudge) return 'meal_time';

  const h = new Date().getHours();
  const log = loadLog();
  const today = log[todayKey()];
  if (today && h >= 11 && h < 16 && !today.mid_mood) return 'midday';
  if (today && h >= 18 && !today.eod_rating) return 'eod';
  if (!today && h >= 20) return 'eod';
  if (!today) return 'greeting';
  const r = Math.random();
  if (r < 0.3) return 'move';
  if (r < 0.6) return 'drink';
  return 'breathe';
}

let _currentTaskReminder = null;

function showNudge() {
  const nudgeEl = el('companion-nudge');
  if (!nudgeEl) return;

  const lastNudge = parseInt(localStorage.getItem(NUDGE_KEY) || '0', 10);
  const now = Date.now();
  if (now - lastNudge < 30 * 60 * 1000) return;

  _currentTaskReminder = checkTaskReminders();
  const _mealNudge = checkMealNudge();
  const rawType = selectNudgeType();
  const isTaskNudge = rawType === 'task_pre' || rawType === 'task_due' || rawType === 'task_progress';
  const isMealNudge = rawType === 'meal_time';
  const taskTitle = _currentTaskReminder?.task?.title || '';
  const mealLabel = _mealNudge?.block?.label || '';
  const type = isTaskNudge ? rawType : rawType;
  const msgs = getNudgeMessages(type, taskTitle, mealLabel);
  const msg = msgs[Math.floor(Math.random() * msgs.length)];

  const isActionNudge = type === 'midday' || type === 'eod' || type === 'greeting' || isTaskNudge;
  const nudgeAction = isTaskNudge ? (rawType + '|' + (_currentTaskReminder?.task?.id || '')) : type;
  nudgeEl.innerHTML = `
    <span class="companion-nudge-text">${esc(msg)}</span>
    ${isActionNudge ? `<button class="companion-nudge-btn" data-nudge-action="${nudgeAction}">${isTaskNudge ? 'View task' : 'Open companion'}</button>` : ''}
    <button class="companion-nudge-dismiss" data-nudge-action="dismiss">&times;</button>
  `;
  nudgeEl.classList.remove('hidden');

  /* Mark meal nudge as shown for today */
  if (isMealNudge && _mealNudge?.shownKey) {
    localStorage.setItem(_mealNudge.shownKey, '1');
  }

  /* Mark task reminder as sent */
  if (isTaskNudge && _currentTaskReminder?.task?.id) {
    const taskReminder = _currentTaskReminder.task;
    const patchBody = {};
    if (rawType === 'task_pre') patchBody.reminder_sent_pre = true;
    else if (rawType === 'task_due') patchBody.reminder_sent_due = true;
    else if (rawType === 'task_progress') patchBody.last_progress_check_ts = new Date().toISOString();
    patchBody.reminder_sent_pre = patchBody.reminder_sent_pre ?? taskReminder.reminder_sent_pre;
    patchBody.reminder_sent_due = patchBody.reminder_sent_due ?? taskReminder.reminder_sent_due;
    if (Object.keys(patchBody).length) {
      fetch(`${API_BASE}/api/companion/tasks/${taskReminder.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patchBody)
      }).catch(() => {});
      /* Update local state */
      const tasks = loadTasks();
      const local = tasks.find(t => t.id === taskReminder.id);
      if (local) {
        Object.assign(local, patchBody);
        saveTasks(tasks);
      }
    }
  }

  localStorage.setItem(NUDGE_KEY, String(now));
}

function dismissNudge() {
  const nudgeEl = el('companion-nudge');
  if (nudgeEl) nudgeEl.classList.add('hidden');
  _currentTaskReminder = null;
}

/* Nudge click handler */
function handleNudgeClick(e) {
  const target = e.target;
  const action = target.dataset.nudgeAction;
  if (!action) return;
  if (action === 'dismiss') {
    dismissNudge();
    return;
  }
  dismissNudge();
  openPanel();
  if (action === 'midday') switchTab('today');
  else if (action === 'eod') switchTab('today');
  else if (action.startsWith('task_')) {
    switchTab('today');
  } else if (action === 'meal_time') {
    /* Dismiss only, no action needed */
  } else switchTab('checkin');
}

/* ── Task list ── */
function renderTaskList() {
  const container = el('companion-task-list');
  if (!container) return;
  const tasks = loadTasks();
  const profile = loadProfile();
  const hasAdhd = profile.conditions.includes('adhd');
  const todoTasks = tasks.filter(t => t.status !== 'done');
  const doneTasks = tasks.filter(t => t.status === 'done');

  const maxShow = hasAdhd ? 3 : todoTasks.length;
  const showAll = hasAdhd && todoTasks.length <= maxShow;

  let html = '';
  for (let i = 0; i < Math.min(todoTasks.length, hasAdhd ? (showAll ? todoTasks.length : maxShow) : todoTasks.length); i++) {
    html += renderTaskItem(todoTasks[i], i);
  }
  if (hasAdhd && todoTasks.length > maxShow) {
    html += `<button type="button" class="companion-show-all-btn" id="companion-show-all">Show all (${todoTasks.length} tasks)</button>`;
  }
  if (doneTasks.length > 0) {
    html += `<div class="companion-done-header">Completed</div>`;
    for (const t of doneTasks) {
      html += renderTaskItem(t, -1);
    }
  }
  if (tasks.length === 0) {
    html = '<div class="companion-history-empty" style="padding:12px 0;">No tasks yet. Add one below.</div>';
  }
  container.innerHTML = html;
}

function renderFullTaskList() {
  const container = el('companion-task-list');
  if (!container) return;
  const tasks = loadTasks();
  let html = '';
  const todoTasks = tasks.filter(t => t.status !== 'done');
  for (let i = 0; i < todoTasks.length; i++) {
    html += renderTaskItem(todoTasks[i], i);
  }
  const doneTasks = tasks.filter(t => t.status === 'done');
  if (doneTasks.length > 0) {
    html += '<div class="companion-done-header">Completed</div>';
    for (const t of doneTasks) html += renderTaskItem(t, -1);
  }
  container.innerHTML = html;
}

function renderTaskItem(task, idx) {
  const subSteps = task.sub_steps || [];
  const subHtml = subSteps.length > 0
    ? subSteps.map((s, si) => `<label class="companion-sub-step" style="display:flex;align-items:center;gap:4px;font-size:11px;opacity:0.7;padding:2px 0;"><input type="checkbox" ${s.done ? 'checked' : ''} data-task-id="${task.id}" data-step-idx="${si}">${esc(s.title)}</label>`).join('')
    : '';
  const priorityDot = task.priority === 'High' ? '🔴' : task.priority === 'Low' ? '🟢' : '🟡';
  const titleDone = task.status === 'done' ? 'style="text-decoration:line-through;opacity:0.5;"' : '';
  const toggleIcon = subSteps.length > 0 ? '<span class="companion-task-toggle" data-task-id="' + task.id + '" style="cursor:pointer;font-size:10px;opacity:0.4;">▶</span>' : '';

  let html = '<div class="companion-task-item" data-task-id="' + task.id + '">';
  html += '<div class="companion-task-row" style="display:flex;align-items:center;gap:4px;padding:6px 0;">';
  html += toggleIcon;
  if (task.status === 'done') {
    html += '<span class="companion-task-restore" data-task-id="' + task.id + '" style="cursor:pointer;font-size:11px;opacity:0.5;" title="Restore">↩</span>';
  } else {
    html += '<button class="companion-task-done" data-task-id="' + task.id + '" style="background:none;border:none;cursor:pointer;padding:2px 4px;font-size:12px;min-height:32px;min-width:32px;" title="Mark done">✓</button>';
  }
  html += '<span class="companion-task-edit" data-task-id="' + task.id + '" style="cursor:pointer;font-size:10px;opacity:0.3;min-height:32px;display:inline-flex;align-items:center;" title="Edit">✎</span>';
  html += '<span class="companion-task-delete" data-task-id="' + task.id + '" style="cursor:pointer;font-size:10px;opacity:0.3;min-height:32px;display:inline-flex;align-items:center;" title="Delete">✕</span>';
  html += '<span class="companion-task-priority" title="' + task.priority + '">' + priorityDot + '</span>';
  html += '<span class="companion-task-title" data-task-id="' + task.id + '" ' + titleDone + ' style="flex:1;min-width:0;font-size:12px;word-break:break-word;cursor:pointer;">' + esc(task.title) + '</span>';
  if (task.estimated_minutes) html += '<span style="font-size:10px;opacity:0.4;flex-shrink:0;">' + task.estimated_minutes + 'm</span>';
  if (task.due_time) html += '<span style="font-size:10px;opacity:0.4;flex-shrink:0;font-family:monospace;">⏰' + esc(task.due_time) + '</span>';
  if (task.carried_over) html += '<span style="font-size:9px;opacity:0.4;flex-shrink:0;">↻</span>';
  if (task.milestone_title) html += '<span style="font-size:9px;opacity:0.6;flex-shrink:0;background:var(--accent,#5b9);color:var(--accent-text,#fff);border-radius:3px;padding:1px 5px;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + esc(task.milestone_title) + '">🏁' + esc(task.milestone_title) + '</span>';
  html += '<span class="companion-task-link" data-task-id="' + task.id + '" style="cursor:pointer;font-size:11px;opacity:0.35;min-height:32px;display:inline-flex;align-items:center;" title="Link to milestone">🔗</span>';
  if (idx >= 0) {
    html += '<span class="companion-task-up" data-idx="' + idx + '" style="cursor:pointer;font-size:12px;opacity:0.3;min-height:32px;display:inline-flex;align-items:center;">▲</span>';
    html += '<span class="companion-task-down" data-idx="' + idx + '" style="cursor:pointer;font-size:12px;opacity:0.3;min-height:32px;display:inline-flex;align-items:center;">▼</span>';
  }
  if (task.status !== 'done' && subSteps.length === 0) {
    html += '<button class="companion-task-breakdown" data-task-id="' + task.id + '" style="background:none;border:none;cursor:pointer;padding:2px 4px;font-size:10px;min-height:32px;" title="Break into steps">🔧</button>';
  }
  html += '</div>';
  if (subSteps.length > 0) {
    html += '<div class="companion-task-detail hidden" style="padding-left:20px;">' + subHtml + '</div>';
  }
  html += '</div>';
  return html;
}

async function aiBreakdown(taskTitle) {
  try {
    const res = await fetch(`${API_BASE}/api/companion/tasks/breakdown`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: taskTitle })
    });
    const data = await res.json();
    if (data.steps) {
      return data.steps.map(s => ({ title: s, done: false }));
    }
  } catch (_) {}
  return null;
}

/* ── Add task ── */
async function addTask() {
  const input = el('companion-task-input');
  const timeInput = el('companion-task-time');
  const prioritySelect = el('companion-task-priority');
  const title = (input?.value || '').trim();
  if (!title) return;

  const dueInput = el('companion-task-due');
  const task = {
    title: title.slice(0, 80),
    estimated_minutes: timeInput ? parseInt(timeInput.value, 10) || null : null,
    priority: prioritySelect?.value || 'Medium',
    status: 'todo',
    date: todayKey(),
    carried_over: false,
    sub_steps: [],
    sort_order: 0,
    due_time: dueInput?.value || null
  };
  if (dueInput) dueInput.value = '';

  const tasks = loadTasks();
  tasks.push(task);
  saveTasks(tasks);
  if (input) input.value = '';
  if (timeInput) timeInput.value = '';
  await syncTaskToBackend(task);
  if (task.id) {
    const tasks2 = loadTasks();
    const found = tasks2.find(t => t.id === undefined || t.id === null);
    if (found) found.id = task.id;
    saveTasks(tasks2);
  }
  renderTaskList();
  renderBar();
}

/* ── AI task triage ── */
async function triageTasks() {
  const triageBtn = el('companion-triage-btn');
  if (triageBtn) { triageBtn.disabled = true; triageBtn.textContent = 'Analyzing...'; }

  try {
    const tasks = loadTasks().filter(t => t.status !== 'done');
    const log = loadLog();
    const today = log[todayKey()] || {};
    const profile = loadProfile();

    const payload = tasks.map(t => ({
      id: t.id,
      title: t.title,
      priority: t.priority,
      estimated_minutes: t.estimated_minutes
    }));

    const res = await fetch(`${API_BASE}/api/companion/tasks/prioritize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        tasks: payload,
        mood: today.mood || 5,
        energy: today.energy || 5,
        conditions: profile.conditions
      })
    });
    const data = await res.json();
    const container = el('companion-task-list');
    if (!container) return;

    /* Remove existing card */
    document.getElementById('prioritize-card')?.remove();

    if (data.suggestions && data.suggestions.length > 0) {
      const suggestions = data.suggestions.sort((a, b) => a.suggested_order - b.suggested_order);
      let cardHtml = '<div id="prioritize-card" class="companion-triage-note" style="background:color-mix(in srgb, var(--accent,var(--red)) 10%,transparent);border:1px solid var(--accent,var(--red));border-radius:8px;padding:10px;margin:8px 0;font-size:12px;">';
      cardHtml += '<div style="font-weight:600;font-size:11px;margin-bottom:6px;">Suggested Order</div>';
      for (const s of suggestions) {
        const t = tasks.find(tt => tt.id === s.id);
        cardHtml += '<div style="display:flex;gap:6px;align-items:flex-start;padding:4px 0;border-bottom:1px solid color-mix(in srgb,var(--border) 50%,transparent);">';
        cardHtml += `<span style="font-weight:600;flex-shrink:0;width:18px;">${s.suggested_order}.</span>`;
        cardHtml += `<div style="flex:1;min-width:0;"><div style="font-weight:500;">${esc(t ? t.title : '')}</div><div style="font-size:10px;opacity:0.6;margin-top:1px;">${esc(s.reason || '')}</div></div>`;
        cardHtml += '</div>';
      }
      cardHtml += '<div style="display:flex;gap:6px;margin-top:6px;">';
      cardHtml += `<button class="companion-prioritize-apply confirm-btn confirm-btn-primary" data-order="${esc(JSON.stringify(suggestions))}" style="flex:1;min-height:36px;">Apply this order</button>`;
      cardHtml += '<button class="companion-prioritize-dismiss confirm-btn" style="flex:1;min-height:36px;">Dismiss</button>';
      cardHtml += '</div></div>';
      container.insertAdjacentHTML('afterbegin', cardHtml);
    } else if (data.error) {
      const err = document.createElement('div');
      err.style.cssText = 'color:var(--red,#e06c75);font-size:11px;padding:6px 0;';
      err.textContent = data.detail ? `AI call failed: ${esc(data.detail)}` : `Error: ${esc(data.error)}`;
      container.prepend(err);
    } else {
      const err = document.createElement('div');
      err.style.cssText = 'color:var(--red,#e06c75);font-size:11px;padding:6px 0;';
      err.textContent = 'Could not get suggestion. Check your model connection.';
      container.prepend(err);
    }
  } catch (_) {
    const container = el('companion-task-list');
    if (container) {
      document.getElementById('prioritize-card')?.remove();
      const err = document.createElement('div');
      err.style.cssText = 'color:var(--red,#e06c75);font-size:11px;padding:6px 0;';
      err.textContent = 'Could not get suggestion. Check your model connection.';
      container.prepend(err);
    }
  }

  if (triageBtn) { triageBtn.disabled = false; triageBtn.textContent = 'Help me prioritize'; }
}

/* ── Profile ── */
function populateProfileForm() {
  const p = loadProfile();
  const nameIn = el('companion-profile-name');
  const tzIn = el('companion-profile-tz');
  const sleepIn = el('companion-profile-sleep');
  if (nameIn) nameIn.value = p.displayName || '';
  if (tzIn) tzIn.value = p.timezone || '';
  if (sleepIn) sleepIn.value = p.idealSleepHours || 8;

  const bdayIn = el('companion-profile-birthday');
  if (bdayIn) bdayIn.value = p.birthday || '';
  const mbtiIn = el('companion-profile-mbti');
  if (mbtiIn) mbtiIn.value = p.mbtiType || '';
  const enneIn = el('companion-profile-enneagram');
  if (enneIn) enneIn.value = p.enneagramType || '';
  const addIn = el('companion-profile-additional');
  if (addIn) { addIn.value = p.additionalConditions || ''; updateAdditionalCounter(); }
  const ssIn = el('companion-profile-sleep-start');
  if (ssIn) ssIn.value = p.sleepScheduleStart || '';
  const seIn = el('companion-profile-sleep-end');
  if (seIn) seIn.value = p.sleepScheduleEnd || '';

  document.querySelectorAll('[data-companion-condition]').forEach(chk => {
    chk.checked = p.conditions.includes(chk.dataset.companionCondition);
  });

  document.querySelectorAll('[data-companion-pattern]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.companionPattern === p.energyPattern);
  });

  renderSysInfo();
}

function updateAdditionalCounter() {
  const ta = el('companion-profile-additional');
  const counter = el('companion-profile-additional-counter');
  if (ta && counter) counter.textContent = ta.value.length;
}

function renderSysInfo() {
  const p = loadProfile();
  const sysinfo = p.systemInfo;
  const updatedAt = p.systemInfoUpdatedAt;
  const empty = document.getElementById('companion-sysinfo-empty');
  const table = document.getElementById('companion-sysinfo-table');
  const updatedEl = document.getElementById('companion-sysinfo-updated');

  if (!sysinfo) {
    if (empty) empty.classList.remove('hidden');
    if (table) table.classList.add('hidden');
    if (updatedEl) updatedEl.textContent = '';
    return;
  }

  if (empty) empty.classList.add('hidden');
  if (table) table.classList.remove('hidden');

  const os = sysinfo.os || {};
  const cpu = sysinfo.cpu || {};
  const ram = sysinfo.ram || {};
  let html = '<table style="width:100%;border-collapse:collapse;">';
  const rows = [
    ['Hostname', sysinfo.hostname_hash || sysinfo.hostname],
    ['OS', os.name || sysinfo.platform],
    ['Kernel', os.release || sysinfo.release],
    ['CPU', cpu.brand || sysinfo.cpu_name],
    ['Cores', cpu.logical_cores || cpu.physical_cores || sysinfo.cpu_cores],
    ['RAM', ram.total_gb || sysinfo.ram_gb ? (ram.total_gb || sysinfo.ram_gb) + ' GB' : null],
  ];
  if (sysinfo.gpus && sysinfo.gpus.length) {
    sysinfo.gpus.forEach((g, i) => {
      rows.push([i === 0 ? 'GPU' : '', g.name + (g.vram_total_mb || g.vram_mb ? ' (' + (g.vram_total_mb || g.vram_mb) + ' MB)' : '')]);
    });
  }
  if (sysinfo.disks && sysinfo.disks.length) {
    sysinfo.disks.forEach(d => {
      const dev = d.mount || d.device || d.mountpoint || '?';
      const size = d.total_gb || d.size_gb || '?';
      rows.push(['Disk', dev + ' (' + size + ' GB)']);
    });
  }
  if (os.python_version || sysinfo.python_version) {
    const py = (os.python_version || sysinfo.python_version || '').split(' ')[0];
    if (py) rows.push(['Python', py]);
  }
  if (sysinfo.battery) {
    const b = sysinfo.battery;
    rows.push(['Battery', b.percent + '%' + (b.plugged_in ? ' (charging)' : '')]);
  }
  if (sysinfo.uptime_hours || sysinfo.uptime_seconds) {
    let hours = sysinfo.uptime_hours || (sysinfo.uptime_seconds / 3600);
    const days = Math.floor(hours / 24);
    hours = Math.floor(hours % 24);
    rows.push(['Uptime', days + 'd ' + hours + 'h']);
  }
  for (const [label, value] of rows) {
    if (value !== null && value !== undefined) {
      html += '<tr><td style="padding:2px 8px 2px 0;opacity:0.6;white-space:nowrap;vertical-align:top;">' + label + '</td><td style="padding:2px 0;">' + value + '</td></tr>';
    }
  }
  html += '</table>';
  table.innerHTML = html;

  if (updatedEl && updatedAt) {
    try {
      const d = new Date(updatedAt);
      updatedEl.textContent = 'Last updated: ' + d.toLocaleString();
    } catch (_) {
      updatedEl.textContent = '';
    }
  }
}

/* ── Downloads tab ── */
function renderDownloads() {
  fetch('/api/companion/sysinfo/downloads/status').then(r => r.json()).then(s => {
    const normalBtn = el('companion-dl-normal');
    const silentBtn = el('companion-dl-silent');
    const normalLegend = el('companion-dl-normal-legend');
    const silentLegend = el('companion-dl-silent-legend');
    if (s.exe_normal) {
      normalBtn.disabled = false;
      normalBtn.style.opacity = '1';
      normalLegend.textContent = 'Shows you exactly what data it found and asks before sending. Use this the first time, or if you want to review each time.';
    } else {
      normalBtn.disabled = true;
      normalBtn.style.opacity = '0.4';
      normalLegend.textContent = 'Not available yet.';
    }
    if (s.exe_silent) {
      silentBtn.disabled = false;
      silentBtn.style.opacity = '1';
      silentLegend.textContent = 'Runs quietly in the background and sends automatically once it finds Odysseus. Only asks for input if it can\'t find the server on its own. Good for routine re-syncs.';
    } else {
      silentBtn.disabled = true;
      silentBtn.style.opacity = '0.4';
      silentLegend.textContent = 'Not available yet.';
    }
  }).catch(() => {});
}

function saveProfileForm() {
  const p = loadProfile();
  const nameIn = el('companion-profile-name');
  const tzIn = el('companion-profile-tz');
  const sleepIn = el('companion-profile-sleep');
  if (nameIn) p.displayName = nameIn.value.trim();
  if (tzIn) p.timezone = tzIn.value.trim() || Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (sleepIn) p.idealSleepHours = parseFloat(sleepIn.value) || 8;

  const bdayIn = el('companion-profile-birthday');
  if (bdayIn) p.birthday = bdayIn.value || '';
  const mbtiIn = el('companion-profile-mbti');
  if (mbtiIn) p.mbtiType = mbtiIn.value.trim() || '';
  const enneIn = el('companion-profile-enneagram');
  if (enneIn) p.enneagramType = enneIn.value.trim() || '';
  const addIn = el('companion-profile-additional');
  if (addIn) p.additionalConditions = addIn.value.trim() || '';
  const ssIn = el('companion-profile-sleep-start');
  if (ssIn) p.sleepScheduleStart = ssIn.value || '';
  const seIn = el('companion-profile-sleep-end');
  if (seIn) p.sleepScheduleEnd = seIn.value || '';

  p.conditions = [];
  document.querySelectorAll('[data-companion-condition]:checked').forEach(chk => {
    p.conditions.push(chk.dataset.companionCondition);
  });

  const activePattern = document.querySelector('[data-companion-pattern].active');
  if (activePattern) p.energyPattern = activePattern.dataset.companionPattern;

  saveProfile(p);
}

/* ── Sync from backend on load ── */
async function syncFromBackend() {
  try {
    const [profileRes, checkinsRes, tasksRes, lifestyleRes] = await Promise.all([
      fetch(`${API_BASE}/api/companion/profile`),
      fetch(`${API_BASE}/api/companion/checkins`),
      fetch(`${API_BASE}/api/companion/tasks?date=${todayKey()}`),
      fetch(`${API_BASE}/api/companion/lifestyle`)
    ]);

    const profileData = await profileRes.json();
    if (profileData && profileData.display_name !== undefined) {
      const p = loadProfile();
      p.displayName = profileData.display_name || '';
      p.timezone = profileData.timezone || p.timezone;
      p.conditions = profileData.conditions || [];
      p.energyPattern = profileData.energy_pattern || p.energyPattern;
      p.idealSleepHours = profileData.ideal_sleep_hours || p.idealSleepHours;
      p.birthday = profileData.birthday || '';
      p.mbtiType = profileData.mbti_type || '';
      p.enneagramType = profileData.enneagram_type || '';
      p.additionalConditions = profileData.additional_conditions || '';
      p.sleepScheduleStart = profileData.sleep_schedule_start || '';
      p.sleepScheduleEnd = profileData.sleep_schedule_end || '';
      p.systemInfo = profileData.system_info || null;
      p.systemInfoUpdatedAt = profileData.system_info_updated_at || null;
      saveProfile(p);
      renderSysInfo();
    }

    const checkinsData = await checkinsRes.json();
    if (checkinsData && checkinsData.entries) {
      const log = loadLog();
      for (const [date, entry] of Object.entries(checkinsData.entries)) {
        if (!log[date]) {
          log[date] = {
            mood: entry.mood,
            energy: entry.energy,
            sleep: entry.sleep,
            text: entry.text || '',
            message: entry.message || '',
            briefing: entry.briefing || '',
            timestamp: entry.timestamp || '',
          };
        }
        /* Always sync stage3 fields */
        log[date].mid_mood = entry.mid_mood;
        log[date].mid_energy = entry.mid_energy;
        log[date].mid_feeling = entry.mid_feeling || '';
        log[date].eod_done = entry.eod_done || '';
        log[date].eod_blocked = entry.eod_blocked || '';
        log[date].eod_tomorrow = entry.eod_tomorrow || '';
        log[date].eod_rating = entry.eod_rating;
        log[date].eod_message = entry.eod_message || '';
      }
      saveLog(log);
    }

    const tasksData = await tasksRes.json();
    if (tasksData && tasksData.tasks) {
      saveTasks(tasksData.tasks);
    }

    const lifestyleData = await lifestyleRes.json();
    if (lifestyleData && lifestyleData.weekday_schedule) {
      saveLifestyle({ weekday_schedule: lifestyleData.weekday_schedule, weekend_schedule: lifestyleData.weekend_schedule || [] });
      if (document.querySelector('[data-companion-panel="lifestyle"]:not(.hidden)')) populateLifestyleForm();
    }
  } catch (_) {}
}

/* ── Lifestyle ── */
let _lifestyleData = { weekday_schedule: [], weekend_schedule: [] };
const LIFESTYLE_KEY = 'companion-lifestyle';

function defaultBlock() {
  return { id: crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2, 8), label: '', type: 'other', start: '', end: '' };
}

function loadLifestyle() {
  return Storage.getJSON(LIFESTYLE_KEY, { weekday_schedule: [], weekend_schedule: [] });
}

function saveLifestyle(data) {
  Storage.setJSON(LIFESTYLE_KEY, data);
}

function renderBlockRow(block, dayType, idx) {
  const typeOptions = ['school','work','commute','meal','free','sleep','other'].map(t =>
    `<option value="${t}" ${block.type === t ? 'selected' : ''}>${t.charAt(0).toUpperCase() + t.slice(1)}</option>`
  ).join('');
  return `<div class="companion-lifestyle-row" data-block-id="${block.id}">
    <input type="text" class="companion-lifestyle-label companion-input" value="${esc(block.label)}" maxlength="30" placeholder="Label" data-idx="${idx}" data-day="${dayType}">
    <select class="companion-lifestyle-type companion-input" data-idx="${idx}" data-day="${dayType}">${typeOptions}</select>
    <input type="time" class="companion-lifestyle-start companion-input" value="${block.start}" data-idx="${idx}" data-day="${dayType}">
    <input type="time" class="companion-lifestyle-end companion-input" value="${block.end}" data-idx="${idx}" data-day="${dayType}">
    <button type="button" class="companion-lifestyle-remove confirm-btn" data-block-id="${block.id}">✕</button>
  </div>`;
}

function renderLifestyleBlocks() {
  const data = _lifestyleData;
  const weekdayContainer = el('companion-weekday-blocks');
  const weekendContainer = el('companion-weekend-blocks');
  if (!weekdayContainer || !weekendContainer) return;

  const emptyEl = el('companion-lifestyle-empty');
  const hasAny = data.weekday_schedule.length > 0 || data.weekend_schedule.length > 0;
  if (emptyEl) emptyEl.classList.toggle('hidden', hasAny);

  weekdayContainer.innerHTML = data.weekday_schedule.map((b, i) => renderBlockRow(b, 'weekday', i)).join('');
  weekendContainer.innerHTML = data.weekend_schedule.map((b, i) => renderBlockRow(b, 'weekend', i)).join('');
}

function collectLifestyleFromDOM() {
  const data = { weekday_schedule: [], weekend_schedule: [] };
  ['weekday', 'weekend'].forEach(dayType => {
    const rows = document.querySelectorAll(`#companion-${dayType}-blocks .companion-lifestyle-row`);
    rows.forEach(row => {
      const block = {
        id: row.dataset.blockId,
        label: row.querySelector('.companion-lifestyle-label')?.value?.trim() || '',
        type: row.querySelector('.companion-lifestyle-type')?.value || 'other',
        start: row.querySelector('.companion-lifestyle-start')?.value || '',
        end: row.querySelector('.companion-lifestyle-end')?.value || '',
      };
      data[`${dayType}_schedule`].push(block);
    });
  });
  return data;
}

function populateLifestyleForm() {
  _lifestyleData = loadLifestyle();
  renderLifestyleBlocks();
}

function addLifestyleBlock(dayType) {
  const data = _lifestyleData;
  data[`${dayType}_schedule`].push(defaultBlock());
  renderLifestyleBlocks();
  saveLifestyle(data);
}

function removeLifestyleBlock(blockId) {
  let data = collectLifestyleFromDOM();
  data.weekday_schedule = data.weekday_schedule.filter(b => b.id !== blockId);
  data.weekend_schedule = data.weekend_schedule.filter(b => b.id !== blockId);
  _lifestyleData = data;
  renderLifestyleBlocks();
  saveLifestyle(data);
}

async function saveLifestyleToBackend() {
  const data = collectLifestyleFromDOM();
  _lifestyleData = data;
  saveLifestyle(data);
  try {
    await fetch(`${API_BASE}/api/companion/lifestyle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
  } catch (_) {}
}

/* ── Manual sync button ── */
async function manualSync() {
  const btn = el('companion-sync-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Syncing...'; }
  await syncFromBackend();
  renderBar();
  if (btn) { btn.disabled = false; btn.textContent = 'Synced ✓'; setTimeout(() => { btn.textContent = 'Sync to server'; }, 2000); }
}

/* ── Helpers ── */
function esc(s) {
  return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function moodColor(val, isMid) {
  if (isMid) {
    if (val < 3) return '#e06c75';
    if (val === 3) return '#e5c07b';
    return '#98c379';
  }
  if (val < 4) return '#e06c75';
  if (val <= 6) return '#e5c07b';
  return '#98c379';
}

function energyColor(val) {
  if (val < 4) return '#61afef';
  if (val <= 6) return '#56b6c2';
  return '#c678dd';
}

/* ── Carry over unfinished tasks from previous days ── */
function carryOverTasks() {
  const tasks = loadTasks();
  const key = todayKey();
  let changed = false;
  for (const t of tasks) {
    if (t.date !== key && t.status !== 'done' && t.status !== 'deleted') {
      t.date = key;
      t.carried_over = true;
      t.sort_order = 0;
      changed = true;
    }
  }
  if (changed) {
    tasks.forEach((t, i) => t.sort_order = i);
    saveTasks(tasks);
    for (const t of tasks) syncTaskToBackend(t);
  }
}

/* ── EOD auto carry-over ── */
function autoCarryOverTasks() {
  const log = loadLog();
  const today = log[todayKey()];
  if (!today || !today.eod_rating) return;
  const tasks = loadTasks();
  const undone = tasks.filter(t => t.status !== 'done' && t.status !== 'deleted');
  if (undone.length === 0) return;
  /* Save a snapshot of what's being carried to tomorrow */
  const titles = undone.map(t => t.title).join(', ');
  /* In a real implementation, we'd set a flag. For now, just log. */
  console.log(`[companion] ${undone.length} task(s) carried over: ${titles}`);
}

/* ── Panel opener button (Stage 3) ── */
function renderPanelOpener() {
  const container = document.querySelector('.companion-bar-inner');
  if (!container) return;
  if (el('companion-panel-opener')) return;
  const btn = document.createElement('button');
  btn.id = 'companion-panel-opener';
  btn.className = 'companion-bar-btn';
  btn.title = 'What\'s next?';
  btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>';
  btn.addEventListener('click', showStage3Panel);
  container.appendChild(btn);
}

function showStage3Panel() {
  openPanel();
  switchTab('today');
  /* Scroll to bottom of the panel where the Stage-3 widgets live */
  const body = document.querySelector('.companion-panel-body');
  if (body) setTimeout(() => body.scrollTop = body.scrollHeight, 100);
}

/* ── Task-milestone linking ── */

async function showTaskLinkPicker(taskId) {
  /* Fetch all goals */
  let goals;
  try {
    const res = await fetch('/api/goals/goals');
    const data = await res.json();
    goals = data.goals || [];
  } catch (e) {
    alert('Failed to load goals');
    return;
  }

  /* Fetch milestones for each goal */
  const groups = [];
  for (const g of goals) {
    try {
      const res = await fetch(`/api/goals/goals/${g.id}`);
      const data = await res.json();
      const ms = (data.milestones || []).filter(m => m.status !== 'completed');
      if (ms.length > 0) groups.push({ goalTitle: g.title, milestones: ms });
    } catch (_) {}
  }

  /* Build popup */
  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.4);z-index:10000;display:flex;align-items:center;justify-content:center;';
  let html = '<div style="background:var(--surface,#222);border:1px solid var(--border,#444);border-radius:12px;padding:16px;width:400px;max-width:90vw;max-height:80vh;overflow-y:auto;">';
  html += '<h4 style="margin:0 0 12px 0;">Link Task to Milestone</h4>';

  if (groups.length === 0) {
    html += '<p style="opacity:0.6;">No milestones available. Create milestones in a goal first.</p>';
  } else {
    for (const g of groups) {
      html += '<div style="font-weight:600;font-size:12px;margin:8px 0 4px 0;opacity:0.7;">' + esc(g.goalTitle) + '</div>';
      for (const m of g.milestones) {
        html += '<div class="companion-milestone-opt" data-task-id="' + taskId + '" data-ms-id="' + m.id + '" style="padding:6px 10px;cursor:pointer;border-radius:4px;font-size:13px;transition:background .1s;">' + esc(m.title) + '</div>';
      }
    }
  }

  html += '<hr style="border-color:var(--border,#444);margin:12px 0;">';
  html += '<div style="display:flex;justify-content:space-between;">';
  html += '<button class="companion-link-unlink" data-task-id="' + taskId + '" style="background:none;border:1px solid var(--border,#444);border-radius:6px;cursor:pointer;padding:6px 12px;font-size:12px;color:var(--danger,#c44);">None (unlink)</button>';
  html += '<button class="companion-link-cancel" style="background:none;border:1px solid var(--border,#444);border-radius:6px;cursor:pointer;padding:6px 12px;font-size:12px;">Cancel</button>';
  html += '</div></div>';
  overlay.innerHTML = html;
  document.body.appendChild(overlay);

  /* Event handlers */
  overlay.addEventListener('click', async e => {
    const opt = e.target.closest('.companion-milestone-opt');
    if (opt) {
      await setTaskMilestone(taskId, opt.dataset.msId);
      overlay.remove();
      return;
    }
    if (e.target.matches('.companion-link-unlink')) {
      await setTaskMilestone(taskId, null);
      overlay.remove();
      return;
    }
    if (e.target.closest('.companion-link-cancel') || e.target === overlay) {
      overlay.remove();
    }
  });
}

async function setTaskMilestone(taskId, msId) {
  try {
    const res = await fetch('/api/goals/companion/tasks/' + taskId + '/link-milestone', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ milestone_id: msId })
    });
    if (!res.ok) { console.error('Link-milestone PATCH failed'); return; }
  } catch (e) {
    console.error('Failed to link milestone', e);
    return;
  }
  await fetchTasksFromBackend();
  renderTaskList();
}

/* ── Init ── */
function init() {
  carryOverTasks();
  syncFromBackend().then(() => {
    renderBar();
    populateProfileForm();
    populateLifestyleForm();
  });

  renderBar();
  populateProfileForm();
  populateLifestyleForm();

  /* Tabs */
  const panel = el('companion-panel');
  if (panel) {
    panel.querySelectorAll('[data-companion-tab]').forEach(btn => {
      btn.addEventListener('click', () => switchTab(btn.dataset.companionTab));
    });
  }

  /* Profile buttons */
  document.querySelectorAll('[data-companion-condition]').forEach(chk => {
    chk.addEventListener('change', saveProfileForm);
  });
  document.querySelectorAll('[data-companion-pattern]').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('[data-companion-pattern]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      saveProfileForm();
    });
  });
  ['companion-profile-name', 'companion-profile-tz', 'companion-profile-sleep',
    'companion-profile-birthday', 'companion-profile-mbti',
    'companion-profile-enneagram', 'companion-profile-sleep-start',
    'companion-profile-sleep-end'].forEach(id => {
    const inp = el(id);
    if (inp) inp.addEventListener('change', saveProfileForm);
  });
  const addTa = el('companion-profile-additional');
  if (addTa) {
    addTa.addEventListener('input', () => { updateAdditionalCounter(); saveProfileForm(); });
  }

  /* Lifestyle buttons */
  el('companion-weekday-add')?.addEventListener('click', () => addLifestyleBlock('weekday'));
  el('companion-weekend-add')?.addEventListener('click', () => addLifestyleBlock('weekend'));
  el('companion-lifestyle-save')?.addEventListener('click', saveLifestyleToBackend);

  /* Sysinfo card collapse */
  const sysinfoHeader = el('companion-sysinfo-header');
  if (sysinfoHeader) {
    sysinfoHeader.addEventListener('click', () => {
      document.getElementById('companion-sysinfo-card')?.classList.toggle('expanded');
    });
  }

  /* Sysinfo refresh */
  el('companion-sysinfo-refresh')?.addEventListener('click', async () => {
    const btn = el('companion-sysinfo-refresh');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Refreshing...'; }
    try {
      const res = await fetch(`${API_BASE}/api/companion/profile`);
      if (res.ok) {
        const data = await res.json();
        if (data && data.display_name !== undefined) {
          const p = loadProfile();
          p.systemInfo = data.system_info || null;
          p.systemInfoUpdatedAt = data.system_info_updated_at || null;
          saveProfile(p);
        }
      }
    } catch (e) {
      console.warn('Sysinfo refresh failed', e);
    }
    renderSysInfo();
    if (btn) { btn.disabled = false; btn.textContent = '🔄 Refresh'; }
  });

  /* Sysinfo — open downloads modal */
  el('companion-sysinfo-get-tool')?.addEventListener('click', () => {
    const modal = el('companion-dl-modal');
    if (modal) {
      modal.classList.remove('hidden');
      renderDownloads();
    }
  });

  /* Downloads modal — close */
  function closeDlModal() {
    el('companion-dl-modal')?.classList.add('hidden');
  }
  el('companion-dl-close')?.addEventListener('click', closeDlModal);
  el('companion-dl-modal')?.addEventListener('click', e => {
    if (e.target === e.currentTarget) closeDlModal();
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeDlModal();
  });

  /* Download buttons (inside modal) */
  el('companion-dl-normal')?.addEventListener('click', () => window.open('/api/companion/sysinfo/downloads/exe?variant=normal', '_blank'));
  el('companion-dl-silent')?.addEventListener('click', () => window.open('/api/companion/sysinfo/downloads/exe?variant=silent', '_blank'));
  el('companion-dl-source')?.addEventListener('click', () => window.open('/api/companion/sysinfo/downloads/source', '_blank'));

  /* Lifestyle block removal via delegation */
  document.querySelector('#companion-panel .companion-panel-body')?.addEventListener('click', e => {
    const removeBtn = e.target.closest('.companion-lifestyle-remove');
    if (removeBtn) removeLifestyleBlock(removeBtn.dataset.blockId);
  });

  /* Auto-save lifestyle on field change (debounced) */
  let _lsDebounce = null;
  document.querySelector('#companion-panel .companion-panel-body')?.addEventListener('change', e => {
    if (e.target.closest('.companion-lifestyle-label, .companion-lifestyle-type, .companion-lifestyle-start, .companion-lifestyle-end')) {
      clearTimeout(_lsDebounce);
      _lsDebounce = setTimeout(() => {
        const data = collectLifestyleFromDOM();
        _lifestyleData = data;
        saveLifestyle(data);
      }, 500);
    }
  });

  /* Check-in submit */
  el('companion-submit')?.addEventListener('click', submitCheckin);

  /* Side panel close */
  el('companion-close')?.addEventListener('click', closePanel);
  el('companion-overlay')?.addEventListener('click', closePanel);

  /* Escape key */
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      if (!el('companion-panel')?.classList.contains('hidden')) closePanel();
    }
  });

  /* Slider value display */
  const moodSlider = el('companion-mood');
  const energySlider = el('companion-energy');
  if (moodSlider) moodSlider.addEventListener('input', () => {
    const v = el('companion-mood-val');
    if (v) v.textContent = moodSlider.value;
  });
  if (energySlider) energySlider.addEventListener('input', () => {
    const v = el('companion-energy-val');
    if (v) v.textContent = energySlider.value;
  });

  /* Task add */
  el('companion-task-add')?.addEventListener('click', addTask);
  el('companion-task-input')?.addEventListener('keydown', e => {
    if (e.key === 'Enter') addTask();
  });

  /* Triage */
  el('companion-triage-btn')?.addEventListener('click', triageTasks);

  /* Manual sync */
  el('companion-sync-btn')?.addEventListener('click', manualSync);

  /* Mid-day check-in */
  const midMd = el('companion-mid-mood');
  const midEn = el('companion-mid-energy');
  if (midMd) midMd.addEventListener('input', () => {
    const v = el('companion-mid-mood-val');
    if (v) v.textContent = midMd.value;
  });
  if (midEn) midEn.addEventListener('input', () => {
    const v = el('companion-mid-energy-val');
    if (v) v.textContent = midEn.value;
  });
  el('companion-mid-submit')?.addEventListener('click', submitMidday);

  /* Feeling buttons */
  el('companion-feeling-grid')?.addEventListener('click', e => {
    const btn = e.target.closest('.companion-feeling-btn');
    if (!btn) return;
    el('companion-feeling-grid')?.querySelectorAll('.companion-feeling-btn').forEach(b => b.classList.remove('selected'));
    btn.classList.add('selected');
  });

  /* EOD reflection */
  const eodRt = el('companion-eod-rating');
  if (eodRt) eodRt.addEventListener('input', () => {
    const v = el('companion-eod-rating-val');
    if (v) v.textContent = eodRt.value;
  });
  el('companion-eod-submit')?.addEventListener('click', submitEod);

  /* Just talk chat */
  el('companion-chat-send')?.addEventListener('click', sendChatMessage);
  el('companion-chat-input')?.addEventListener('keydown', e => {
    if (e.key === 'Enter') sendChatMessage();
  });
  renderChatLog();

  /* Collapsible chat card */
  const chatHeader = el('companion-chat-header');
  if (chatHeader) {
    chatHeader.addEventListener('click', e => {
      if (e.target.closest('.companion-card-collapse-btn')) return;
      document.getElementById('companion-chat-card')?.classList.toggle('expanded');
    });
  }
  el('companion-chat-collapse')?.addEventListener('click', () => {
    document.getElementById('companion-chat-card')?.classList.remove('expanded');
  });

  /* Collapsible patterns card */
  const patternsHeader = el('companion-patterns-header');
  if (patternsHeader) {
    patternsHeader.addEventListener('click', () => {
      document.getElementById('companion-patterns-card')?.classList.toggle('expanded');
    });
  }

  /* Nudge system */
  el('companion-nudge')?.addEventListener('click', handleNudgeClick);

  /* Retry button delegation */
  el('companion-chat-log')?.addEventListener('click', e => {
    if (e.target.closest('[data-companion-retry]')) {
      sendChatMessage(_lastUserMessage);
    }
  });

  /* Show nudge on load and periodically */
  setTimeout(() => showNudge(), 5000);
  setInterval(() => showNudge(), 30000);

  /* ── "Noted" indicator polling (extraction toast) ── */
  let _lastMemoryCheckTs = Date.now();
  async function checkForNotedMemory() {
    try {
      const res = await fetch('/api/companion/memory/latest');
      const data = await res.json();
      if (data && data.fact && data.fact.id) {
        showNotedIndicator(data.fact.content);
      }
    } catch (_) { /* silent */ }
  }
  function showNotedIndicator(content) {
    const existing = document.getElementById('companion-noted-toast');
    if (existing) existing.remove();
    const toast = document.createElement('div');
    toast.id = 'companion-noted-toast';
    toast.textContent = '💭 Noted';
    toast.style.cssText = 'position:fixed;bottom:70px;right:20px;background:var(--accent,var(--red));color:#fff;padding:6px 14px;border-radius:20px;font-size:13px;font-weight:500;z-index:9999;opacity:0;transition:opacity 0.3s ease;pointer-events:none;box-shadow:0 2px 12px rgba(0,0,0,0.2);';
    document.body.appendChild(toast);
    requestAnimationFrame(() => { toast.style.opacity = '1'; });
    setTimeout(() => {
      toast.style.opacity = '0';
      setTimeout(() => toast.remove(), 400);
    }, 2500);
  }
  setInterval(() => checkForNotedMemory(), 5000);

  /* Auto-carry-over EOD tasks */
  autoCarryOverTasks();

  /* ── Delegated task action clicks on #companion-task-list ── */
  const taskList = el('companion-task-list');
  if (taskList) {
    taskList.addEventListener('click', async e => {
      const target = e.target;

      /* Toggle sub-steps */
      if (target.matches('.companion-task-toggle')) {
        const detail = target.parentElement.nextElementSibling;
        if (detail) detail.classList.toggle('hidden');
        return;
      }

      /* Mark in progress on interaction */
      if (target.matches('.companion-task-title, .companion-task-breakdown')) {
        const taskId = target.dataset.taskId || target.closest('[data-task-id]')?.dataset.taskId;
        if (taskId) {
          const tasks = loadTasks();
          const task = tasks.find(t => t.id === taskId);
          if (task && task.status === 'todo') {
            task.status = 'in_progress';
            task.started_at = new Date().toISOString();
            saveTasks(tasks);
            await syncTaskToBackend({ id: task.id, status: 'in_progress', started_at: task.started_at });
          }
        }
      }

      /* Mark done */
      if (target.matches('.companion-task-done')) {
        const taskId = target.dataset.taskId;
        const tasks = loadTasks();
        const task = tasks.find(t => t.id === taskId);
        if (task) {
          task.status = 'done';
          task.completed_at = new Date().toISOString();
          saveTasks(tasks);
          await syncTaskToBackend({ id: task.id, status: 'done', completed_at: task.completed_at });
          renderTaskList();
          renderBar();
        }
        return;
      }

      /* Restore */
      if (target.matches('.companion-task-restore')) {
        const taskId = target.dataset.taskId;
        const tasks = loadTasks();
        const task = tasks.find(t => t.id === taskId);
        if (task) {
          task.status = 'todo';
          task.completed_at = null;
          task.started_at = null;
          saveTasks(tasks);
          await syncTaskToBackend({ id: task.id, status: 'todo', started_at: null });
          renderTaskList();
          renderBar();
        }
        return;
      }

      /* Delete */
      if (target.matches('.companion-task-delete')) {
        const taskId = target.dataset.taskId;
        if (!confirm('Delete this task?')) return;
        const tasks = loadTasks();
        const idx = tasks.findIndex(t => t.id === taskId);
        if (idx !== -1) tasks.splice(idx, 1);
        saveTasks(tasks);
        if (taskId) {
          try {
            await fetch(`${API_BASE}/api/companion/tasks/${taskId}`, { method: 'DELETE' });
          } catch (_) {}
        }
        renderTaskList();
        renderBar();
        return;
      }

      /* Edit */
      if (target.matches('.companion-task-edit')) {
        const taskId = target.dataset.taskId;
        const row = target.closest('.companion-task-row');
        if (!row) return;
        const titleSpan = row.querySelector('.companion-task-title');
        if (!titleSpan) return;
        const currentTitle = titleSpan.textContent;
        titleSpan.innerHTML = `<input class="companion-task-edit-input" value="${esc(currentTitle)}" maxlength="80" style="width:100%;background:var(--input-bg,var(--panel));border:1px solid var(--border);border-radius:4px;padding:2px 6px;color:var(--fg);font-size:12px;">`;
        const inp = titleSpan.querySelector('input');
        if (inp) {
          inp.focus();
          inp.select();
          inp.addEventListener('keydown', async ev => {
            if (ev.key === 'Enter') {
              const newTitle = inp.value.trim();
              if (newTitle && newTitle !== currentTitle) {
                const tasks = loadTasks();
                const task = tasks.find(t => t.id === taskId);
                if (task) {
                  task.title = newTitle;
                  saveTasks(tasks);
                  await syncTaskToBackend({ id: task.id, title: newTitle });
                }
              }
              titleSpan.textContent = newTitle || currentTitle;
            } else if (ev.key === 'Escape') {
              titleSpan.textContent = currentTitle;
    }
  });

          inp.addEventListener('blur', () => {
            titleSpan.textContent = inp.value.trim() || currentTitle;
          });
        }
        return;
      }

      /* Move up */
      if (target.matches('.companion-task-up')) {
        const idx = parseInt(target.dataset.idx, 10);
        const tasks = loadTasks();
        const todoTasks = tasks.filter(t => t.status !== 'done');
        if (idx > 0 && idx < todoTasks.length) {
          const realIdx = tasks.indexOf(todoTasks[idx]);
          const prevRealIdx = tasks.indexOf(todoTasks[idx - 1]);
          [tasks[realIdx], tasks[prevRealIdx]] = [tasks[prevRealIdx], tasks[realIdx]];
          tasks.forEach((t, i) => t.sort_order = i);
          saveTasks(tasks);
          const updated = [tasks[realIdx], tasks[prevRealIdx]];
          for (const t of updated) {
            if (t.id) await syncTaskToBackend({ id: t.id, sort_order: t.sort_order });
          }
          renderTaskList();
        }
        return;
      }

      /* Move down */
      if (target.matches('.companion-task-down')) {
        const idx = parseInt(target.dataset.idx, 10);
        const tasks = loadTasks();
        const todoTasks = tasks.filter(t => t.status !== 'done');
        if (idx >= 0 && idx < todoTasks.length - 1) {
          const realIdx = tasks.indexOf(todoTasks[idx]);
          const nextRealIdx = tasks.indexOf(todoTasks[idx + 1]);
          [tasks[realIdx], tasks[nextRealIdx]] = [tasks[nextRealIdx], tasks[realIdx]];
          tasks.forEach((t, i) => t.sort_order = i);
          saveTasks(tasks);
          const updated = [tasks[realIdx], tasks[nextRealIdx]];
          for (const t of updated) {
            if (t.id) await syncTaskToBackend({ id: t.id, sort_order: t.sort_order });
          }
          renderTaskList();
        }
        return;
      }

      /* Link milestone */
      if (target.matches('.companion-task-link')) {
        const taskId = target.dataset.taskId || target.closest('[data-task-id]')?.dataset.taskId;
        if (taskId) showTaskLinkPicker(taskId);
        return;
      }

      /* AI breakdown */
      if (target.matches('.companion-task-breakdown')) {
        const taskId = target.dataset.taskId;
        target.disabled = true;
        target.textContent = '...';
        const tasks = loadTasks();
        const task = tasks.find(t => t.id === taskId);
        if (task) {
          const steps = await aiBreakdown(task.title);
          if (steps) {
            task.sub_steps = steps;
            saveTasks(tasks);
            if (task.id) await syncTaskToBackend({ id: task.id, sub_steps: task.sub_steps });
            renderTaskList();
          }
        }
        return;
      }

      /* Apply prioritize */
      if (target.matches('.companion-prioritize-apply')) {
        const order = JSON.parse(target.dataset.order || '[]');
        const tasks = loadTasks();
        for (const item of order) {
          const t = tasks.find(tt => tt.id === item.id);
          if (t) t.sort_order = item.suggested_order;
        }
        tasks.sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0));
        tasks.forEach((t, i) => t.sort_order = i);
        saveTasks(tasks);
        for (const t of tasks) {
          if (t.id) await syncTaskToBackend({ id: t.id, sort_order: t.sort_order });
        }
        document.getElementById('prioritize-card')?.remove();
        renderTaskList();
        renderBar();
        return;
      }

      /* Dismiss prioritize */
      if (target.matches('.companion-prioritize-dismiss')) {
        document.getElementById('prioritize-card')?.remove();
        return;
      }

      /* Show all tasks */
      if (target.matches('.companion-show-all-btn')) {
        renderFullTaskList();
        return;
      }
    });
  }

  /* Delegated sub-step checkbox */
  document.addEventListener('change', e => {
    const cb = e.target;
    if (cb.matches('[data-task-id][data-step-idx]')) {
      const taskId = cb.dataset.taskId;
      const stepIdx = parseInt(cb.dataset.stepIdx, 10);
      const tasks = loadTasks();
      const task = tasks.find(t => t.id === taskId);
      if (task && task.sub_steps && task.sub_steps[stepIdx] !== undefined) {
        task.sub_steps[stepIdx].done = cb.checked;
        saveTasks(tasks);
        if (task.id) syncTaskToBackend({ id: task.id, sub_steps: task.sub_steps });
      }
    }
  });
}

const CompanionModule = {
  init,
  renderBar,
  openPanel,
  closePanel,
  switchTab,
  loadProfile,
  saveProfile,
  loadLog,
  saveLog,
  loadTasks,
  saveTasks,
  addTask,
  triageTasks,
  submitMidday,
  submitEod,
  sendChatMessage,
  loadChatMessages,
  saveChatMessages,
  clearChatMessages,
  showTaskLinkPicker,
};

export default CompanionModule;
