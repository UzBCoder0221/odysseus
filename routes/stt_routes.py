# routes/stt_routes.py
"""STT API routes — multi-provider (local Whisper, API endpoint, browser)."""

import logging

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from src.upload_limits import read_upload_limited, STT_MAX_AUDIO_BYTES
from src.settings import get_setting

logger = logging.getLogger(__name__)


def setup_stt_routes(stt_service):
    router = APIRouter(prefix="/api/stt", tags=["stt"])

    @router.get("/stats")
    async def get_stt_stats():
        try:
            return stt_service.get_stats()
        except Exception as e:
            logger.error(f"Failed to get STT stats: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/transcribe")
    async def transcribe_audio(
        file: UploadFile = File(...),
        provider: str = Form("local"),
    ):
        try:
            if provider == "browser":
                return {"text": ""}

            audio_bytes = await read_upload_limited(file, STT_MAX_AUDIO_BYTES, "Audio file")
            if not audio_bytes:
                return {"text": "", "error": "Empty audio file"}

            if provider == "openai":
                api_key = get_setting("openai_api_key", "")
                if not api_key:
                    return {"text": "", "error": "OpenAI API key not configured"}
                import httpx
                async with httpx.AsyncClient(timeout=120) as client:
                    files = {"file": (file.filename or "audio.webm", audio_bytes, file.content_type or "audio/webm")}
                    data = {"model": "whisper-1"}
                    r = await client.post(
                        "https://api.openai.com/v1/audio/transcriptions",
                        headers={"Authorization": f"Bearer {api_key}"},
                        files=files,
                        data=data,
                    )
                    r.raise_for_status()
                    result = r.json()
                    return {"text": result.get("text", "")}

            text = stt_service.transcribe(audio_bytes, file.content_type or "audio/webm")
            if text is None:
                return {"text": "", "error": "Transcription failed"}
            return {"text": text}

        except Exception as e:
            logger.error(f"Transcription error: {e}", exc_info=True)
            return {"text": "", "error": str(e)}

    return router
