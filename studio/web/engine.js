/* engine.js —— 播放/混音引擎（WebAudio）。三种模式：
 *   master：听渲染好的成品（一个 OGG）
 *   stems ：把**分轨 OGG** 同时播放并按轨道实时调音量/声像/静音/独奏 —— 面板里的"多轨合并"
 *   ref   ：听参考曲（和上面同一位置、同一循环区间，用于 A/B）
 * 另提供：循环区间、波形峰值（画波形/拖动定位）、逐轨电平表。
 */
const ENG = (() => {
  let ac = null, masterGain = null, masterAn = null;
  const buf = { master: null, ref: null, stems: {} };
  const urlCache = new Map();
  let chain = [];                     // [{src, gain, pan, an, track}]
  const st = { mode: 'master', playing: false, t0: 0, offset: 0, dur: 0,
               loop: [null, null], loading: {} };
  const mix = {};                     // track -> {gain, pan, mute, solo}
  let peakCache = null;

  const ctx = () => {
    if (!ac) {
      ac = new (window.AudioContext || window.webkitAudioContext)();
      masterGain = ac.createGain();
      masterAn = ac.createAnalyser(); masterAn.fftSize = 1024;
      masterGain.connect(masterAn); masterAn.connect(ac.destination);
    }
    return ac;
  };
  async function decode(url) {
    if (urlCache.has(url)) return urlCache.get(url);
    /* ⚠ 解码失败**不许把异常往上冒**：主界面 `loadSong()` 会一路 await 到它，
     *   一抛异常整条初始化就断在中间（用户侧日志出现 "Unable to decode audio data"，
     *   同时音轨卡与卷帘都是空的）—— 单个音频坏了不该拖垮整个页面。
     *   这里吞掉并 warn，返回 null，调用方按"没有这份音频"处理。 */
    try {
      const r = await fetch(url);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const ab = await r.arrayBuffer();
      const b = await ctx().decodeAudioData(ab);
      urlCache.set(url, b);
      return b;
    } catch (e) {
      try { console.warn('[engine] 音频解码失败: ' + url, e); } catch (_) {}
      return null;
    }
  }
  async function loadMaster(url) {
    st.loading.master = true;
    try {
      const b = await decode(url);
      if (b) { buf.master = b; st.dur = b.duration; peakCache = null; }
      /* 返回"到底载进来了没有"：调用方要能据此报错/回退（试听某一轨时音频可能没渲染过）。
       * ⚠ 这个返回值是**必须**的：原来它返回 undefined，调用方写 `if(!ok)` 就会永远早退
       *   （实测：solo 试听"点了没反应"的另一半原因）。老调用方忽略返回值，不受影响。 */
      return !!b;
    } finally { st.loading.master = false; }
  }
  async function loadRef(url) {
    st.loading.ref = true;
    try { const b = await decode(url); if (b) buf.ref = b; }
    finally { st.loading.ref = false; }
  }
  async function loadStems(map) {
    st.loading.stems = true;
    for (const k of Object.keys(map)) {
      if (!buf.stems[k] || buf.stems[k].__url !== map[k]) {
        const b = await decode(map[k]);
        if (b) { b.__url = map[k]; buf.stems[k] = b; }
      }
      if (!mix[k]) mix[k] = { gain: 1, pan: 0, mute: false, solo: false };
    }
    if (!st.dur && Object.keys(buf.stems).length) {
      st.dur = Math.max(...Object.values(buf.stems).map(b => b.duration));
    }
    st.loading.stems = false;
  }
  const anySolo = () => Object.values(mix).some(m => m.solo);
  function stopChain() {
    for (const c of chain) { try { c.src.stop(); } catch (e) {} }
    chain = [];
  }
  function startChain(at) {
    const c = ctx(); stopChain();
    const mk = (b, track) => {
      const g = c.createGain(), p = c.createStereoPanner(), an = c.createAnalyser();
      an.fftSize = 512;
      const m = track ? (mix[track] || { gain: 1, pan: 0, mute: false }) : { gain: 1, pan: 0 };
      const solo = anySolo();
      const vol = (track && (m.mute || (solo && !m.solo))) ? 0 : (m.gain ?? 1);
      g.gain.value = vol; p.pan.value = m.pan ?? 0;
      const src = c.createBufferSource(); src.buffer = b;
      src.connect(g); g.connect(p); p.connect(masterGain); p.connect(an);
      chain.push({ src, gain: g, pan: p, an, track });
      return src;
    };
    if (st.mode === 'stems') {
      for (const k of Object.keys(buf.stems)) mk(buf.stems[k], k);
    } else {
      const b = st.mode === 'ref' ? buf.ref : buf.master;
      if (b) mk(b, null);
    }
    for (const c of chain) { try { c.src.start(0, Math.max(0, at)); } catch (e) {} }
    st.t0 = c.currentTime; st.offset = at; st.playing = true;
  }
  async function play(mode) {
    if (mode) st.mode = mode;
    if (ctx().state === 'suspended') await ctx().resume();
    if (!hasBuffer()) return false;
    startChain(st.offset >= st.dur - 0.05 ? 0 : st.offset);
    return true;
  }
  function hasBuffer() {
    if (st.mode === 'stems') return Object.keys(buf.stems).length > 0;
    return !!(st.mode === 'ref' ? buf.ref : buf.master);
  }
  function pause() {
    const t = position(); stopChain(); st.offset = t; st.playing = false;
  }
  function stop() { stopChain(); st.playing = false; st.offset = 0; }
  function seek(t) {
    const was = st.playing;
    if (was) stopChain();
    st.offset = Math.max(0, Math.min(st.dur || 0, t));
    if (was) startChain(st.offset);
  }
  function position() {
    if (!st.playing || !ac) return st.offset;
    let p = st.offset + (ac.currentTime - st.t0);
    const [a, b] = st.loop;
    if (a != null && b != null && b > a && p >= b) {
      // 循环：回到起点重新起（BufferSource 不能 seek）
      stopChain(); st.offset = a; startChain(a); return a;
    }
    if (p >= (st.dur || 0)) { stopChain(); st.playing = false; st.offset = 0; return 0; }
    return p;
  }
  function setLoop(a, b) { st.loop = [a, b]; }
  function setTrack(track, o) { mix[track] = Object.assign(mix[track] || { gain: 1, pan: 0, mute: false, solo: false }, o); applyMix(); }
  function applyMix() {
    const solo = anySolo();
    for (const c of chain) {
      if (!c.track) continue;
      const m = mix[c.track] || {};
      const v = (m.mute || (solo && !m.solo)) ? 0 : (m.gain ?? 1);
      if (c.gain) c.gain.gain.value = v;
      if (c.pan) c.pan.pan.value = m.pan ?? 0;
    }
  }
  function setMasterGain(v) { if (masterGain) masterGain.gain.value = v; }
  function levels() {
    const out = { master: 0, tracks: {} };
    const rms = (an) => {
      if (!an) return 0;
      const d = new Float32Array(an.fftSize); an.getFloatTimeDomainData(d);
      let s = 0; for (let i = 0; i < d.length; i++) s += d[i] * d[i];
      return Math.sqrt(s / d.length);
    };
    out.master = rms(masterAn);
    for (const c of chain) if (c.track) out.tracks[c.track] = rms(c.an);
    return out;
  }
  function peaks(n) {
    const b = st.mode === 'ref' ? buf.ref : buf.master;
    if (!b) return null;
    if (peakCache && peakCache.n === n && peakCache.b === b) return peakCache.p;
    const ch = b.getChannelData(0), step = Math.floor(ch.length / n) || 1, out = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      let mx = 0, s = i * step;
      for (let j = 0; j < step; j += 8) { const v = Math.abs(ch[s + j] || 0); if (v > mx) mx = v; }
      out[i] = mx;
    }
    peakCache = { n, b, p: out };
    return out;
  }
  const state = () => ({ mode: st.mode, playing: st.playing, dur: st.dur, loop: st.loop,
                         loading: { ...st.loading },
                         has: { master: !!buf.master, ref: !!buf.ref,
                                stems: Object.keys(buf.stems).length } });
  return { play, pause, stop, seek, position, setLoop, setTrack, setMasterGain,
           levels, peaks, loadMaster, loadRef, loadStems, state, mix, get ctx() { return ctx(); } };
})();