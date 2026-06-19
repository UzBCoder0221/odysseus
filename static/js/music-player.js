/**
 * music-player.js — SEVEN.FM-style AI Music Player
 *
 * Self-contained module: renders into a container element.
 * Uses Web Audio API for spectrum visualization.
 *
 * Usage:
 *   import { MusicPlayer } from './js/music-player.js';
 *   const player = new MusicPlayer(document.getElementById('my-container'));
 *   player.load({ title: '...', artist: '...', src: '...', art: '...', lyrics: [...] });
 */

const MP_STATE = {
  IDLE: 'idle',
  PLAYING: 'playing',
  PAUSED: 'paused',
};

class MusicPlayer {
  constructor(container) {
    this.container = container;
    this.state = MP_STATE.IDLE;
    this.autoScroll = true;
    this.currentLine = -1;
    this.animFrame = null;
    this.spectrumBars = [];
    this.audioContext = null;
    this.analyser = null;
    this.sourceNode = null;

    this.track = {
      title: '',
      artist: '',
      src: '',
      art: '',
      lyrics: [],       // array of { time: seconds, text: string }
    };

    this._build();
    this._bindEvents();
  }

  /* ================================================================
     DOM Construction
     ================================================================ */

  _build() {
    this.container.classList.add('mp-root');
    this.container.innerHTML = '';

    // Top bar
    const topbar = this._el('div', 'mp-topbar');
    topbar.innerHTML = `
      <span class="mp-topbar-diamond">&#9670;</span>
      <span>SEVEN.FM</span>
      <span class="mp-topbar-diamond">&#9670;</span>
    `;
    this.container.appendChild(topbar);

    // Main area
    const main = this._el('div', 'mp-main');
    this.container.appendChild(main);

    // --- Left panel ---
    const left = this._el('div', 'mp-left');
    main.appendChild(left);

    // Artwork
    const artWrap = this._el('div', 'mp-artwork-wrap');
    this.artImg = this._el('img');
    this.artImg.style.display = 'none';
    this.artPlaceholder = this._el('div', 'mp-artwork-placeholder');
    this.artPlaceholder.textContent = '\u266B';
    artWrap.appendChild(this.artImg);
    artWrap.appendChild(this.artPlaceholder);
    left.appendChild(artWrap);

    // Info
    const info = this._el('div', 'mp-info');
    info.innerHTML = `
      <div class="mp-info-label">track</div>
      <div class="mp-info-track" data-ref="trackName">&mdash;</div>
      <div class="mp-info-label">artist</div>
      <div class="mp-info-artist" data-ref="artistName">&mdash;</div>
      <div class="mp-info-status mp-paused" data-ref="statusBadge">
        <span class="mp-dot"></span> paused
      </div>
    `;
    left.appendChild(info);
    this.trackName = info.querySelector('[data-ref="trackName"]');
    this.artistName = info.querySelector('[data-ref="artistName"]');
    this.statusBadge = info.querySelector('[data-ref="statusBadge"]');

    // Spectrum
    const specLabel = this._el('div', 'mp-spectrum-label');
    specLabel.textContent = 'spectrum';
    left.appendChild(specLabel);

    const spec = this._el('div', 'mp-spectrum');
    for (let i = 0; i < 32; i++) {
      const bar = this._el('div', 'mp-spectrum-bar');
      bar.style.height = '2px';
      spec.appendChild(bar);
      this.spectrumBars.push(bar);
    }
    left.appendChild(spec);

    // Controls
    const controls = this._el('div', 'mp-controls');
    this.btnPrev = this._ctrlBtn('\u23EE', 'prev');
    this.btnPlay = this._ctrlBtn('\u23F8', 'play');
    this.btnNext = this._ctrlBtn('\u23ED', 'next');
    controls.appendChild(this.btnPrev);
    controls.appendChild(this.btnPlay);
    controls.appendChild(this.btnNext);
    left.appendChild(controls);

    // Volume
    const volRow = this._el('div', 'mp-volume-row');
    const volLabel = this._el('span', 'mp-volume-label');
    volLabel.textContent = 'VOL';
    this.volumeSlider = this._el('input', 'mp-volume-slider');
    this.volumeSlider.type = 'range';
    this.volumeSlider.min = '0';
    this.volumeSlider.max = '100';
    this.volumeSlider.value = '80';
    volRow.appendChild(volLabel);
    volRow.appendChild(this.volumeSlider);
    left.appendChild(volRow);

    // Auto-scroll toggle
    const asRow = this._el('div', 'mp-autoscroll-row');
    this.btnAutoScroll = this._el('button', 'mp-toggle-btn active');
    this.btnAutoScroll.innerHTML = `<span class="mp-toggle-indicator"></span> auto-scroll <b>&nbsp; on</b>`;
    asRow.appendChild(this.btnAutoScroll);
    left.appendChild(asRow);

    // --- Right panel: lyrics ---
    const right = this._el('div', 'mp-right');
    main.appendChild(right);

    // Lyrics header
    const lHeader = this._el('div', 'mp-lyrics-header');
    lHeader.innerHTML = `
      <span class="mp-lyrics-header-left">&#9654; lyrics</span>
      <span class="mp-lyrics-header-right">
        <span><span class="mp-status-dot online"></span>online</span>
        <span># sync</span>
      </span>
    `;
    right.appendChild(lHeader);

    // Lyrics scroll area
    this.lyricsScroll = this._el('div', 'mp-lyrics-scroll');
    this.lyricsEmpty = this._el('div');
    this.lyricsEmpty.style.cssText = 'text-align:center;color:var(--mp-text-dim);padding:40px 0;font-size:13px;';
    this.lyricsEmpty.textContent = 'No lyrics loaded';
    this.lyricsScroll.appendChild(this.lyricsEmpty);
    right.appendChild(this.lyricsScroll);

    // --- Bottom bar: seek ---
    const bottom = this._el('div', 'mp-bottombar');
    this.timeElapsed = this._el('span', 'mp-time');
    this.timeElapsed.textContent = '00:00';
    this.timeRight = this._el('span', 'mp-time right');
    this.timeRight.textContent = '00:00';

    const seekWrap = this._el('div', 'mp-seekbar-wrap');
    this.seekBar = this._el('input', 'mp-seekbar');
    this.seekBar.type = 'range';
    this.seekBar.min = '0';
    this.seekBar.max = '1000';
    this.seekBar.value = '0';
    seekWrap.appendChild(this.seekBar);

    bottom.appendChild(this.timeElapsed);
    bottom.appendChild(seekWrap);
    bottom.appendChild(this.timeRight);
    this.container.appendChild(bottom);

    // Hidden audio element
    this.audio = new Audio();
    this.audio.preload = 'auto';
    this.audio.crossOrigin = 'anonymous';
  }

