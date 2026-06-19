import { MusicPlayer } from './music-player.js';

const player = new MusicPlayer(document.getElementById('player-root'));
const searchInput = document.getElementById('search-input');
const searchBtn = document.getElementById('search-btn');
const resultsEl = document.getElementById('results');

window.player = player;

async function doSearch() {
  const q = searchInput.value.trim();
  if (!q) return;
  resultsEl.innerHTML = '<div style="padding:12px;color:#604090;">Searching...</div>';
  try {
    const res = await fetch(`/api/music/search?q=${encodeURIComponent(q)}&limit=10`);
    if (!res.ok) throw new Error('Search failed');
    const data = await res.json();
    const results = data.results || [];
    resultsEl.innerHTML = '';
    if (!results.length) {
      resultsEl.innerHTML = '<div style="padding:12px;color:#604090;">No results</div>';
      return;
    }
    results.forEach((track, i) => {
      const div = document.createElement('div');
      div.className = 'result-item';
      div.innerHTML = `<img src="${track.thumbnail}" alt="" onerror="this.style.display='none'" />
        <span class="title">${track.title}</span>
        <span class="artist">${track.artist_name}</span>
        <span class="dur">${track.duration_str || ''}</span>`;
      div.onclick = async () => {
        resultsEl.innerHTML = '<div style="padding:12px;color:#604090;">Loading stream...</div>';
        try {
          // Fetch captions as timed lyrics
          const lyricsRes = await fetch(`/api/music/song/${track.id}/lyrics`).catch(() => null);
          let lyrics = [];
          if (lyricsRes && lyricsRes.ok) {
            const data = await lyricsRes.json();
            lyrics = data.lyrics || [];
          }

          // Use the proxy endpoint so audio loads same-origin (no CORS issues with Web Audio API)
          player.load({
            title: track.title,
            artist: track.artist_name,
            src: `/api/music/song/${track.id}/proxy`,
            art: track.thumbnail,
            lyrics: lyrics,
            videoId: track.id,
          });
          player.togglePlay();
          resultsEl.innerHTML = '';
        } catch (e) {
          resultsEl.innerHTML = `<div style="padding:12px;color:#ff4ad0;">${e.message}</div>`;
        }
      };
      resultsEl.appendChild(div);
    });
  } catch (e) {
    resultsEl.innerHTML = `<div style="padding:12px;color:#ff4ad0;">Error: ${e.message}</div>`;
  }
}

searchBtn.onclick = doSearch;
searchInput.onkeydown = (e) => { if (e.key === 'Enter') doSearch(); };
