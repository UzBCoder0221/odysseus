// static/js/stt.js
/* Speech-to-Text module — VAD, recording, multiple providers */

let micStream = null;
let audioContext = null;
let analyser = null;
let mediaRecorder = null;
let recordedChunks = [];
let vadActive = false;
let silenceTimer = null;
let isRecording = false;
let sttEnabled = false;
let pollInterval = null;

const VAD_SPEECH_THRESHOLD = 0.01;
const VAD_POLL_MS = 100;

let _onTranscription = null;
let _micBtn = null;
let _container = null;
let _dropdownBtn = null;
let _dropdownEl = null;
let _dropdownVisible = false;

function getRMS(analyserNode) {
  const data = new Float32Array(analyserNode.fftSize);
  analyserNode.getFloatTimeDomainData(data);
  let sum = 0;
  for (let i = 0; i < data.length; i++) sum += data[i] * data[i];
  return Math.sqrt(sum / data.length);
}

function vadSilenceMs() {
  try {
    const raw = localStorage.getItem('stt_silence_ms');
    if (raw) { const v = parseInt(raw, 10); if (v >= 500 && v <= 3000) return v; }
  } catch (_) {}
  return 1200;
}

function getSTTProvider() {
  try { return localStorage.getItem('stt_provider') || 'local'; } catch (_) { return 'local'; }
}

function getSTTMode() {
  try { return localStorage.getItem('stt_mode') || 'fill'; } catch (_) { return 'fill'; }
}

function updateMicUI(state) {
  if (!_micBtn) return;
  _micBtn.className = 'stt-btn ' + state;
}

function handleTranscription(text) {
  const mode = getSTTMode();
  const input = document.getElementById('message');
  if (!input) return;
  if (mode === 'fill') {
    input.value += (input.value ? ' ' : '') + text;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.focus();
  } else {
    input.value = text;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    setTimeout(() => {
      if (window.chatModule && typeof window.chatModule.handleChatSubmit === 'function') {
        window.chatModule.handleChatSubmit(new Event('submit'));
      }
    }, 100);
  }
}

function handleTranscriptionError() {
  if (_micBtn) {
    _micBtn.className = 'stt-btn stt-error';
    setTimeout(() => { if (vadActive) updateMicUI('stt-listening'); }, 2000);
  }
}

function processRecording() {
  if (recordedChunks.length === 0) {
    if (vadActive) updateMicUI('stt-listening');
    return;
  }
  const blob = new Blob(recordedChunks, { type: 'audio/webm' });
  const provider = getSTTProvider();
  if (provider === 'browser') {
    startBrowserSpeech();
    return;
  }
  updateMicUI('stt-processing');
  const formData = new FormData();
  formData.append('file', blob, 'recording.webm');
  formData.append('provider', provider);
  fetch('/api/stt/transcribe', { method: 'POST', body: formData })
    .then(function(res) { return res.json(); })
    .then(function(data) {
      if (data.text && data.text.trim().length > 0) {
        handleTranscription(data.text.trim());
      } else {
        handleTranscriptionError();
      }
      if (vadActive) { updateMicUI('stt-listening'); } else { updateMicUI('stt-idle'); }
    })
    .catch(function() {
      handleTranscriptionError();
      if (vadActive) { updateMicUI('stt-listening'); } else { updateMicUI('stt-idle'); }
    });
}

function startBrowserSpeech() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { handleTranscriptionError(); return; }
  updateMicUI('stt-recording');
  const recognition = new SR();
  recognition.continuous = false;
  recognition.interimResults = false;
  recognition.lang = 'en-US';
  recognition.onresult = function(e) {
    const text = e.results[0][0].transcript;
    if (text) handleTranscription(text); else handleTranscriptionError();
  };
  recognition.onerror = function() { handleTranscriptionError(); };
  recognition.onend = function() {
    if (vadActive) updateMicUI('stt-listening');
  };
  recognition.start();
}

function onRecordingComplete() {
  processRecording();
}

function startRecording() {
  isRecording = true;
  recordedChunks = [];
  try {
    mediaRecorder = new MediaRecorder(micStream, { mimeType: 'audio/webm' });
  } catch (_) {
    mediaRecorder = new MediaRecorder(micStream);
  }
  mediaRecorder.ondataavailable = function(e) {
    if (e.data.size > 0) recordedChunks.push(e.data);
  };
  mediaRecorder.onstop = onRecordingComplete;
  mediaRecorder.start(100);
  updateMicUI('stt-recording');
}

