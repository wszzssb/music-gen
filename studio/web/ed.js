/* ed.js —— MIDI Studio 编辑器（对标 miditoolbox：导入 → 编辑 → 导出）
 *
 * 架构约定：**音乐语义全在 Python**（scripts/midi_ops.py 是唯一口径），前端只负责
 * 显示与交互，操作通过 `/api/ed/op` 走同一套函数并拿回统计 —— 这样 UI 与自检不会漂。
 * 撤销 = 本地快照栈（不进后端），重做同理。
 */
'use strict';
const $ = (id) => document.getElementById(id);
const PX_PER_BEAT_MIN = 8, PX_PER_BEAT_MAX = 240;
const PALETTE = ['#e06c75', '#61afef', '#98c379', '#e5c07b', '#c678dd', '#56b6c2',
                 '#d19a66', '#7f9cf5', '#f783ac', '#4dd4ac', '#ffa94d', '#74c0fc'];
const NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];

const S = {
  eid: null, model: null, summary: null,
  selTracks: new Set(), selNotes: [],           // selNotes: {t, i}
  undo: [], redo: [], dirty: false,
  x0: 0, ppb: 48,                               // 视窗：起始拍 / 每拍像素
  lowPitch: 36, highPitch: 84,                  // 纵向音域窗
  drag: null, lastOp: null,
  loopA: null, loopB: null, chords: [],
  playing: false, ctx: null, startedAt: 0, posBeat: 0,
  /* 播放音源：`synth` = WebAudio 合成（默认兜底）；`media` = 服务端真实音源渲染的音频。
   * 用户口径"还是比不上主界面"—— 引擎面板放的是 GeneralUser GS 渲染的真音频，
   * 合成音再复杂也比不过，所以默认提供"渲染音频"这条路（见 `renderAudio`）。 */
  audioMode: 'synth', audioUrl: null, audioFp: null, audioSec: null,
  mediaEl: null, mediaStart: 0, anchorBeat: 0, anchorT: 0,
  scratch: null, anchorT2: 0, mediaPaused: false,
  master: null, reverbDry: 1.0, reverbWet: 0.22,      // 混响：干声全通、湿声 22%
  timbre: 'auto',                                     // 音色族（auto = 按 GM program 推断）
  editMode: false,                            // 🔒 只读 / ✏️ 可编辑（默认只读，防误改）
  raf: null, live: new Map(), seq: 0,          // 播放：rAF 句柄 / 活着的声音（key→{t,v,stop}） / 计数
  index: null, indexDirty: true,               // 音符时间索引（播放时按窗口取音，别每帧扫全曲）
  scheduled: new Set(),
};

/* ------------------------------------------------------------------ 日志 */
function log(msg) {
  const l = $('log');
  l.textContent = ('[' + new Date().toLocaleTimeString() + '] ' + msg + '\n' + l.textContent).slice(0, 6000);
}
function setStatus(t) { $('status').textContent = t || ''; }

/* ------------------------------------------------------------------ API */
async function api(path, opt) {
  const r = await fetch(path, opt);
  let j = null;
  try { j = await r.json(); } catch (e) { j = { ok: false, error: 'HTTP ' + r.status }; }
  if (!j.ok) throw new Error(j.error || ('HTTP ' + r.status));
  return j;
}
const post = (p, body) => api(p, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {})
});

/* 操作名的中文说明（只读拦截时的提示用） */
const OP_CN = {
  quantize: '量化', transpose: '移调', set_velocity: '改力度', velocity: '改力度',
  ramp_velocity: '画力度曲线', add_note: '加音符', delete_notes: '删音符',
  move_notes: '移动音符', set_note: '改音符', copy_range: '复制', paste: '粘贴',
  duplicate_track: '复制轨', delete_track: '删轨', set_track: '改轨道', clean: '清理',
  snap_all: '吸附', chords_track: '生成和弦轨',
};
const opLabel = (n) => OP_CN[n] || n;

/* ------------------------------------------------------------------ 模型工具 */
function stats() {
  const notes = allNotes();
  return {
    tracks: S.model.tracks.length,
    notes: notes.length,
    vel: notes.length ? [Math.min(...notes.map(n => n[3])), Math.max(...notes.map(n => n[3]))] : [0, 0],
  };
}
function allNotes() {
  const out = [];
  for (const t of S.model.tracks) for (const n of (t.notes || [])) out.push(n);
  return out;
}
function activeTracks() {
  if (S.selTracks.size) return [...S.selTracks];
  return S.model.tracks.map((_, i) => i);
}
function visibleTracks() {
  const hasSolo = S.model.tracks.some(t => t.solo);
  return S.model.tracks.map((t, i) => i).filter(i => {
    const t = S.model.tracks[i];
    if (t.hidden && !S.selTracks.has(i)) return false;
    if (hasSolo && !t.solo) return false;
    if (t.mute) return false;
    return true;
  });
}
function endBeat() {
  if (!S.model) return 4;
  let m = 0;
  for (const t of S.model.tracks) for (const n of (t.notes || [])) m = Math.max(m, n[0] + n[1]);
  return m || 4;
}
function barBeats() {
  const [num, den] = S.model.timesig || [4, 4];
  return num * 4 / den;
}
const gridStep = () => {
  const g = { '1/1': 1, '1/2': 0.5, '1/4': 0.25, '1/8': 0.125, '1/16': 0.0625, '1/32': 0.03125, '1/8T': 1 / 12, '1/16T': 1 / 24 }[$('gridSel').value] || 0.25;
  return g;
};
const snapBeat = (b) => $('snapChk').checked ? Math.round(b / gridStep()) * gridStep() : b;
const noteName = (p) => NOTE_NAMES[p % 12] + (Math.floor(p / 12) - 1);

/* ------------------------------------------------------------------ 撤销 */
function pushUndo() {
  if (!S.model) return;
  S.undo.push(JSON.stringify(S.model));
  if (S.undo.length > 40) S.undo.shift();
  S.redo = [];
  updateUndoUI();
}
function applySnapshot(txt) {
  S.model = JSON.parse(txt);
  S.selNotes = [];
  renderAll();
}
function undo() {
  if (!S.undo.length) return log('没有可撤销的步骤');
  S.redo.push(JSON.stringify(S.model));
  applySnapshot(S.undo.pop());
  S.dirty = true; updateUndoUI(); log('撤销');
}
function redo() {
  if (!S.redo.length) return log('没有可重做的步骤');
  S.undo.push(JSON.stringify(S.model));
  applySnapshot(S.redo.pop());
  S.dirty = true; updateUndoUI(); log('重做');
}
function updateUndoUI() {
  $('btnUndo').disabled = !S.undo.length;
  $('btnRedo').disabled = !S.redo.length;
}

/* ------------------------------------------------------------------ 操作（走后端） */
async function op(name, params, opts) {
  opts = opts || {};
  if (!S.model) return log('先导入一个 MIDI 文件');
  if (!opts.noEditGate && !needEdit(opLabel(name))) return;
  if (!opts.noUndo) pushUndo();
  const body = { op: name, params: params || {}, model: S.model };
  try {
    const r = await post('/api/ed/op?eid=' + encodeURIComponent(S.eid), body);
    S.model = opts.local || S.model;
    S.lastOp = r.report;
    S.dirty = true;
    renderAll();
    log(name + ' ✓ ' + JSON.stringify(r.report));
    return r;
  } catch (e) {
    if (!opts.noUndo) S.undo.pop();
    updateUndoUI();
    log('!! ' + name + ' 失败：' + e.message);
    throw e;
  }
}
/* 本地操作（前端已经改好了模型，只让后端复算统计/落盘） */
async function opLocal(name, params) {
  if (!S.model) return;
  const body = { op: name, params: params || {}, model: S.model };
  try {
    await post('/api/ed/op?eid=' + encodeURIComponent(S.eid), body);
    S.dirty = true;
  } catch (e) { log('!! ' + name + ' 失败：' + e.message); }
}

/* ------------------------------------------------------------------ 渲染 */
function renderAll() {
  S.indexDirty = true;                         // 音符/轨变了 → 播放索引要重建
  S.totalSec = endBeat() * 60 / ((S.model && S.model.bpm) || 120);
  renderTracks(); renderRoll(); renderVel(); renderProps(); renderInfo(); updateSeekUI();
}

