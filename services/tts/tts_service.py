# src/tts_service.py
"""Multi-provider TTS service — dispatches to local Kokoro, Edge TTS, OpenAI-compatible API, or browser."""

import asyncio
import io
import re
import wave
import logging
import hashlib
import httpx
from pathlib import Path
from typing import Optional, Dict, Any

from src.constants import TTS_CACHE_DIR

logger = logging.getLogger(__name__)


def _safe_speed(value, default: float = 1.0) -> float:
    """Parse the stored tts_speed defensively. The settings layer tolerates
    corrupt/agent-written config, so a non-numeric or empty value (e.g. an agent
    setting "speech speed" = "fast", or a hand-edited settings.json) must not
    crash synthesis or the stats endpoint with a ValueError."""
    try:
        speed = float(value)
    except (TypeError, ValueError):
        return default
    return speed if speed > 0 else default


def preprocess_for_tts(text: str) -> str:
    # 1. Remove code blocks entirely
    text = re.sub(r'```[\s\S]*?```', ' [code block] ', text)
    text = re.sub(r'`[^`]+`', '', text)

    # 2. Remove LaTeX math blocks
    text = re.sub(r'\$\$[\s\S]*?\$\$', ' ', text)
    text = re.sub(r'\$[^\$]+\$', ' ', text)

    # 3. Remove LaTeX commands
    text = re.sub(r'\\[a-zA-Z]+\{[^}]*\}', '', text)
    text = re.sub(r'\\[a-zA-Z]+', '', text)
    text = re.sub(r'\\[^a-zA-Z\s]', '', text)

    # 4. Strip markdown formatting
    text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)
    text = re.sub(r'_{1,2}([^_]+)_{1,2}', r'\1', text)
    text = re.sub(r'#{1,6}\s+', '', text)
    text = re.sub(r'^[-*+]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\d+\.\s+', '', text, flags=re.MULTILINE)

    # 5. Remove URLs
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'www\.\S+', '', text)

    # 6. Handle special characters
    text = text.replace('&', ' and ')
    text = text.replace('%', ' percent ')
    text = text.replace('\u2192', ' to ')
    text = text.replace('\u2190', ' from ')
    text = text.replace('\u2265', ' greater than or equal to ')
    text = text.replace('\u2264', ' less than or equal to ')
    text = text.replace('\u2260', ' not equal to ')
    text = text.replace('\u2026', '...')

    # 7. Remove table formatting
    text = re.sub(r'\|[^\n]+\|', '', text)
    text = re.sub(r'[-|:]{3,}', '', text)

    # 8. Clean up leftover symbols and whitespace
    text = re.sub(r'[<>{}[\]^~]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()

    # 9. Trim to 2000 chars at a sentence boundary
    if len(text) > 2000:
        cut = text[:2000].rfind('.')
        text = text[:cut+1] if cut > 1500 else text[:2000]

    return text


# ── Edge TTS (Microsoft, free/cloud) ──

async def synthesize_edge(text: str, voice: str = "en-US-AriaNeural", speed: float = 1.0) -> bytes:
    """
    Edge TTS via the edge-tts library. Returns MP3 bytes.
    Voice: full Edge voice name e.g. 'en-US-AriaNeural'
    Speed: 0.5-2.0 → Edge rate string ('+0%', '+50%', '-25%')
    """
    import edge_tts
    rate_pct = int((speed - 1.0) * 100)
    rate_str = f"+{rate_pct}%" if rate_pct >= 0 else f"{rate_pct}%"
    mp3_bytes = b""
    communicate = edge_tts.Communicate(text, voice=voice, rate=rate_str)
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            mp3_bytes += chunk["data"]
    if not mp3_bytes:
        raise RuntimeError("Edge TTS returned empty audio")
    return mp3_bytes


KOKORO_VOICES = [
    ("af_heart",    "Heart (US female, warm)"),
    ("af_bella",    "Bella (US female, cheerful)"),
    ("af_nicole",   "Nicole (US female, soothing)"),
    ("af_sarah",    "Sarah (US female, friendly)"),
    ("af_sky",      "Sky (US female, calm)"),
    ("am_adam",     "Adam (US male, confident)"),
    ("am_michael",  "Michael (US male, warm)"),
    ("am_george",   "George (US male, narrative)"),
    ("bf_emma",     "Emma (GB female, elegant)"),
    ("bf_isabella", "Isabella (GB female, soft)"),
    ("bm_george",   "George (GB male, refined)"),
    ("bm_lewis",    "Lewis (GB male, gentle)"),
    ("af_allay",    "Allay (US female, gentle)"),
    ("af_aoede",    "Aoede (US female, expressive)"),
    ("af_kore",     "Kore (US female, bright)"),
    ("af_nova",     "Nova (US female, clear)"),
    ("af_jadzia",   "Jadzia (US female, melodic)"),
    ("af_messi",    "Messi (US female, energetic)"),
    ("am_fenrir",   "Fenrir (US male, deep)"),
    ("am_liam",     "Liam (US male, smooth)"),
    ("am_onyx",     "Onyx (US male, rich)"),
    ("am_puck",     "Puck (US male, playful)"),
    ("am_echo",     "Echo (US male, resonant)"),
    ("am_gwyn",     "Gwyn (US male, soft)"),
    ("am_leo",      "Leo (US male, natural)"),
]

EDGE_VOICES = [
    ("en-US-AriaNeural",    "Aria (US female, warm)"),
    ("en-US-GuyNeural",     "Guy (US male, natural)"),
    ("en-US-JennyNeural",   "Jenny (US female, friendly)"),
    ("en-GB-SoniaNeural",   "Sonia (GB female)"),
    ("en-GB-RyanNeural",    "Ryan (GB male)"),
    ("en-AU-NatashaNeural", "Natasha (AU female)"),
]

OPENAI_VOICES = [
    ("alloy",   "Alloy (neutral)"),
    ("ash",     "Ash (neutral)"),
    ("coral",   "Coral (neutral)"),
    ("echo",    "Echo (male)"),
    ("fable",   "Fable (British female)"),
    ("nova",    "Nova (female)"),
    ("onyx",    "Onyx (male)"),
    ("sage",    "Sage (female)"),
    ("shimmer", "Shimmer (female)"),
]


class TTSService:
    """Multi-provider TTS service.

    Reads provider config from data/settings.json on each call.
    Providers:
      "disabled"        — no TTS
      "browser"         — client-side Web Speech API (no server synthesis)
      "edge"            — Microsoft Edge TTS (free, cloud, MP3)
      "local"           — Kokoro-82M on CPU
      "endpoint:<id>"   — OpenAI-compatible /audio/speech via ModelEndpoint
    """

    def __init__(self, cache_dir: str = TTS_CACHE_DIR):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._kokoro = None  # lazy-init

    # ── Settings ──

    def _load_settings(self) -> dict:
        from src.settings import load_settings
        saved = load_settings()
        return {
            "tts_enabled": saved.get("tts_enabled", True),
            "tts_provider": saved.get("tts_provider", "disabled"),
            "tts_model": saved.get("tts_model", "tts-1"),
            "tts_voice": saved.get("tts_voice", "alloy"),
            "tts_speed": saved.get("tts_speed", "1"),
            "tts_auto_play": saved.get("tts_auto_play", False),
            "openai_api_key": saved.get("openai_api_key", ""),
        }

    @property
    def available(self) -> bool:
        settings = self._load_settings()
        if settings.get("tts_enabled") is False:
            return False
        provider = settings["tts_provider"]
        if provider == "disabled":
            return False
        if provider == "browser":
            return True  # handled client-side
        if provider == "local":
            kokoro = self._get_kokoro()
            return kokoro is not None and kokoro.available
        if provider in ("edge",) or provider.startswith("endpoint:"):
            return True  # assume reachable; errors surface at synthesis time
        return False

    # ── Cache ──

    def _cache_key(self, text: str, provider: str, model: str, voice: str, speed: float = 1.0) -> str:
        raw = f"{provider}|{model}|{voice}|{speed}|{text}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _get_cached(self, key: str) -> Optional[bytes]:
        for ext in (".mp3", ".wav"):
            path = self.cache_dir / f"{key}{ext}"
            if path.exists():
                return path.read_bytes()
        return None

    def _put_cache(self, key: str, data: bytes):
        ext = ".mp3" if (len(data) >= 3 and (data[:3] == b'ID3' or (data[0] == 0xff and (data[1] & 0xe0) == 0xe0))) else ".wav"
        (self.cache_dir / f"{key}{ext}").write_bytes(data)

    def clear_cache(self):
        count = 0
        for f in self.cache_dir.glob("*.*"):
            f.unlink()
            count += 1
        logger.info(f"Cleared {count} cached TTS files")

    # ── Kokoro (local) ──

    def _get_kokoro(self):
        if self._kokoro is None:
            self._kokoro = _KokoroPipeline()
        return self._kokoro

    # ── OpenAI cloud fallback ──

    def _synthesize_openai(self, text: str, voice: str, speed: float = 1.0) -> Optional[bytes]:
        api_key = self._load_settings().get("openai_api_key", "")
        if not api_key:
            return None
        logger.info("Falling back to OpenAI TTS cloud")
        try:
            r = httpx.post(
                "https://api.openai.com/v1/audio/speech",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "tts-1",
                    "input": text,
                    "voice": voice if voice in ("alloy", "echo", "fable", "onyx", "nova", "shimmer") else "alloy",
                    "response_format": "mp3",
                    "speed": speed,
                },
                timeout=60,
            )
            r.raise_for_status()
            return r.content
        except Exception as e:
            logger.error(f"OpenAI TTS fallback failed: {e}")
            return None

    # ── API endpoint ──

    def _synthesize_api(self, text: str, endpoint_id: str, model: str, voice: str, speed: float = 1.0) -> Optional[bytes]:
        from src.database import SessionLocal, ModelEndpoint

        db = SessionLocal()
        try:
            ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == endpoint_id).first()
            if not ep:
                logger.error(f"TTS endpoint {endpoint_id} not found")
                return None
            base_url = ep.base_url.rstrip("/")
            api_key = ep.api_key
        finally:
            db.close()

        url = base_url + "/audio/speech"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": model,
            "input": text,
            "voice": voice,
            "response_format": "mp3",
            "speed": speed,
        }

        try:
            r = httpx.post(url, json=payload, headers=headers, timeout=60)
            r.raise_for_status()
            logger.info(f"API TTS: {len(r.content)} bytes from {base_url}")
            return r.content
        except Exception as e:
            logger.error(f"API TTS synthesis failed: {e}")
            return None

    # ── Public interface ──

    def synthesize(self, text: str, use_cache: bool = True,
                   provider: Optional[str] = None,
                   voice: Optional[str] = None,
                   speed: Optional[float] = None) -> Optional[bytes]:
        settings = self._load_settings()
        if settings.get("tts_enabled") is False:
            return None
        provider = provider or settings["tts_provider"]
        model = settings["tts_model"]
        voice = voice or settings["tts_voice"]
        speed = speed or _safe_speed(settings.get("tts_speed", "1"))

        if provider in ("disabled", "browser"):
            return None

        text = preprocess_for_tts(text)

        if use_cache:
            key = self._cache_key(text, provider, model, voice, speed)
            cached = self._get_cached(key)
            if cached:
                logger.info(f"TTS cache hit ({len(text)} chars)")
                return cached

        audio_data = None

        if provider == "edge":
            try:
                audio_data = asyncio.run(synthesize_edge(text, voice, speed))
            except Exception as e:
                logger.warning(f"Edge TTS failed ({e}), falling back to local")
                kokoro = self._get_kokoro()
                if kokoro and kokoro.available:
                    audio_data = kokoro.synthesize_raw(text, voice)
                if not audio_data:
                    audio_data = self._synthesize_openai(text, voice, speed)
        elif provider == "local":
            kokoro = self._get_kokoro()
            if kokoro and kokoro.available:
                audio_data = kokoro.synthesize_raw(text, voice)
            if not audio_data:
                audio_data = self._synthesize_openai(text, voice, speed)
            if not audio_data:
                logger.warning("Kokoro TTS not available and no fallback")
                return None
        elif provider.startswith("endpoint:"):
            endpoint_id = provider.split(":", 1)[1]
            audio_data = self._synthesize_api(text, endpoint_id, model, voice, speed)
        else:
            logger.error(f"Unknown TTS provider: {provider}")
            return None

        if audio_data and use_cache:
            key = self._cache_key(text, provider, model, voice, speed)
            self._put_cache(key, audio_data)

        return audio_data

    def synthesize_to_base64(self, text: str, **kwargs) -> Optional[str]:
        import base64
        audio = self.synthesize(text, **kwargs)
        if audio:
            return base64.b64encode(audio).decode("utf-8")
        return None

    def get_voices(self, provider: Optional[str] = None) -> list[dict]:
        """Return available voices for a provider.

        Each entry: {"value": "...", "label": "..."}
        """
        if provider is None:
            provider = self._load_settings().get("tts_provider", "disabled")

        if provider == "local":
            return [{"value": v, "label": l} for v, l in KOKORO_VOICES]
        if provider == "edge":
            return [{"value": v, "label": l} for v, l in EDGE_VOICES]
        if provider == "endpoint" or provider.startswith("endpoint:"):
            return [{"value": v, "label": l} for v, l in OPENAI_VOICES]
        return []

    def set_voice(self, voice: str):
        """Legacy no-op — voice is now managed via admin settings."""

    def get_stats(self) -> Dict[str, Any]:
        settings = self._load_settings()
        provider = settings["tts_provider"]
        tts_enabled = settings.get("tts_enabled", True)

        cache_files = list(self.cache_dir.glob("*.wav")) + list(self.cache_dir.glob("*.mp3"))
        cache_size = sum(f.stat().st_size for f in cache_files)

        is_available = self.available and tts_enabled
        stats = {
            "available": is_available,
            "ready": is_available,
            "provider": provider,
            "model": settings["tts_model"],
            "voice": settings["tts_voice"],
            "speed": _safe_speed(settings.get("tts_speed", "1")),
            "cache_entries": len(cache_files),
            "cache_size_mb": round(cache_size / (1024 * 1024), 2),
        }

        if provider == "local":
            kokoro = self._get_kokoro()
            stats["model"] = "Kokoro-82M (CPU/ONNX)" if (kokoro and kokoro.available) else "Kokoro (not loaded)"
        elif provider == "edge":
            stats["model"] = "Edge TTS (Microsoft Neural)"
        elif provider == "browser":
            stats["model"] = "Browser (Web Speech API)"
        elif provider.startswith("endpoint:"):
            stats["endpoint_id"] = provider.split(":", 1)[1]

        return stats


