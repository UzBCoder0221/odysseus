"""Tests for music service get_lyrics (ytmusicapi official + YouTube CC fallback)."""
from unittest.mock import patch, MagicMock
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.music.music_service import MusicService


class FakeTranscriptEntry:
    __slots__ = ("text", "start", "duration")
    def __init__(self, text, start, duration):
        self.text = text
        self.start = start
        self.duration = duration


FAKE_TRANSCRIPT = [
    FakeTranscriptEntry("♪ Hello ♪", 1.5, 3.0),
    FakeTranscriptEntry("♪ World ♪", 5.0, 2.5),
    FakeTranscriptEntry("♪ Test ♪", 8.0, 4.0),
]


def _make_svc(with_ytmusic=None):
    """Create MusicService with _ytmusic patched to control test flow."""
    svc = MusicService()
    svc._ytmusic = with_ytmusic
    return svc


def test_captions_fallback_when_ytmusic_returns_empty():
    """ytmusicapi returns no lyrics → fall back to YouTube captions (CC)."""
    mock_ytmusic = MagicMock()
    mock_ytmusic.get_lyrics.return_value = None
    svc = _make_svc(mock_ytmusic)

    with patch("youtube_transcript_api.YouTubeTranscriptApi") as mock_api:
        mock_api.return_value.fetch.return_value = FAKE_TRANSCRIPT
        result = svc.get_lyrics("test_video_id")

    assert len(result) == 3
    assert result[0] == {"time": 1.5, "text": "♪ Hello ♪"}
    assert result[1] == {"time": 5.0, "text": "♪ World ♪"}


def test_captions_fallback_when_ytmusic_unavailable():
    """_ytmusic is None → skip ytmusicapi, use captions."""
    svc = _make_svc(None)

    with patch("youtube_transcript_api.YouTubeTranscriptApi") as mock_api:
        mock_api.return_value.fetch.return_value = FAKE_TRANSCRIPT
        result = svc.get_lyrics("test_video_id")

    assert len(result) == 3


def test_ytmusicapi_lyrics_used_when_available():
    """ytmusicapi returns lyrics → those are used (with fake timestamps)."""
    mock_ytmusic = MagicMock()
    mock_ytmusic.get_lyrics.return_value = {
        "lyrics": "Verse one\n\nVerse two\n\nChorus"
    }
    svc = _make_svc(mock_ytmusic)

    result = svc.get_lyrics("test_video_id")

    assert len(result) == 3
    assert result[0] == {"time": 0.0, "text": "Verse one"}
    assert result[1] == {"time": 5.0, "text": "Verse two"}
    assert result[2] == {"time": 10.0, "text": "Chorus"}


def test_both_fail_returns_empty():
    """Both sources fail → return empty list."""
    mock_ytmusic = MagicMock()
    mock_ytmusic.get_lyrics.side_effect = Exception("ytmusicapi error")
    svc = _make_svc(mock_ytmusic)

    with patch("youtube_transcript_api.YouTubeTranscriptApi") as mock_api:
        mock_api.return_value.fetch.side_effect = Exception("No captions")
        result = svc.get_lyrics("no_captions_vid")

    assert isinstance(result, list)
    assert result == []
