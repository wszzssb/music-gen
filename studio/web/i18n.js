/* i18n.js —— 面板的中/英切换（引擎面板 + MIDI 编辑器共用）
 *
 * 设计（刻意做成"最小侵入"）：**中文就是 HTML 里的原文**，切英文时按字典把文本节点
 * 与 title/placeholder 替换掉；切回中文只需 `location.reload()`（原文自己回来）。
 * 于是：
 *   · **不动任何现有 HTML/JS 结构** —— 回归脚本 `smoke_ui.js` 只加载 `engine.js`+`app.js`，
 *     压根不碰这个文件；`browser_check.js` 在中文系统下语言仍是 zh，界面一字不变。
 *   · app.js / ed.js **动态填充**的内容也盖得住：用 MutationObserver 持续替换。
 *   · 语言选择存 localStorage；首次按 `navigator.language` 自动判（中文系统 = 中文）。
 *
 * 切换按钮浮在右上角（`🌐 EN` / `🌐 中文`），点一下切语言并刷新。
 *
 * 覆盖范围（**有硬检查，别凭印象**）：
 *   · 静态文案 = `scripts/i18n_check.py` 抓 index.html / ed.html 里所有含汉字的文本节点
 *     与 title/placeholder，逐条比对 DICT，缺一条就退出码 1。
 *   · 运行时动态文案 = 音轨卡（`338 音` / `音色` / `🎧 试听`）、曲目下拉（`12_velvet_light
 *     · 108BPM · 64小节 · 有成品`）、段落条、测量提示等，走下面的 DICT 词条 + REGEX 模式。
 *   · 真浏览器验收 = `studio/tools/smoke_i18n.js`（headless Edge + CDP，切 en 后统计
 *     界面上还剩多少汉字）。
 * **仍未覆盖**：app.js / ed.js 写进**日志区**的整句、以及 canvas 里 `fillText` 画的文字
 * （如编辑器空态那句"先导入一个 .mid 文件"）—— 前者是随状态拼的句子，后者压根不是 DOM
 * 文本，要翻得改那两个文件本身，暂不做。`smoke_i18n.js` 会把这类残留单独列出来。
 */