class _KokoroPipeline:
    """Encapsulates the Kokoro-82M local ONNX pipeline (CPU via kokoro-onnx).

    Model files are downloaded from GitHub on first use and cached in the
    TTS cache directory.
    """

    _MODEL_URL = (
        "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
        "model-files-v1.0/kokoro-v1.0.onnx"
    )
    _VOICES_URL = (
        "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
        "model-files-v1.0/voices-v1.0.bin"
    )

    def __init__(self, model_dir: Optional[str] = None):
        self._kokoro = None
        self.available = False
        self._model_dir = Path(model_dir or TTS_CACHE_DIR)
        self._model_dir.mkdir(parents=True, exist_ok=True)
        self._init()

    def _ensure_models(self) -> bool:
        model_path = self._model_dir / "kokoro-v1.0.onnx"
        voices_path = self._model_dir / "voices-v1.0.bin"
        if model_path.exists() and voices_path.exists():
            return True
        logger.info("Downloading Kokoro ONNX models (~170 MB total)...")
        try:
            for name, url in [
                ("kokoro-v1.0.onnx", self._MODEL_URL),
                ("voices-v1.0.bin", self._VOICES_URL),
            ]:
                dest = self._model_dir / name
                if not dest.exists():
                    logger.info(f"Downloading {name}...")
                    r = httpx.get(url, follow_redirects=True, timeout=300)
                    r.raise_for_status()
                    dest.write_bytes(r.content)
            return True
        except Exception as e:
            logger.error(f"Failed to download Kokoro ONNX models: {e}")
            return False

    def _init(self):
        try:
            from kokoro_onnx import Kokoro
            if not self._ensure_models():
                return
            model_path = str(self._model_dir / "kokoro-v1.0.onnx")
            voices_path = str(self._model_dir / "voices-v1.0.bin")
            self._kokoro = Kokoro(model_path, voices_path)
            self.available = True
            logger.info("Kokoro-82M ONNX pipeline loaded on CPU")
        except ImportError as e:
            logger.warning(f"Kokoro ONNX TTS not available: {e}")
            logger.warning("Install with: pip install kokoro-onnx")
        except Exception as e:
            logger.error(f"Kokoro ONNX init failed: {e}", exc_info=True)

    def synthesize_raw(self, text: str, voice: str = "af_heart") -> Optional[bytes]:
        if not self.available or not self._kokoro:
            return None
        try:
            import numpy as np
            samples, sample_rate = self._kokoro.create(
                text, voice=voice, speed=1.0, lang="en-us"
            )
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes((samples * 32767).astype(np.int16).tobytes())
            return buf.getvalue()
        except Exception as e:
            logger.error(f"Kokoro ONNX synthesis failed: {e}", exc_info=True)
            return None


# Module-level singleton
_tts_service = None

def get_tts_service() -> TTSService:
    global _tts_service
    if _tts_service is None:
        _tts_service = TTSService()
    return _tts_service


# ── Inline unit tests ──
if __name__ == "__main__":
    test_cases = [
        ("\\frac{a}{b} equals 0.5", "equals 0.5"),
        ("**bold text** here", "bold text here"),
        ("check `code here` for bugs", "check  for bugs"),
        ("visit https://example.com now", "visit  now"),
        ("A \u2192 B means A leads to B", "A  to B means A leads to B"),
        ("```python\nx=1\n```", "[code block]"),
    ]
    for input_text, expected_fragment in test_cases:
        result = preprocess_for_tts(input_text)
        assert expected_fragment in result, f"FAIL: '{input_text}' \u2192 '{result}'"
        print(f"PASS: '{input_text}' \u2192 '{result}'")
    print("All TTS preprocessing tests passed.")
