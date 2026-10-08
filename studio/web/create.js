/* BGM Studio 创作台 —— 前端逻辑
 *
 * 三条纪律（都是被现有面板的坑逼出来的）：
 *   1. **不自己造音频逻辑**：这里只调 server.py 的路由，路由再调 scripts/*.py；
 *   2. **生成/提取都走后台任务**：几分钟的活不能塞进同步请求（浏览器会等到超时），
 *      一律 POST /api/job 拿 jid，再 1 秒轮询 /api/job?id=<jid> 看日志；
 *   3. **解析结果先给人看**：`ask_parse` 读错一个词不该毁掉整首歌 —— 每一项都摊开可改。
 */
(function () {
  'use strict';

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }
  function toast(msg, isErr) {
    var t = $('toast');
    t.textContent = msg;
    t.className = 'toast on' + (isErr ? ' err' : '');
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { t.className = 'toast' + (isErr ? ' err' : ''); }, 4200);
  }

  async function api(path, opt) {
    var r = await fetch(path, opt);
    var txt = await r.text();
    var j;
    try { j = JSON.parse(txt); } catch (e) {
      throw new Error('服务端返回的不是 JSON（HTTP ' + r.status + '）：' + txt.slice(0, 200));
    }
    if (j && j.ok === false && j.error) { throw new Error(j.error); }
    return j;
  }
  function post(path, body) {
    return api(path, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body || {})
    });
  }

  /* ------------------------------------------------------------ 状态 */
  var ST = { themes: [], songs: [], mode: 'fast', picked: null, jid: null, cur: '',
             themeMap: {}, bpmTouched: false };
  var STEPS = {
    'extract:fast': ['分轨', '转录 → song.json', '作曲 + 渲染（不调参）', '写提取记录 notes.md'],
    'extract:full': ['分轨', '转录 → song.json', '摊平分轨（h6_*）', '六阶段精修 + 渲染',
                     '写提取记录 notes.md'],
    'render-tune': ['作曲 + 渲染 + 自动调参'],
    'render-tune-solo': ['单乐器独奏化 + 渲染'],
    'compose': ['作曲'],
    'render': ['渲染'],
    'export': ['合并交付 → export/<曲目>/'],
    'export:stems': ['逐轨导出 OGG → export/<曲目>/stems/']
  };

  /* ---- 主题画像 → 让 BPM / 风格 / 拍号 / 时长都**看得见、改得动** -------------
   * 默认值一律取画像里量出来的数（`/api/create/themes` 带回来的 bpm/engine_style/meter/total_bars），
   * 不是猜的；"想要多少秒"反过来换算成建议 BPM（总拍数 ÷ BPM = 秒数）。 */
  function barBeats(meter) {
    var m = meter || [4, 4];
    return m[0] * 4 / (m[1] || 4);
  }
  function curTheme() { return ST.themeMap[$('genTheme').value] || {}; }
  function estSeconds(t) {
    t = t || curTheme();
    var bpm = Number($('genBpm').value) || t.bpm || 0;
    if (!bpm || !t.total_bars) { return null; }
    return t.total_bars * barBeats(t.meter) * 60 / bpm;
  }
  function renderThemeMeta(extra) {
    var t = curTheme();
    var chips = [];
    if (t.engine_style) {
      // 风格（引擎预设）**跟随主题**：`new_song` 的主题路径会把音色/织体显式写进 song.json，
      // 单改 song.json 的 `style` 字段会被那些显式值盖住（实测确认）⇒ 要换风格就换主题。
      chips.push('<span class="chip">引擎预设 <b>' + esc(t.engine_style) + '</b>（跟随主题）</span>');
    }
    if (t.meter) { chips.push('<span class="chip">拍号 <b>' + t.meter[0] + '/' + t.meter[1] + '</b></span>'); }
    if (t.total_bars) { chips.push('<span class="chip">结构 <b>' + t.total_bars + ' 小节</b></span>'); }
    var s = estSeconds(t);
    if (s) { chips.push('<span class="chip">预计时长 <b>' + Math.round(s) + ' 秒</b>（总拍数 ÷ BPM）</span>'); }
    $('themeMeta').innerHTML = chips.concat(extra || []).join('');
  }
  function onThemeChange() {
    var t = curTheme();
    if (!ST.bpmTouched) { $('genBpm').value = t.bpm || ''; }
    $('bpmHint').textContent = t.bpm
      ? ('主题实测中位 ' + t.bpm + '，区间 ' + t.bpm_p25 + '~' + t.bpm_p75)
      : '（该主题画像没记速度）';
    renderThemeMeta();
  }
  /** 想把总时长压到 `sec` 秒，BPM 得是多少（超出常用区间就只报数、不硬填） */
  function suggestBpm(sec) {
    var t = curTheme();
    if (!t.total_bars || !sec) { return null; }
    return t.total_bars * barBeats(t.meter) * 60 / sec;
  }

  /* ------------------------------------------------------------ 启动 */
  async function boot() {
    try {
      var t = await api('/api/create/themes');
      ST.themes = t.themes || [];
      var sel = $('genTheme');
      ST.themeMap = {};
      ST.themes.forEach(function (x) { ST.themeMap[x.key] = x; });
      sel.innerHTML = ST.themes.map(function (x) {
        return '<option value="' + esc(x.key) + '">' + esc(x.cn) + '（' + esc(x.key) + '）</option>';
      }).join('');
      onThemeChange();
      // ⚠ 只扫一次曲库：`loadSongs()` 内部已经取过 `/api/songs`（那是全库扫描，几十首要几秒），
      //   再单独请求一次 = 页面首屏慢一倍（2026-10-07 实测：状态栏卡在"正在连接引擎…"）。
      var songs = await loadSongs();
      $('engineDot').className = 'dot ok';
      $('engineInfo').textContent = '引擎就绪 · 曲库 ' + songs.length + ' 首';    } catch (e) {
      $('engineDot').className = 'dot err';
      $('engineInfo').textContent = '连不上引擎：' + e.message;
    }
    autoId();
  }

  async function loadSongs() {
    var s = await api('/api/songs');
    ST.songs = s.songs || [];
    $('libCount').textContent = ST.songs.length;
    $('songList').innerHTML = ST.songs.map(function (x) {
      var has = (x.mids && x.mids.length) ? 'MIDI' : (x.files ? '音频' : '空');
      return '<div class="song-item" data-id="' + esc(x.id) + '" title="' + esc(x.id) + '">' +
        '<span class="nm">' + esc(x.id) + '</span><span class="tag">' + has + '</span></div>';
    }).join('') || '<div class="empty">曲库是空的</div>';
    Array.prototype.forEach.call($('songList').querySelectorAll('.song-item'), function (el) {
      el.onclick = function () { showOuts(el.dataset.id); };
    });
    return ST.songs;
  }

  function autoId() {
    var d = new Date();
    var p = function (n) { return (n < 10 ? '0' : '') + n; };
    var id = 'ask_' + d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) +
             '_' + p(d.getHours()) + p(d.getMinutes());
    if (!$('genId').value) { $('genId').value = id; }
    if (!$('extId').value) { $('extId').value = id.replace(/^ask_/, 'ref_'); }
  }

  /* ------------------------------------------------------------ 提要求 */
  async function ask() {
    var text = $('askText').value.trim();
    if (!text) { toast('先写一句要求', true); return; }
    var b = $('btnAsk');
    b.disabled = true; b.textContent = '正在读…';
    try {
      var r = await post('/api/ask', { text: text });
      var p = r.parsed || {};
      $('askHint').textContent = '读完啦 —— 下面是它理解到的，不对就改。';
      // ⚠ **先把主题定下来再干别的**：BPM 默认值 / 拍号 / 小节数都由它决定 —— 顺序反了会拿
      //   上一个主题的小节数算"建议 BPM"（2026-10-07 实测：说"欢快的钢琴曲"，界面却按默认
      //   battle 的 80 小节算出 213 BPM，元信息也还写着旧主题）。
      //   另注：给 `select.value` 赋值**不会**触发 change，必须显式调 `onThemeChange()`。
      if (p.theme) {
        $('genTheme').value = p.theme;
        ST.bpmTouched = false;
        onThemeChange();
      }
      // chips：把"读到了什么"摊开
      var chips = [];
      (p.matched || []).forEach(function (m) {
        var cls = m.kind === 'theme' ? 'chip theme' : 'chip';
        chips.push('<span class="' + cls + '">' + esc(m.word) + ' → <b>' + esc(m.value) + '</b></span>');
      });
      (p.unknown || []).forEach(function (u) {
        chips.push('<span class="chip warn">没读懂：' + esc(u) + '</span>');
      });
      // 时长：**用 BPM 去接近**（总拍数 ÷ BPM = 秒数），不动段落结构。
      if (p.seconds) {
        var need = suggestBpm(p.seconds);
        if (need && need >= 50 && need <= 220) {
          $('genBpm').value = Math.round(need);
          ST.bpmTouched = true;
          chips.push('<span class="chip theme">要 ' + p.seconds + ' 秒 → 建议 BPM ' +
            Math.round(need) + '（已填进「速度 BPM」）</span>');
        } else if (need) {
          chips.push('<span class="chip warn">要 ' + p.seconds + ' 秒 → 需约 ' +
            Math.round(need) + ' BPM，超出常用区间；本版不改段落结构，只能接近</span>');
        }
      }
      // ⚠ **不自动勾「独奏化」**：说"钢琴曲"是想要那种风格，不等于要"整首只用钢琴"。
      //   第一版直接把它填进下拉 → 用户一句"欢快的钢琴曲"就悄悄变成了**独奏改造版**，
      //   而且成品落在另一个曲目里、界面上还看不到（2026-10-07 实测）。
      if (p.instrument) {
        var _cn = { piano: '钢琴', strings: '弦乐', ep: '电钢' }[p.instrument] || p.instrument;
        chips.push('<span class="chip warn">听到「' + esc(_cn) + '」—— 想<b>整首只用它</b>' +
          '就在「独奏化」里选；不选就按主题自己编配</span>');
      }
      $('askChips').innerHTML = chips.join('') ||
        '<span class="chip warn">一句话里没认出任何音乐词，试试「欢快的钢琴曲」这种说法</span>';
      $('askChips').classList.remove('hidden');
      // 填表单（主题已在上面定好）
      if (p.seed != null) { $('genSeed').value = p.seed; }
      if (p.energy_gain) { $('genGain').value = p.energy_gain; }
      $('gainVal').textContent = Number($('genGain').value).toFixed(2);
      $('genForm').classList.remove('hidden');
      renderThemeMeta();     // 最终 BPM（可能被"要 N 秒"改过）与元信息对齐
      var extra = (p.notes || []).join(' · ');
      if (extra) { $('askHint').textContent = extra; }
    } catch (e) {
      toast(e.message, true);
    } finally {
      b.disabled = false; b.textContent = '读一下我的要求';
    }
  }

  /* ------------------------------------------------------------ 生成 */
  async function generate() {
    var id = $('genId').value.trim();
    if (!/^[0-9A-Za-z_][0-9A-Za-z_-]{0,40}$/.test(id)) {
      toast('曲目名只能用字母/数字/下划线', true); return;
    }
    // 独奏化：从**按钮组**取（一件 = 合并成独奏；多件 = 保留声部；空 = 不做）
    var inst = picked('instChips', 'data-inst').join(',');
    var b = $('btnGen');
    b.disabled = true; b.textContent = '正在建曲目…';
    try {
      // ① 只写 song.json（快）；② 渲染交给后台任务（有日志可看）
      await post('/api/new', {
        id: id, theme: $('genTheme').value,
        seed: $('genSeed').value === '' ? null : $('genSeed').value,
        energy_gain: Number($('genGain').value),
        // **只用这几件乐器**：从按钮组取（不选 = null → 不传 `--arr-only`，按主题自动编配）
        arr_only: picked('arrChips', 'data-layer').join(',') || null,
        render: false
      });
      // 速度：与主题画像的默认值不同才写回 song.json —— 引擎就是拿 `bpm` 做 拍→秒 换算的，
      // 所以"想在多少秒内听完"这件事，改 BPM 是真的会生效（不是只显示）。
      var _t = curTheme();
      var want = Number($('genBpm').value) || 0;
      if (want && Math.abs(want - (_t.bpm || 0)) > 0.5) {
        b.textContent = '正在写入速度…';
        var cur = await api('/api/song?id=' + encodeURIComponent(id));
        if (cur && cur.song) {
          cur.song.bpm = want;
          await post('/api/song?id=' + encodeURIComponent(id), { song: cur.song });
        }
      }
      resetJob();
      // ⚠ 独奏化的产物落在**新曲目** `<曲名>_solo/`（`solo_instrument.py --out`）——
      //   产物区必须去读那一个。第一版这里传的是原曲名，于是"选了全用钢琴 → 生成完
      //   界面上什么都没有"（没有试听、没有下载，只有一个空的"操作"卡；2026-10-07 实测）。
      // ⚠ 输出名必须与 `solo_instrument.py` 的 `suffix` 规则**逐字一致**，否则面板读的是
      //   一个不存在的目录（原来写死 `_solo`，对 `ep`/`strings` 就已经对不上了 —— 2026-10-08 修）。
      //   规则：piano（GM 0）→ `_solo`；其余 → `_solo_<清单，逗号换横线，去空格，截 24>`。
      var _slug = String(inst || '').replace(/,/g, '-').replace(/\s+/g, '').slice(0, 24);
      var soloOut = id + ((!inst || inst === 'piano' || inst === '0') ? '_solo' : ('_solo_' + _slug));
      $('jobKind').textContent = '· ' + (inst ? soloOut : id);
      showSteps(inst ? 'render-tune-solo' : 'render-tune');
      var r = await post('/api/job?kind=' + (inst ? 'render-tune-solo' : 'render-tune') +
                         '&id=' + encodeURIComponent(id),
                         inst ? { opts: { instrument: inst, out: soloOut } } : {});
      watchJob(r.job, inst ? soloOut : id);
    } catch (e) {
      toast(e.message, true);
    } finally {
      b.disabled = false; b.textContent = '🎼 开始生成';
    }
  }

  /* ------------------------------------------------------------ 提取 */
  function pick(file) {
    if (!file) { return; }
    ST.picked = file;
    $('pickName').textContent = '已选：' + file.name + '（' + (file.size / 1048576).toFixed(1) + ' MB）';
    $('pickName').classList.remove('hidden');
    $('btnExtract').disabled = false;
    $('extHint').textContent = '可以开始了 —— 第一次跑要下模型/分轨，慢一点是正常的。';
    var base = file.name.replace(/\.[^.]+$/, '').replace(/[^0-9A-Za-z_-]+/g, '_').replace(/^_+|_+$/g, '');
    if (base) { $('extId').value = base.slice(0, 40); }
  }

  function fileToB64(file) {
    return new Promise(function (res, rej) {
      var fr = new FileReader();
      fr.onload = function () { res(String(fr.result).split(',')[1] || ''); };
      fr.onerror = function () { rej(new Error('读文件失败')); };
      fr.readAsDataURL(file);
    });
  }

  async function extract() {
    var id = $('extId').value.trim();
    if (!/^[0-9A-Za-z_][0-9A-Za-z_-]{0,40}$/.test(id)) {
      toast('曲目名只能用字母/数字/下划线', true); return;
    }
    if (!ST.picked) { toast('先选一个音频文件', true); return; }
    var b = $('btnExtract');
    b.disabled = true; b.textContent = '正在上传音频…';
    try {
      var b64 = await fileToB64(ST.picked);
      var up = await post('/api/upload', { id: id, name: ST.picked.name, data_b64: b64 });
      resetJob();
      $('jobKind').textContent = '· ' + id + ' · ' + (ST.mode === 'fast' ? '快速版' : '完整还原');
      showSteps('extract:' + ST.mode);
      var r = await post('/api/extract', {
        id: id, audio: up.path, mode: ST.mode, force: $('extForce').value === '1'
      });
      watchJob(r.job, id);
    } catch (e) {
      toast(e.message, true);
    } finally {
      b.disabled = false; b.textContent = '🎧 开始提取';
    }
  }

  /* ------------------------------------------------------------ 任务 */
  function showSteps(kind) {
    var names = STEPS[kind] || ['处理中'];
    $('jobSteps').innerHTML = names.map(function (n, i) {
      return '<span class="step-pill" data-i="' + i + '">' + (i + 1) + '. ' + esc(n) + '</span>';
    }).join('');
  }

  function resetJob() {
    $('jobCard').classList.remove('hidden');
    $('jobLog').innerHTML = '';
    $('jobState').textContent = '进行中…';
    $('jobClock').textContent = '';
    $('jobBar').className = 'bar indet';
    $('jobBar').firstElementChild.style.width = '';
    $('btnStopJob').disabled = false;
    ST.jid = null;
  }

  function paintLog(text) {
    var el = $('jobLog');
    var atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 30;
    el.innerHTML = String(text || '').split('\n').slice(-260).map(function (ln) {
      var cls = '';
      if (/^===/.test(ln)) { cls = 'l-step'; }
      else if (/^\s*!|失败|Error|Traceback/.test(ln)) { cls = 'l-err'; }
      else if (/✓|完成|PASS|已生成/.test(ln)) { cls = 'l-ok'; }
      return '<div class="' + cls + '">' + esc(ln) + '</div>';
    }).join('');
    if (atBottom) { el.scrollTop = el.scrollHeight; }
  }

  function markSteps(text) {
    // 任务日志里 `=== [i/N] … ===` 是现成的阶段标记，直接拿来点亮 pills
    var m = String(text || '').match(/=== \[(\d+)\/(\d+)\]/g);
    if (!m) { return; }
    var cur = parseInt(m[m.length - 1].replace(/[^\d/]/g, '').split('/')[0], 10);
    Array.prototype.forEach.call($('jobSteps').querySelectorAll('.step-pill'), function (el, i) {
      el.className = 'step-pill' + (i + 1 < cur ? ' done' : (i + 1 === cur ? ' on' : ''));
    });
  }

  var _t0 = 0;
  async function watchJob(jid, songId) {
    ST.jid = jid; ST.cur = songId || ST.cur;
    _t0 = Date.now();
    var timer = setInterval(function () {
      $('jobClock').textContent = '已跑 ' + Math.round((Date.now() - _t0) / 1000) + 's';
    }, 1000);
    try {
      while (true) {
        var r = await api('/api/job?id=' + encodeURIComponent(jid));
        paintLog(r.log);
        markSteps(r.log);
        var st = r.job.state;
        if (st !== 'running') {
          $('jobState').textContent = st === 'done' ? '✅ 完成' :
            (st === 'stopped' ? '⏹ 已停止' : '❌ 失败（看上面日志最后几行）');
          $('jobBar').className = 'bar';
          $('jobBar').firstElementChild.style.width = '100%';
          $('jobBar').firstElementChild.style.background =
            st === 'done' ? 'var(--ok)' : (st === 'stopped' ? 'var(--text-3)' : 'var(--err)');
          Array.prototype.forEach.call($('jobSteps').querySelectorAll('.step-pill'), function (el) {
            if (st === 'done') { el.className = 'step-pill done'; }
          });
          $('btnStopJob').disabled = true;
          await loadSongs();
          // 失败时曲目目录可能压根没建出来 —— 这时去读产物只会再报一个 404
          if (st === 'done') { showOuts(ST.cur); }
          toast(st === 'done' ? '做完啦：' + ST.cur : '任务结束：' + st, st !== 'done');
          break;
        }
        await new Promise(function (r2) { setTimeout(r2, 1000); });
      }
    } catch (e) {
      toast('轮询任务失败：' + e.message, true);
    } finally {
      clearInterval(timer);
    }
  }

  async function stopJob() {
    if (!ST.jid) { return; }
    try { await post('/api/stop?id=' + encodeURIComponent(ST.jid)); toast('已请求停止'); }
    catch (e) { toast(e.message, true); }
  }

  /** 合并交付：MIDI + OGG + WAV + notes + song.json → `<曲库>/export/<曲目>/`（后端既有任务类型） */
  async function exportSong(id) {
    resetJob();
    $('jobKind').textContent = '· ' + id + ' · 合并导出';
    showSteps('export');
    try {
      var r = await post('/api/job?kind=export&id=' + encodeURIComponent(id), {});
      watchJob(r.job, id);
    } catch (e) { toast(e.message, true); }
  }

  /* ------------------------------------------------------------ 产物 */
  async function showOuts(id) {
    if (!id) { return; }
    ST.cur = id;
    Array.prototype.forEach.call($('songList').querySelectorAll('.song-item'), function (el) {
      el.classList.toggle('sel', el.dataset.id === id);
    });
    $('outHint').textContent = '· ' + id;
    var box = $('outs');
    try {
      var r = await api('/api/files?id=' + encodeURIComponent(id));
      var fs = (r.files || []).filter(function (f) {
        return /\.(mid|midi|ogg|wav|mp3)$/i.test(f.name) && f.name[0] !== '_';
      });
      var hasAudio = fs.some(function (f) { return /\.(ogg|wav|mp3)$/i.test(f.name); });
      var html = '';
      // 操作卡：**不依赖有没有音频** —— 导出的是文件集合，只有 MIDI 的曲目也该能导
      // （第一版把导出按钮塞在"试听卡"里，没音频的曲目就没有出口，实测发现）。
      html += '<div class="out"><div class="t">🧰 操作</div>' +
        '<div class="meta mono">' + esc(id) + '</div>' +
        '<div class="acts"><button class="btn ghost sm" data-export="1">📦 合并导出</button>' +
        '<a class="btn ghost sm" href="/?id=' + encodeURIComponent(id) +
        '">🎛 在引擎面板打开</a>' +
        '<a class="btn ghost sm" href="/editor">🎹 MIDI 编辑器</a></div></div>';
      if (hasAudio) {
        html += '<div class="out"><div class="t">🔊 试听</div>' +
          '<div class="meta">主混音（渲染好的成品）</div>' +
          '<audio controls preload="none" src="/api/audio?id=' + encodeURIComponent(id) +
          '&kind=mix&t=' + Date.now() + '"></audio></div>';
      }
      fs.forEach(function (f) {
        var isMidi = /\.(mid|midi)$/i.test(f.name);
        html += '<div class="out"><div class="t">' + (isMidi ? '🎼 ' : '🎵 ') + esc(f.name) + '</div>' +
          '<div class="meta">' + (f.size / 1024).toFixed(0) + ' KB</div>' +
          '<div class="acts">' +
          '<a class="btn ghost sm" href="/api/dl?id=' + encodeURIComponent(id) + '&f=' +
          encodeURIComponent(f.name) + '">⬇ 下载</a>' +
          (isMidi ? '<a class="btn ghost sm" href="/editor?mid=' + encodeURIComponent(f.name) +
                    '&id=' + encodeURIComponent(id) + '">🎹 去编辑器改</a>' : '') +
          '</div></div>';
      });
      box.innerHTML = html;
      var _ex = box.querySelector('[data-export]');
      if (_ex) { _ex.onclick = function () { exportSong(id); }; }
      $('outsEmpty').classList.toggle('hidden', !!html);
      if (!html) {
        box.innerHTML = '';
        $('outsEmpty').textContent = '这首曲目还没有音频/MIDI 产物 —— 渲染一下就有。';
        $('outsEmpty').classList.remove('hidden');
      }
    } catch (e) {
      box.innerHTML = '';
      $('outsEmpty').textContent = '读产物失败：' + e.message;
      $('outsEmpty').classList.remove('hidden');
    }
  }

  /* ------------------------------------------------------------ 事件绑定 */
  // **窗口内刷新**（2026-10-08）：`创作台.cmd` 是 pywebview + WebView2 的原生窗口，它只在
  // **启动那一刻**加载 `/create` —— 写代码的人改了前端却忘了关窗重开时，看到的一直是旧页面
  // （用户实测反馈"创作台.cmd，这个页面没有改"，而服务端其实早就是新版：`/create` 已含新控件、
  // `cache-control` 也是 no-store）。这里给窗口内一个刷新入口，省掉"必须关窗重开"这一步。
  window.addEventListener('keydown', function (e) {
    if (e.key === 'F5' || ((e.ctrlKey || e.metaKey) && (e.key === 'r' || e.key === 'R'))) {
      e.preventDefault();
      location.reload();
    }
  });
  /* ------------------------------------------------------------ 点选按钮 */
  // **乐器按钮组**（2026-10-08 用户口径："能不能不让我填，改成几个按钮"）：点一下选中/取消。
  // · `arrChips` → `--arr-only`（生成时只用这几件）；一个都不选 = 不传该参数（按主题编配）
  // · `instChips` → `--instruments` / `--instrument`（独奏化）；不选 = 不做独奏化
  function bindChips(box) {
    var bs = $(box).querySelectorAll('button.chip');
    for (var i = 0; i < bs.length; i++) {
      bs[i].onclick = function (e) {
        if (e) { e.preventDefault(); }
        this.classList.toggle('on');
      };
    }
  }
  function picked(box, attr) {
    var out = [], bs = $(box).querySelectorAll('button.chip.on');
    for (var i = 0; i < bs.length; i++) { out.push(bs[i].getAttribute(attr)); }
    return out;
  }
  bindChips('arrChips');
  bindChips('instChips');
  $('btnAsk').onclick = ask;
  $('askText').addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { ask(); }
  });
  $('btnGen').onclick = generate;
  $('btnExtract').onclick = extract;
  $('btnStopJob').onclick = stopJob;
  $('btnRefresh').onclick = function () {
    loadSongs().then(function () { toast('曲库已刷新'); }, function (e) { toast(e.message, true); });
  };
  $('genGain').oninput = function () { $('gainVal').textContent = Number(this.value).toFixed(2); };
  $('genTheme').onchange = onThemeChange;
  $('genBpm').oninput = function () { ST.bpmTouched = true; renderThemeMeta(); };
  $('btnToggleLog').onclick = function () {
    var h = $('jobLog').classList.toggle('hidden');
    this.textContent = h ? '展开日志' : '收起日志';
  };
  $('drop').onclick = function () { $('fileInput').click(); };
  $('fileInput').onchange = function () { pick(this.files && this.files[0]); };
  ['dragenter', 'dragover'].forEach(function (ev) {
    $('drop').addEventListener(ev, function (e) {
      e.preventDefault(); $('drop').classList.add('over');
    });
  });
  ['dragleave', 'drop'].forEach(function (ev) {
    $('drop').addEventListener(ev, function (e) {
      e.preventDefault(); $('drop').classList.remove('over');
    });
  });
  $('drop').addEventListener('drop', function (e) {
    var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) { pick(f); }
  });
  Array.prototype.forEach.call(document.querySelectorAll('.mode'), function (el) {
    el.onclick = function () {
      Array.prototype.forEach.call(document.querySelectorAll('.mode'), function (o) {
        o.classList.remove('on');
      });
      el.classList.add('on');
      ST.mode = el.dataset.mode;
      $('extHint').textContent = ST.mode === 'fast'
        ? '快速版：约 1~2 分钟，出 MIDI + 可试听成品（不做力度/段级精修）'
        : '完整还原：内容整形 + 力度 + 段级编配 + 渲染 + 体检，十分钟级';
    };
  });

  boot();
})();
