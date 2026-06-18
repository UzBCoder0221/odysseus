# Odysseus — Inheritance Document

## Project Overview

Odysseus is a self-hosted AI workspace — the self-hosted equivalent of ChatGPT/Claude UX, running on your own hardware. It provides Chat, Agent, Deep Research, Compare, Documents, Memory/Skills, Email triage, Notes/Tasks, Calendar, Gallery, TTS, STT, and a Companion layer for personal analytics.

- **Default branch**: `dev` (unstable development)
- **Stable branch**: `main`
- **Our fork branch**: `cai-temp`
- **Container port**: 7000
- **Auth**: Cookie-based sessions or Bearer `ody_` tokens; AuthMiddleware on all `/api/*` routes
- **Database**: SQLAlchemy ORM (SQLite default, PostgreSQL via `DATABASE_URL`)
- **Frontend**: ES6 modules, no bundler, native browser import maps

---

## Project Structure

```
odysseus/
├── app.py                    # FastAPI app — middleware, lifespan, router mounting
├── routes/                   # Backend route files (FastAPI APIRouter)
│   ├── chat_routes.py        # /api/chat_stream (SSE), /api/chat (sync)
│   ├── chat_helpers.py       # build_chat_context(), save_assistant_response()
│   ├── auth_routes.py        # login/logout/signup/settings CRUD
│   ├── stt_routes.py         # POST /api/stt/transcribe, GET /api/stt/stats
│   ├── tts_routes.py         # GET /api/tts/voices, POST /api/tts
│   └── ...
├── services/
│   ├── stt/
│   │   ├── __init__.py       # Exports STTService, get_stt_service, get_model
│   │   └── stt_service.py    # faster-whisper singleton, VAD, OpenAI Whisper API
│   └── tts/
│       └── tts_service.py    # Edge TTS, Kokoro (local), OpenAI TTS
├── src/
│   ├── settings.py           # DEFAULT_SETTINGS, get_setting(), save_settings()
│   ├── chat_processor.py     # build_context_preface(), topic overlap analysis
│   ├── llm_core.py           # stream_llm(), stream_llm_with_fallback()
│   ├── chat_handler.py       # preprocess_message()
│   └── ...
├── core/
│   ├── models.py             # ChatMessage, Session dataclasses
│   ├── session_manager.py    # Session persistence, _persist_message()
│   └── database.py           # SQLAlchemy engine, SessionLocal
├── companion/
│   └── routes.py             # /api/companion/* — profile, checkin, patterns, sysinfo
├── static/
│   ├── index.html            # SPA entry — all tabs, modals, STT/TTS UI
│   ├── app.js                # Orchestrator — imports 30+ modules, wires init
│   ├── style.css             # Global styles + STT/TTS/Companion CSS
│   └── js/
│       ├── chat.js           # Core chat module — handleChatSubmit, streaming, TTS
│       ├── stt.js            # STT — VAD, MediaRecorder, Web Speech API fallback
│       ├── companion.js      # Companion panel — profile, checkin, sysinfo, patterns
│       ├── settings.js       # Settings UI — all settings tabs and live listeners
│       └── ... (80+ other JS files)
├── tools/
│   ├── sysinfo_helpers.py    # Shared collectors (CPU, GPU, RAM, disks, OS, network)
│   ├── sysinfo_collector.py  # Interactive wizard for sysinfo upload
│   ├── sysinfo_collector_silent.py  # Silent auto-upload EXE
│   └── BUILD_INSTRUCTIONS.txt
├── todo.md                   # Future features brain dump
└── README.md                 # Project README
```

---

## Git History (Our Work)

```
af307aa — Add stop keywords and input_source voice flag
e12e99b — fix STT sync + accuracy: unified server path, model reload, language support
f973e4e — fix STT: fix mic button bugs, remove animations, add settings dropdown
338239f — companion: sysinfo integration — backend profile endpoints, collector tools, frontend UI panel
13cd0cb — Edge TTS integration + voice-list API + history TTS button fix
e4c9adf — Dual-phase TTS pipeline: 1-ahead lookahead during stream, parallel synth on flush
06bd1db — Fix TTS chunked pipeline: find LAST boundary, drop chunk min-size, fix think-close branch
10a1aa3 — TTS: chunked pipeline, improved preprocess, CPU kokoro-onnx, UI unhide
14ee751 — Consolidate memory: merge companion extraction into Brain, remove separate memory table/UI
f693e65 — feat(companion): Stage 4 — Pattern Memory & Continuity
```

