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
    idealSleepHours: 8
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
      ideal_sleep_hours: p.idealSleepHours || 8
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
}

function closePanel() {
  const panel = el('companion-panel');
  const overlay = el('companion-overlay');
  if (panel) panel.classList.add('hidden');
  if (overlay) overlay.classList.add('hidden');
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

async function sendChatMessage() {
  const input = el('companion-chat-input');
  const text = (input?.value || '').trim();
  if (!text) return;
  if (input) input.value = '';

  const msgs = loadChatMessages();
  msgs.push({ role: 'user', content: text });
  saveChatMessages(msgs);
  renderChatLog();

  /* Call AI */
  const profile = loadProfile();
  const log = loadLog();
  const today = log[todayKey()] || {};

  try {
    const res = await fetch(`${API_BASE}/api/companion/message`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
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
    if (data.message) {
      msgs.push({ role: 'assistant', content: data.message });
      saveChatMessages(msgs);
      renderChatLog();
    } else if (data.error) {
      msgs.push({ role: 'assistant', content: 'I\'m here.' });
      saveChatMessages(msgs);
      renderChatLog();
    }
  } catch (_) {
    msgs.push({ role: 'assistant', content: 'I\'m here.' });
    saveChatMessages(msgs);
    renderChatLog();
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

function getNudgeMessages(type) {
  const nudges = {
    greeting: ['Hey, just checking in. How are you feeling?'],
    midday: ['Quick check: how\'s your day going so far?', 'Mid-day pause — how are you holding up?', 'Halfway through the day — how\'s the energy?'],
    eod: ['Evening reflection time. How was your day?', 'Day's winding down — want to reflect?', 'End-of-day pause — what went well?'],
    move: ['Time to stretch those legs. Walk a few steps?', 'Stand up, roll your shoulders, breathe.'],
    drink: ['Sip some water. Your brain will thank you.', 'Hydration check: had water recently?'],
    breathe: ['Close your eyes. Take three slow breaths.', 'Breathe in for 4, hold for 4, out for 4.'],
  };
  return nudges[type] || nudges.greeting;
}

function selectNudgeType() {
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

function showNudge() {
  const nudgeEl = el('companion-nudge');
  if (!nudgeEl) return;

  const lastNudge = parseInt(localStorage.getItem(NUDGE_KEY) || '0', 10);
  const now = Date.now();
  if (now - lastNudge < 30 * 60 * 1000) return;

  const type = selectNudgeType();
  const msgs = getNudgeMessages(type);
  const msg = msgs[Math.floor(Math.random() * msgs.length)];

  const isActionNudge = type === 'midday' || type === 'eod' || type === 'greeting';
  nudgeEl.innerHTML = `
    <span class="companion-nudge-text">${esc(msg)}</span>
    ${isActionNudge ? `<button class="companion-nudge-btn" data-nudge-action="${type}">Open companion</button>` : ''}
    <button class="companion-nudge-dismiss" data-nudge-action="dismiss">&times;</button>
  `;
  nudgeEl.classList.remove('hidden');

  localStorage.setItem(NUDGE_KEY, String(now));
}

function dismissNudge() {
  const nudgeEl = el('companion-nudge');
  if (nudgeEl) nudgeEl.classList.add('hidden');
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
  else switchTab('checkin');
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
  html += '<span class="companion-task-title" ' + titleDone + ' style="flex:1;min-width:0;font-size:12px;word-break:break-word;">' + esc(task.title) + '</span>';
  if (task.estimated_minutes) html += '<span style="font-size:10px;opacity:0.4;flex-shrink:0;">' + task.estimated_minutes + 'm</span>';
  if (task.carried_over) html += '<span style="font-size:9px;opacity:0.4;flex-shrink:0;">↻</span>';
  if (idx >= 0) {
    html += '<span class="companion-task-up" data-idx="' + idx + '" style="cursor:pointer;font-size:12px;opacity:0.3;min-height:32px;display:inline-flex;align-items:center;">▲</span>';
    html += '<span class="companion-task-down" data-idx="' + idx + '" style="cursor:pointer;font-size:12px;opacity:0.3;min-height:32px;display:inline-flex;align-items:center;">▼</span>';
  }
  if (task.status !== 'done' && subSteps.length === 0) {
    html += '<button class="companion-task-breakdown" data-task-id="' + task.id + '" style="background:none;border:none;cursor:pointer;padding:2px 4px;font-size:10px;min-height:32px;">🔧</button>';
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

  const task = {
    title: title.slice(0, 80),
    estimated_minutes: timeInput ? parseInt(timeInput.value, 10) || null : null,
    priority: prioritySelect?.value || 'Medium',
    status: 'todo',
    date: todayKey(),
    carried_over: false,
    sub_steps: [],
    sort_order: 0
  };

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

  document.querySelectorAll('[data-companion-condition]').forEach(chk => {
    chk.checked = p.conditions.includes(chk.dataset.companionCondition);
  });

  document.querySelectorAll('[data-companion-pattern]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.companionPattern === p.energyPattern);
  });
}

function saveProfileForm() {
  const p = loadProfile();
  const nameIn = el('companion-profile-name');
  const tzIn = el('companion-profile-tz');
  const sleepIn = el('companion-profile-sleep');
  if (nameIn) p.displayName = nameIn.value.trim();
  if (tzIn) p.timezone = tzIn.value.trim() || Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (sleepIn) p.idealSleepHours = parseFloat(sleepIn.value) || 8;

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
    const [profileRes, checkinsRes, tasksRes] = await Promise.all([
      fetch(`${API_BASE}/api/companion/profile`),
      fetch(`${API_BASE}/api/companion/checkins`),
      fetch(`${API_BASE}/api/companion/tasks?date=${todayKey()}`)
    ]);

    const profileData = await profileRes.json();
    if (profileData && profileData.display_name !== undefined) {
      const p = loadProfile();
      p.displayName = profileData.display_name || '';
      p.timezone = profileData.timezone || p.timezone;
      p.conditions = profileData.conditions || [];
      p.energyPattern = profileData.energy_pattern || p.energyPattern;
      p.idealSleepHours = profileData.ideal_sleep_hours || p.idealSleepHours;
      saveProfile(p);
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

/* ── Init ── */
function init() {
  carryOverTasks();
  syncFromBackend().then(() => {
    renderBar();
    populateProfileForm();
  });

  renderBar();
  populateProfileForm();

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
  ['companion-profile-name', 'companion-profile-tz', 'companion-profile-sleep'].forEach(id => {
    const inp = el(id);
    if (inp) inp.addEventListener('change', saveProfileForm);
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

  /* Nudge system */
  el('companion-nudge')?.addEventListener('click', handleNudgeClick);

  /* Show nudge on load and periodically */
  setTimeout(() => showNudge(), 5000);
  setInterval(() => showNudge(), 30000);

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
          saveTasks(tasks);
          await syncTaskToBackend({ id: task.id, status: 'todo' });
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
};

export default CompanionModule;
