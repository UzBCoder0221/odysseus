# services/music/music_service.py
"""Music service — wraps yt-dlp for audio streaming and ytmusicapi for search/metadata."""

import asyncio
import logging
import re
from typing import Optional, Dict, Any, List
from functools import partial

logger = logging.getLogger(__name__)

_service_instance: Optional["MusicService"] = None


def get_music_service() -> "MusicService":
    global _service_instance
    if _service_instance is None:
        _service_instance = MusicService()
    return _service_instance


class MusicService:
    """Unified music service: search via ytmusicapi, stream via yt-dlp."""

    def __init__(self):
        self._ytmusic = None
        self._yt_dlp_available = False
        self._check_dependencies()

    def _check_dependencies(self):
        try:
            from ytmusicapi import YTMusic
            self._ytmusic = YTMusic()
            logger.info("ytmusicapi loaded OK")
        except Exception as e:
            logger.warning(f"ytmusicapi not available: {e}")

        try:
            import yt_dlp
            self._yt_dlp_available = True
            logger.info("yt-dlp loaded OK")
        except ImportError:
            logger.warning("yt-dlp not available")

    @property
    def available(self) -> bool:
        return self._ytmusic is not None or self._yt_dlp_available

    # ── Search ──────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 10, filter_type: str = "songs") -> List[Dict[str, Any]]:
        """Search YouTube Music. Returns list of tracks.
        Uses ytmusicapi first, falls back to yt-dlp if no results.
        """
        # Try ytmusicapi first
        if self._ytmusic:
            try:
                results = self._ytmusic.search(query, filter=filter_type, limit=limit)
                tracks = []
                for r in results:
                    if r.get("videoId"):
                        tracks.append(self._parse_track(r))
                if tracks:
                    return tracks
            except Exception as e:
                logger.warning(f"ytmusicapi search failed, falling back to yt-dlp: {e}")

        # Fallback: yt-dlp search
        return self._search_ytdlp(query, limit)

    def get_song(self, video_id: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a single song."""
        if not self._ytmusic:
            raise RuntimeError("ytmusicapi not available")

        try:
            results = self._ytmusic.get_song(video_id)
            return self._parse_track(results)
        except Exception as e:
            logger.error(f"Get song failed: {e}")
            return None

    def get_lyrics(self, video_id: str) -> list:
        """Get lyrics for a song — ytmusicapi first, then YouTube captions (CC) fallback."""
        # Try ytmusicapi official lyrics first
        if self._ytmusic:
            try:
                result = self._ytmusic.get_lyrics(video_id)
                if result:
                    lyrics = result.get("lyrics", "")
                    if lyrics:
                        # ytmusicapi returns plain text with \n\n verse separators
                        lines = [l.strip() for l in lyrics.split("\n") if l.strip()]
                        # Fake timestamps at 5s intervals for display
                        return [
                            {"time": i * 5.0, "text": line}
                            for i, line in enumerate(lines)
                        ]
            except Exception as e:
                logger.debug(f"ytmusicapi lyrics not available for {video_id}: {e}")

        # Fallback: YouTube captions (CC)
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            transcript = YouTubeTranscriptApi().fetch(
                video_id, languages=['en', 'en-US', 'en-GB']
            )
            return [
                {"time": round(entry.start, 2), "text": entry.text}
                for entry in transcript
            ]
        except Exception as e:
            logger.debug(f"No captions for {video_id}: {e}")
            return []

    def get_watch_playlist(self, video_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get up-next / radio tracks."""
        if not self._ytmusic:
            return []
        try:
            result = self._ytmusic.get_watch_playlist(video_id, limit=limit)
            tracks = []
            for t in result.get("tracks", []):
                if t.get("videoId"):
                    tracks.append(self._parse_track(t))
            return tracks
        except Exception as e:
            logger.error(f"Get watch playlist failed: {e}")
            return []

    def search_suggestions(self, query: str) -> List[str]:
        """Get search autocomplete suggestions."""
        if not self._ytmusic:
            return []
        try:
            return self._ytmusic.get_search_suggestions(query)
        except Exception:
            return []

    # ── Stream URL ──────────────────────────────────────────────────

    def _search_ytdlp(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search via yt-dlp (ytsearch). Used as fallback."""
        if not self._yt_dlp_available:
            return []

        import yt_dlp

        search_url = f"ytsearch{limit}:{query}"
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "default_search": "ytsearch",
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(search_url, download=False)
                entries = info.get("entries", []) if info else []
                tracks = []
                for e in entries:
                    if e.get("id") or e.get("url"):
                        vid = e.get("id", "") or e.get("url", "")
                        # Extract flat results don't have full metadata
                        duration = e.get("duration") or 0
                        tracks.append({
                            "id": vid,
                            "title": e.get("title", ""),
                            "artists": [{"name": e.get("uploader", ""), "id": ""}],
                            "artist_name": e.get("uploader", ""),
                            "album": "",
                            "duration": int(duration) if duration else 0,
                            "duration_str": self._fmt_duration(int(duration) if duration else 0),
                            "thumbnail": e.get("thumbnails", [{}])[-1].get("url", "") if e.get("thumbnails") else "",
                            "url": f"https://www.youtube.com/watch?v={vid}" if vid else "",
                        })
                return tracks
        except Exception as e:
            logger.error(f"yt-dlp search failed: {e}")
            return []

    def _fmt_duration(self, seconds: int) -> str:
        if not seconds:
            return ""
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    def get_stream_url(self, video_id: str) -> Optional[str]:
        """Get direct audio stream URL via yt-dlp. No download, just the URL."""
        if not self._yt_dlp_available:
            raise RuntimeError("yt-dlp not available")

        import yt_dlp

        url = f"https://www.youtube.com/watch?v={video_id}"
        ydl_opts = {
            "format": "bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if info and "url" in info:
                    return info["url"]
                # Some formats require merging
                if info and "formats" in info:
                    for f in reversed(info["formats"]):
                        if f.get("url") and f.get("acodec") != "none":
                            return f["url"]
                return None
        except Exception as e:
            logger.error(f"Stream URL extraction failed for {video_id}: {e}")
            return None

    async def get_stream_url_async(self, video_id: str) -> Optional[str]:
        """Async wrapper for get_stream_url."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(self.get_stream_url, video_id))

    # ── Helpers ─────────────────────────────────────────────────────

    def _parse_track(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize a ytmusicapi track dict into a clean structure."""
        video_id = raw.get("videoId", "")

        # Duration string "3:45" → seconds
        duration = 0
        dur_str = raw.get("duration", "") or raw.get("length", "")
        if isinstance(dur_str, str) and ":" in dur_str:
            parts = dur_str.split(":")
            try:
                if len(parts) == 2:
                    duration = int(parts[0]) * 60 + int(parts[1])
                elif len(parts) == 3:
                    duration = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            except ValueError:
                pass
        elif isinstance(dur_str, (int, float)):
            duration = int(dur_str)

        # Thumbnail
        thumbs = raw.get("thumbnails", [])
        thumb_url = ""
        if thumbs:
            # Pick highest resolution
            best = max(thumbs, key=lambda t: t.get("width", 0) * t.get("height", 0))
            thumb_url = best.get("url", "")

        # Artists
        artists = []
        for a in raw.get("artists", []):
            name = a.get("name", "")
            aid = a.get("id", "")
            if name:
                artists.append({"name": name, "id": aid})
        # Fallback: single author field
        if not artists and raw.get("author"):
            artists.append({"name": raw["author"], "id": ""})

        return {
            "id": video_id,
            "title": raw.get("title", ""),
            "artists": artists,
            "artist_name": artists[0]["name"] if artists else "",
            "album": raw.get("album", {}).get("name", "") if isinstance(raw.get("album"), dict) else str(raw.get("album", "")),
            "duration": duration,
            "duration_str": dur_str if isinstance(dur_str, str) else "",
            "thumbnail": thumb_url,
            "url": f"https://www.youtube.com/watch?v={video_id}" if video_id else "",
        }

    # ── Stats ───────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        return {
            "ytmusicapi": self._ytmusic is not None,
            "yt_dlp": self._yt_dlp_available,
        }