---

## What We Have Done

### 1. Companion Panel — UI Restructuring

**Goal**: Replace System and Downloads top-level tabs with a cleaner layout.

**Changes**:
- Removed `System` and `Downloads` tabs from the companion tab bar
- Moved **System Info** into the **Profile** tab as a collapsible card
- Moved **Downloads** content into a **modal** (triggered from settings)
- Tab bar now shows exactly: **Check-in, Today, History, Profile, Routine**

**Files**:
- `static/index.html` — Removed sysinfo/downloads tab buttons, added sysinfo card inside Profile, downloads modal
- `static/js/companion.js` — `renderSysInfo()` fetches and renders system info inside Profile; `renderDownloadsModal()` shows downloads in modal; removed `switchTab` cases for sysinfo/downloads
- `static/style.css` — Collapsible card styles, modal styles

**Key patterns**:
- Collapsible cards use `.companion-card-header` + chevron + `.companion-card-body` (existing pattern reused)
- Modal dismisses via X button, click-outside (`.modal-backdrop`), and Escape key
- `syncFromBackend()` replaced by single profile fetch (was 4 endpoints)

### 2. Sysinfo Collector Tools

**Goal**: Standalone executables that collect and upload system information.

**Files created**:
- `tools/sysinfo_helpers.py` — Shared collectors with **daemon-thread timeout** (5s per section) to prevent hangs on VMs/containers. Collects: CPU (name, cores, usage, freq), GPU (name, VRAM, driver), RAM (total, used, percent), disks (mount, total, used, fstype), OS (platform, release, hostname, boot time), network (interfaces, IPs, MACs), screen (resolution, count), system (uptime, processes, logged-in users)
- `tools/sysinfo_collector.py` — Interactive wizard: runs all collectors, previews data, POSTs to `/api/companion/sysinfo` with token `sysinfo`
- `tools/sysinfo_collector_silent.py` — Silent auto-upload with no prompts, designed for PyInstaller EXE
- `tools/BUILD_INSTRUCTIONS.txt` — PyInstaller build commands

**Backend endpoints** (in `companion/routes.py`):
- `POST /api/companion/sysinfo` — Accept sysinfo data, store per-owner
- `GET /api/companion/sysinfo` — Return stored system info (falls back to any profile with `system_info` if current owner has none)
- `GET /api/companion/sysinfo/downloads/status` — Check EXE availability in `dist/`
- `GET /api/companion/sysinfo/downloads/exe` — Serve EXE files
- `GET /api/companion/sysinfo/downloads/source` — Serve Python source

**Key details**:
- `_last_active_username` module-level variable in `companion/routes.py` tracked by `token_owner()` — ensures sysinfo is attributed to the right user
- `GET /profile` falls back to any profile with `system_info` for display

### 3. STT — Speech-to-Text (Three-Provider Architecture)

**Goal**: Voice input via mic button with VAD, supporting local, cloud, and browser providers.

#### Backend (`services/stt/`)

**`services/stt/stt_service.py`**:
- `STTService` singleton class with lazy-loaded `faster-whisper` model (CPU int8)
- `get_model(model_name)` — Loads model on first call; reloads when `stt_model` setting changes (tracked by `_loaded_model_name`)
- `transcribe(audio_data, provider)` — Multiplexes to:
  - `_transcribe_local()`: Writes temp WAV, calls `model.transcribe()` with VAD filter, language, beam_size=5
  - `_transcribe_openai()`: Sends to OpenAI Whisper API
  - `_transcribe_browser()`: Passthrough (browser handles its own STT)
- `get_stats()` — Returns model name, uptime, total duration, error count

**`services/stt/__init__.py`**: Exports `STTService`, `get_stt_service`, `get_model`

**`routes/stt_routes.py`**:
- `POST /api/stt/transcribe` — Accepts `file` + `provider` form fields; dispatches to service; returns JSON `{text: "..."}`; upload size limited via `read_upload_limited()`
- `GET /api/stt/stats` — Returns service stats

#### Frontend (`static/js/stt.js`)