  _el(tag, cls) {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    return el;
  }

  _ctrlBtn(label, action) {
    const btn = this._el('button', 'mp-ctrl-btn');
    btn.textContent = label;
    btn.dataset.action = action;
    return btn;
  }

  /* ================================================================
     Event Binding
     ================================================================ */

  _bindEvents() {
    // Play / Pause
    this.btnPlay.addEventListener('click', () => this.togglePlay());
    this.btnPrev.addEventListener('click', () => this.prev());
    this.btnNext.addEventListener('click', () => this.next());

    // Volume
    this.volumeSlider.addEventListener('input', () => {
      this.audio.volume = this.volumeSlider.value / 100;
    });
    this.audio.volume = 0.8;

    // Seek
    this.seekBar.addEventListener('input', () => {
      if (!this.audio.duration) return;
      const pct = this.seekBar.value / 1000;
      this.audio.currentTime = pct * this.audio.duration;
    });

    // Time update
    this.audio.addEventListener('timeupdate', () => this._onTimeUpdate());
    this.audio.addEventListener('ended', () => this._onEnded());
    this.audio.addEventListener('play', () => this._setPlayState(MP_STATE.PLAYING));
    this.audio.addEventListener('pause', () => this._setPlayState(MP_STATE.PAUSED));

    // Auto-scroll toggle
    this.btnAutoScroll.addEventListener('click', () => {
      this.autoScroll = !this.autoScroll;
      this.btnAutoScroll.classList.toggle('active', this.autoScroll);
      const b = this.btnAutoScroll.querySelector('b');
      b.textContent = this.autoScroll ? ' on' : ' off';
    });

    // Lyrics line click to seek
    this.lyricsScroll.addEventListener('click', (e) => {
      const line = e.target.closest('.mp-lyrics-line');
      if (line && line.dataset.time) {
        this.audio.currentTime = parseFloat(line.dataset.time);
      }
    });

    // Keyboard shortcuts
    this.container.addEventListener('keydown', (e) => {
      if (e.key === ' ' || e.code === 'Space') {
        e.preventDefault();
        this.togglePlay();
      } else if (e.key === 'ArrowLeft') {
        this.audio.currentTime = Math.max(0, this.audio.currentTime - 5);
      } else if (e.key === 'ArrowRight') {
        this.audio.currentTime = Math.min(this.audio.duration || 0, this.audio.currentTime + 5);
      }
    });

    // Tabindex for keyboard
    this.container.setAttribute('tabindex', '0');
  }