(function () {
  'use strict';

  var DICT = {
    /* ── 引擎面板 index.html：按钮与标签 ── */
    'BGM Studio — 按音轨制作 / 可视化编曲 / 合并导出': 'BGM Studio — track-based production / visual arrangement / export',
    '✅ 校验': '✅ Check',
    '✨ 渲染+调参': '✨ Render + autotune',
    '↩ 写回 song.json': '↩ Write back song.json',
    '↻ 刷新': '↻ Refresh',
    '↻ 重新测量': '↻ Re-measure',
    '⏹ 停止': '⏹ Stop',
    '▶ 播放': '▶ Play',
    '▶ 开始搜索': '▶ Start search',
    '⚡ 试听本段': '⚡ Preview this section',
    '➕ 新建': '➕ New',
    '⬇ 载入分轨': '⬇ Load stems',
    '主混音': 'Master mix',
    '分轨混音': 'Stem mix',
    '参考曲': 'Reference',
    '循环': 'Loop',
    '整曲': 'Full song',
    '音轨': 'Tracks',
    '混音台': 'Mixer',
    '段落时间轴': 'Section timeline',
    '钢琴卷帘': 'Piano roll',
    '指标': 'Metrics',
    '日志': 'Log',
    '段': 'Sec',
    '并行': 'Workers',
    '速度档': 'Search budget',
    '标准（8 小节 / 48 次 / 120s）': 'Standard (8 bars / 48 renders / 120s)',
    '细致（16 小节 / 96 次 / 300s）': 'Thorough (16 bars / 96 renders / 300s)',
    '快（4 小节 / 24 次 / 60s）': 'Fast (4 bars / 24 renders / 60s)',
    '引子 0-3s': 'Intro 0-3s',
    '安静段 6-20s': 'Quiet 6-20s',
    '主体段 45-90s': 'Main 45-90s',
    '尾奏 93-99s': 'Outro 93-99s',
    '搜索结果会显示在这里（每行一个候选：mad = 对齐带平均差，越小越像）。':
      'Search results appear here (one candidate per line; mad = mean abs. band diff, lower = closer).',
    '（点轨名 → 卷帘高亮）': '(click a track name → highlights it in the roll)',
    '（点选一段编辑；宽度 ∝ 小节数）': '(click a section to edit; width ∝ bar count)',
    '（拖拽=移动，空白处拖=新建，右键=删除；只对 Melody 可编辑）':
      '(drag=move, drag empty=new, right-click=delete; only Melody is editable)',
    '（绝对倍频程 + 占用率）': '(absolute octave bands + occupancy)',
    '（分轨实时混音 → 写回 song.json → 渲染即成合并成品）':
      '(real-time stem mix → write back song.json → render becomes the final mix)',
    '（只渲染 8 小节切片 + 多路并行 → 秒级/轮；用指标当适应度）':
      '(renders only 8-bar slices, multi-process → seconds per round; metrics as fitness)',
    /* 顶栏按钮与标签（覆盖率检查查出来的漏项） */
    '🎹 MIDI 编辑器': '🎹 MIDI editor',
    '📂 打开': '📂 Open',
    '💾 保存': '💾 Save',
    '🎼 作曲': '🎼 Compose',
    '🔊 渲染': '🔊 Render',
    '📦 合并导出': '📦 Merge & export',
    '🎚 分轨导出': '🎚 Export stems',
    '🎹 和弦表': '🎹 Chord table',
    '🔊 以当前混音渲染': '🔊 Render current mix',
    '🎚 自动配平': '🎚 Auto-balance',
    '🧬 候选搜索': '🧬 Candidate search',
    '时间窗': 'Time window',

    /* ── 引擎面板：title / placeholder ── */
    'MIDI 编辑器：导入任意 .mid → 钢琴卷帘/量化/移调/力度 → 导出':
      'MIDI editor: import any .mid → piano roll / quantize / transpose / velocity → export',
    'check_song.py：和弦音集/强拍/通道等数据契约': 'check_song.py: chord-set / downbeat / channel data contract',
    'make_song --no-tune：作曲+渲染（约 30 秒）': 'make_song --no-tune: compose + render (~30 s)',
    'make_song：渲染+自动调参（2~3 分钟）': 'make_song: render + autotune (2-3 min)',
    '保存 + 渲染（合并成品）': 'Save + render (final mix)',
    '停止': 'Stop',
    '写回 song.json（规范格式）': 'Write back song.json (canonical format)',
    '只渲染当前段落切片（约 1 秒）并循环播放 —— 改一版立刻听':
      'Render only the current section slice (~1 s) and loop it — hear a change instantly',
    '听参考曲（同位置 A/B）': 'Hear the reference (A/B at the same position)',
    '听渲染好的成品': 'Hear the rendered mix',
    '打开目录或 MIDI：目录→当曲库（含 songs/、或 <曲目>/song.json、或本身就是一首）；.mid→在编辑器里打开':
      'Open a folder or MIDI: folder → use as library (with songs/, or <song>/song.json, or a song itself); .mid → open in editor',
    '把 MIDI/OGG/WAV/notes 拷到导出目录 = 合并交付':
      'Copy MIDI/OGG/WAV/notes to the export dir = deliver',
    '把推子/声像写回 song.json 的 mix': 'Write faders/pan back into song.json mix',
    '拖动波形定位；Shift+拖 = 设循环区间': 'Drag the waveform to seek; Shift+drag = set loop range',
    '按分轨解方程配平每轨音量：解 → 真渲染 → 实测 → 更好才留（会自动回滚）':
      'Solve per-stem gain, render for real, measure, keep only if better (auto-rollback)',
    '播放（空格）': 'Play (space)',
    '编辑和弦表（名字必须能被 song_engine 校验）': 'Edit the chord table (names must pass song_engine)',
    '脚手架：新建曲目（new_song.py）': 'Scaffold: create a new song (new_song.py)',
    '这个曲目目录里的 .mid（含中间产物）——选一个就在 MIDI 编辑器里打开':
      '.mid files in this song folder (incl. intermediates) — pick one to open in the MIDI editor',
    '选择曲目': 'Choose song',
    '逐轨渲染成 OGG（bridge stems），缓存到临时目录': 'Render each track to OGG (bridge stems), cached in temp',
    '逐轨渲染成 OGG（合并前的一轨一文件）': 'Render each track to OGG (one file per track before mixing)',
    '分轨实时混音（先': 'Real-time stem mixing (first',

    /* ── MIDI 编辑器 ed.html：按钮与标签 ── */
    'MIDI Studio — 导入 / 编辑 / 导出': 'MIDI Studio — import / edit / export',
    '⬆ 导入 MIDI': '⬆ Import MIDI',
    '⬇ 导出 .mid': '⬇ Export .mid',
    '⚡ 量化': '⚡ Quantize',
    '✂ 修剪重叠': '✂ Trim overlaps',
    '⧉ 复制轨': '⧉ Duplicate track',
    '人性化抖动': 'Humanize',
    '力度': 'Velocity',
    '半音': 'Semitones',
    '吉他': 'Guitar',
    '钢琴': 'Piano',
    '弦乐': 'Strings',
    '铺垫': 'Pad',
    '拨弦': 'Pluck',
    '纯净': 'Pure',
    '自动': 'Auto',
    '同时量化时值': 'Quantize durations too',
    '吸附到网格': 'Snap to grid',
    '和弦': 'Chords',
    '夹取到区间': 'Clamp to range',
    '导出预览': 'Export preview',
    '应用': 'Apply',
    '强度': 'Strength',
    '摇摆': 'Swing',
    '文件信息': 'File info',
    '移调': 'Transpose',
    '轨道': 'Tracks',
    '音符属性': 'Note properties',
    '+ 偏移': '+ offset',
    '= 设为': '= set',
    '× 缩放': '× scale',
    '1/8 三连': '1/8 triplet',
    '1/16 三连': '1/16 triplet',
    '（导出后显示往返校验结果）': '(round-trip check appears after export)',
    '（拖=移动 · 右缘拖=改时值 · 空白拖=新建 · 右键=删除 · 滚轮=缩放 · Alt+拖=力度）':
      '(drag=move · right edge=duration · empty=new · right-click=delete · wheel=zoom · Alt+drag=velocity)',
    '（点选 → 下面的卷帘只显示它；Ctrl+点可多选）':
      '(click → roll shows only it; Ctrl+click to multi-select)',
    '（识别结果 / 每小节）': '(detection result / per bar)',
    '（还没导入文件）': '(no file imported yet)',
    '（选中音符后这里可以精确改数值）': '(select notes to edit exact values here)',
    /* 工具条按钮与标签（覆盖率检查查出来的漏项） */
    '🎧 渲染音频': '🎧 Render audio',
    '🎹 合成音': '🎹 Synth timbre',
    '🎛 引擎面板': '🎛 Engine panel',
    '🔒 只读': '🔒 Read-only',
    '网格': 'Grid',
    '📈 渐变': '📈 Ramp',
    '🎵 和弦检测': '🎵 Detect chords',
    '🎼 生成和弦轨': '🎼 Make chord track',
    '🗑 删轨': '🗑 Delete track',
    '🧹 去重': '🧹 Dedupe',
    '（点「🎵 和弦检测」按小节识别；🎼 可写成一条和弦轨）':
      '(click 🎵 Detect chords to scan per bar; 🎼 writes a chord track)',

    /* ── MIDI 编辑器：title ── */
    '修剪同音高重叠（后音截断前音）': 'Trim same-pitch overlaps (later note cuts the earlier one)',
    '删除当前轨': 'Delete current track',
    '力度通道：拖竖直条改力度，与卷帘同步': 'Velocity lane: drag bars to set velocity, synced with the roll',
    '去掉完全重复的音': 'Remove exact duplicate notes',
    '合成音色：自动 = 按每个音的 GM 乐器选；也可强制成一种':
      'Synth timbre: Auto = per-note GM instrument; you can also force one',
    '回到引擎面板（作曲/渲染/混音/指标）': 'Back to the engine panel (compose / render / mix / metrics)',
    '复制整轨': 'Duplicate the whole track',
    '对选中的音（或整轨）应用力度操作': 'Apply velocity ops to selected notes (or the whole track)',
    '导入 .mid / .midi / .kar（也可以用浏览器原生的文件选择，文件只在本地解析）':
      'Import .mid / .midi / .kar (the native file picker works too; files are parsed locally)',
    '导出 Type 0（单轨，所有轨合并）': 'Export Type 0 (single track, all merged)',
    '导出标准 MIDI（Type 1 多轨）': 'Export standard MIDI (Type 1, multi-track)',
    '把识别出的和弦写成一条新轨（不动已有轨）': 'Write detected chords into a new track (existing tracks untouched)',
    '把起点（可选时值）吸附到网格；强度 <1 = 部分量化，保住演奏呼吸感':
      'Snap onsets (and durations) to the grid; strength <1 = partial quantize, keeps the groove',
    '拖动 = 定位（也会同步卷帘与力度通道）': 'Drag = seek (also syncs the roll and velocity lane)',
    '按小节识别和弦（显示和弦名 + 置信度）': 'Detect chords per bar (shows name + confidence)',
    '按时间顺序做力度渐变（渐强/渐弱）': 'Velocity ramp over time (crescendo / diminuendo)',
    '撤销（Ctrl+Z）': 'Undo (Ctrl+Z)',
    '重做（Ctrl+Y）': 'Redo (Ctrl+Y)',
    '播放 / 暂停（空格）': 'Play / pause (space)',
    '用真实音源（GeneralUser GS，与引擎面板同一条渲染管线）把当前编辑结果渲染成音频；改了音符要重渲（几秒~几十秒）':
      'Render the current edit to audio with the real soundfont (GeneralUser GS, same pipeline as the engine panel); re-render after editing notes (seconds to a minute)',
    '选中的音（或整轨）按半音移调': 'Transpose selected notes (or the whole track) by semitones',
    '锁定 / 解锁编辑：锁定时卷帘只读（防误改），点一下进入编辑':
      'Lock / unlock editing: when locked the roll is read-only (prevents accidents); click to start editing'
  };

  /* 动态区：app.js / ed.js **运行时写进 DOM** 的词，HTML 里找不到它们
   * —— 所以 `scripts/i18n_check.py` 不拿这些条目去和 HTML 对账（否则一律误报"僵尸条目"）。
   * 出处写在注释里，改那两个文件的文案时记得回来对一眼；改漏只会**退回中文**，不会报错。 */
  var DYN = {
    /* app.js：音轨卡 / 混音台 / 产物表 */
    '音色': 'Timbre',
    '音量': 'Volume',
    '声像': 'Pan',
    '🎧 试听': '🎧 Audition',
    '▶ 试听': '▶ Audition',
    '产物': 'Artifacts',
    '大小': 'Size',
    '下载': 'Download',
    '小节数': 'Bars',
    '旋律': 'Melody',
    '每轨在此段的 CC7：': 'CC7 per track in this section:',
    '(全局)': '(global)',
    '测量中…（读音频 + 画像）': 'Measuring… (reading audio + profile)',
    '鼓组': 'Drum kit',
    /* ed.js：编辑锁状态 / 空态提示 */
    '✏️ 编辑中': '✏️ Editing',
    '编辑已解锁': 'Editing unlocked',
    '只读（点右上「🔒 只读」解锁编辑）': 'Read-only (click 🔒 Read-only at top-right to unlock)',
    '先导入 .mid': 'Import a .mid first',
    '（选中音符后这里可以精确改数值；多选时改的是第一个）':
      '(select notes to edit exact values; with a multi-selection only the first one changes)'
  };

  /* 运行时拼出来的文本：整句由 JS 生成、中间夹着数值或曲名（`12_velvet_light · 108BPM ·
     64小节 · 有成品`），精确匹配抓不住，按模式换。替换值可以是字符串（`$1`/`$2` 引捕获组）
     或函数 —— 需要按捕获内容选词时（`有成品`/`未渲染`）用函数。
     每条都注明了出处，改 app.js/ed.js 的模板时记得回来对一眼：模式失配只会**退回中文**，
     不会报错，所以漏改是静默的。 */
  var REGEX = [
    /* ed.js：播放位置 `0.0 / 0.0 拍` */
    [/^([\d.]+)\s*\/\s*([\d.]+)\s*拍$/, '$1 / $2 beats'],
    /* app.js:361 音轨卡 `${notes} 音${row.hasTrack?'':'（无音符）'}` */
    [/^(\d+) 音$/, '$1 notes'],
    [/^(\d+) 音（无音符）$/, '$1 notes (empty)'],
    /* app.js:365 音色下拉旁的 `${t in programs ? '' : ' · 预设'}` */
    [/^(.+?) · 预设$/, '$1 · preset'],
    /* app.js:407 段落条的 `${bars}小节` */
    [/^(\d+)小节$/, '$1 bars'],
    /* app.js:163 候选搜索的段落下拉 `${s.name}（${s.bars} 小节）` */
    [/^(.+?)（(\d+) 小节）$/, '$1 ($2 bars)'],
    /* app.js:51 曲目下拉 `名字  · 128BPM · 40小节 · 有成品|未渲染` */
    [/^(.+?)  · ([\d.]+)BPM · (\d+)小节 · (有成品|未渲染)$/, function (m, id, bpm, bars, done) {
      return id + '  · ' + bpm + 'BPM · ' + bars + ' bars · ' +
        (done === '有成品' ? 'rendered' : 'not rendered');
    }],
    /* app.js:50 纯音频目录 `名字  · 纯音频 · 3 个文件可试听`（源码里读到，本机曲库里没这种目录） */
    [/^(.+?)  · 纯音频 · (\d+) 个文件可试听$/, '$1  · audio only · $2 files to audition'],
    /* app.js:129 `#songInfo`：`128BPM · 40小节 · 75s · daily · ref=daily_mix`（尾段原样保留） */
    [/^([\d.]+)BPM · (\d+)小节(.*)$/, '$1BPM · $2 bars$3'],
    /* app.js:58 `#filesHint`：`曲库: D:\...` */
    [/^曲库: (.+)$/, 'Library: $1'],
    /* app.js:59 `#btnLib` 的 title（带换行） */
    [/^更改曲库目录（当前：(.+?)）\n＝含 songs\/ 的父目录；改完记住，重启仍生效$/,
      'Change library folder (current: $1)\n= parent folder containing songs/; remembered after restart'],
    /* app.js:170 `#searchEta` */
    [/^切片最长 (\d+) 小节 · 最多 (\d+) 次渲染 \/ (\d+)s（实测约 ([\d.]+)s\/次 → 预计 (\d+) 秒）$/,
      'longest slice $1 bars · up to $2 renders / $3s (~$4s each → about $5 s)']
  ];

  var ATTRS = ['title', 'placeholder'];
  var KEY = 'bgmLang';

  function detect() {
    var saved = null;
    try { saved = localStorage.getItem(KEY); } catch (e) { /* 隐私模式 */ }
    if (saved === 'zh' || saved === 'en') return saved;
    var n = (navigator.language || navigator.userLanguage || 'en');
    return /^zh/i.test(n) ? 'zh' : 'en';
  }

  var LANG = detect();

  function tr(s) {
    if (Object.prototype.hasOwnProperty.call(DICT, s)) return DICT[s];
    return Object.prototype.hasOwnProperty.call(DYN, s) ? DYN[s] : null;
  }

  /* 正则命中后，结果里可能还夹着字典里的词：`鼓组 · 预设` 走 `· 预设` 那条正则变成
   * `鼓组 · preset`，而 `鼓组` 自己没被翻（实测就这么漏了一处）。这里对**短文本**
   * （≤24 字）再补一遍整词替换；长文本不碰 —— 日志区长句本来就整句不翻，
   * 做词替换只会把中文句子弄成中英混杂。 */
  function trWords(s) {
    if (s.length > 24) return s;
    var tables = [DICT, DYN];
    for (var t = 0; t < tables.length; t++) {
      for (var k in tables[t]) {
        if (Object.prototype.hasOwnProperty.call(tables[t], k) && s.indexOf(k) >= 0) {
          s = s.split(k).join(tables[t][k]);
        }
      }
    }
    return s;
  }

  /* 按 REGEX 表换（返回 null = 没命中）；命中结果再过一遍词替换 */
  function trRe(s) {
    for (var i = 0; i < REGEX.length; i++) {
      if (REGEX[i][0].test(s)) return trWords(s.replace(REGEX[i][0], REGEX[i][1]));
    }
    return null;
  }

  /* 遍历并替换（文本节点 + title/placeholder） */
  function walk(root) {
    if (LANG === 'zh' || !root) return;
    var kids = root.childNodes || [];
    for (var i = 0; i < kids.length; i++) {
      var n = kids[i];
      if (n.nodeType === 3) {                       // 文本节点
        var raw = n.nodeValue;
        var key = raw && raw.trim();
        if (key) {
          var hit = tr(key);
          if (hit === null) hit = trRe(key);
          if (hit !== null) n.nodeValue = raw.replace(key, hit);
        }
      } else if (n.nodeType === 1) {
        var tag = n.tagName;
        if (tag === 'SCRIPT' || tag === 'STYLE') continue;
        for (var a = 0; a < ATTRS.length; a++) {
          var v = n.getAttribute && n.getAttribute(ATTRS[a]);
          if (v) { var h = tr(v); if (h !== null) n.setAttribute(ATTRS[a], h); }
        }
        walk(n);
      }
    }
  }

  /* 右上角的「🌐 EN / 🌐 中文」开关 */
  function button() {
    if (document.getElementById('langBtn')) return;
    var b = document.createElement('button');
    b.id = 'langBtn';
    b.textContent = LANG === 'zh' ? '🌐 EN' : '🌐 中文';
    b.title = LANG === 'zh' ? 'Switch to English' : '切换到中文';
    /* ⚠ 位置是踩出来的：原来贴 `top:6px;right:8px`，实测**盖住了顶栏最右端的 ⏹ 停止按钮**
     *   （`index.html:24`；headless 截图里那个按钮直接不见了）。改到右下角 —— 那里是空白
     *   与日志区边缘，不压任何控件。 */
    b.style.cssText = 'position:fixed;bottom:10px;right:10px;z-index:99999;font:12px/1.6 system-ui,' +
      'sans-serif;padding:2px 10px;border:1px solid #4c9aff;border-radius:12px;' +
      'background:#1b2233;color:#9dc3ff;cursor:pointer;opacity:.85';
    b.onmouseenter = function () { b.style.opacity = '1'; };
    b.onmouseleave = function () { b.style.opacity = '.85'; };
    b.onclick = function () {
      var next = LANG === 'zh' ? 'en' : 'zh';
      try { localStorage.setItem(KEY, next); } catch (e) { /* 忽略 */ }
      location.reload();                            // 中文是原文 → 刷新即还原
    };
    (document.body || document.documentElement).appendChild(b);
  }

  function start() {
    button();
    if (LANG === 'zh') return;                      // 中文：原文不动
    walk(document.documentElement);                 // 连 <head> 里的 <title> 一起（标签页标题也是中文）
    // app.js / ed.js 动态填充的内容也盖住
    try {
      new MutationObserver(function (ms) {
        for (var i = 0; i < ms.length; i++) {
          for (var j = 0; j < ms[i].addedNodes.length; j++) {
            var n = ms[i].addedNodes[j];
            if (n.nodeType === 1) walk(n);
            else if (n.nodeType === 3 && n.parentNode) walk(n.parentNode);
          }
        }
      }).observe(document.documentElement, { childList: true, subtree: true, characterData: true });
    } catch (e) { /* 老浏览器：至少静态部分已翻 */ }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
