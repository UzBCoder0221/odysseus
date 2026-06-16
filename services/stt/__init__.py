# services/stt/__init__.py
"""STT service — speech-to-text."""

from .stt_service import STTService, get_stt_service, get_model

__all__ = ["STTService", "get_stt_service", "get_model"]