  /* ================================================================
     Public API
     ================================================================ */

  /**
   * Load a track.
   * @param {Object} opts - { title, artist, src, art, lyrics: [{time, text}] }
   */
  load(opts) {
    Object.assign(this.track, opts);

    // Update UI
    this.trackName.textContent = this.track.title || '\u2014';
    this.artistName.textContent = this.track.artist || '\u2014';

    if (this.track.art) {
      this.artImg.src = this.track.art;
      this.artImg.style.display = 'block';
      this.artPlaceholder.style.display = 'none';
    } else {
      this.artImg.style.display = 'none';
      this.artPlaceholder.style.display = 'flex';
    }

    // Audio
    if (this.track.src) {
      this.audio.src = this.track.src;
      this.audio.load();
    }

    // Lyrics
    this._renderLyrics();

    // Reset state
    this._setPlayState(MP_STATE.IDLE);
    this.timeElapsed.textContent = '00:00';
    this.timeRight.textContent = this._fmtTime(this.audio.duration || 0);
    this.seekBar.value = '0';
    this.currentLine = -1;

    // Init audio context for spectrum
    this._initAudioContext();
  }

  /** Play or pause */
  togglePlay() {
    if (!this.audio.src) return;
    if (this.state === MP_STATE.PLAYING) {
      this.audio.pause();
    } else {
      this.audio.play().catch(() => {});
    }
  }

  /** Skip to previous lyric line */
  prev() {
    if (!this.track.lyrics.length) return;
    const cur = this.audio.currentTime;
    let target = 0;
    for (let i = this.track.lyrics.length - 1; i >= 0; i--) {
      if (this.track.lyrics[i].time < cur - 0.5) {
        target = this.track.lyrics[i].time;
        break;
      }
    }
    this.audio.currentTime = target;
  }

  /** Skip to next lyric line */
  next() {
    if (!this.track.lyrics.length) return;
    const cur = this.audio.currentTime;
    for (let i = 0; i < this.track.lyrics.length; i++) {
      if (this.track.lyrics[i].time > cur + 0.3) {
        this.audio.currentTime = this.track.lyrics[i].time;
        break;
      }
    }
  }

  /** Set volume (0-100) */
  setVolume(v) {
    this.volumeSlider.value = v;
    this.audio.volume = v / 100;
  }

  /* ================================================================
     Backend Integration — calls /api/music/*
     ================================================================ */

