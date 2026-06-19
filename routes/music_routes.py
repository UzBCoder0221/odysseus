# routes/music_routes.py
"""
Music API routes — search, stream, metadata via ytmusicapi + yt-dlp.
"""

import logging
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

_music_service = None


def _get_service():
    global _music_service
    if _music_service is None:
        from services.music import get_music_service
        _music_service = get_music_service()
    return _music_service


def setup_music_routes(music_service=None):
    """Setup music routes. Service is lazy-loaded on first request if not provided."""
    router = APIRouter(prefix="/api/music", tags=["music"])

    def svc():
        return music_service or _get_service()

    @router.get("/stats")
    async def get_music_stats():
        return svc().get_stats()

    @router.get("/search")
    async def search_music(
        q: str = Query(..., description="Search query"),
        limit: int = Query(10, ge=1, le=50),
        filter: str = Query("songs"),
    ):
        try:
            results = svc().search(q, limit=limit, filter_type=filter)
            return {"results": results, "count": len(results)}
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e))
        except Exception as e:
            logger.error(f"Search failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/search/suggestions")
    async def search_suggestions(q: str = Query(...)):
        try:
            suggestions = svc().search_suggestions(q)
            return {"suggestions": suggestions}
        except Exception:
            return {"suggestions": []}

    @router.get("/song/{video_id}")
    async def get_song(video_id: str):
        try:
            track = svc().get_song(video_id)
            if not track:
                raise HTTPException(status_code=404, detail="Song not found")
            return track
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Get song failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/song/{video_id}/lyrics")
    async def get_lyrics(video_id: str):
        try:
            lyrics = svc().get_lyrics(video_id)
            return {"lyrics": lyrics or ""}
        except Exception as e:
            logger.error(f"Get lyrics failed: {e}")
            return {"lyrics": ""}

    @router.get("/song/{video_id}/stream")
    async def get_stream_url(video_id: str):
        try:
            url = await svc().get_stream_url_async(video_id)
            if not url:
                raise HTTPException(status_code=404, detail="Stream not available")
            return {"stream_url": url, "video_id": video_id}
        except HTTPException:
            raise
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e))
        except Exception as e:
            logger.error(f"Stream URL failed for {video_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/song/{video_id}/next")
    async def get_next_tracks(video_id: str, limit: int = Query(10, ge=1, le=30)):
        try:
            tracks = svc().get_watch_playlist(video_id, limit=limit)
            return {"tracks": tracks, "count": len(tracks)}
        except Exception as e:
            logger.error(f"Get next tracks failed: {e}")
            return {"tracks": [], "count": 0}

    @router.get("/song/{video_id}/proxy")
    async def proxy_audio(video_id: str, request: Request):
        """Proxy audio from googlevideo through our server so the browser loads it same-origin (no CORS)."""
        import aiohttp
        try:
            stream_url = await svc().get_stream_url_async(video_id)
            if not stream_url:
                raise HTTPException(status_code=404, detail="Stream not available")

            range_header = request.headers.get("range")
            req_headers = {"Range": range_header} if range_header else {}

            # HEAD request for metadata
            async with aiohttp.ClientSession() as hs:
                async with hs.head(stream_url, headers=req_headers) as head_resp:
                    content_type = head_resp.headers.get("Content-Type", "audio/webm")
                    content_length = head_resp.headers.get("Content-Length")
                    content_range = head_resp.headers.get("Content-Range")
                    upstream_status = head_resp.status

            resp_headers = {
                "Access-Control-Allow-Origin": "*",
                "Accept-Ranges": "bytes",
                "Content-Type": content_type,
            }
            if content_length:
                resp_headers["Content-Length"] = content_length
            if content_range:
                resp_headers["Content-Range"] = content_range

            status_code = 206 if range_header else upstream_status

            async def _stream():
                async with aiohttp.ClientSession() as ss:
                    async with ss.get(stream_url, headers=req_headers) as up:
                        async for chunk in up.content.iter_chunked(65536):
                            yield chunk

            return StreamingResponse(_stream(), status_code=status_code, headers=resp_headers)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Proxy failed for {video_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    return router
