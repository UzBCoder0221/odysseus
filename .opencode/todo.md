# Music Player — Real Spectrum Fix

## Problem
Spectrum visualizer was showing a fake sine-wave animation instead of real frequency data. This was because googlevideo.com doesn't send CORS headers, so `createMediaElementSource` would route silence through the Web Audio graph.

## Solution
### Backend: Same-origin audio proxy
Added `/api/music/song/{video_id}/proxy` endpoint in `routes/music_routes.py` that:
- Fetches the stream URL from yt-dlp
- Makes a HEAD request to googlevideo for metadata  
- Streams the audio through our server with `Access-Control-Allow-Origin: *`
- Forwards `Range` headers for seek support
- Uses `aiohttp` for async streaming (avoids blocking issues with `requests`/`httpx` in executor)

### Frontend: Web Audio API restored
- `crossOrigin = 'anonymous'` on the audio element (same-origin proxy, so no CORS issue)
- `_initAudioContext()` creates AudioContext → analyser → `createMediaElementSource` → real frequency data
- `_startSpectrumLoop()` uses `getByteFrequencyData()` for real spectrum

### Demo updated
- Changed `music-player-demo.js` to use proxy URL instead of direct googlevideo URL (no more extra fetch)

## Key Files
- `routes/music_routes.py` — `/proxy` route (lines 104-148)
- `static/js/music-player.js` — Web Audio API spectrum restored (lines 540-590)
- `static/js/music-player-demo.js` — Uses proxy URL (line 36)

---

# Part 2 — Lyrics from YouTube Captions (CC)

## Problem
The music player had a lyrics panel but no lyrics to display — `lyrics: []` was hardcoded in the demo, so the panel always showed "No lyrics loaded".

## Solution
### Backend: `get_lyrics()` via `youtube-transcript-api`
- Added `get_lyrics(video_id)` to `MusicService` that calls `YouTubeTranscriptApi().fetch(video_id, languages=['en', 'en-US', 'en-GB'])`  
- Returns a list of `{time: float, text: string}` — same format the player already expects for synced lyrics
- Gracefully returns `[]` when no captions are available

### Frontend: Fetch lyrics on track load
- Updated `music-player-demo.js` to fetch `/api/music/song/{video_id}/lyrics` **before** calling `player.load()`  
- Lyrics are passed through the `lyrics` field so they render immediately when the track starts

## Key Files
- `services/music/music_service.py:83` — `get_lyrics()` method (lines 83–114)
- `static/js/music-player-demo.js` — fetches captions from the API before loading a track
- `tests/test_music_lyrics.py` — unit tests covering success and failure paths

## Verified
- `GET /api/music/song/dQw4w9WgXcQ/lyrics` returns 61 timed lines with Rick Astley lyrics  
- Mocked tests pass (2 tests, success + exception paths)
- All existing YouTube tests still pass (6/6)
- Empty response (`[]`) correctly becomes `""` via `lyrics or ""` in the route, JS falls back to `[]`

---

# Part 3 — Multi-Source Music Research (Mopidy + others)

## Goal
Extend the music player beyond YouTube-only to support Spotify, SoundCloud, Deezer, TIDAL, Bandcamp.

## Architecture Reference: Mopidy
**Repo**: [github.com/mopidy/mopidy](https://github.com/mopidy/mopidy)
Extensible Python music server with plugin backends. Each source is a pip package implementing:
- `search(query) → tracks[]`
- `lookup(uri) → track`
- `get_stream_url(uri) → url`
- URI routing: `spotify:track:xxx` → SpotifyBackend

### Available Backend Extensions
| Source | Extension | Status |
|--------|-----------|--------|
| Spotify | `mopidy-spotify` | ✅ Active (librespot/GStreamer, needs Premium) |
| YouTube | `mopidy-youtube` / `mopidy-ytmusic` | ✅ Active |
| SoundCloud | `mopidy-soundcloud` | ⚠️ Unmaintained |
| TIDAL | `mopidy-tidal` | ✅ Active |
| Bandcamp | `mopidy-bandcamp` | ✅ Active |

## Per-Platform Libraries (Direct Integration)
| Platform | Library | Stars | Notes |
|----------|---------|-------|-------|
| **Spotify** | [librespot-python](https://github.com/kokarare1212/librespot-python) | 325 ★ | Pure Python, gets audio streams. Needs Premium. |
| **Spotify** | [spotipy](https://github.com/spotipy-dev/spotipy) | 5k+ ★ | Web API: search + metadata only |
| **Deezer** | [deezer-python](https://github.com/browniebroke/deezer-python) | 150 ★ | API client |
| **TIDAL** | `tidalapi` (PyPI) | — | Unofficial API |
| **SoundCloud** | [musicdl's soundcloud module](https://github.com/CharlesPikachu/musicdl) | 5.1k ★ | Part of multi-platform downloader |
| **Bandcamp** | [bandcamp_async_api](https://github.com/ALERTua/bandcamp_async_api) | 5 ★ | Async wrapper |

## Key Takeaway
No single library covers all platforms. Best approach: compose per-platform modules under a common `Backend` abstract class (Mopidy pattern), using `librespot-python` for Spotify streaming, `tidalapi` for TIDAL, `deezer-python` for Deezer, and `musicdl`'s scrapers for SoundCloud/Bandcamp.

---

# Part 3 — Multi-Source Music Research (Mopidy & others)

## Goal
Extend music service beyond YouTube to support Spotify, SoundCloud, Deezer, TIDAL, Bandcamp, etc.

## Best Architecture to Follow: Mopidy
**Repo**: [github.com/mopidy/mopidy](https://github.com/mopidy/mopidy)
- Extensible Python music server with **plugin backend interface**
- Each source implements: `search()`, `lookup()`, `get_stream_url()`
- URI-based routing: `spotify:track:xxx` → SpotifyBackend

### Available Backends (reference)
| Source | Extension | Status |
|--------|-----------|--------|
| **Spotify** | `mopidy-spotify` | ✓ Active (librespot/GStreamer, needs Premium) |
| **YouTube** | `mopidy-youtube` / `mopidy-ytmusic` | ✓ Active |
| **SoundCloud** | `mopidy-soundcloud` | ⚠️ Needs maintainer |
| **TIDAL** | `mopidy-tidal` | ✓ Active |
| **Bandcamp** | `mopidy-bandcamp` | ✓ Active |

### Per-Platform Libraries
| Platform | Library | Stars | Notes |
|----------|---------|-------|-------|
| **Spotify** | [librespot-python](https://github.com/kokarare1212/librespot-python) | 325★ | Pure Python client, gets audio streams. Needs Premium |
| **Spotify** | [spotipy](https://github.com/spotipy-dev/spotipy) | 5k★ | Web API (search/metadata only) |
| **Deezer** | [deezer-python](https://github.com/browniebroke/deezer-python) | 150★ | API client |
| **TIDAL** | `tidalapi` (PyPI) | — | Unofficial API |
| **Multi-API** | [musicdl](https://github.com/CharlesPikachu/musicdl) | 5.1k★ | 20+ platforms, scraper-based |
| **Multi-API** | [minim](https://github.com/bbye98/minim) | 104★ | Spotify/TIDAL/Qobuz/Deezer/iTunes/Discogs metadata |

## Next Steps
- Review Mopidy backend interface source code for pattern
- Implement abstract `Backend` class in music service
- Add Spotify via librespot-python (needs Premium)
- Add other sources via existing libraries