**Architecture**:
- **VAD engine**: RMS-based voice activity detection via Web Audio API (`AnalyserNode.getFloatTimeDomainData`). Threshold: 0.01. Poll interval: 100ms
- **Recording**: `MediaRecorder` with `audio/webm` mime type, chunks collected via `ondataavailable`
- **Silence detection**: Configurable timeout (500-3000ms, default 1200). Timer starts when RMS drops below threshold, stops recording if silence persists
- **TTS coexistence**: VAD pauses recording when `window.aiTTSManager.isPlaying` is true; resumes automatically
- **Three providers**:
  - `local` — Sends audio to backend faster-whisper
  - `openai` — Sends to OpenAI Whisper API via backend
  - `browser` — Client-side Web Speech API (`SpeechRecognition`/`webkitSpeechRecognition`)
- **Two modes**:
  - `fill` — Transcribed text appended to input with space separator; user presses Enter to send
  - `send` — Text replaces input content; auto-submits via `handleChatSubmit()` after 100ms delay

**Key functions**:
- `startVAD()` — Gets mic stream, creates AudioContext + Analyser, starts polling loop
- `stopVAD()` — Stops tracks, closes AudioContext, cancels timers, resets state
- `startRecording()` / `stopRecording()` — Manages MediaRecorder lifecycle
- `cancelRecording()` — Stops without processing (used for stop keywords)
- `processRecording()` — Sends blob to backend via `POST /api/stt/transcribe`, calls `handleTranscription()` on result
- `handleTranscription(text)` — Routes text to fill/send mode; checks stop keywords first
- `startBrowserSpeech()` — Web Speech API fallback
- `isStopCommand(text)` — Exact-match check against `STT_STOP_PHRASES`

**UI** (`static/index.html`, `static/style.css`):
- Mic button (`#stt-mic-btn`) + dropdown arrow (`#stt-dd-btn`) in `.stt-container`, inside the chat input bar
- Mic states: `stt-idle`, `stt-listening`, `stt-recording`, `stt-processing`, `stt-error` (no animations — plain color transitions)
- Dropdown with: Mic toggle, Provider select, Mode select, Silence range slider
- STT settings card in settings modal (mirrors dropdown — both write through `window.syncSTTSetting()`)