  /**
   * Search YouTube Music via backend.
   * @param {string} query
   * @param {number} limit
   * @param {string} filter - songs, videos, albums, artists, playlists
   * @returns {Promise<Array>} array of track objects
   */
  async search(query, limit = 10, filter = 'songs') {
    const params = new URLSearchParams({ q: query, limit, filter });
    const res = await fetch(`/api/music/search?${params}`);
    if (!res.ok) throw new Error(`Search failed: ${res.status}`);
    const data = await res.json();
    return data.results || [];
  }

  /**
   * Get search autocomplete suggestions.
   */
  async searchSuggestions(query) {
    const res = await fetch(`/api/music/search/suggestions?q=${encodeURIComponent(query)}`);
    if (!res.ok) return [];
    const data = await res.json();
    return data.suggestions || [];
  }

  /**
   * Load a track by YouTube video ID — fetches stream URL + metadata from backend.
   * @param {string} videoId - YouTube video ID
   * @param {Object} [meta] - optional pre-fetched metadata { title, artist_name, thumbnail }
   */
  async loadById(videoId, meta = {}) {
    // Fetch stream URL
    const streamRes = await fetch(`/api/music/song/${videoId}/stream`);
    if (!streamRes.ok) throw new Error(`Stream not available: ${streamRes.status}`);
    const { stream_url } = await streamRes.json();

    // Fetch metadata if not provided
    let title = meta.title || '';
    let artist = meta.artist_name || '';
    let art = meta.thumbnail || '';
    if (!title) {
      try {
        const songRes = await fetch(`/api/music/song/${videoId}`);
        if (songRes.ok) {
          const song = await songRes.json();
          title = song.title || '';
          artist = song.artist_name || '';
          art = song.thumbnail || '';
        }
      } catch (e) { /* use what we have */ }
    }

    // Try to get lyrics
    let lyrics = [];
    try {
      const lyricsRes = await fetch(`/api/music/song/${videoId}/lyrics`);
      if (lyricsRes.ok) {
        const { lyrics: text } = await lyricsRes.json();
        if (text) lyrics = text.split('\n').map((line, i) => ({ time: i * 3, text: line }));
      }
    } catch (e) { /* no lyrics */ }

    this.load({ title, artist, src: stream_url, art, lyrics, videoId });
  }

  /**
   * Play a search result by index.
   * @param {Array} results - from search()
   * @param {number} index
   */
  async playFromResults(results, index = 0) {
    const track = results[index];
    if (!track) return;
    await this.loadById(track.id, track);
    this.togglePlay();
  }

  /**
   * Get next/up-next tracks for current song.
   */
  async getNextTracks(limit = 10) {
    const vid = this.track?.videoId;
    if (!vid) return [];
    const res = await fetch(`/api/music/song/${vid}/next?limit=${limit}`);
    if (!res.ok) return [];
    const data = await res.json();
    return data.tracks || [];
  }

  /** Destroy player, release resources */
  destroy() {
    if (this.animFrame) cancelAnimationFrame(this.animFrame);
    this.audio.pause();
    this.audio.src = '';
    this.container.innerHTML = '';
  }

  /* ================================================================
     Internal
     ================================================================ */

  _setPlayState(s) {
    this.state = s;
    if (s === MP_STATE.PLAYING) {
      this.btnPlay.textContent = '\u23F8';
      this.statusBadge.classList.remove('mp-paused');
      this.statusBadge.innerHTML = '<span class="mp-dot"></span> playing';
    } else {
      this.btnPlay.textContent = '\u25B6';
      this.statusBadge.classList.add('mp-paused');
      this.statusBadge.innerHTML = '<span class="mp-dot"></span> paused';
    }
  }

  _onTimeUpdate() {
    const cur = this.audio.currentTime;
    const dur = this.audio.duration || 0;

    this.timeElapsed.textContent = this._fmtTime(cur);
    this.timeRight.textContent = this._fmtTime(dur);

    if (dur > 0) {
      this.seekBar.value = String(Math.round((cur / dur) * 1000));
    }

    this._syncLyrics(cur);
  }