function stopRecording() {
  if (silenceTimer) { clearTimeout(silenceTimer); silenceTimer = null; }
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.onstop = onRecordingComplete;
    mediaRecorder.stop();
  }
  isRecording = false;
}

function cancelRecording() {
  if (silenceTimer) { clearTimeout(silenceTimer); silenceTimer = null; }
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.onstop = null;
    try { mediaRecorder.stop(); } catch (_) {}
  }
  isRecording = false;
  recordedChunks = [];
}

function startVAD() {
  if (vadActive) return;
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    if (_micBtn) _micBtn.title = 'Microphone not supported';
    return;
  }
  navigator.mediaDevices.getUserMedia({ audio: true })
    .then(function(stream) {
      micStream = stream;
      audioContext = new (window.AudioContext || window.webkitAudioContext)();
      analyser = audioContext.createAnalyser();
      analyser.fftSize = 512;
      var src = audioContext.createMediaStreamSource(stream);
      src.connect(analyser);
      vadActive = true;
      updateMicUI('stt-listening');
      pollInterval = setInterval(function() {
        var ttsPlaying = window.aiTTSManager && window.aiTTSManager.isPlaying;
        if (ttsPlaying) {
          if (isRecording) {
            stopRecording();
          }
          return;
        }
        var rms = getRMS(analyser);
        if (rms > VAD_SPEECH_THRESHOLD && !isRecording) {
          startRecording();
        }
        if (rms < VAD_SPEECH_THRESHOLD && isRecording) {
          if (!silenceTimer) {
            silenceTimer = setTimeout(function() { stopRecording(); }, vadSilenceMs());
          }
        }
        if (rms > VAD_SPEECH_THRESHOLD && silenceTimer) {
          clearTimeout(silenceTimer);
          silenceTimer = null;
        }
      }, VAD_POLL_MS);
    })
    .catch(function(err) {
      console.warn('Mic access denied:', err);
      if (_micBtn) _micBtn.title = 'Microphone access denied';
      sttEnabled = false;
      if (_container) _container.style.display = 'none';
    });
}

function stopVAD() {
  vadActive = false;
  if (silenceTimer) { clearTimeout(silenceTimer); silenceTimer = null; }
  if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    try { mediaRecorder.stop(); } catch (_) {}
  }
  if (micStream) {
    micStream.getTracks().forEach(function(t) { t.stop(); });
    micStream = null;
  }
  if (audioContext) {
    try { audioContext.close(); } catch (_) {}
    audioContext = null;
  }
  analyser = null;
  isRecording = false;
  recordedChunks = [];
  updateMicUI('stt-idle');
}

/* ── Dropdown ── */

function buildDropdownHTML() {
  return '<div class="stt-dropdown-inner">' +
    '<div class="stt-dd-row"><label class="stt-dd-label">Mic</label><label class="admin-switch" style="margin:0"><input type="checkbox" id="stt-dd-toggle"><span class="admin-slider"></span></label></div>' +
    '<div class="stt-dd-row"><label class="stt-dd-label">Provider</label><select id="stt-dd-provider" class="stt-dd-select"><option value="disabled">Off</option><option value="browser">Browser</option><option value="local">Local</option><option value="openai">OpenAI</option></select></div>' +
    '<div class="stt-dd-row"><label class="stt-dd-label">Mode</label><select id="stt-dd-mode" class="stt-dd-select"><option value="fill">Fill</option><option value="send">Send</option></select></div>' +
    '<div class="stt-dd-row"><label class="stt-dd-label">Silence</label><input type="range" id="stt-dd-silence" min="800" max="2000" step="100" value="1200" style="flex:1;max-width:80px;height:4px;"><span id="stt-dd-silence-label" style="font-size:10px;opacity:0.6;min-width:40px;">1200ms</span></div>' +
    '</div>';
}

function openDropdown() {
  if (_dropdownVisible) return;
  _dropdownVisible = true;
  if (!_dropdownEl) {
    _dropdownEl = document.createElement('div');
    _dropdownEl.className = 'stt-dropdown';
    _dropdownEl.innerHTML = buildDropdownHTML();
    if (_container) _container.appendChild(_dropdownEl);
    else if (_micBtn) _micBtn.parentNode.insertBefore(_dropdownEl, _micBtn.nextSibling);
    wireDropdownEvents();
  }
  _dropdownEl.classList.add('stt-dropdown-open');
  syncDropdownFromState();
  setTimeout(function() {
    document.addEventListener('click', closeDropdownOnOutside, true);
  }, 0);
}

function closeDropdown() {
  if (!_dropdownVisible) return;
  _dropdownVisible = false;
  if (_dropdownEl) _dropdownEl.classList.remove('stt-dropdown-open');
  document.removeEventListener('click', closeDropdownOnOutside, true);
}

