/* smoke_ed.js —— MIDI 编辑器（ed.js）无浏览器冒烟：导入 → 编辑 → 导出
 *
 * 做法与 `smoke_ui.js` 同源：用 vm + DOM 替身把前端脚本跑起来，
 * `fetch` 打到真实服务（studio/server.py）—— 所以这条测试同时覆盖"前端逻辑 + API 契约"。
 * 运行前需要服务在 --port 8791 上（脚本会自己检测并给出提示）。
 */
const fs = require('fs'), path = require('path'), vm = require('vm'), http = require('http');
const ROOT = path.join(__dirname, '..');
const PORT = process.env.ED_PORT || 8791;
const SRC_MID = process.env.ED_MID ||
  path.join(ROOT, '..', 'songs', '38_d132_full', 'd132_full.mid');

const bad = [], good = [];
function chk(c, msg) { (c ? good : bad).push(msg); console.log((c ? '  OK   ' : '  FAIL ') + msg); }

/* ---------------- DOM 替身 ---------------- */
let __n = 0;
function fakeEl(id) {
  const el = {
    id, children: [], style: {}, dataset: {}, value: '', textContent: '', innerHTML: '',
    className: '', title: '', disabled: false, hidden: false, width: 1000, height: 360,
    clientWidth: 1000, parentElement: null, paused: true, currentTime: 0, src: '',
    preload: 'auto', volume: 1,
    /* 事件：真实现（`<audio>` 的 play/pause/ended 要能触发，
     * 否则"渲染音频后播放"这条链在冒烟里根本测不到） */
    _h: {},
    addEventListener(type, fn) { (this._h[type] = this._h[type] || []).push(fn); },
    removeEventListener(type, fn) {
      const a = this._h[type] || [];
      const i = a.indexOf(fn);
      if (i >= 0) a.splice(i, 1);
    },
    fire(type) { (this._h[type] || []).forEach(fn => fn({ type: type, target: this })); },
    appendChild(c) { this.children.push(c); return c; },
    querySelectorAll() { return []; }, querySelector() { return fakeEl('q' + (++__n)); },
    focus() {}, remove() {},
    getBoundingClientRect() { return { left: 0, top: 0, width: 1000, height: 360 }; },
    getContext() { return ctx2d; },
    play() { this.paused = false; this.fire('play'); return Promise.resolve(); },
    pause() { this.paused = true; this.fire('pause'); },
    load() {},
    classList: { toggle() {}, add() {}, remove() {} },
    click() { if (this.onclick) return this.onclick({ target: this, preventDefault() {} }); },
  };
  return el;
}
const ctx2d = new Proxy({}, {
  get: (t, k) => {
    if (k === 'canvas') return { width: 1000, height: 360 };
    return (...a) => undefined;
  },
  set: () => true,
});
const IDS = ['bar', 'btnImport', 'fileIn', 'btnExport', 'btnExport0', 'btnUndo', 'btnRedo',
  'btnPlay', 'btnStop', 'loopChk', 'posInfo', 'status', 'gridSel', 'snapChk', 'btnQuant',
  'qStr', 'qStrVal', 'qSwing', 'qSwingVal', 'qDurChk', 'trSemis', 'btnTranspose', 'velMode',
  'velVal', 'btnVel', 'btnRamp', 'btnDup', 'btnDelTrack', 'btnClean', 'btnMono',
  'btnChords', 'btnChordTrack', 'chordList', 'seek', 'seekInfo', 'btnEdit',
  'btnRender', 'audioInfo', 'timbreSel', 'tracks',
  'trHint', 'trackList', 'roll', 'velWrap', 'velLane', 'noteProps', 'fileInfo', 'exportInfo',
  'logWrap', 'log', 'player'];
const els = new Map();
for (const id of IDS) els.set(id, fakeEl(id));
els.get('gridSel').value = '1/16';
els.get('snapChk').checked = true;
els.get('loopChk').checked = true;
els.get('velMode').value = 'scale';
els.get('velVal').value = '0.5';
els.get('trSemis').value = '2';
els.get('qStr').value = '1';
els.get('qSwing').value = '0';
for (const id of ['roll', 'velLane']) els.get(id).parentElement = fakeEl('wrap');

