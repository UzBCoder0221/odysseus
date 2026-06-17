# services/stt/stt_service.py
"""Multi-provider STT service — dispatches to local faster-whisper, OpenAI Whisper API, or browser."""

import logging
import os
import tempfile
from typing import Optional

from src.settings import get_setting

logger = logging.getLogger(__name__)

_model = None
_loaded_model_name = None


def get_model():
    global _model, _loaded_model_name
    current_name = get_setting("stt_model", "small")
    if _model is None or _loaded_model_name != current_name:
        logger.info(f"Loading faster-whisper model '{current_name}' (CPU, int8)...")
        try:
            from faster_whisper import WhisperModel
            _model = WhisperModel(current_name, device="cpu", compute_type="int8")
            _loaded_model_name = current_name
            logger.info("faster-whisper model loaded OK")
        except Exception as e:
            logger.error(f"Failed to load faster-whisper: {e}")
    return _model


def transcribe(audio_bytes: bytes, mime_type: str = "audio/webm") -> str:
    model = get_model()
    lang_setting = get_setting("stt_language", "").strip()
    language = lang_setting if lang_setting else None
    suffix = ".webm" if "webm" in mime_type else ".wav" if "wav" in mime_type else ".mp4"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(audio_bytes)
        tmp_path = f.name
    try:
        segments, info = model.transcribe(
            tmp_path,
            language=language,
            vad_filter=True,
            vad_parameters={
                "min_silence_duration_ms": 300,
                "threshold": 0.5,
            },
            beam_size=5,
            word_timestamps=False,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return text
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


class STTService:
    """Multi-provider STT service.

    Reads provider config from data/settings.json on each call.
    Providers:
      "disabled"  — no STT (handled client-side)
      "browser"   — client-side Web Speech API (no server call)
      "local"     — faster-whisper on CPU
    """

    def __init__(self):
        self._available = True

    def _load_settings(self) -> dict:
        return {
            "stt_enabled": get_setting("stt_enabled", False),
            "stt_provider": get_setting("stt_provider", "disabled"),
            "stt_model": get_setting("stt_model", "base"),
            "stt_language": get_setting("stt_language", ""),
        }

    @property
    def available(self) -> bool:
        settings = self._load_settings()
        if not settings.get("stt_enabled"):
            return False
        provider = settings["stt_provider"]
        if provider in ("disabled", "browser"):
            return False
        if provider == "local":
            try:
                get_model()
                return True
            except Exception:
                return False
        return False

    def transcribe(self, audio_bytes: bytes, mime_type: str = "audio/webm") -> Optional[str]:
        try:
            return transcribe(audio_bytes, mime_type)
        except Exception as e:
            logger.error(f"Transcription failed: {e}", exc_info=True)
            return None

    def get_stats(self) -> dict:
        settings = self._load_settings()
        provider = settings["stt_provider"]
        enabled = settings.get("stt_enabled", False)
        return {
            "available": enabled and provider not in ("disabled", "browser"),
            "ready": self.available,
            "provider": provider,
            "model": settings["stt_model"],
        }


_stt_service = None


def get_stt_service() -> STTService:
    global _stt_service
    if _stt_service is None:
        _stt_service = STTService()
    return _stt_service