function renderTracks() {
  const box = $('trackList');
  box.innerHTML = '';
  if (!S.model) { box.innerHTML = '<div class="dim">先导入 .mid</div>'; return; }
  S.model.tracks.forEach((t, i) => {
    const notes = (t.notes || []).length;
    const div = document.createElement('div');
    div.className = 'tr' + (S.selTracks.has(i) ? ' on' : '') + (t.hidden ? ' hid' : '');
    div.style.borderLeftColor = PALETTE[i % PALETTE.length];
    div.innerHTML =
      '<span class="nm" title="' + (t.name || '') + '">' + (t.name || ('轨 ' + (i + 1))) + '</span>' +
      '<span class="dim">ch' + ((t.channel || 0) + 1) + ' · ' + (t.drum ? '鼓组' : 'prog ' + (t.program == null ? '—' : t.program)) + ' · ' + notes + ' 音</span>' +
      '<span class="btns">' +
      '<button data-a="solo" class="' + (t.solo ? 'on' : '') + '" title="独奏">S</button>' +
      '<button data-a="mute" class="' + (t.mute ? 'on' : '') + '" title="静音">M</button>' +
      '<button data-a="hidden" class="' + (t.hidden ? 'on' : '') + '" title="隐藏">👁</button>' +
      '</span>';
    div.querySelector('.nm').onclick = (e) => {
      if (!e.ctrlKey && !e.metaKey) S.selTracks.clear();
      S.selTracks.has(i) ? S.selTracks.delete(i) : S.selTracks.add(i);
      renderTracks(); renderRoll(); renderVel(); renderProps();
    };
    div.querySelectorAll('button').forEach(b => b.onclick = async (ev) => {
      ev.stopPropagation();
      const a = b.dataset.a;
      const v = !t[a];
      t[a] = v;
      await op('set_track', { track_idx: i, [a]: v });
    });
    box.appendChild(div);
  });
  const st = stats();
  $('trHint').textContent = '（共 ' + st.tracks + ' 轨 / ' + st.notes + ' 音符；点轨名筛选）';
}

function rollGeom() {
  const cv = $('roll');
  const w = cv.parentElement.clientWidth - 4;
  const h = 360;
  const dpr = window.devicePixelRatio || 1;
  if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
    cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
    cv.style.width = w + 'px'; cv.style.height = h + 'px';
  }
  cv.classList.add('rolling');
  return { cv, ctx: cv.getContext('2d'), w, h, dpr, keyH: Math.max(7, (h - 18) / (S.highPitch - S.lowPitch + 1)) };
}
const pitchY = (g, p) => 18 + (S.highPitch - p) * g.keyH;
const yPitch = (g, y) => Math.round(S.highPitch - (y - 18) / g.keyH);
const beatX = (g, b) => (b - S.x0) * S.ppb;
const xBeat = (g, x) => S.x0 + x / S.ppb;

function renderRoll() {
  const g = rollGeom();
  const c = g.ctx;
  c.setTransform(g.dpr, 0, 0, g.dpr, 0, 0);
  c.clearRect(0, 0, g.w, g.h);
  c.fillStyle = '#14161a'; c.fillRect(0, 0, g.w, g.h);
  if (!S.model) {
    c.fillStyle = '#666'; c.font = '13px system-ui';
    c.fillText('先导入一个 .mid 文件（左上「⬆ 导入 MIDI」）', 16, 40);
    return;
  }
  if (S.selTracks.size === 1) {                 // 单轨：自动把纵向对准它的音域
    const t = S.model.tracks[[...S.selTracks][0]];
    const ps = (t.notes || []).map(n => n[2]);
    if (ps.length) { S.lowPitch = Math.max(0, Math.min(...ps) - 3); S.highPitch = Math.min(127, Math.max(...ps) + 3); }
  }
  // 循环区间（在标尺上拖选；Ctrl+拖 = 取消）
  if ($('loopChk').checked && S.loopA != null && S.loopB != null && S.loopB > S.loopA) {
    const xa = beatX(g, S.loopA), xb = beatX(g, S.loopB);
    c.fillStyle = 'rgba(76,154,255,.13)';
    c.fillRect(xa, 0, xb - xa, g.h);
    c.fillStyle = '#4c9aff';
    c.fillRect(xa, 0, 2, 14); c.fillRect(xb - 2, 0, 2, 14);
    c.fillRect(xa, 0, xb - xa, 3);
    c.font = 'bold 9px system-ui';
    c.fillText('A', xa + 3, 10);
    c.fillText('B', xb - 9, 10);
  }
  // 横向网格
  const bg = barBeats();
  const endB = Math.max(endBeat(), S.x0 + g.w / S.ppb);
  c.lineWidth = 1;
  for (let b = Math.floor(S.x0 / gridStep()) * gridStep(); b <= endB; b += gridStep()) {
    const x = Math.round(beatX(g, b)) + 0.5;
    if (x < 0 || x > g.w) continue;
    const isBar = Math.abs(b / bg - Math.round(b / bg)) < 1e-6;
    const isBeat = Math.abs(b - Math.round(b)) < 1e-6;
    c.strokeStyle = isBar ? '#4a5260' : (isBeat ? '#2c313a' : '#20242b');
    c.beginPath(); c.moveTo(x, 14); c.lineTo(x, g.h); c.stroke();
    if (isBar) {
      c.fillStyle = '#6b7280'; c.font = '10px system-ui';
      c.fillText(String(Math.round(b / bg) + 1), x + 3, 11);
    }
  }
  // 纵向：黑键行 + 音名
  for (let p = S.lowPitch; p <= S.highPitch; p++) {
    const y = pitchY(g, p);
    const black = [1, 3, 6, 8, 10].includes(p % 12);
    c.fillStyle = black ? '#171a20' : '#1b1f26';
    c.fillRect(0, y, g.w, g.keyH);
    if (p % 12 === 0) {
      c.fillStyle = '#4b5563'; c.font = '9px system-ui';
      c.fillText(noteName(p), 2, y + g.keyH - 1);
    }
  }
  // 音符
  const vis = new Set(visibleTracks());
  S.model.tracks.forEach((t, ti) => {
    if (!vis.has(ti)) return;
    const col = PALETTE[ti % PALETTE.length];
    for (const n of (t.notes || [])) {
      const [a, d, p, v] = n;
      if (p < S.lowPitch || p > S.highPitch) continue;
      const x = beatX(g, a), w = Math.max(2, d * S.ppb), y = pitchY(g, p);
      if (x + w < 0 || x > g.w) continue;
      const sel = S.selNotes.some(s => s.t === ti && S.model.tracks[ti].notes[s.i] === n);
      c.globalAlpha = 0.35 + 0.65 * (v / 127);
      c.fillStyle = sel ? '#ffffff' : col;
      c.fillRect(x, y + 0.5, w, Math.max(2, g.keyH - 1));
      c.globalAlpha = 1;
      if (sel) { c.strokeStyle = '#fff'; c.lineWidth = 1.5; c.strokeRect(x + 0.5, y + 1, w - 1, Math.max(2, g.keyH - 2)); }
    }
  });
  // 播放头 / 定位线：不在播放时也画（虚线），否则"定位到哪"完全看不出来
  if (S.playing || S.posBeat > 0) {
    const x = Math.round(beatX(g, S.posBeat)) + 0.5;
    if (x >= -40 && x <= g.w + 40) {
      if (S.playing) {
        c.strokeStyle = '#ff5c5c'; c.lineWidth = 2; c.setLineDash([]);
      } else {
        c.strokeStyle = '#ffb454'; c.lineWidth = 1.5; c.setLineDash([4, 3]);
      }
      c.beginPath(); c.moveTo(x, 14); c.lineTo(x, g.h); c.stroke();
      c.setLineDash([]);
    }
  }
}

function renderVel() {
  const cv = $('velLane');
  const w = cv.parentElement.clientWidth - 4, h = 64;
  const dpr = window.devicePixelRatio || 1;
  if (cv.width !== Math.round(w * dpr)) { cv.width = Math.round(w * dpr); cv.style.width = w + 'px'; }
  cv.height = Math.round(h * dpr); cv.style.height = h + 'px';
  const c = cv.getContext('2d');
  c.setTransform(dpr, 0, 0, dpr, 0, 0);
  c.fillStyle = '#14161a'; c.fillRect(0, 0, w, h);
  if (!S.model) return;
  c.strokeStyle = '#2c313a';
  for (let k = 1; k < 4; k++) { const y = h * k / 4; c.beginPath(); c.moveTo(0, y); c.lineTo(w, y); c.stroke(); }
  const vis = new Set(visibleTracks());
  S.model.tracks.forEach((t, ti) => {
    if (!vis.has(ti)) return;
    const col = PALETTE[ti % PALETTE.length];
    c.fillStyle = col;
    for (const n of (t.notes || [])) {
      const x = beatX(rollGeomCache || rollGeom(), n[0]);
      if (x < -2 || x > w) continue;
      const bh = (n[3] / 127) * (h - 4);
      c.fillRect(x, h - bh, Math.max(1.5, Math.min(4, n[1] * S.ppb * 0.5)), bh);
    }
  });
  const x = (S.posBeat - S.x0) * S.ppb;
  if (x >= 0 && x <= w) { c.strokeStyle = '#ff5c5c'; c.beginPath(); c.moveTo(x, 0); c.lineTo(x, h); c.stroke(); }
}
let rollGeomCache = null;