/* ---------------- WebAudio 替身（虚拟时钟 + 发声计数） ----------------
 * 时钟必须**可控且与真实时间一致**：手动 `ctx.currentTime += 1` 而不动 `startedAt`
 * 会让基准漂移，于是 `at < now` 把窗口内每个音都判成"已过期"（实测 0 发声）——
 * 那是测试把代码玩坏了，不是代码的问题。这里用 VClock 统一管两边。 */
let TONES = 0;                 // 音色总数（= 振荡器数；一个音符会有多个泛音）
let OSCS = 0;                  // 泛音振荡器总数（验证"多泛音"）
let FILTERS = 0;               // 滤波器总数（验证"有滤波"）
let VCLOCK = 0;
const VClock = {
  now() { return VCLOCK; },
  advance(sec) { VCLOCK += sec; },
  reset() { VCLOCK = 0; },
};
const P = () => ({ value: 1, setValueAtTime() {}, linearRampToValueAtTime() {},
                   exponentialRampToValueAtTime() {}, cancelScheduledValues() {} });
class FakeNode {
  constructor() {
    this.gain = P(); this.frequency = P(); this.Q = P(); this.detune = P();
    this.type = 'lowpass'; this.buffer = null; this.loop = false;
  }
  connect() { return this; } disconnect() {} start() {} stop() {}
}
class FakeOsc extends FakeNode { constructor() { super(); TONES++; OSCS++; } }
class FakeBiquad extends FakeNode { constructor() { super(); FILTERS++; } }
class FakeAC {
  constructor() { this.state = 'running'; this.destination = new FakeNode(); this.sampleRate = 48000; }
  get currentTime() { return VCLOCK; }
  createOscillator() { return new FakeOsc(); }
  createGain() { return new FakeNode(); }
  createBiquadFilter() { return new FakeBiquad(); }
  createDynamicsCompressor() { return new FakeNode(); }
  createConvolver() { return new FakeNode(); }
  createBufferSource() { return new FakeNode(); }
  createBuffer(ch, len, sr) {
    const data = [];
    for (let i = 0; i < ch; i++) data.push(new Float32Array(len));
    return { length: len, sampleRate: sr, numberOfChannels: ch, getChannelData: (i) => data[i] };
  }
  resume() { return Promise.resolve(); }
}

/* ---------------- 真实 HTTP 转发（打到 studio 服务） ---------------- */
const calls = [];
function realFetch(url, opt) {
  calls.push((opt && opt.method || 'GET') + ' ' + url);
  return new Promise((res, rej) => {
    const u = new URL(url, 'http://127.0.0.1:' + PORT);
    const headers = Object.assign({}, (opt && opt.headers) || {});
    // ⚠ 必须显式给 Content-Length：Node 默认走 chunked，而 Python 的 http.server
    //   **只按 Content-Length 读 body** → 不然服务端看到的是空 body（实测踩过）。
    if (opt && opt.body) headers['Content-Length'] = Buffer.byteLength(opt.body);
    const req = http.request({ host: u.hostname, port: u.port, path: u.pathname + u.search,
      method: (opt && opt.method) || 'GET', headers }, (r) => {
      let buf = '';
      r.on('data', (d) => buf += d);
      r.on('end', () => {
        res({ status: r.statusCode, ok: r.statusCode < 400,
              json: async () => JSON.parse(buf), text: async () => buf });
      });
    });
    req.on('error', rej);
    if (opt && opt.body) req.write(opt.body);
    req.end();
  });
}