**Sync mechanism** (`window.syncSTTSetting()`):
- Defined on `window` in `stt.js`
- Writes to `localStorage` + `POST /api/auth/settings` atomically
- Called by both dropdown handlers (`stt.js`) and settings modal listeners (`settings.js`)
- Fails silently on 403 (non-admin users can't change global settings)

#### Bug Fixes Applied

**Commit `f973e4e`** (fix STT mic button bugs):
- Removed auto-start VAD on page load (must be explicitly toggled by user)
- Mic button only toggles VAD, does not change `sttEnabled` setting
- `onstop` set in `startRecording()` (not `stopRecording()`) to ensure `processRecording()` always fires
- TTS interruption now properly processes recording data
- Check `vadActive` after transcription before setting listening state
- Use `#stt-container` for show/hide (consistent, not raw button)
- Removed pulse/spin animations from mic button states
- Added STT settings dropdown with Mic toggle, Provider, Mode, Silence slider

**Commit `e12e99b`** (fix STT sync + accuracy):
- Added `window.syncSTTSetting(key, value)` — dual-writes localStorage + server
- Updated all dropdown handlers to use `syncSTTSetting` instead of localStorage-only
- Updated `settings.js` silence/mode listeners to sync to server via `window.syncSTTSetting()`
- Added `stt_silence_ms` to `_INT_RANGES` in `auth_routes.py` for integer coercion
- Changed default `stt_model` from `base` to `small` in `src/settings.py`
- Fixed `get_model()` to reload when `stt_model` changes (added `_loaded_model_name` tracking)
- Fixed `transcribe()` to read `stt_language` setting and pass to faster-whisper
- Unhid custom model name input
- Added `large` option to model dropdown

### 4. Stop Keywords

**Commit `af307aa`**:

**Frontend** (`static/js/stt.js`):
- `STT_STOP_PHRASES = ['stop', 'cancel', 'never mind', 'nevermind']`
- `isStopCommand(text)` — Normalizes: `text.toLowerCase().trim().replace(/[.,!?]+$/, '')`, then `Array.includes()` for exact-match. Only matches whole utterance, not substring (e.g., "I need to stop and think" passes through normally)
- Guard at top of `handleTranscription()`: if `isStopCommand(text)`, calls `stopVAD()` and returns without forwarding text

### 5. `input_source` Voice Flag

**Commit `af307aa`**:

**Goal**: When a message is sent via voice (STT 'send' mode), signal the backend so it can inject an ephemeral instruction for the LLM to handle homophone errors more leniently.

**Frontend** (`static/js/chat.js`):
- `lastMessageWasVoice` (module-level boolean, starts `false`)
- `setVoiceOrigin()` — Sets `lastMessageWasVoice = true`; exported on `chatModule`
- In `handleChatSubmit()`: if `lastMessageWasVoice`, appends `input_source: 'voice'` to FormData, then resets to `false`
- `input` event listener on `#message` with `e.isTrusted` guard — resets flag only on real keystrokes, not programmatic dispatches

**Frontend** (`static/js/stt.js`):
- In `handleTranscription()` 'send' mode: calls `window.chatModule.setVoiceOrigin()` before `window.chatModule.handleChatSubmit()`

**Backend** (`routes/chat_routes.py`):
- Reads `input_source` from FormData at line 558: `input_source = str(form_data.get("input_source", "")).strip().lower()`
- Passes to `build_chat_context()` at line 586

**Backend** (`routes/chat_helpers.py`):
- `build_chat_context()` accepts `input_source: str = ""` parameter (line 521)
- When `input_source == "voice"` (lines 643-651): injects an ephemeral user-role message right before the latest user turn:
  ```python
  {"role": "user", "content": "[Voice Input] The message was spoken via voice input. Transcribe homophones accordingly, preferring words that match the context despite similar sounds."}
  ```
- Same injection pattern as the datetime context message — injected into `messages` list, never persisted to DB

### 6. TTS — Text-to-Speech (Three-Provider Architecture)

**Goal**: Stream AI responses as speech with chunked pipeline, lookahead, and parallel synthesis.

**Providers** (from `services/tts/tts_service.py`):
1. **Edge TTS** (Microsoft neural, free/cloud) — Primary, fallback chain: edge -> local Kokoro -> OpenAI
2. **Kokoro (local)** — CPU onnx runtime, `kokoro.create()` wrapped in `run_in_executor`
3. **OpenAI TTS** — API-based

**Pipeline** (`static/js/chat.js`):
- **Dual-phase**: 1-ahead lookahead during stream + parallel synthesis on flush
- Chunked: finds LAST sentence boundary, buffers until min-size met
- TTS buttons added to AI bubbles (`static/js/tts-ai.js`)
- History messages get TTS buttons on page refresh via `_attachButtonsToExisting()`

**Voice management**:
- `GET /api/tts/voices?provider=...` — Returns voice lists per provider
- Dynamic voice select in settings; `Custom...` option for ad-hoc names
- Per-provider voice memory (in-memory)

### 7. Pattern Memory & Continuity (Companion Stage 4)

**Goal**: Detect mood/sleep/task patterns from check-in data and inject into AI context.

**Backend** (`companion/routes.py`):
- `detect_patterns()` — SQL/Python aggregation returning mood trends, sleep patterns, task completion rates, low-mood days, check-in streak, carry-over tasks
- `GET /api/companion/patterns` — Returns pattern analysis
- Pattern context injection into `/briefing` and `/message` endpoints (bar + conversation)

**Frontend** (`static/js/companion.js`):
- Collapsible `📊 Patterns` card in History tab
- Pattern visualization with trend indicators

### 8. Memory Consolidation

**Commit `14ee751`**:
- Merged companion extraction into Brain module
- Removed separate memory table and UI
- Companion routes updated to use unified memory system

---

## Key Architecture Patterns

### Chat Submit Flow

```
Send button click / Enter key
  → chatForm.onsubmit (app.js:3550)
    → handleSubmit(e) (app.js:3520) — debounce, compare/group routing
      → chatModule.handleChatSubmit(e) (chat.js:288)
        → reads msg from #message, validates attachments
        → builds FormData (chat.js:794-828) with message, session, mode, toggles
        → POST /api/chat_stream (SSE)
          → chat_routes.py:442-503 — parse FormData
          → build_chat_context() (chat_helpers.py:500-657)
            → preprocess_message() — CoT, YouTube, VL
            → add_user_message() — saves to DB
            → build_context_preface() — system prompt, memories, RAG, web
            → sess.get_context_messages() — history
            → inject datetime context (ephemeral)
            → inject voice hint if input_source (ephemeral)
            → maybe_compact() / trim_for_context()
          → stream_llm_with_fallback() or stream_agent_loop()
          → yield SSE chunks (delta, tool_start/end, usage, metrics)
        → SSE parser parses chunks
        → chatRenderer displays messages
```

### STT Flow

```
User clicks mic button → startVAD()
  → getUserMedia({audio}) → AudioContext + Analyser
  → poll RMS every 100ms
  → speech detected (RMS > 0.01) → startRecording() → MediaRecorder starts
  → silence detected (RMS < 0.01 for silence_ms) → stopRecording()
    → onRecordingComplete → processRecording()
      → POST /api/stt/transcribe (FormData: file + provider)
        → stt_routes.py dispatches to provider
          → local: stt_service.transcribe() → faster-whisper
          → openai: stt_service.transcribe() → OpenAI API
          → browser: passthrough (handled client-side)
      → returns {text: "..."}
    → handleTranscription(text)
      → if isStopCommand(text): stopVAD(), return (text NOT forwarded)
      → mode 'fill': append to #message, dispatch input event
      → mode 'send': set #message value, dispatch input, setVoiceOrigin(), handleChatSubmit()
```

### Context Building (build_chat_context)

Located at `routes/chat_helpers.py:500-657`.

1. Extract preset
2. Preprocess message (CoT, vision, YouTube transcripts)
3. `add_user_message()` — saves to DB immediately
4. Fire webhook events (if not incognito)
5. Resolve user prefs (memory/skills toggles)
6. `build_context_preface()` — system prompt, untrusted context policy, memories, RAG, web search, URL fetches, skills index
7. `messages = preface + sess.get_context_messages()`
8. Inject current datetime (user-role, ephemeral — right before latest user turn)
9. Inject voice hint if `input_source == "voice"` (user-role, ephemeral — same position)
10. `maybe_compact()` + `trim_for_context()`
11. Return `ChatContext` dataclass

### Settings System

- `src/settings.py`: `DEFAULT_SETTINGS` dict (~100 keys), TTL-cached (2s) JSON I/O at `data/settings.json`
- `get_setting(key, default)` — Single key read
- `save_settings(settings)` — Atomic write via `core.atomic_io`
- Per-user overrides for vision/image/research settings (whitelisted in `_PER_USER_KEYS`)
- Frontend settings modal writes to `POST /api/auth/settings`
- STT settings have a special dual-write path: `window.syncSTTSetting()` writes both `localStorage` and server

### Docker/Deployment

- Container runs on port 7000
- Static files (JS/CSS/HTML) picked up immediately on browser refresh (no build step)
- Backend changes require `docker cp` + `docker restart` or image rebuild
- Health check may be disabled; model loading (faster-whisper) can take 30-60s
- `docker compose up -d --build` from project root

---

## Important Implementation Details

### Critical Flags & Variables

| Variable | Location | Purpose |
|---|---|---|
| `lastMessageWasVoice` | `chat.js:45` | Whether current message originated from voice STT |
| `vadActive` | `stt.js` | Whether VAD polling loop is running |
| `isRecording` | `stt.js` | Whether MediaRecorder is actively recording |
| `sttEnabled` | `stt.js` | Whether STT is enabled (persisted to localStorage + server) |
| `_sendInFlight` | `chat.js` | Guards against double-submit |
| `isStreaming` | `chat.js` | Whether SSE stream is active |
| `_onTranscription` | `stt.js` | Optional callback (currently unused — handleTranscription is the actual handler) |

### Key Constants

| Constant | Value | Location |
|---|---|---|
| `VAD_SPEECH_THRESHOLD` | 0.01 | `stt.js` |
| `VAD_POLL_MS` | 100 | `stt.js` |
| `STT_STOP_PHRASES` | ['stop', 'cancel', 'never mind', 'nevermind'] | `stt.js` |
| Default `stt_silence_ms` | 1200 | `stt.js`, `src/settings.py` |
| Default `stt_model` | "small" | `src/settings.py` |
| `_CACHE_TTL` | 2.0s | `src/settings.py` |
| `STALL_THRESHOLD_MS` | 60000 | `chat.js` |
| `DEFAULT_TIMEOUT_MS` | 120000 | `chat.js` |

### Important Edge Cases

1. **Stop keyword false positives**: `isStopCommand()` normalizes trailing punctuation only (`. , ! ?`). Does NOT check for substring — "I need to stop" does NOT match. Only exact utterance-level match.

2. **input_source race condition**: The `input` event at `chat.js:649` fires inside `handleChatSubmit()` after `setVoiceOrigin()` but before FormData append. The `isTrusted` guard on the `input` listener prevents untrusted (programmatic) events from resetting the flag.

3. **STT + TTS coexistence**: VAD poll checks `window.aiTTSManager.isPlaying` each cycle. When TTS is playing, active recording is stopped and new recording is suppressed. Recording resumes when TTS finishes.

4. **Model reload**: `get_model()` compares `_loaded_model_name` against current `stt_model` setting on every call. If different, unloads old model and loads new one. Container logs confirm each switch.

5. **Silence range**: `stt_silence_ms` is clamped to 500-3000ms via `_INT_RANGES` in `auth_routes.py`. The UI slider goes 800-2000ms, but server accepts the full range.

6. **Settings sync failures**: `syncSTTSetting()` fails silently on 403 (non-admin users can't modify global settings via `/api/auth/settings`). localStorage still works for them.

7. **Forms without `action`**: The chat form uses `action="javascript:void(0);"`. The submit event is captured via `chatForm.onsubmit = handleSubmit` in `app.js:3550`. Direct calls to `chatModule.handleChatSubmit()` skip the `handleSubmit` wrapper (debounce/compare/group routing).

8. **Sysinfo collection timeout**: Each collector section runs in a daemon thread with 5s timeout to prevent hangs on VMs/containers where certain hardware info is unavailable.

---

## Files Modified (Summary by Feature)

### STT Implementation
- `static/js/stt.js` — Full STT module (441 lines): VAD, MediaRecorder, providers, modes, stop keywords, sync
- `static/index.html` — Mic button + dropdown in `.stt-container`, STT settings card
- `static/style.css` — Mic states, dropdown styles (no animations)
- `services/stt/__init__.py` — Module exports
- `services/stt/stt_service.py` — faster-whisper singleton, multi-provider transcribe
- `routes/stt_routes.py` — `/api/stt/transcribe`, `/api/stt/stats`
- `routes/auth_routes.py` — `_INT_RANGES` for `stt_silence_ms`
- `src/settings.py` — STT defaults in `DEFAULT_SETTINGS`
- `static/js/settings.js` — STT settings tab with live listeners calling `syncSTTSetting()`
- `app.py` — Eager STT model load on startup

### Stop Keywords + input_source
- `static/js/stt.js` — `STT_STOP_PHRASES`, `isStopCommand()`, guard in `handleTranscription`, `setVoiceOrigin()` call
- `static/js/chat.js` — `lastMessageWasVoice`, `setVoiceOrigin()`, FormData append, input listener with `isTrusted`
- `routes/chat_routes.py` — Read `input_source` from form data
- `routes/chat_helpers.py` — Accept `input_source` param, inject voice hint message

### Companion + Sysinfo
- `static/js/companion.js` — Profile tab, sysinfo card, downloads modal, pattern card
- `static/index.html` — Tab bar restructuring, sysinfo card HTML, downloads modal HTML
- `static/style.css` — Card collapse, modal, pattern card styles
- `companion/routes.py` — Profile/checkin/patterns/sysinfo/downloads endpoints
- `tools/sysinfo_helpers.py` — Shared collector functions with timeout
- `tools/sysinfo_collector.py` — Interactive upload wizard
- `tools/sysinfo_collector_silent.py` — Silent auto-upload EXE
- `tools/BUILD_INSTRUCTIONS.txt` — PyInstaller build docs
- `core/database.py` — Companion profile/checkin tables
- `.gitignore` — `*.spec` files

### TTS
- `services/tts/tts_service.py` — Edge/Kokoro/OpenAI providers, voice lists
- `routes/tts_routes.py` — `/api/tts/voices`, `/api/tts`
- `static/js/chat.js` — Dual-phase pipeline, chunked synthesis, lookahead
- `static/js/tts-ai.js` — TTS buttons on AI bubbles
- `static/js/chatRenderer.js` — `_attachButtonsToExisting()` for history
- `static/js/settings.js` — Dynamic voice select, per-provider memory
- `static/index.html` — TTS UI unhide
- `src/settings.py` — TTS defaults
- `requirements.txt` — `edge-tts` dependency

### Memory Consolidation
- `companion/routes.py` — Merge extraction into Brain
- `core/database.py` — Memory tables
- `routes/chat_helpers.py`, `routes/chat_routes.py` — Use unified memory
- `static/index.html`, `static/js/companion.js` — Updated UI
- `static/style.css` — Memory styles