function renderProps() {
  const box = $('noteProps');
  if (!S.model || !S.selNotes.length) {
    box.innerHTML = '<span class="dim">（选中音符后这里可以精确改数值；多选时改的是第一个）</span>';
    return;
  }
  const s = S.selNotes[0];
  const n = S.model.tracks[s.t].notes[s.i];
  if (!n) { S.selNotes = []; return renderProps(); }
  box.innerHTML =
    '<div class="row"><span class="dim">轨</span> ' + (S.model.tracks[s.t].name || s.t) + '</div>' +
    '<div class="row"><span class="dim">起始(拍)</span><input id="pStart" type="number" step="0.0625" value="' + n[0].toFixed(4) + '"></div>' +
    '<div class="row"><span class="dim">时值(拍)</span><input id="pDur" type="number" step="0.0625" value="' + n[1].toFixed(4) + '"></div>' +
    '<div class="row"><span class="dim">音高</span><input id="pPitch" type="number" min="0" max="127" value="' + n[2] + '"> <span class="dim">' + noteName(n[2]) + '</span></div>' +
    '<div class="row"><span class="dim">力度</span><input id="pVel" type="number" min="1" max="127" value="' + n[3] + '"></div>' +
    '<div class="row"><button id="pApply" class="mini primary">应用</button> <button id="pDel" class="mini">删除</button> 选中 ' + S.selNotes.length + ' 个</div>';
  $('pApply').onclick = async () => {
    const st = parseFloat($('pStart').value), du = parseFloat($('pDur').value);
    const pi = parseInt($('pPitch').value, 10), ve = parseInt($('pVel').value, 10);
    pushUndo();
    n[0] = Math.max(0, st); n[1] = Math.max(1 / 64, du); n[2] = Math.max(0, Math.min(127, pi)); n[3] = Math.max(1, Math.min(127, ve));
    await opLocal('set_note', { track_idx: s.t, note_idx: s.i, start: n[0], dur: n[1], pitch: n[2], vel: n[3] });
    S.dirty = true; renderRoll(); renderVel(); renderProps(); log('改音符 → ' + JSON.stringify(n));
  };
  $('pDel').onclick = async () => {
    const idx = S.selNotes.filter(x => x.t === s.t).map(x => x.i);
    pushUndo();
    S.model.tracks[s.t].notes = S.model.tracks[s.t].notes.filter((_, i) => !idx.includes(i));
    S.selNotes = [];
    await opLocal('delete_notes', { track_idx: s.t, note_idx: idx });
    renderAll(); log('删了 ' + idx.length + ' 个音');
  };
}

function renderInfo() {
  const s = $('fileInfo');
  if (!S.model) { s.innerHTML = '<span class="dim">（还没导入文件）</span>'; return; }
  const st = stats();
  s.innerHTML =
    '<div class="row"><b>' + (S.model.title || '(无标题)') + '</b></div>' +
    '<div class="row dim">格式 Type ' + S.model.format + ' · ' + S.model.division + ' tick/拍 · ' +
    (S.model.bpm || 0).toFixed(2) + ' BPM · 拍号 ' + (S.model.timesig || []).join('/') + '</div>' +
    '<div class="row dim">轨 ' + st.tracks + ' · 音符 ' + st.notes + ' · 力度 ' + st.vel[0] + '~' + st.vel[1] +
    ' · 长度 ' + endBeat().toFixed(1) + ' 拍</div>' +
    (S.model.source ? '<div class="row dim">来源 ' + S.model.source + '</div>' : '');
}

/* ------------------------------------------------------------------ 卷帘交互 */
function noteAt(g, x, y) {
  const p = yPitch(g, y);
  const b = xBeat(g, x);
  const vis = new Set(visibleTracks());
  for (let ti = S.model.tracks.length - 1; ti >= 0; ti--) {
    if (!vis.has(ti)) continue;
    const arr = S.model.tracks[ti].notes || [];
    for (let i = arr.length - 1; i >= 0; i--) {
      const n = arr[i];
      if (n[2] !== p) continue;
      if (b >= n[0] - 1e-9 && b <= n[0] + n[1] + 1e-9) return { t: ti, i, n, g };
    }
  }
  return null;
}
function bindRoll() {
  const cv = $('roll');
  cv.oncontextmenu = (e) => e.preventDefault();
  cv.onmousedown = async (e) => {
    if (!S.model) return;
    if (e.button === 2 && !S.editMode) return;         // 右键删：只读时直接不响应
    const g = rollGeom();
    const r = cv.getBoundingClientRect();
    const x = e.clientX - r.left, y = e.clientY - r.top;
    // 标尺区（最上面 14px）：拖动 = 选循环区间 · Ctrl+点 = 取消循环 · Alt+点 = 定位播放头
    if (y < 14) {
      if (e.ctrlKey || e.metaKey) {
        S.loopA = S.loopB = null; renderRoll(); log('已取消循环区间');
        return;
      }
      if (e.altKey) {
        seek(Math.max(0, snapBeat(xBeat(g, x))));
        log('定位到 ' + S.posBeat.toFixed(2) + ' 拍（视窗已跟随）');
        return;
      }
      S.drag = { kind: 'loop', b0: Math.max(0, snapBeat(xBeat(g, x))), moved: false };
      renderRoll();
      return;
    }
    if (!needEdit('改音符')) return;         // 🔒 只读：浏览/播放/缩放/循环不受影响，改音符被拦下
    const hit = noteAt(g, x, y);
    if (e.button === 2) {                                  // 右键删
      if (!hit) return;
      pushUndo();
      S.model.tracks[hit.t].notes.splice(hit.i, 1);
      S.selNotes = [];
      await opLocal('delete_notes', { track_idx: hit.t, note_idx: [hit.i] });
      renderRoll(); renderVel(); renderProps(); log('删 1 个音');
      return;
    }
    if (hit) {
      const n = hit.n;
      const rightEdge = (xBeat(g, x) - (n[0] + n[1])) * S.ppb > -4;
      if (!e.ctrlKey && !e.metaKey && !S.selNotes.some(s => s.t === hit.t && S.model.tracks[hit.t].notes[s.i] === n)) {
        S.selNotes = [{ t: hit.t, i: hit.i }];
      } else if (e.ctrlKey || e.metaKey) {
        S.selNotes.push({ t: hit.t, i: hit.i });
      }
      S.drag = { kind: rightEdge ? 'dur' : (e.altKey ? 'vel' : 'move'), t: hit.t, i: hit.i, n,
                 x0: x, y0: y, a0: n[0], d0: n[1], p0: n[2], v0: n[3], moved: false };
      renderRoll(); renderProps();
      return;
    }
    // 空白：新建（在选中的轨上；没选就用第一条不隐藏的轨）
    const ti = activeTracks().find(i => !S.model.tracks[i].hidden);
    if (ti == null) return;
    const b = Math.max(0, snapBeat(xBeat(g, x)));
    const p = Math.max(0, Math.min(127, yPitch(g, y)));
    const dur = Math.max(gridStep(), 0.25);
    pushUndo();
    S.model.tracks[ti].notes.push([b, dur, p, 96]);
    renderAll();
    await opLocal('add_note', { track_idx: ti, start: b, dur, pitch: p, vel: 96 });
    log('新建音符 ' + noteName(p) + ' @' + b.toFixed(3) + ' 拍');
  };
  cv.onmousemove = (e) => {
    if (!S.drag) return;
    const g = rollGeom();
    const r = cv.getBoundingClientRect();
    const x = e.clientX - r.left, y = e.clientY - r.top;
    const d = S.drag;
    if (d.kind === 'loop') {                       // 标尺拖选循环区间
      const b = Math.max(0, snapBeat(xBeat(g, x)));
      S.loopA = Math.min(d.b0, b);
      S.loopB = Math.max(d.b0, b);
      d.moved = true;
      renderRoll();
      return;
    }
    const n = d.n;
    const dB = (x - d.x0) / S.ppb;
    const dP = -Math.round((y - d.y0) / g.keyH);
    d.moved = d.moved || Math.abs(x - d.x0) > 2 || Math.abs(y - d.y0) > 2;
    if (d.kind === 'move') {
      n[0] = Math.max(0, snapBeat(d.a0 + dB));
      n[2] = Math.max(0, Math.min(127, d.p0 + dP));
    } else if (d.kind === 'dur') {
      n[1] = Math.max(1 / 64, snapBeat(Math.max(1 / 64, d.d0 + dB)));
    } else if (d.kind === 'vel') {
      n[3] = Math.max(1, Math.min(127, Math.round(d.v0 - (y - d.y0) * 1.2)));
    }
    renderRoll(); renderVel(); renderProps();
  };
  cv.onmouseup = async () => {
    const d = S.drag; S.drag = null;
    if (!d) return;
    if (d.kind === 'loop') {
      if (S.loopA != null && S.loopB - S.loopA < 1 / 16) { S.loopA = S.loopB = null; }
      else log('循环区间 A=' + (S.loopA || 0).toFixed(3) + ' B=' + (S.loopB || 0).toFixed(3) + ' 拍');
      renderRoll();
      return;
    }
    if (!d.moved) { renderRoll(); return; }
    pushUndo();
    await opLocal('set_note', { track_idx: d.t, note_idx: d.i, start: d.n[0], dur: d.n[1], pitch: d.n[2], vel: d.n[3] });
    S.dirty = true;
    log('拖动 → ' + JSON.stringify(d.n));
  };
  cv.onwheel = (e) => {
    if (!S.model) return;
    e.preventDefault();
    const g = rollGeom();
    const r = cv.getBoundingClientRect();
    if (e.ctrlKey || e.shiftKey) {                          // 纵向缩放
      const c = (S.lowPitch + S.highPitch) / 2;
      const span = (S.highPitch - S.lowPitch) * (e.deltaY > 0 ? 1.15 : 0.87);
      S.lowPitch = Math.max(0, Math.round(c - span / 2));
      S.highPitch = Math.min(127, Math.round(c + span / 2));
    } else if (e.altKey) {                                  // 横向滚动
      S.x0 = Math.max(0, S.x0 + (e.deltaY > 0 ? 2 : -2));
    } else {                                                // 横向缩放（以鼠标为锚）
      const ax = e.clientX - r.left;
      const anchor = xBeat(g, ax);
      S.ppb = Math.max(PX_PER_BEAT_MIN, Math.min(PX_PER_BEAT_MAX, S.ppb * (e.deltaY > 0 ? 0.88 : 1.14)));
      S.x0 = Math.max(0, anchor - ax / S.ppb);
    }
    renderRoll(); renderVel();
  };
}