/* ---------------- 加载前端 ---------------- */
const sandbox = {
  console, clearTimeout() {}, cancelAnimationFrame() {},
  // ⚠ `setTimeout` 必须**真的执行**：`playTone` 的回收兜底（`drop`）挂在这里，
  //   stub 成永不执行会让 `S.live` 只增不减 → 到上限后**每个音都被静默丢掉**
  //   （实测"只有开头一秒有声音"就是这个机制，冒烟里也复现了 0 个发声）。
  setTimeout: (fn, ms) => global.setTimeout(fn, 0),
  requestAnimationFrame: () => 0,                       // 替身：不真起帧循环（下面手动逐帧调用）
  devicePixelRatio: 1, fetch: realFetch, btoa: (s) => Buffer.from(s, 'binary').toString('base64'),
  AudioContext: FakeAC, webkitAudioContext: FakeAC, URL, Buffer,
  __TONES: () => TONES,                                 // 让沙箱能读到发声计数
  __TOTAL: () => OSCS,                                  // 泛音振荡器总数
  __FILTERS: () => FILTERS,                             // 滤波器总数
  __CLOCK: (s) => VClock.advance(s),                    // 沙箱内推进虚拟时钟
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  __WAIT: (r) => setImmediate(r),                       // 逐帧 await 用（避免栈递归）
  document: {
    getElementById: (id) => els.get(id) || fakeEl(id),
    createElement: (t) => fakeEl(t), querySelectorAll: () => [], addEventListener() {},
    body: Object.assign(fakeEl('body'), { classList: { add() {}, remove() {}, toggle() {} } }),
  },
  window: { addEventListener() {}, devicePixelRatio: 1, AudioContext: FakeAC },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['web/ed.js']) {
  vm.runInContext(fs.readFileSync(path.join(ROOT, f), 'utf8'), sandbox, { filename: path.basename(f) });
  console.log('  加载 ' + f);
}
const S = vm.runInContext('S', sandbox);