function toggleDropdown() {
  if (_dropdownVisible) { closeDropdown(); } else { openDropdown(); }
}

function closeDropdownOnOutside(e) {
  if (!_dropdownEl) return;
  if (!_dropdownEl.contains(e.target) &&
      e.target !== _micBtn && (!_micBtn || !_micBtn.contains(e.target)) &&
      e.target !== _dropdownBtn && (!_dropdownBtn || !_dropdownBtn.contains(e.target))) {
    closeDropdown();
  }
}

function syncDropdownFromState() {
  var toggle = document.getElementById('stt-dd-toggle');
  var prov = document.getElementById('stt-dd-provider');
  var mode = document.getElementById('stt-dd-mode');
  var silence = document.getElementById('stt-dd-silence');
  var silenceLabel = document.getElementById('stt-dd-silence-label');
  if (toggle) toggle.checked = sttEnabled;
  try {
    if (prov) prov.value = localStorage.getItem('stt_provider') || 'local';
    if (mode) mode.value = getSTTMode();
    var ms = vadSilenceMs();
    if (silence) silence.value = ms;
    if (silenceLabel) silenceLabel.textContent = ms + 'ms';
  } catch (_) {}
}

function wireDropdownEvents() {
  var toggle = document.getElementById('stt-dd-toggle');
  var prov = document.getElementById('stt-dd-provider');
  var mode = document.getElementById('stt-dd-mode');
  var silence = document.getElementById('stt-dd-silence');
  var silenceLabel = document.getElementById('stt-dd-silence-label');

  if (toggle) {
    toggle.addEventListener('change', function() {
      var on = this.checked;
      try { localStorage.setItem('stt_enabled', on); } catch (_) {}
      sttEnabled = on;
      if (on) {
        startVAD();
      } else {
        stopVAD();
      }
      if (_container) _container.style.display = on ? '' : 'none';
      if (!on) closeDropdown();
    });
  }

  if (prov) {
    prov.addEventListener('change', function() {
      try { localStorage.setItem('stt_provider', this.value); } catch (_) {}
    });
  }

  if (mode) {
    mode.addEventListener('change', function() {
      try { localStorage.setItem('stt_mode', this.value); } catch (_) {}
    });
  }

  if (silence && silenceLabel) {
    silence.addEventListener('input', function() {
      silenceLabel.textContent = this.value + 'ms';
      try { localStorage.setItem('stt_silence_ms', this.value); } catch (_) {}
    });
  }
}

/* ── Public API ── */

export function init(onTranscriptionCallback) {
  _onTranscription = onTranscriptionCallback || null;
  _container = document.getElementById('stt-container');
  var micBtn = document.getElementById('stt-mic-btn');
  var ddBtn = document.getElementById('stt-dd-btn');
  if (micBtn) {
    _micBtn = micBtn;
    micBtn.addEventListener('click', function(e) {
      if (!sttEnabled) return;
      if (vadActive) {
        stopVAD();
      } else {
        startVAD();
      }
    });
  }
  if (ddBtn) {
    _dropdownBtn = ddBtn;
    ddBtn.addEventListener('click', function(e) {
      e.stopPropagation();
      if (!sttEnabled) return;
      toggleDropdown();
    });
  }

  try {
    var enabled = localStorage.getItem('stt_enabled');
    sttEnabled = enabled === 'true';
    if (_container) _container.style.display = sttEnabled ? '' : 'none';
  } catch (_) {
    sttEnabled = false;
  }
}

export function stop() {
  stopVAD();
  sttEnabled = false;
}

export function setEnabled(enabled) {
  sttEnabled = enabled;
  if (_container) _container.style.display = enabled ? '' : 'none';
  if (enabled) {
    startVAD();
  } else {
    stopVAD();
  }
  if (!enabled) closeDropdown();
}

export function refreshSettings() {
  try {
    var enabled = localStorage.getItem('stt_enabled');
    sttEnabled = enabled === 'true';
    if (!sttEnabled) {
      stopVAD();
      if (_container) _container.style.display = 'none';
      closeDropdown();
      return;
    }
    if (_container) _container.style.display = '';
    syncDropdownFromState();
  } catch (_) {}
}

var sttModule = { init: init, stop: stop, setEnabled: setEnabled, refreshSettings: refreshSettings, _dropdownVisible: _dropdownVisible, toggleDropdown: toggleDropdown, closeDropdown: closeDropdown, openDropdown: openDropdown };
export default sttModule;