function bindVelLane() {
  const cv = $('velLane');
  cv.onmousedown = async (e) => {
    if (!S.model) return;
    const r = cv.getBoundingClientRect();
    const x = e.clientX - r.left, y = e.clientY - r.top;
    const b = S.x0 + x / S.ppb;
    const v = Math.max(1, Math.min(127, Math.round((1 - y / 64) * 127)));
    // 找最近的音（同一拍位 ±1/16）
    let best = null;
    S.model.tracks.forEach((t, ti) => {
      if (t.hidden) return;
      (t.notes || []).forEach((n, i) => {
        const d = Math.abs(n[0] - b);
        if (d < 0.07 && (!best || d < best.d)) best = { t: ti, i, n, d };
      });
    });
    if (!best) return;
    pushUndo();
    best.n[3] = v;
    await opLocal('set_note', { track_idx: best.t, note_idx: best.i, vel: v });
    renderVel(); renderRoll();
    log('力度 → ' + v);
  };
}

/* ------------------------------------------------------------------ 播放（WebAudio 合成） */
/* 播放时钟。
 *
 * ⚠ 关键坑（实测"点了播放没反应、播放头不动"的根因）：
 *   Chrome/Safari 要求**用户手势之后**才能启动 AudioContext，`resume()` 是**异步**的，
 *   而挂起状态下 `ctx.currentTime` 一动不动。第一版直接用
 *   `startedAt = ctx.currentTime - (pos - x0)*spb` 起算 → 拿到的是"还没走"的时钟，
 *   于是 `cur` 恒等于起点、播放头永远不动。
 *   正确做法：**先把 ctx 起起来（await resume），再取 currentTime 当基准**；
 *   ctx 没 running 就不进入播放（并给出提示），而不是假装在播。 */
let AUDIO_BLOCKED = false;
async function ensureCtx() {
  if (!S.ctx) {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) { log('!! 这个浏览器没有 WebAudio，播放不可用（编辑与导出不受影响）'); return null; }
    S.ctx = new AC();
    buildMaster(S.ctx);
  }
  if (S.ctx.state !== 'running') {
    try { await S.ctx.resume(); } catch (e) { /* 下面按 state 统一处理 */ }
  }
  if (S.ctx.state !== 'running' && !AUDIO_BLOCKED) {
    AUDIO_BLOCKED = true;
    log('!! 浏览器拦住了音频（需要一次用户点击）：请再点一下「▶ 播放」');
    setStatus('音频被浏览器拦住 —— 再点一下播放');
  }
  return S.ctx;
}
/* ---------------------------------------------------------------------------
 * 合成音色（把"单振荡器三角波直连 destination"换成有泛音/包络/滤波/混响的音色）
 *
 * 为什么改（用户口径："声音听起来有点奇怪，不如其它播放器"）：原来一个音 = 一个
 * `triangle` 振荡器 + 线性增益包络 → 没有泛音结构（像"电子豆子"）、没有滤波器
 * （力度/音区不改变明亮度）、没有空间感（干声贴耳）。现在：
 *   ① 每音 2~4 个**失谐/泛音**振荡器 + 低通滤波（亮度随音区与力度变化）
 *   ② 按乐器族给 ADSR（拨弦快起快落、弦乐缓起、Pad 更缓）
 *   ③ 主输出串 压缩器（防叠加削波）+ 低通（去刺）+ **混响**（程序生成脉冲响应）
 * 音色族按轨的 GM program 自动选，也可用顶栏「🎨 音色」手动指定。全部程序生成。
 * ------------------------------------------------------------------------- */
function buildMaster(w) {
  const comp = w.createDynamicsCompressor ? w.createDynamicsCompressor() : null;
  const tone = w.createBiquadFilter();
  tone.type = 'lowpass'; tone.frequency.value = 9000; tone.Q.value = 0.5;
  const out = w.createGain(); out.gain.value = 0.9;
  const dry = w.createGain(); dry.gain.value = S.reverbDry;
  const wet = w.createGain(); wet.gain.value = S.reverbWet;
  let conv = null;
  if (w.createConvolver) {
    conv = w.createConvolver();
    const sr = w.sampleRate || 44100, len = Math.floor(sr * 1.6);
    const buf = w.createBuffer(2, len, sr);
    for (let ch = 0; ch < 2; ch++) {
      const d = buf.getChannelData(ch);
      for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, 2.6);
    }
    conv.buffer = buf;
    wet.connect(conv); conv.connect(tone);
  }
  dry.connect(tone);
  if (comp) { tone.connect(comp); comp.connect(out); } else { tone.connect(out); }
  out.connect(w.destination);
  S.master = { in: dry, wet: conv ? wet : null, tone, out, comp };
}

/* 音色族 → 泛音表（[倍数, 相对增益]）+ ADSR + 滤波/失谐/揉弦 */
const FAMILIES = {
  clean:   { harm: [[1, 1], [2, 0.12]], atk: 0.006, dec: 0.25, sus: 0.55, rel: 0.24,
             cutoff: 4200, q: 0.7, detune: 4, vib: 0 },
  piano:   { harm: [[1, 1], [2, 0.45], [3, 0.20], [4, 0.08]], atk: 0.004, dec: 0.9,
             sus: 0.22, rel: 0.5, cutoff: 5200, q: 0.6, detune: 3, vib: 0 },
  guitar:  { harm: [[1, 1], [2, 0.5], [3, 0.28], [5, 0.10]], atk: 0.003, dec: 0.35,
             sus: 0.16, rel: 0.3, cutoff: 6000, q: 0.9, detune: 5, vib: 0 },
  strings: { harm: [[1, 1], [2, 0.35], [3, 0.18]], atk: 0.10, dec: 0.5, sus: 0.75,
             rel: 0.55, cutoff: 3800, q: 0.8, detune: 9, vib: 4.6 },
  pad:     { harm: [[1, 1], [2, 0.22]], atk: 0.22, dec: 0.8, sus: 0.8, rel: 0.9,
             cutoff: 2400, q: 0.7, detune: 12, vib: 3.2 },
  pluck:   { harm: [[1, 1], [2, 0.30], [4, 0.10]], atk: 0.002, dec: 0.18, sus: 0.05,
             rel: 0.16, cutoff: 7000, q: 1.2, detune: 2, vib: 0 },
};
const FAMILY_CN = { clean: '纯净', piano: '钢琴', guitar: '吉他', strings: '弦乐',
                    pad: '铺垫', pluck: '拨弦' };

function familyOf(program, drum) {
  if (S.timbre && S.timbre !== 'auto') return FAMILIES[S.timbre] ? S.timbre : 'clean';
  if (drum) return 'pluck';
  const p = (program == null ? 0 : program);
  if (p <= 7) return 'piano';
  if (p >= 24 && p <= 31) return 'guitar';
  if (p >= 40 && p <= 51) return 'strings';
  if (p >= 88 && p <= 95) return 'pad';
  if (p >= 80 && p <= 87) return 'pluck';
  return 'clean';
}