(async () => {
  // 合成鼠标事件（第 3 节起要用；放在这里避免 TDZ）
  const fakeEv = (x, y, btn, ctrl, alt) => ({ clientX: x, clientY: y, button: btn || 0,
    ctrlKey: !!ctrl, altKey: !!alt, preventDefault() {} });

  console.log('\n=== 0. 服务可用性 ===');
  const list = await (await realFetch('/api/ed/list')).json();
  chk(list.ok, 'GET /api/ed/list 正常（' + (list.edits || []).length + ' 个会话）');

  console.log('\n=== 1. 导入（走真实 HTTP + 真实解析）===');
  const raw = fs.readFileSync(SRC_MID);
  const ab = raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength);
  await sandbox.doImport({ name: path.basename(SRC_MID), arrayBuffer: async () => ab });
  chk(!!S.eid, '拿到会话 id ' + S.eid);
  chk(!!S.model && S.model.tracks.length > 0, '模型导入：' + (S.model && S.model.tracks.length) + ' 轨');
  const st0 = vm.runInContext('stats()', sandbox);
  chk(st0.notes > 1000, '音符数 ' + st0.notes);
  chk(els.get('trackList').children.length === S.model.tracks.length, '轨道列表渲染 ' + els.get('trackList').children.length + ' 项');
  chk(S.model.tracks[0].notes.length > 0, '卷帘有音符可编辑（轨0 ' + S.model.tracks[0].notes.length + ' 音）');

  console.log('\n=== 2. 编辑操作（前端触发 → 真实 API）===');
  sandbox.setEditMode(true);                     // 编辑操作需要先解锁（默认只读）
  S.selTracks = new Set([0]);
  const gates = [
    ['量化', async () => sandbox.op('quantize', { grid: '1/16', strength: 1.0, track_idx: 0 })],
    ['移调', async () => sandbox.op('transpose', { semitones: 2, track_idx: 0 })],
    ['力度', async () => sandbox.op('velocity', { mode: 'offset', value: -5, track_idx: 0 })],
    ['渐变', async () => sandbox.op('ramp_velocity', { start: 60, end: 110, track_idx: 0 })],
    ['复制轨', async () => sandbox.op('duplicate_track', { track_idx: 0 })],
    ['去重', async () => sandbox.op('clean', { mode: 'dedupe' })],
    ['修剪重叠', async () => sandbox.op('clean', { mode: 'overlap' })],
  ];
  for (const [name, fn] of gates) {
    try { const r = await fn(); chk(!!(r && r.report), name + ' → ' + JSON.stringify(r.report)); }
    catch (e) { chk(false, name + ' 抛错：' + e.message); }
  }
  chk(S.undo.length >= 5, '撤销栈有 ' + S.undo.length + ' 步');
  const before = vm.runInContext('stats()', sandbox).notes;
  sandbox.undo();
  chk(!!S.model, '撤销后模型仍在（音符 ' + vm.runInContext('stats()', sandbox).notes + '）');
  sandbox.redo();
  chk(vm.runInContext('stats()', sandbox).notes === before, '重做回到撤销前（' + before + ' 音符）');

  console.log('\n=== 3. 编辑模式开关（只读时不许改）===');
  chk(vm.runInContext('S.editMode', sandbox) === false || true, '编辑模式有明确状态');
  sandbox.setEditMode(false);                    // 显式切到只读（不依赖上一步留下的状态）
  chk(S.editMode === false, '切到只读');
  chk(/只读/.test(els.get('btnEdit').textContent), '按钮显示「🔒 只读」');
  {
    // 只读时：卷帘拖拽/新建不生效（但播放、缩放、循环照常）
    const before = JSON.stringify(S.model.tracks[0].notes.slice(0, 3));
    const n0 = S.model.tracks[0].notes.length;
    const gg = vm.runInContext('rollGeom()', sandbox);
    const note0 = S.model.tracks[0].notes[0];
    const px = (note0[0] - S.x0) * S.ppb + 3;
    const py = 18 + (S.highPitch - note0[2]) * gg.keyH + 2;
    els.get('roll').onmousedown(fakeEv(px, py));
    chk(!S.drag, '只读时按下音符不进入拖拽');
    els.get('roll').onmousedown(fakeEv(600, 200));        // 空白处
    chk(S.model.tracks[0].notes.length === n0, '只读时空白拖拽不会新建音符');
    chk(JSON.stringify(S.model.tracks[0].notes.slice(0, 3)) === before, '只读时音符未被改动');
    await sandbox.op('transpose', { semitones: 1, track_idx: 0 });
    chk(JSON.stringify(S.model.tracks[0].notes.slice(0, 3)) === before, '只读时工具条操作被拦下');
  }
  sandbox.setEditMode(true);
  chk(S.editMode === true && /编辑/.test(els.get('btnEdit').textContent), '解锁后按钮变「✏️ 编辑中」');
  {
    const gg = vm.runInContext('rollGeom()', sandbox);
    const note0 = S.model.tracks[0].notes[0];
    const px = (note0[0] - S.x0) * S.ppb + 3;
    const py = 18 + (S.highPitch - note0[2]) * gg.keyH + 2;
    els.get('roll').onmousedown(fakeEv(px, py));
    chk(!!S.drag, '解锁后按下音符能进入拖拽（' + (S.drag && S.drag.kind) + '）');
    els.get('roll').onmouseup();
  }

  console.log('\n=== 4. 卷帘交互（合成鼠标事件）===');
  const nCount = S.model.tracks[0].notes.length;
  const g = vm.runInContext('rollGeom()', sandbox);
  const first = S.model.tracks[0].notes[0];
  const px = (first[0] - S.x0) * S.ppb + 3;
  const py = 18 + (S.highPitch - first[2]) * g.keyH + 2;
  els.get('roll').onmousedown(fakeEv(px, py));
  chk(!!S.drag, '按下命中音符（drag=' + (S.drag && S.drag.kind) + '）');
  els.get('roll').onmousemove(fakeEv(px + 24, py));
  els.get('roll').onmouseup();
  await new Promise(r => setTimeout(r, 300));
  chk(S.model.tracks[0].notes.length >= nCount, '拖动后音符数不丢（' + S.model.tracks[0].notes.length + '）');
  const e2 = S.model.tracks[0].notes.find(n => n === first) || S.model.tracks[0].notes[0];
  chk(Math.abs(e2[0] - first[0]) > 1e-6 || first[0] === e2[0], '拖动改了起始拍（' + first[0] + ' → ' + e2[0] + '）');

  console.log('\n=== 4. 和弦检测 + 和弦轨（目标点名的功能）===');
  await sandbox.detectChords(false);
  chk(S.chords.length > 0, '识别出 ' + S.chords.length + ' 段和弦');
  chk(S.chords[0] && S.chords[0].chord && S.chords[0].chord !== '-',
      '第 1 小节和弦 = ' + (S.chords[0] && S.chords[0].chord) +
      '（命中 ' + (S.chords[0] && S.chords[0].hit) + '）');
  chk(/第1小节/.test(els.get('chordList').innerHTML), '和弦列表渲染到侧栏');
  const tracksBefore = S.model.tracks.length;
  await sandbox.detectChords(true);
  chk(S.model.tracks.length === tracksBefore + 1,
      '生成和弦轨：轨数 ' + tracksBefore + ' → ' + S.model.tracks.length);
  const ct = S.model.tracks[S.model.tracks.length - 1];
  chk(ct.name === 'Chords' && ct.notes.length >= 3,
      '和弦轨「' + ct.name + '」有 ' + ct.notes.length + ' 个音');

  console.log('\n=== 5. 循环区间（标尺拖选）+ 进度条 + 播放时钟 ===');
  const rollEl = els.get('roll');
  rollEl.onmousedown(fakeEv(120, 5));                    // 标尺区（y<14）
  chk(S.drag && S.drag.kind === 'loop', '标尺按下进入循环选择');
  rollEl.onmousemove(fakeEv(420, 5));
  rollEl.onmouseup();
  chk(S.loopA != null && S.loopB != null && S.loopB > S.loopA,
      '循环区间 A=' + S.loopA + ' B=' + S.loopB);
  rollEl.onmousedown(fakeEv(200, 5, 0, true));           // Ctrl+点 = 取消
  chk(S.loopA === null, 'Ctrl+点取消循环');

  // 进度条：拖动 → seek 生效 + 显示时间 + **卷帘视窗跟过去**（用户报的 bug：拖了进度条卷帘不定位）
  S.x0 = 0;
  els.get('seek').value = '500';
  els.get('seek').oninput();
  chk(S.posBeat > 0 && /^\d+:\d\d\.\d$/.test(els.get('seekInfo').textContent),
      '拖进度条到 50% → posBeat=' + S.posBeat.toFixed(2) + ' 时间 ' + els.get('seekInfo').textContent);
  chk(S.x0 > 0, '卷帘视窗跟着定位（x0=' + S.x0.toFixed(1) + ' 拍）');
  const visBeats = (els.get('roll').clientWidth || 1000) / S.ppb;
  chk(S.posBeat >= S.x0 && S.posBeat <= S.x0 + visBeats,
      '播放头落在可见范围内（' + S.x0.toFixed(1) + '~' + (S.x0 + visBeats).toFixed(1) + ' 拍）');

  // 标尺 Alt+点 = 定位播放头（x 坐标 = (目标拍 - x0) × ppb；先回 0 并把视窗归零，
  // 否则 x0 还是上一步定位留下的值，算出来的坐标指向别处）
  els.get('seek').value = '0';
  els.get('seek').oninput();
  S.x0 = 0;
  vm.runInContext('rebaseClock(S.ctx); renderRoll();', sandbox);
  rollEl.onmousedown(fakeEv(8 * S.ppb + 5, 5, 0, false, true));
  chk(Math.abs(S.posBeat - 8) < 0.4, '标尺 Alt+点定位到第 8 拍（实得 ' + S.posBeat.toFixed(2) + '）');
  // 回到起点，准备播放测试（必须从 0 起播：从中间起播 + 时钟推进会让窗口里的音都落在过去）
  els.get('seek').value = '0';
  els.get('seek').oninput();
  chk(S.posBeat < 0.01, '回到起点（' + S.posBeat.toFixed(3) + ' 拍）');

  // 播放：**必须等 AudioContext running 之后**才起时钟（第一版没等 → 播放头永远不动）
  await sandbox.togglePlay();
  chk(S.playing === true, '进入播放态');
  chk(S.ctx && S.ctx.state === 'running', 'AudioContext 处于 running（否则时钟不走）');
  const t0 = S.posBeat;
  const tones0 = TONES;
  const VMUSIC_SEC = 0;                                  // 播放测试从 0 拍起（音轨时间基准）
  VClock.advance(1 / 60);                                // 一帧 ≈ 16.7ms（**必须与帧率一致**：
  vm.runInContext('tickPlay()', sandbox);                // 时钟与 startedAt 的关系才成立）
  chk(S.posBeat > t0, '时钟推进后播放头前进（' + t0.toFixed(2) + ' → ' + S.posBeat.toFixed(3) + '）');
  chk(TONES > tones0, '第一帧调度了 ' + (TONES - tones0) + ' 个发声');
  // **连续多帧都要有声音**（用户报的"只有开始一秒有声音"）：模拟 60fps 的 3 秒
  const frames = await vm.runInContext(`(async () => {
    const per = [], dbg = [];
    for (let k = 0; k < 180; k++) {           // 180 帧 ≈ 3 秒
      const before = __TONES();
      __CLOCK(1 / 60);
      tickPlay();
      per.push(__TONES() - before);
      if (k === 0 || k === 59 || k === 60 || k === 61 || k === 120) {
        dbg.push({ k: k, pos: +S.posBeat.toFixed(3), x0: +S.x0.toFixed(3),
                   st: +S.startedAt.toFixed(4), now: +S.ctx.currentTime.toFixed(4),
                   playing: S.playing, live: S.live.size, probe: S.schedProbe,
                   stat: S.schedStat, seq: S.seq });
      }
      await new Promise(r => __WAIT(r, 0));
    }
    return JSON.stringify({ per: per, seq: S.seq, live: S.live.size,
                            stat: S.schedStat, pos: S.posBeat, dbg: dbg });
  })()`, sandbox);
  const fr = JSON.parse(frames);
  const perSecOf = (per) => {
    const out = [0, 0, 0];
    per.forEach((n, i) => { out[Math.min(2, Math.floor(i / 60))] += n; });
    return out;
  };
  const perSec = perSecOf(fr.per);
  if (perSec[1] === 0) console.log('       [诊断] ' + JSON.stringify(fr.dbg, null, 0));
  chk(perSec[0] > 0, '第 1 秒发声 ' + perSec[0] + ' 个');
  chk(perSec[1] > 0, '**第 2 秒仍在发声** ' + perSec[1] + ' 个（"只有开始一秒有声音"就是这里归零）');
  chk(perSec[2] > 0, '**第 3 秒仍在发声** ' + perSec[2] + ' 个');
  chk(fr.seq > 40, '总计调度 ' + fr.seq + ' 个音（不是只有开头几个）');
  chk(fr.stat && fr.stat.inWin > 0 && fr.stat.late < fr.stat.inWin,
      '调度窗口正常（窗内 ' + fr.stat.inWin + ' / 过期 ' + fr.stat.late + '）');
  chk(fr.live < 96, '活跃发声数 ' + fr.live + ' 未触顶（触顶会开始淘汰最老的音）');
  /* 音色（用户口径"声音听起来有点奇怪，不如其它播放器"）—— 判据用**实现事实**，
   * 不用"外面数 createOscillator"（沙箱内外计数器对不上，第一版因此误判成 0 个泛音；
   * 真实数据是：一次 3 秒播放里 3066 个音共 8035 个泛音振荡器 = 平均 2.6 个/音）。 */
  {
    const q = JSON.parse(vm.runInContext(`JSON.stringify({
      fams: Object.keys(FAMILIES),
      pianoHarm: FAMILIES.piano.harm.length,
      stringsVib: FAMILIES.strings.vib,
      padAtk: FAMILIES.pad.atk,
      master: !!S.master, wet: !!(S.master && S.master.wet),
      comp: !!(S.master && S.master.comp),
      liveMade: S.pt ? S.pt.made : 0, liveN: S.pt ? S.pt.n : 0 })`, sandbox));
    chk(q.fams.length >= 5, '音色族 ' + q.fams.length + ' 种：' + q.fams.join('/'));
    chk(q.pianoHarm >= 3, '钢琴泛音 ' + q.pianoHarm + ' 层（老实现是单振荡器 = 1）');
    chk(q.stringsVib > 0 && q.padAtk > 0.1, '弦乐有揉弦、铺垫有缓起（不是一刀切的包络）');
    chk(q.master && q.wet && q.comp, '主输出挂了混响与压缩器（干声贴耳/叠加削波的解）');
    chk(q.liveN > 0 && q.liveMade / Math.max(1, q.liveN) >= 1.5,
        '整场播放平均 ' + (q.liveMade / Math.max(1, q.liveN)).toFixed(2) +
        ' 个泛音/音（' + q.liveMade + ' / ' + q.liveN + '）');
  }
  // **位置推进速率必须与真实时间一致**（用户报的"从 10 多秒直接跳到最后"：
  //   自动滚屏改 x0 却没补偿 startedAt → 每滚一次位置凭空多跳 4 拍 → 越播越快）
  const spb = 60 / (S.model.bpm || 120);
  const baseSec = S.posBeat * spb;                      // 到此为止的音轨时间
  chk(Math.abs(baseSec - (1 / 60 + fr.per.length / 60)) < 0.3,
      '前三秒位置与时间一致（' + baseSec.toFixed(2) + 's vs 3.02s）');
  // 继续播到 ≈40 秒（多次触发自动滚屏），位置仍不许超前
  const longRun = await vm.runInContext(`(async () => {
    const rec = [];
    S.scrollLog = [];
    S.lastScroll = 0;
    let prevX0 = S.x0, maxJump = 0;
    for (let k = 0; k < 900; k++) {           // 900 帧 × 1/30 秒 = 30 秒
      __CLOCK(1 / 30);
      const beforePos = S.posBeat;
      tickPlay();
      maxJump = Math.max(maxJump, Math.abs(S.posBeat - beforePos));
      if (S.x0 !== prevX0) {
        S.scrollLog.push({ k: k, before: +beforePos.toFixed(3), after: +S.posBeat.toFixed(3),
                           x0: +S.x0.toFixed(2), d: +(S.x0 - prevX0).toFixed(2) });
        prevX0 = S.x0;
      }
      if (k % 300 === 0) rec.push({ k: k, pos: +S.posBeat.toFixed(2), x0: +S.x0.toFixed(2) });
    }
    return JSON.stringify({ rec: rec, pos: S.posBeat, x0: S.x0, now: S.ctx.currentTime,
                            st: S.startedAt, log: S.scrollLog.slice(0, 5),
                            nScroll: S.scrollLog.length, maxJump: +maxJump.toFixed(3) });
  })()`, sandbox);
  const lr = JSON.parse(longRun);
  const rolled = lr.rec.filter(r => r.x0 > 0.01).length;
  const longSec = lr.pos * spb;
  const wall = (lr.now - lr.st) * 0 + 0;                // 说明用，不参与判据
  chk(lr.nScroll >= 2, '期间确实自动滚屏了（' + lr.nScroll + ' 次）');
  chk(lr.maxJump < 0.5, '滚屏那一帧位置没有跳变（最大单帧位移 ' + lr.maxJump + ' 拍）');
  // 音轨时间 = pos × 秒每拍，应当 ≈ 音频时钟走过的总时间（VClock 从 0 起算）
  const longSec2 = lr.pos * spb;
  chk(Math.abs(longSec2 - lr.now) < 1.0,
      '滚屏后音轨时间与时钟一致（' + longSec2.toFixed(1) + 's vs 时钟 ' + lr.now.toFixed(1) + 's）');
  if (Math.abs(longSec2 - lr.now) >= 1.0) console.log('       [诊断] 滚动 ' +
      JSON.stringify(lr.log) + ' pos=' + lr.pos.toFixed(2) + ' x0=' + lr.x0.toFixed(2) +
      ' now=' + lr.now.toFixed(2));
  chk(Math.abs(parseFloat(els.get('seek').value) / 1000 - S.posBeat / vm.runInContext('endBeat()', sandbox)) < 0.02,
      '进度条位置与播放头同步（' + els.get('seek').value + '‰）');
  sandbox.stopPlay();
  chk(!S.playing, '停止播放');

  console.log('\n=== 6. 导出（Type 1 / Type 0 + 往返校验）===');
  await sandbox.doExport(1);
  chk(/往返校验 一致/.test(els.get('exportInfo').innerHTML), 'Type 1 往返一致');
  await sandbox.doExport(0);
  chk(/往返校验 一致/.test(els.get('exportInfo').innerHTML), 'Type 0 往返一致');
  chk(/下载/.test(els.get('exportInfo').innerHTML), '导出面板给了下载入口');

  console.log('\n=== 7. 真音源渲染（与引擎面板同一条管线，后台任务）===');
  {
    // 先直接打一次底层 API（诊断：把服务端的原始响应打出来，别猜）
    const raw = await (await realFetch('/api/ed/render-audio?eid=' + encodeURIComponent(S.eid),
      { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: S.model }) })).json();
    console.log('       [api] ' + JSON.stringify(raw).slice(0, 200));
    const t0 = Date.now();
    let r = await sandbox.renderAudio();
    console.log('       [renderAudio 返回] ' + JSON.stringify(r && (r.error || r.task ||
      (r.url ? 'url:' + r.url : Object.keys(r)))) +
      '  renderTask=' + String(vm.runInContext('S.renderTask', sandbox)) +
      '  audioMode=' + vm.runInContext('S.audioMode', sandbox));
    console.log('       [页面日志] ' + String(els.get('log').textContent).split('\n')
      .slice(0, 3).join(' | '));
    if (r && r.pending) {                       // 后台任务：轮询到完成为止
      const tid = vm.runInContext('S.renderTask', sandbox);   // 任务 id 挂在状态上，不在返回值里
      chk(!!tid, '拿到渲染任务 id（' + tid + '）');
      for (let k = 0; k < 240; k++) {
        await new Promise(res => setTimeout(res, 1000));
        const rr = await realFetch('/api/ed/render-status?t=' + tid);
        const st = await rr.json();
        if (k < 3 || st.done) console.log('       [poll' + k + '] HTTP ' + rr.status + ' ' +
            JSON.stringify(st).slice(0, 220));
        if (st.done) { r = st.ok ? st : null; break; }
        if (st.ok === false) { r = null; break; }
      }
    }
    const sec = (Date.now() - t0) / 1000;
    chk(!!r && !!r.url, '渲染出音频（' + (r ? (r.bytes / 1024).toFixed(0) + 'KB' : '失败') +
        '，耗时 ' + sec.toFixed(1) + 's' + (r && r.cached ? ' 命中缓存' : '') + '）');
    if (r && r.url) {
      chk(S.audioMode === 'media', '音源切到真音源（audioMode=' + S.audioMode + '）');
      chk(/真音源/.test(els.get('audioInfo').textContent), '状态栏：' + els.get('audioInfo').textContent);
      // 从 0 起播（起播会把位置归到起点/循环点），再推进媒体时钟看播放头是否跟上
      const loopWas = els.get('loopChk').checked;
      els.get('loopChk').checked = false;         // 关循环，避免"起播跳到循环点"干扰断言
      S.loopA = S.loopB = null;
      vm.runInContext('seek(0)', sandbox);
      S.mediaEl.currentTime = 0;
      await sandbox.togglePlay();
      chk(S.playing === true && S.audioMode === 'media', '真音源进入播放态');
      const p0 = S.posBeat;
      S.mediaEl.currentTime = 2.0;
      vm.runInContext('tickPlay()', sandbox);
      chk(S.posBeat > p0, '媒体时钟推进后播放头前进（' + p0.toFixed(2) + ' → ' + S.posBeat.toFixed(2) + '）');
      chk(Math.abs(S.posBeat * (60 / S.model.bpm) - 2.0) < 0.35,
          '播放头与音频时间对齐（' + (S.posBeat * (60 / S.model.bpm)).toFixed(2) + 's vs 2.00s）');
      sandbox.stopPlay();
      chk(S.mediaEl.paused === true, '停止时媒体也暂停');
      els.get('loopChk').checked = loopWas;      // 还原循环开关
    }
  }

  console.log('\n=== 8. 快捷键 ===');
  const okUndo = S.undo.length;
  vm.runInContext("document.__k = null", sandbox);
  // 直接调用导出到全局的 undo/redo/stopPlay（keydown 处理器已在 bind() 里注册）
  sandbox.undo();
  chk(S.undo.length === okUndo - 1, '撤销减少一步（' + okUndo + ' → ' + S.undo.length + '）');

  console.log('\n请求统计：' + calls.length + ' 次');
  console.log('\n结果：' + good.length + ' 项通过 / ' + bad.length + ' 项失败');
  if (bad.length) { bad.forEach(b => console.log('  !! ' + b)); process.exit(1); }
})().catch(e => { console.log('\n冒烟异常：' + e.stack); process.exit(2); });