  _onEnded() {
    this._setPlayState(MP_STATE.IDLE);
    this._resetSpectrum();
  }

  _fmtTime(sec) {
    if (!sec || !isFinite(sec)) return '00:00';
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
  }

  /* -- Lyrics -- */

  _renderLyrics() {
    this.lyricsScroll.innerHTML = '';

    if (!this.track.lyrics || !this.track.lyrics.length) {
      this.lyricsEmpty = this._el('div');
      this.lyricsEmpty.style.cssText = 'text-align:center;color:var(--mp-text-dim);padding:40px 0;font-size:13px;';
      this.lyricsEmpty.textContent = 'No lyrics loaded';
      this.lyricsScroll.appendChild(this.lyricsEmpty);
      return;
    }

    this.lyricEls = [];
    this.track.lyrics.forEach((line, i) => {
      const el = this._el('div', 'mp-lyrics-line future-dim');
      el.textContent = line.text;
      el.dataset.time = line.time;
      el.dataset.index = i;
      this.lyricsScroll.appendChild(el);
      this.lyricEls.push(el);
    });
  }

  _syncLyrics(time) {
    if (!this.lyricEls || !this.lyricEls.length) return;

    let newLine = 0;
    for (let i = 0; i < this.track.lyrics.length; i++) {
      if (time >= this.track.lyrics[i].time) {
        newLine = i;
      } else {
        break;
      }
    }

    if (newLine === this.currentLine) return;
    this.currentLine = newLine;

    this.lyricEls.forEach((el, i) => {
      el.classList.remove('past', 'current', 'future', 'future-dim');
      if (i < newLine) {
        el.classList.add('past');
      } else if (i === newLine) {
        el.classList.add('current');
      } else if (i <= newLine + 4) {
        el.classList.add('future');
      } else {
        el.classList.add('future-dim');
      }
    });

    // Auto-scroll
    if (this.autoScroll && this.lyricEls[newLine]) {
      const container = this.lyricsScroll;
      const el = this.lyricEls[newLine];
      const offset = el.offsetTop - container.offsetTop - container.clientHeight / 3;
      container.scrollTo({ top: Math.max(0, offset), behavior: 'smooth' });
    }
  }

  /* -- Spectrum (Web Audio API) -- */

  _initAudioContext() {
    if (this.analyser) return;
    if (this.audioContext && this.audioContext.state === 'closed') return;
    try {
      this.audioContext = this.audioContext || new (window.AudioContext || window.webkitAudioContext)();
      this.analyser = this.audioContext.createAnalyser();
      this.analyser.fftSize = 128;
      this.sourceNode = this.audioContext.createMediaElementSource(this.audio);
      this.sourceNode.connect(this.analyser);
      this.analyser.connect(this.audioContext.destination);
      if (this.audioContext.state === 'suspended') this.audioContext.resume();
      this._startSpectrumLoop();
    } catch (e) {
      console.warn('MusicPlayer: spectrum init failed', e.message);
    }
  }

  _startSpectrumLoop() {
    if (!this.analyser) return;
    const data = new Uint8Array(this.analyser.frequencyBinCount);

    const loop = () => {
      this.analyser.getByteFrequencyData(data);
      const barCount = this.spectrumBars.length;
      const step = Math.floor(data.length / barCount);

      for (let i = 0; i < barCount; i++) {
        let sum = 0;
        for (let j = 0; j < step; j++) {
          sum += data[i * step + j];
        }
        const avg = sum / step;
        const h = Math.max(2, (avg / 255) * 44);
        this.spectrumBars[i].style.height = h + 'px';
        this.spectrumBars[i].classList.toggle('active', avg > 30);
      }

      this.animFrame = requestAnimationFrame(loop);
    };

    loop();
  }

  _resetSpectrum() {
    this.spectrumBars.forEach(bar => {
      bar.style.height = '2px';
      bar.classList.remove('active');
    });
  }
}

/* -- Export -- */
export { MusicPlayer };
export default MusicPlayer;