/* 一个音：多振荡器 + 低通 + ADSR（力度同时影响音量与亮度，真实乐器就是这样） */
function voice(w, at, want, freq, vel, fam) {
  const spec = FAMILIES[fam] || FAMILIES.clean;
  const g = w.createGain();
  const lp = w.createBiquadFilter();
  lp.type = 'lowpass';
  const vN = vel / 127;
  lp.frequency.value = Math.max(500, Math.min(12000,
    spec.cutoff * (0.55 + 0.75 * vN) * (freq > 700 ? Math.max(0.45, 1200 / freq) : 1)));
  lp.Q.value = spec.q;
  const peak = Math.min(0.085, 0.010 + 0.00075 * vel);
  const a = spec.atk, d = spec.dec, s = spec.sus, r = spec.rel;
  g.gain.setValueAtTime(0.0001, at);
  g.gain.linearRampToValueAtTime(peak, at + a);
  const dEnd = at + a + d;
  g.gain.linearRampToValueAtTime(peak * s, dEnd);
  const relAt = Math.max(dEnd, at + want);
  g.gain.setValueAtTime(Math.max(0.0001, peak * s), relAt);
  g.gain.exponentialRampToValueAtTime(0.0001, relAt + r + 0.02);
  lp.connect(g);
  g.connect(S.master.in);
  if (S.master.wet) g.connect(S.master.wet);
  const oscs = [];
  let made = 0, skipped = 0;
  for (const [mult, amp] of spec.harm) {
    let f = freq * mult;
    if (f > 16000) { skipped++; continue; }
    const o = w.createOscillator();
    o.type = 'sine';
    o.frequency.setValueAtTime(f * (1 + (Math.random() * 2 - 1) * spec.detune / 1200), at);
    const og = w.createGain();
    og.gain.value = amp / spec.harm.length;
    o.connect(og); og.connect(lp);
    o.start(at);
    o.stop(relAt + r + 0.06);
    oscs.push(o);
    made++;
  }
  if (spec.vib && oscs.length) {                 // 弦乐/Pad 的轻微揉弦
    const lfo = w.createOscillator(), lg = w.createGain();
    lfo.frequency.value = spec.vib;
    lg.gain.value = freq * 0.004;
    lfo.connect(lg);
    for (const o of oscs) lg.connect(o.frequency);
    lfo.start(at); lfo.stop(relAt + r + 0.06);
    oscs.push(lfo);
  }
  return { stop: () => { for (const o of oscs) { try { o.stop(); } catch (e) { /* 已停 */ } } },
           made: made, skipped: skipped, fam: fam, harmLen: spec.harm.length,
           nHarm: spec.harm.length, fs: freq };
}

