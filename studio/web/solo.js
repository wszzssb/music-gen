/* solo.js —— 「独奏化」独立页（2026-10-08）
 *
 * 工序只有三步：**选一首已有的曲子 ＋ 选一件乐器 → 起一个 `render-tune-solo` 作业**。
 * 为什么独立成页（而不是塞进创作台或引擎面板）：
 *   · 创作台是"生成新曲"，独奏化是"改造已有曲子"——两件事放一个表单里用户不知道该怎么用；
 *   · 引擎面板是"调一首曲子的编配/混音"，独奏化会**产出另一个曲目**，混在它的工具行里也不对。
 * 独奏 = **一件**乐器（多件各弹各的声部是另一件事，走 `solo_instrument --instruments`）。
 */
(function () {
  function $(id) { return document.getElementById(id); }
  async function api(path, opt) {
    var r = await fetch(path, opt);
    var t = await r.text();
    try { return JSON.parse(t); } catch (e) { return { ok: false, error: t.slice(0, 300) }; }
  }
  function post(path, body) {
    return api(path, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body || {})
    });
  }
  function log(s) {
    var el = $('log');
    el.textContent = (el.textContent ? el.textContent + '\n' : '') + s;
    el.scrollTop = el.scrollHeight;
  }

  var ST = { picked: 'piano', job: null, out: '', sid: '' };

  /* ------------------------------------------------ 曲库 */
  async function loadSongs() {
    var s = await api('/api/songs');
    var list = (s && (s.songs || s.list)) || [];
    var sel = $('soloSong');
    sel.innerHTML = '';
    for (var i = 0; i < list.length; i++) {
      var o = document.createElement('option');
      o.value = list[i].id;
      o.textContent = list[i].id + (list[i].theme ? ('　[' + list[i].theme + ']') : '');
      sel.appendChild(o);
    }
    $('engineInfo').textContent = list.length ? (list.length + ' 首曲子') : '曲库是空的';
    $('engineDot').className = 'dot' + (list.length ? ' ok' : '');
  }

  /* ------------------------------------------------ 乐器（单选） */
  function bindChips() {
    var bs = $('soloChips').querySelectorAll('button.chip');
    for (var i = 0; i < bs.length; i++) {
      bs[i].onclick = function (e) {
        if (e) { e.preventDefault(); }
        var all = $('soloChips').querySelectorAll('button.chip');
        for (var j = 0; j < all.length; j++) { all[j].classList.remove('on'); }
        this.classList.add('on');
        ST.picked = this.getAttribute('data-inst');
      };
    }
  }

  /* ------------------------------------------------ 起作业 */
  async function go() {
    var sid = $('soloSong').value;
    if (!sid) { alert('先选一首曲子'); return; }
    ST.sid = sid;
    // ⚠ 输出名必须与 `solo_instrument.py` 的 `suffix` 规则逐字一致（piano → `_solo`，其余 `_solo_<名>`），
    //   否则作业跑完、产物在另一个目录，这一页会以为"没反应"。
    ST.out = sid + ((ST.picked === 'piano') ? '_solo' : ('_solo_' + ST.picked));
    log('== 开始：' + sid + ' → ' + ST.out + '（乐器 ' + ST.picked + '）');
    var d = await post('/api/job?id=' + encodeURIComponent(sid) + '&kind=render-tune-solo',
      { opts: { instrument: ST.picked, out: ST.out, no_render: $('noRender').checked } });
    if (!d.ok) { log('!! 起任务失败：' + (d.error || '未知')); return; }
    ST.job = d.job;
    $('btnGo').disabled = true;
    $('goHint').textContent = '运行中…';
    poll();
  }
  async function poll() {
    if (!ST.job) { return; }
    var d = await api('/api/job?id=' + encodeURIComponent(ST.job));
    if (!d.ok) {
      log('!! 取任务状态失败：' + (d.error || '未知') + '（服务重启过的话任务表会丢，重跑一次即可）');
      done(); return;
    }
    if (d.log) { $('log').textContent = d.log; $('log').scrollTop = $('log').scrollHeight; }
    var st = (d.job && d.job.state) || '';
    if (st === 'running') { setTimeout(poll, 800); return; }
    var rc = (d.job && d.job.rc);
    if (rc === 0 || rc === null) {
      log('== 完成 → 新曲目「' + ST.out + '」已加进曲库（回左边点它试听）');
      $('goHint').textContent = '完成：' + ST.out;
      await loadSongs();
      $('soloSong').value = ST.out;      // 直接选中新产物，方便回去试听
    } else {
      log('!! 任务失败（rc=' + rc + '）：看上面的日志');
      $('goHint').textContent = '失败（rc=' + rc + '）';
    }
    done();
  }
  function done() {
    ST.job = null;
    $('btnGo').disabled = false;
  }

  /* ------------------------------------------------ 入口 */
  bindChips();
  $('btnGo').onclick = go;
  $('btnRefresh').onclick = function () { loadSongs(); log('曲库已刷新'); };
  loadSongs();
})();