/* 鼓：底鼓=正弦下滑 · 军鼓=带通噪声 · 踩镲/沙锤=高通噪声（比"方波"像得多） */
function drumVoice(w, at, want, pitch, vel) {
  const vN = vel / 127;
  const g = w.createGain();
  g.connect(S.master.in);
  if (S.master.wet) g.connect(S.master.wet);
  const parts = [];
  if (pitch < 40) {
    g.gain.setValueAtTime(Math.min(0.17, 0.03 + 0.0012 * vel), at);
    const o = w.createOscillator();
    o.frequency.setValueAtTime(110, at);
    o.frequency.exponentialRampToValueAtTime(42, at + 0.11);
    o.connect(g); o.start(at); o.stop(at + 0.24);
    g.gain.exponentialRampToValueAtTime(0.0001, at + 0.22);
    parts.push(o);
  } else if (pitch < 46) {
    g.gain.setValueAtTime(Math.min(0.13, 0.02 + 0.001 * vel), at);
    const src = w.createBufferSource();
    src.buffer = noiseBuf(w);
    const bp = w.createBiquadFilter();
    bp.type = 'bandpass'; bp.frequency.value = 1900; bp.Q.value = 0.9;
    src.connect(bp); bp.connect(g);
    src.start(at); src.stop(at + 0.2);
    g.gain.exponentialRampToValueAtTime(0.0001, at + 0.18);
    parts.push(src);
  } else {
    g.gain.setValueAtTime(Math.min(0.085, 0.012 + 0.0008 * vel), at);
    const src = w.createBufferSource();
    src.buffer = noiseBuf(w);
    const hp = w.createBiquadFilter();
    hp.type = 'highpass'; hp.frequency.value = pitch > 60 ? 7000 : 4500;
    src.connect(hp); hp.connect(g);
    const dd = Math.min(0.09, 0.03 + 0.05 * vN);
    src.start(at); src.stop(at + dd + 0.03);
    g.gain.exponentialRampToValueAtTime(0.0001, at + dd);
    parts.push(src);
  }
  return { stop: () => { for (const p of parts) { try { p.stop(); } catch (e) { /* 已停 */ } } } };
}
let NOISE = null;
function noiseBuf(w) {
  const sr = w.sampleRate || 44100;
  if (NOISE && NOISE.__sr === sr) return NOISE;
  const len = Math.floor(sr * 0.5);
  const b = w.createBuffer(1, len, sr);
  const d = b.getChannelData(0);
  for (let i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;
  b.__sr = sr;
  NOISE = b;
  return b;
}

/* 把音符按起点排好序 + 建索引：每帧只取"这一小段窗口"里的音，
 * 不再每帧扫全部音符（4500 音的歌扫 60 次/秒 = 27 万次/秒，白烧 CPU 还会掉帧） */
function buildIndex() {
  const idx = { notes: [] };
  const vis = new Set(visibleTracks());
  S.model.tracks.forEach((t, ti) => {
    if (!vis.has(ti)) return;
    for (const n of (t.notes || [])) idx.notes.push({ b: n[0], d: n[1], p: n[2], v: n[3], t: ti, drum: !!t.drum });
  });
  idx.notes.sort((a, b) => a.b - b.b);
  S.index = idx;
  S.indexDirty = false;
}
/* 播放时钟基准。
 *
 * ⚠ 这里踩过三次坑，别再"简化"：
 *   ① Chrome/Safari 要用户手势后才 running，`resume()` 是异步的，挂起时 `currentTime`
 *      一动不动 —— 必须 `await resume()` 之后再取基准（否则播放头永远不动）。
 *   ② 基准**只能在一处算**（`seek` / `togglePlay` 调 `rebaseClock`）。多处各算一套
 *      （`x0`/`posBeat` 的先后顺序不同）会让 `startedAt` 与 `currentTime` 不同源。
 *   ③ 不同源的后果不是"位置差一点"，而是**窗口内每个音的绝对时刻都算在过去**
 *      （实测 `stat.late = inWin`）→ 一个音都不发 = 用户听到的"只有开始一秒有声音"。
 *      `tickPlay` 里用 `isFinite` + 单调性兜底：基准一旦不可用就按当前时钟重算。 */
function rebaseClock(w) {
  if (!w || w.state !== 'running' || !S.model) return;
  const spb = 60 / (S.model.bpm || 120);
  S.startedAt = w.currentTime - (S.posBeat - S.x0) * spb;
}
/* 把 [fromBeat, toBeat) 窗口内的音排进 WebAudio 时间轴。
 *
 * 绝对时刻的算法（**这里错过一次，症状是"只有开头一秒有声音"**）：
 *   拍位 p 的音在音频时钟上的时刻 = `startedAt + (p - x0) * spb`
 *   （`x0` = 视窗起始拍，`startedAt` = 拍位 x0 对应的时钟时刻；
 *     由 `pos = x0 + (now - startedAt)/spb` 反解而来。）
 *   第一版写成 `startedAt + (p - 当前播放位置) * spb` —— 与正确值差 `now - startedAt`
 *   （播放越久差越大，实测 2 秒后差 2 秒）→ 窗口内每个音的 `at` 都落在 `now` 之前，
 *   被"已经过去的音不追"全部丢掉 → 听感就是放了一小段之后彻底没声。
 */
function scheduleWindow(fromBeat, toBeat, baseTime) {
  const w = S.ctx;
  if (!w) return;
  S.schedCalls = (S.schedCalls || 0) + 1;
  const spb = 60 / (S.model.bpm || 120);
  if (!S.index || S.indexDirty) buildIndex();
  const arr = S.index.notes;
  let lo = 0, hi = arr.length;
  while (lo < hi) { const m = (lo + hi) >> 1; if (arr[m].b < fromBeat) lo = m + 1; else hi = m; }
  const now = w.currentTime;
  let inWin = 0, late = 0;
  for (let i = lo; i < arr.length; i++) {
    const n = arr[i];
    if (n.b >= toBeat) break;
    inWin++;
    const at = baseTime + (n.b - S.x0) * spb;
    if (at < now - 0.01) { late++; continue; }
    playTone(w, at, Math.max(0.05, n.d * spb), n.p, n.v, n.drum, 'k' + (S.seq++), n.t);
  }
  S.schedStat = { from: fromBeat, to: toBeat, inWin: inWin, late: late, lo: lo };
}
function playTone(w, at, dur, pitch, vel, drum, key, trackIdx) {  /* 并发控制（**这里踩过大坑**）：原来只做一个全局上限 `S.live.size > 48` 就 return，
   * 而 `S.live` 靠 `onended` / `setTimeout` 回收 —— 调度提前量 1.5 秒时，被拒的音
   * 会**一直累积**，实测十几秒后每个新音都被丢掉 → 用户听感"只有开始一秒有声音"。
   * 现在：① 上限**按振荡器预算算**（见下）；② 按轨限额，超了就**丢力度最小的那个**
   * 而不是直接丢新音（新音更靠前、更该被听见）；③ 回收时间用真实时长算。
   *
   * ⚠ 2026-09-16 再修（用户："听 midi 感觉声音怪怪的一些还卡卡的"）：
   *   旧上限是"96 个音"，但**每个音要 2~4 个振荡器**（`FAMILIES[*].harm`）——
   *   最坏 **384 个振荡器**同时活着，浏览器音频线程直接过载 → 卡顿/爆音/音符错位。
   *   用户那首是 9 轨编配（每轨上限 24 音 = 216 音 × 平均 3 振荡器 ≈ 650 个）。
   *   现在改成按**振荡器预算**限流：全局 160 个振荡器、每轨 40 个。
   *   换算成音数大约"全局 ~55 音 / 每轨 ~13 音"，与真实回放需求相当。 */
  const want = Math.max(0.05, Math.min(3, dur));
  const OSC_BUDGET = 160;        // 全局振荡器预算（不是音数）
  const OSC_PER_TRACK = 40;      // 每轨预算
  // **并发保护**（不是"拒绝新音"）：到上限时**停掉最弱/最老的**再发新的。
  let guard = 0;
  while ((S.liveOsc || 0) >= OSC_BUDGET && guard++ < 8) {
    let victim = null;
    for (const [k, v] of S.live) {
      if (!victim || v.v <= victim[1].v) victim = [k, v];
    }
    if (!victim) break;
    try { victim[1].stop(); } catch (e) { /* 已停 */ }
    S.live.delete(victim[0]);
    S.liveOsc = Math.max(0, (S.liveOsc || 0) - (victim[1].osc || 0));
  }
  // 同轨也限一次（避免某一轨（比如鼓）把全局预算吃光）
  if (trackIdx != null) {
    let tOsc = 0;
    for (const [, v] of S.live) if (v.t === trackIdx) tOsc += (v.osc || 0);
    if (tOsc >= OSC_PER_TRACK) {
      let victim = null;
      for (const [k, v] of S.live) {
        if (v.t === trackIdx && (!victim || v.v <= victim[1].v)) victim = [k, v];
      }
      if (victim) {
        try { victim[1].stop(); } catch (e) { /* 已停 */ }
        S.live.delete(victim[0]);
        S.liveOsc = Math.max(0, (S.liveOsc || 0) - (victim[1].osc || 0));
      }
    }
  }
  // 发声：鼓走鼓组合成，其余走向乐器族（多振荡器 + 滤波 + ADSR + 混响）
  const prog = (trackIdx != null && S.model.tracks[trackIdx])
    ? S.model.tracks[trackIdx].program : null;
  const v = drum
    ? drumVoice(w, at, want, pitch, vel)
    : voice(w, at, want, 440 * Math.pow(2, (pitch - 69) / 12), vel,
            familyOf(prog, false));
  S.pt = S.pt || { n: 0, made: 0, drum: 0, guard: 0 };
  S.pt.n++;
  S.pt.made += (v && v.made) || 0;
  if (drum) S.pt.drum++;
  if (guard > 0) S.pt.guard++;
  try { window.__PT = S.pt; } catch (e) { /* 无 window 环境 */ }
  // ⚠ 振荡器计数必须在**所有**回收路径上减掉（`victim.stop()` / 兜底 setTimeout /
  //   正常播放结束），否则 `S.liveOsc` 会只增不减 → 后面每个音都被预算拦掉（"只有开头有声音"）。
  const oscN = (v && v.made) || 1;
  const drop = () => {
    if (S.live.get(key) === entry) {
      S.live.delete(key);
      S.liveOsc = Math.max(0, (S.liveOsc || 0) - oscN);
    }
  };
  const entry = { t: trackIdx == null ? -1 : trackIdx, v: vel, osc: oscN,
                  stop: () => {
                    try { v.stop(); } catch (e) { /* 已停 */ }
                    drop();
                  } };
  S.live.set(key, entry);
  S.liveOsc = (S.liveOsc || 0) + oscN;
  setTimeout(drop, Math.max(200, (want + 1.2) * 1000));   // 兜底回收（留出包络释放时间）
}
/* --- 播放时钟：两种音源共用一个抽象 -----------------------------------------
 * `synth`（WebAudio 合成）用 `startedAt` 当基准；`media`（渲染音频）用 `player.currentTime`。
 * 统一成 `(anchorBeat, anchorT)` 一对锚点：拍位 = anchorBeat + (t − anchorT) / 秒每拍。
 * 换音源 / seek / 滚屏 都只改锚点，位置天然连续（这是前面几个坑的通用解）。 */
const SPB = () => 60 / ((S.model && S.model.bpm) || 120);
function setAnchor(beat, t) { S.anchorBeat = beat; S.anchorT = t; }
function clockTime() {
  if (S.audioMode === 'media' && S.mediaEl) return S.mediaEl.currentTime;
  if (S.audioMode === 'scratch' && S.scratch) return S.scratch.currentTime;
  return (S.ctx && S.ctx.currentTime) || 0;
}
function tickPlay() {
  if (!S.playing) return;
  S.tickCount = (S.tickCount || 0) + 1;
  S.raf = requestAnimationFrame(tickPlay);
  if (S.audioMode === 'synth') {
    const w = S.ctx;
    if (!w || w.state !== 'running') return;
    S.posBeat = S.anchorBeat + (w.currentTime - S.anchorT) / SPB();
    scheduleWindow(S.posBeat, S.posBeat + 1.5, w.currentTime - (S.posBeat - S.x0) * SPB());
  } else {                                  // 音频/试听毛片：位置直接跟媒体时钟走
    S.posBeat = S.anchorBeat + (clockTime() - S.anchorT) / SPB();
  }
  const hasLoop = S.loopA != null && S.loopB != null && S.loopB > S.loopA;
  if (hasLoop && S.posBeat >= S.loopB - 1e-9) {
    setAnchor(S.loopA, clockTime());
    if (S.audioMode === 'media' && S.mediaEl) S.mediaEl.currentTime = S.loopA * SPB();
    else if (S.audioMode === 'scratch' && S.scratch) S.scratch.currentTime = S.loopA * SPB();
    renderRoll();
    return;
  }
  if (!hasLoop && S.posBeat > endBeat()) {
    if ($('loopChk').checked) {
      setAnchor(S.x0, clockTime());
      if (S.audioMode === 'media' && S.mediaEl) S.mediaEl.currentTime = S.x0 * SPB();
    } else return stopPlay();
  }
  const tot = S.totalSec || 0;
  $('posInfo').textContent = S.posBeat.toFixed(1) + ' / ' + endBeat().toFixed(1) + ' 拍 · '
    + fmtTime(S.posBeat * SPB()) + ' / ' + fmtTime(tot)
    + (hasLoop ? ('  · 循环 ' + S.loopA.toFixed(1) + '~' + S.loopB.toFixed(1)) : '');
  const f = Math.max(0, Math.min(1, S.posBeat / Math.max(1e-6, endBeat())));
  $('seek').value = String(Math.round(f * 1000));
  $('seekInfo').textContent = fmtTime(S.posBeat * SPB());
  const winBeats = Math.max(8, ($('roll').clientWidth || 1000) / S.ppb);
  if (!hasLoop && S.posBeat > S.x0 + winBeats * 0.85) {
    const nowMs = Date.now();
    if (!S.lastScroll || nowMs - S.lastScroll > 200) {
      S.lastScroll = nowMs;
      const x0new = Math.max(0, S.posBeat - winBeats * 0.34);
      setAnchor(S.posBeat, clockTime());       // 位置连续：滚屏只改锚点，不改位置
      S.x0 = x0new;
    }
  }
  renderRoll(); renderVel();
}
/* 音频模式的播放（媒体元素）；被浏览器拦下时回退到合成音 */
async function mediaPlay() {
  const el = S.mediaEl;
  if (!el) return false;
  try {
    el.currentTime = Math.max(0, S.posBeat * SPB());
    await el.play();
  } catch (e) {
    S.audioMode = 'synth';
    log('浏览器拦住了音频播放（需要一次点击）→ 先用合成音播放；再点一次「▶ 播放」即可用真音源');
    updateAudioInfo();
    return false;
  }
  setAnchor(S.posBeat, el.currentTime);
  return true;
}
function fmtTime(sec) {
  sec = Math.max(0, sec || 0);
  const m = Math.floor(sec / 60), s = sec - m * 60;
  return m + ':' + (s < 10 ? '0' : '') + s.toFixed(1);
}
function seek(beat, center) {
  S.posBeat = Math.max(0, Math.min(endBeat(), beat));
  // 视窗跟随：**定位后播放头必须看得见**（第一版只改了 posBeat/进度条，卷帘不重画、
  // 视窗也不动 —— 用户在卷帘里根本看不到"跳到哪了"，等同于没定位）
  if (center !== false && S.model) {
    const w = ($('roll').clientWidth || 1000);
    S.x0 = Math.max(0, S.posBeat - (w / S.ppb) * 0.25);
  }
  setAnchor(S.posBeat, clockTime());    // 多音源：锚点跟着新位置走（音频与合成音都即时对齐）
  rebaseClock(S.ctx);
  if (S.audioMode === 'media' && S.mediaEl) S.mediaEl.currentTime = S.posBeat * SPB();
  else if (S.audioMode === 'scratch' && S.scratch) S.scratch.currentTime = S.posBeat * SPB();
  updateSeekUI();
  renderRoll(); renderVel();
}
function updateSeekUI() {
  if (!S.model) { $('seekInfo').textContent = '0:00.0'; return; }
  const tot = Math.max(1e-6, endBeat());
  $('seek').value = String(Math.round(Math.max(0, Math.min(1, S.posBeat / tot)) * 1000));
  $('seekInfo').textContent = fmtTime(S.posBeat * (60 / (S.model.bpm || 120)));
}
async function togglePlay() {
  if (!S.model) return log('先导入文件');
  if (S.playing) return stopPlay();
  // 有渲染好的真音源就优先用它（用户口径"比不上主界面" —— 合成音只是兜底）
  let started = false;
  if (S.audioMode === 'media' && S.mediaEl) {
    started = await mediaPlay();
  }
  if (!started) {
    const w = await ensureCtx();
    if (!w) return;
    if (w.state !== 'running') { AUDIO_BLOCKED = true; return; }   // 等用户再点一次
    AUDIO_BLOCKED = false;
    S.audioMode = 'synth';
  }
  S.playing = true;
  $('btnPlay').textContent = '⏸ 暂停';
  const hasLoop = S.loopA != null && S.loopB != null && S.loopB > S.loopA;
  if (hasLoop && (S.posBeat < S.loopA - 1e-9 || S.posBeat >= S.loopB)) seek(S.loopA, false);
  else if (!hasLoop) {
    const w = ($('roll').clientWidth || 1000);
    S.x0 = Math.max(0, (S.posBeat || 0) - (w / S.ppb) * 0.25);
    updateSeekUI(); renderRoll();
  }
  setAnchor(S.posBeat, clockTime());     // 播放在此锚定：两种音源同一套位置语义
  rebaseClock(S.ctx);
  setStatus(S.audioMode === 'media' ? '播放中（真音源）…' : '播放中（合成音）…');
  log('播放：从 ' + S.posBeat.toFixed(2) + ' 拍起 · ' +
      (S.audioMode === 'media' ? '真音源渲染音频' : '合成音（可点「🎧 渲染音频」换真音源）'));
  if (!S.raf) S.raf = requestAnimationFrame(tickPlay);
}
function stopPlay() {
  S.playing = false;
  $('btnPlay').textContent = '▶ 播放';
  if (S.raf) { cancelAnimationFrame(S.raf); S.raf = null; }
  if (S.mediaEl) { try { S.mediaEl.pause(); } catch (e) { /* 没在播 */ } }
  if (S.scratch) { try { S.scratch.pause(); } catch (e) { /* 没在播 */ } }
  setStatus('');
  renderRoll(); renderVel(); updateSeekUI();
}

/* ------------------------------------------------------------------ 导入 / 导出 */
/* ArrayBuffer → base64。**必须分块**：`String.fromCharCode(...u8)` 在几十 KB 以上就会
 * 撑爆调用栈（实测 29KB 的文件在 Node 里直接 RangeError/空串）—— 真实 MIDI 常见几百 KB～几 MB。 */
function b64encode(buf) {
  const u8 = new Uint8Array(buf);
  const CH = 0x8000;
  let s = '';
  for (let i = 0; i < u8.length; i += CH) {
    s += String.fromCharCode.apply(null, u8.subarray(i, Math.min(u8.length, i + CH)));
  }
  return btoa(s);
}

async function doImport(file) {
  const buf = await file.arrayBuffer();
  const b64 = b64encode(buf);
  const r = await post('/api/ed/import', { name: file.name, data_b64: b64 });
  S.eid = r.eid; S.model = r.model; S.summary = r.summary;
  S.selTracks.clear(); S.selNotes = []; S.undo = []; S.redo = [];
  S.lowPitch = 36; S.highPitch = 84; S.x0 = 0; S.posBeat = 0; S.dirty = false;
  S.scheduled.clear();
  const ps = allNotes().map(n => n[2]);
  if (ps.length) { S.lowPitch = Math.max(0, Math.min(...ps) - 2); S.highPitch = Math.min(127, Math.max(...ps) + 2); }
  renderAll(); updateUndoUI();
  log('导入 ' + file.name + '：' + r.summary.tracks.length + ' 轨 / ' + r.summary.stats.notes + ' 音符 / ' +
      (r.summary.bpm || 0).toFixed(1) + ' BPM / ' + (r.summary.timesig || []).join('/'));
  setStatus('已导入 ' + file.name);
}
async function doExport(fmt) {
  if (!S.model) return log('先导入文件');
  setStatus('导出中…');
  try {
    const r = await post('/api/ed/export?eid=' + encodeURIComponent(S.eid), { model: S.model, fmt });
    const rt = r.roundtrip || {};
    $('exportInfo').innerHTML =
      '<div class="row"><b>Type ' + r.fmt + '</b> · ' + r.bytes + ' 字节</div>' +
      '<div class="row ' + (rt.ok ? 'ok' : 'bad') + '">往返校验 ' + (rt.ok ? '一致' : '不一致') +
      '（严格 ' + rt.exact + ' 轨 / 听感等价 ' + rt.net + ' 轨，' + rt.notes + ' 音符）</div>' +
      (rt.bad && rt.bad.length ? '<div class="row bad">' + rt.bad.join('<br>') + '</div>' : '') +
      '<div class="row"><a class="link" href="' + r.url + '">⬇ 下载 .mid</a></div>';
    setStatus('导出完成');
    log('导出 Type ' + r.fmt + ' → ' + r.file + '（' + r.bytes + ' 字节，往返 ' +
        (rt.ok ? '一致' : '不一致') + '）');
  } catch (e) { setStatus('导出失败'); log('!! 导出失败：' + e.message); }
}

/* 编辑模式：默认**只读**（载入别人的 .mid 时不该一碰就改），点「🔒 只读」切换成可编辑。
 * 只读时卷帘不响应鼠标编辑（浏览/播放/缩放/循环照常），工具条上的编辑按钮也会被拦下。 */
function setEditMode(on) {
  S.editMode = !!on;
  const b = $('btnEdit');
  b.textContent = S.editMode ? '✏️ 编辑中' : '🔒 只读';
  b.classList.toggle('on', S.editMode);
  b.classList.toggle('lock', !S.editMode);
  document.body.classList.toggle('editing', S.editMode);
  setStatus(S.editMode ? '编辑已解锁' : '只读（点右上「🔒 只读」解锁编辑）');
  renderRoll();
}
function needEdit(what) {
  if (S.editMode) return true;
  log('现在是只读：点右上角「🔒 只读」解锁后才能' + (what || '编辑'));
  return false;
}

/* ------------------------------------------------------------------ 和弦检测 */
function renderChords() {
  const box = $('chordList');
  if (!S.chords.length) { box.innerHTML = '<span class="dim">（还没识别）</span>'; return; }
  const bar = barBeats();
  box.innerHTML = S.chords.map((c, i) => {
    const b0 = Math.round(c.from / bar) + 1, b1 = Math.round(c.to / bar);
    const sure = (c.hit || 0) >= 3 && (c.miss || 0) === 0;
    return '<span class="chord' + (sure ? ' sure' : '') + '" data-i="' + i +
      '" title="' + c.from.toFixed(2) + '~' + c.to.toFixed(2) + ' 拍 · 命中' + c.hit +
      ' 多' + c.extra + ' 缺' + c.miss + '">' +
      (b0 === b1 ? ('第' + b0 + '小节') : (b0 + '–' + b1)) + ' <b>' + c.chord + '</b></span>';
  }).join(' ');
  box.querySelectorAll('.chord').forEach(el => el.onclick = () => {
    const c = S.chords[+el.dataset.i];
    S.loopA = c.from; S.loopB = c.to;
    seek(c.from);                                    // 定位 + 视窗跟随（原来只改了 x0）
    log('定位到「' + c.chord + '」第 ' + Math.round(c.from / barBeats() + 1) + ' 小节并设为循环区间');
  });
  const named = S.chords.filter(c => c.chord && c.chord !== '-');
  const sure = named.filter(c => (c.hit || 0) >= 3 && (c.miss || 0) === 0).length;
  $('status').textContent = '和弦 ' + named.length + ' 段，其中高置信 ' + sure + ' 段';
}
async function detectChords(createTrack) {
  if (!S.model) return log('先导入文件');
  setStatus('识别和弦中…');
  try {
    const r = await post('/api/ed/chords?eid=' + encodeURIComponent(S.eid),
                         { model: S.model, create_track: !!createTrack });
    S.chords = r.chords.segments;
    if (createTrack && r.chords.track && r.chords.track.notes) {
      pushUndo();
      S.model = r.model;                   // 服务端已把和弦轨加进模型并落盘
      const t = r.chords.track;
      log('已生成和弦轨「' + t.name + '」（' + t.notes + ' 个音，通道 ' + (t.channel + 1) + '）');
    }
    renderAll(); renderChords();
    log('和弦检测：' + S.chords.length + ' 段（' +
        S.chords.slice(0, 8).map(c => c.chord).join(' ') + ' …）');
  } catch (e) { setStatus('和弦识别失败'); log('!! 和弦识别失败：' + e.message); }
}

/* ------------------------------------------------------------------ 真音源渲染（🎧）
 * 用户口径："还是比不上主界面" —— 引擎面板放的是 GeneralUser GS 渲染的真音频。
 * 这里让编辑器也能用同一条渲染管线：模型 → MIDI → `render_midi.py` → OGG → `<audio>` 播放。
 * 渲染几秒~几十秒（服务端跑），成功后切成 media 模式；失败/未渲染时无缝退回合成音。 */
function updateAudioInfo() {
  const el = $('audioInfo');
  if (!el) return;
  if (S.audioMode === 'media') {
    el.textContent = '🎧 真音源' + (S.audioSec ? '（' + S.audioSec.toFixed(1) + 's）' : '') +
      ' · 改了音符要重渲';
  } else if (S.audioMode === 'scratch') {
    el.textContent = '⚡ 试听毛片（仅听的段落，无效果链）';
  } else {
    el.textContent = '🎹 合成音（点「🎧 渲染音频」换成真音源）';
  }
}
async function loadAudio(url, sec, fp) {
  if (!S.mediaEl) {
    S.mediaEl = $('player') || document.createElement('audio');
    try { S.mediaEl.preload = 'auto'; } catch (e) { /* 替身环境 */ }
  }
  if (S.audioFp === fp && S.audioMode === 'media') return true;
  S.mediaEl.src = url;
  S.audioUrl = url; S.audioSec = sec || null; S.audioFp = fp || null;
  S.audioMode = 'media';
  stopPlay();
  updateAudioInfo();
  log('已切换到真音源（' + (sec ? sec.toFixed(1) + ' 秒' : '时长未知') + '）—— 点播放即可试听');
  return true;
}
async function renderAudio() {
  if (!S.model) return log('先导入文件');
  if (S.renderTask) return log('已经有一个渲染在进行中…');
  setStatus('渲染中（后台跑，可继续编辑/用合成音试听）…');
  log('开始渲染：当前编辑结果 → MIDI → render_midi.py（与引擎面板同一条管线）。整曲约几十秒。');
  const t0 = Date.now();
  try {
    const r = await post('/api/ed/render-audio?eid=' + encodeURIComponent(S.eid),
                         { model: S.model });
    if (r.pending) {                          // 后台任务：轮询
      S.renderTask = r.task;
      const pollUrl = '/api/ed/render-status?t=' + encodeURIComponent(S.renderTask);
      log('渲染任务已提交：' + String(S.renderTask) + '（' + pollUrl + '）');
      const poll = async () => {
        if (!S.renderTask) return;
        try {
          const st = await api(pollUrl);
          if (!st.done) {
            setStatus('渲染中… 已等 ' + (st.waited || 0).toFixed(0) + 's（可继续用合成音试听）');
            setTimeout(poll, 1500);
            return;
          }
          S.renderTask = null;
          setStatus('');
          await loadAudio(st.url, st.seconds, st.fp);
          log('渲染完成：' + (st.bytes / 1024).toFixed(0) + 'KB · ' +
              (st.seconds ? st.seconds.toFixed(1) + 's 音频' : '') +
              ' · 耗时 ' + ((Date.now() - t0) / 1000).toFixed(1) + 's —— 点播放试听真音源');
        } catch (e) {
          S.renderTask = null;
          setStatus('渲染失败（仍用合成音）');
          log('!! 渲染失败：' + e.message);
        }
      };
      setTimeout(poll, 1500);
      return { pending: true };
    }
    log('渲染完成（命中缓存）：' + (r.bytes / 1024).toFixed(0) + 'KB');
    await loadAudio(r.url, r.seconds, r.fp);
    setStatus('');
    return r;
  } catch (e) {
    setStatus('渲染失败（仍用合成音播放）');
    log('!! 渲染失败：' + (e && e.message ? e.message : String(e)));
    return { error: String(e && e.message ? e.message : e) };
  }
}

/* ------------------------------------------------------------------ 绑定 */
function bind() {
  $('btnImport').onclick = () => $('fileIn').click();
  $('fileIn').onchange = (e) => { if (e.target.files[0]) doImport(e.target.files[0]); e.target.value = ''; };
  $('btnExport').onclick = () => doExport(1);
  $('btnExport0').onclick = () => doExport(0);
  $('btnUndo').onclick = undo; $('btnRedo').onclick = redo;
  $('btnPlay').onclick = togglePlay; $('btnStop').onclick = () => { stopPlay(); seek(0); renderRoll(); };
  $('seek').oninput = () => {                       // 拖进度条 = 定位（卷帘/力度通道跟着走）
    const tot = Math.max(1e-6, endBeat());
    const b = Math.max(0, Math.min(tot, parseFloat($('seek').value) / 1000 * tot));
    seek(b);
  };
  $('qStr').oninput = () => $('qStrVal').textContent = parseFloat($('qStr').value).toFixed(2);
  $('qSwing').oninput = () => $('qSwingVal').textContent = parseFloat($('qSwing').value).toFixed(2);
  $('btnQuant').onclick = () => {
    const ti = S.selTracks.size === 1 ? [...S.selTracks][0] : null;
    return op('quantize', { grid: $('gridSel').value, strength: parseFloat($('qStr').value),
                            swing: parseFloat($('qSwing').value), quant_dur: $('qDurChk').checked,
                            track_idx: ti });
  };
  $('btnTranspose').onclick = () => {
    const ti = S.selTracks.size === 1 ? [...S.selTracks][0] : null;
    return op('transpose', { semitones: parseInt($('trSemis').value, 10) || 0, track_idx: ti });
  };
  $('btnVel').onclick = () => {
    const ti = S.selTracks.size === 1 ? [...S.selTracks][0] : null;
    return op('velocity', { mode: $('velMode').value, value: parseFloat($('velVal').value), track_idx: ti });
  };
  $('btnRamp').onclick = () => {
    const ti = S.selTracks.size === 1 ? [...S.selTracks][0] : null;
    return op('ramp_velocity', { start: 50, end: 120, track_idx: ti });
  };
  $('btnDup').onclick = () => {
    const ti = S.selTracks.size === 1 ? [...S.selTracks][0] : 0;
    return op('duplicate_track', { track_idx: ti });
  };
  $('btnDelTrack').onclick = () => {
    if (S.selTracks.size !== 1) return log('先选中恰好一条轨');
    return op('delete_track', { track_idx: [...S.selTracks][0] });
  };
  $('btnClean').onclick = () => op('clean', { mode: 'dedupe' });
  $('btnMono').onclick = () => op('clean', { mode: 'overlap' });
  $('btnChords').onclick = () => detectChords(false);
  $('btnChordTrack').onclick = () => needEdit('生成和弦轨') && detectChords(true);
  $('btnEdit').onclick = () => setEditMode(!S.editMode);
  $('btnRender').onclick = () => renderAudio();
  $('timbreSel').onchange = () => {
    S.timbre = $('timbreSel').value || 'auto';
    try { localStorage.setItem('ed.timbre', S.timbre); } catch (e) { /* 隐私模式 */ }
    log('音色 → ' + (S.timbre === 'auto' ? '自动（按 GM program 推断）' : FAMILY_CN[S.timbre] || S.timbre));
  };
  try {
    const saved = localStorage.getItem('ed.timbre');
    if (saved && (saved === 'auto' || FAMILIES[saved])) {
      S.timbre = saved; $('timbreSel').value = saved;
    }
  } catch (e) { /* 隐私模式 */ }
  document.addEventListener('keydown', (e) => {
    const tag = (e.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'select' || tag === 'textarea') return;
    if (e.code === 'Space') { e.preventDefault(); togglePlay(); }
    else if (e.key.toLowerCase() === 'q') { detectChords(false); }
    else if (e.key.toLowerCase() === 'w') { detectChords(true); }
    else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); undo(); }
    else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'y') { e.preventDefault(); redo(); }
    else if (e.key === 'Delete' || e.key === 'Backspace') {
      if (!S.selNotes.length) return;
      const byTrack = {};
      S.selNotes.forEach(s => (byTrack[s.t] = byTrack[s.t] || []).push(s.i));
      pushUndo();
      Object.entries(byTrack).forEach(([t, idx]) => { t = +t; S.model.tracks[t].notes = S.model.tracks[t].notes.filter((_, i) => !idx.includes(i)); });
      S.selNotes = [];
      renderAll(); log('删除选中音符');
    }
  });
  window.addEventListener('resize', () => { renderRoll(); renderVel(); });
  bindRoll();          // ⚠ 漏掉这两个 = 卷帘/力度通道在页面上完全没反应（冒烟测试抓到过）
  bindVelLane();
}
bind();
renderAll();
updateUndoUI();
setEditMode(false);        // 默认只读（导入别人的 .mid 时不会一碰就被改）
log('就绪：点「⬆ 导入 MIDI」选一个 .mid（文件不上传，只在本地解析）；改音符前先点右上「🔒 只读」解锁');
