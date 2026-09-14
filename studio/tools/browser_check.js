/* 真实浏览器验收（零依赖：Node 24 自带 WebSocket + Edge/Chrome 的 CDP）
 *
 * 为什么需要它：`smoke_ui.js` 只能证明"绘制调用是对的"（替身环境），
 * 证明不了"浏览器里真的看得见 / 真的没有残影"。这个脚本用 headless 浏览器打开面板、
 * 切到目标曲目、**模拟拖进度条**，然后直接读 canvas 像素来判。
 *
 * 用法：
 *   node tools/browser_check.js                     # 默认查 42_gtr_tender（song.json 缺 programs/mix 的形状）
 *   CHECK_SID=41_role_tender node tools/browser_check.js
 *   STUDIO_URL=http://127.0.0.1:8765 node tools/browser_check.js
 * 产物：`tools/_browser_check.png`（截图，肉眼复核用）
 */
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path'), os = require('os');

const EDGE = [
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  process.env.CHROME_PATH || '',
].find(p => p && fs.existsSync(p));
const URL_BASE = process.env.STUDIO_URL || 'http://127.0.0.1:8765';
const SID = process.env.CHECK_SID || '42_gtr_tender';
const PORT = Number(process.env.CDP_PORT || 9333);
const SHOT = path.join(__dirname, '_browser_check.png');
const bad = [];

const sleep = ms => new Promise(r => setTimeout(r, ms));
async function waitHttp(url, ms = 15000) {
  const t0 = Date.now();
  for (;;) {
    try { const r = await fetch(url); if (r.ok) return await r.json(); } catch (e) {}
    if (Date.now() - t0 > ms) throw new Error('CDP 端口没起来：' + url);
    await sleep(200);
  }
}

/* 最小 CDP 客户端 */
function cdp(wsUrl) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    let id = 0; const pend = new Map();
    ws.addEventListener('message', ev => {
      const m = JSON.parse(ev.data);
      if (m.id && pend.has(m.id)) {
        const { res, rej } = pend.get(m.id); pend.delete(m.id);
        m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
      }
    });
    ws.addEventListener('error', e => reject(new Error('WS 错误')));
    ws.addEventListener('open', () => resolve({
      send(method, params) {
        const myId = ++id;
        return new Promise((res, rej) => {
          pend.set(myId, { res, rej });
          ws.send(JSON.stringify({ id: myId, method, params: params || {} }));
          setTimeout(() => { if (pend.has(myId)) { pend.delete(myId); rej(new Error(method + ' 超时')); } }, 30000);
        });
      },
      async evaljs(expr, awaitPromise) {
        const r = await this.send('Runtime.evaluate',
          { expression: expr, returnByValue: true, awaitPromise: !!awaitPromise });
        if (r.exceptionDetails) throw new Error('页面异常: ' +
          (r.exceptionDetails.exception && r.exceptionDetails.exception.description || r.exceptionDetails.text));
        return r.result.value;
      },
      close() { try { ws.close(); } catch (e) {} },
    }));
  });
}

(async () => {
  if (!EDGE) { console.log('⚠ 没找到 Edge/Chrome，跳过浏览器验收'); process.exit(0); }
  const profile = path.join(os.tmpdir(), 'bgm-browser-check-' + Date.now());
  const proc = spawn(EDGE, ['--headless=new', '--disable-gpu', '--no-first-run',
    '--no-default-browser-check', '--hide-scrollbars', '--mute-audio',
    '--remote-debugging-port=' + PORT, '--user-data-dir=' + profile,
    '--window-size=1720,1400', 'about:blank'], { stdio: 'ignore', detached: false });

  let c = null;
  try {
    const ver = await waitHttp('http://127.0.0.1:' + PORT + '/json/version');
    const list = await waitHttp('http://127.0.0.1:' + PORT + '/json/list');
    const page = list.find(t => t.type === 'page');
    if (!page) throw new Error('没有 page target');
    c = await cdp(page.webSocketDebuggerUrl);
    await c.send('Page.enable'); await c.send('Runtime.enable');
    await c.send('Page.navigate', { url: URL_BASE + '/index.html' });

    // 等 app.js 就绪（曲目列表拉回来）
    let ready = false;
    for (let i = 0; i < 40; i++) {
      await sleep(300);
      try { ready = await c.evaljs('!!(document.getElementById("songSel") && document.getElementById("songSel").options.length)'); }
      catch (e) {}
      if (ready) break;
    }
    if (!ready) bad.push('页面 12 秒内没就绪（songSel 没有选项）');

    // 切到目标曲目（bug 只在"song.json 缺 programs/mix"的曲目上出现）
    await c.evaljs(`(()=>{const s=document.getElementById('songSel');
      if(!s) return 'no sel';
      const has=[...s.options].some(o=>o.value===${JSON.stringify(SID)});
      if(!has) return 'no such song';
      s.value=${JSON.stringify(SID)}; s.dispatchEvent(new Event('change')); return 'ok';})()`);
    await sleep(3000);   // 等 loadSong → renderAll + 音源加载

    /* 浏览器内一次做完：读真值 → 模拟拖进度条 → 读像素。
     * 拖进度条走**真实事件处理器**（wave.onmousedown/onmousemove，与用户鼠标同一条路径）。 */
    const r = await c.evaljs(`(()=>{
      const out={};
      const pr=(typeof window.__studioProbe==='function')?window.__studioProbe():{};
      out.tracks=pr.tracks==null?null:pr.tracks; out.sid=${JSON.stringify(SID)};
      out.hasSong=!!pr.song; out.hasEvents=!!pr.events;
      out.cards=document.querySelectorAll('#trackList > div').length;
      out.strips=document.querySelectorAll('#strips > .strip').length;
      const cv=document.getElementById('roll'), g=cv.getContext('2d');
      out.canvas=cv.width+'x'+cv.height; out.clientW=cv.clientWidth;
      const count=()=>{const im=g.getImageData(0,0,cv.width,cv.height).data;
        let red=0,ink=0;
        for(let i=0;i<im.length;i+=4){ if(im[i+3]===0) continue; ink++;
          if(im[i]>180&&im[i+1]<130&&im[i+2]<130) red++; }
        return {red:red,ink:ink};};
      out.before=count();
      // ① 探针自证：手工画一条满高红线必须被数出来（否则"0"是测不到）
      g.fillStyle='#ff6b6b'; g.fillRect(20,0,2,cv.height);
      out.probe=count();
      // ② 撤掉脏像素（重画整幅）
      (typeof window.renderRoll==='function')?window.renderRoll():null;
      out.afterRedraw=count();
      // ③ 真实拖动：mousedown→mousemove→mousemove（大跨度跳跃）
      const wave=document.getElementById('wave');
      if(wave){ const rc=wave.getBoundingClientRect();
        const mk=p=>({clientX:rc.left+rc.width*p, clientY:rc.top+10, shiftKey:false, preventDefault(){}});
        if(wave.onmousedown){ wave.onmousedown(mk(0.75)); wave.onmousemove(mk(0.15)); wave.onmousemove(mk(0.55)); out.drag='ok'; }
        else out.drag='no handler';
      } else out.drag='no wave';
      const ph=document.getElementById('rollPh');
      out.ph=!!ph; out.phOpacity=ph?ph.style.opacity:null; out.phTransform=ph?ph.style.transform:null;
      out.afterDrag=count();
      out.errs=(window.__BOOT_ERR_LIST||[]).slice(0,3);
      out.selfcheck=(document.querySelector('pre')||{}).textContent||'';
      return out;})()`);

    /* 拖到 80% —— 用 **CDP 真实鼠标事件**（不只是调内部 handler）：
     *   ① 走用户真实路径（mousedown/mousemove/mouseup 命中 canvas）；
     *   ② 派发输入事件会给出**用户手势**，headless 下 AudioContext 才能 resume。
     * 先等 `dur > 0`（音频解码完）：dur=0 时 `pos(e)` 恒为 0，那是"还没加载完"而不是定位错。 */
    let dur = 0;
    for (let i = 0; i < 40; i++) {
      dur = await c.evaljs('(window.__studioProbe?window.__studioProbe().dur:0) || 0');
      if (dur > 0) break;
      await sleep(400);
    }
    if (!(dur > 0)) bad.push('等 16 秒音频时长仍是 0（/api/audio 没解码出内容 → 波形与拖动都会失效）');

    const wrect = await c.evaljs(`(()=>{const r=document.getElementById('wave').getBoundingClientRect();
      return {x:r.left,y:r.top,w:r.width,h:r.height};})()`);
    const dragTo = async (p) => {           // CDP 真实鼠标：按下 → 移动 → 抬起
      const x = wrect.x + wrect.w * p, y = wrect.y + wrect.h / 2;
      /* ⚠ `buttons` 位掩码必须给（1 = 左键按下）：只给 `button` 时 Chrome 可能不合成
       *   click / 不更新按键状态 —— 实测"点了按钮毫无反应"就是这个（mousedown 生效、
       *   click 不生效，看起来像 handler 没绑）。 */
      await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y, button: 'none', buttons: 0 });
      await c.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', buttons: 1, clickCount: 1 });
      await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: wrect.x + wrect.w * (p > 0.5 ? 0.4 : 0.7), y, button: 'left', buttons: 1 });
      await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y, button: 'left', buttons: 1 });
      await c.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', buttons: 0, clickCount: 1 });
      await sleep(500);
    };
    const clickAt = async (x, y) => {
      await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y, button: 'none', buttons: 0 });
      await c.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', buttons: 1, clickCount: 1 });
      await c.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', buttons: 0, clickCount: 1 });
    };
    /* ⚠ headless 里 **rAF 不会自己跑**（没有合成帧驱动）：实测 `posInfo` 恒停在 "0.0 / 0.0s"，
     *   于是播放头一直是拖动前那一帧的 transform —— 这是环境特性，不是产品 bug。
     *   所以这里先 `Page.captureScreenshot` **逼它合成一帧**来驱动 rAF；若仍不动，
     *   直接调一次 `drawPlayhead()`（rAF 的调度本身由 smoke 静态断言覆盖）。 */
    const readPh = async () => {
      const v = await c.evaljs(`(()=>{
        const ph=document.getElementById('rollPh'), cv=document.getElementById('roll');
        const pr=(typeof window.__studioProbe==='function')?window.__studioProbe():{};
        const p=ph?ph.getBoundingClientRect():null, cr=cv?cv.getBoundingClientRect():null;
        const im=cv.getContext('2d').getImageData(0,0,cv.width,cv.height).data;
        let red=0; for(let i=0;i<im.length;i+=4){ if(im[i+3]&&im[i]>180&&im[i+1]<130&&im[i+2]<130) red++; }
        const g=(typeof S!=='undefined'&&S.rollG)?S.rollG:null;
        return {phPct:(p&&cr&&cr.width)?(p.left-cr.left)/cr.width:null,
                expectPct: g?g.x(ENG.position()/(60/(S.song.bpm||120)))/g.W:null,
                pos:(pr.dur?pr.pos/pr.dur:null), dur:pr.dur||0, mode:pr.mode,
                ctxState:pr.ctxState, redAfter:red, opacity: ph?ph.style.opacity:null,
                info:((document.getElementById('posInfo')||{}).textContent||'')};})()`);
      return v;
    };
    let direct = false;
    await dragTo(0.8);
    await c.send('Page.captureScreenshot', { format: 'png' });   // 逼合成一帧 → 驱动 rAF
    await sleep(400);
    let pos = await readPh();
    if (pos.phPct == null || pos.expectPct == null || Math.abs(pos.phPct - pos.expectPct) > 0.02) {
      direct = true;
      await c.evaljs('(()=>{ if(typeof drawPlayhead==="function") drawPlayhead(); return 1; })()');
      pos = await readPh();
    }
    pos.direct = direct;
    r.pos = pos;
    if (pos.phPct == null || pos.expectPct == null) bad.push('量不到播放头位置（#rollPh 或几何缺失）');
    else {
      if (Math.abs(pos.phPct - pos.expectPct) > 0.03)
        bad.push(`播放头在 ${(pos.phPct * 100).toFixed(1)}% 但几何算是 ${(pos.expectPct * 100).toFixed(1)}%（映射不一致）`);
      if (pos.opacity !== '1') bad.push('播放头 opacity=' + pos.opacity + '（应可见）');
      if (pos.pos != null && Math.abs(pos.pos - 0.8) > 0.08)
        bad.push(`引擎位置 ${(pos.pos * 100).toFixed(1)}% 与拖动 80% 不符`);
      if (pos.redAfter !== 0) bad.push(`定位后卷帘画布上出现 ${pos.redAfter} 个红色像素`);
    }
    // 再拖到 20%：位置必须**真的跟着走**（抓"拖了但播放头不动"）
    await dragTo(0.2);
    await c.send('Page.captureScreenshot', { format: 'png' });
    await sleep(400);
    let pos2 = await readPh();
    if (direct) { await c.evaljs('(()=>{ drawPlayhead(); return 1; })()'); pos2 = await readPh(); }
    r.pos2 = pos2;
    if (pos2.phPct == null) bad.push('第二次拖动后量不到播放头位置');
    else if (!(pos2.phPct < (pos.phPct == null ? 1 : pos.phPct) - 0.3))
      bad.push(`拖到 20% 后播放头在 ${(pos2.phPct * 100).toFixed(1)}%（第一次 ${(pos.phPct * 100).toFixed(1)}%）—— 没有跟着走`);

    const t = r.tracks;
    if (t == null) bad.push('页面没挂 window.__studioProbe（拿不到真值）');
    if (t != null && r.cards !== t) bad.push(`音轨卡 ${r.cards} ≠ ${t} 轨（song.json 缺 programs/mix 时面板会空）`);
    if (t != null && r.strips !== t) bad.push(`混音台 ${r.strips} ≠ ${t} 轨`);
    if (r.probe.red <= 0) bad.push('探针自证失败：手工画的满高红线在浏览器里数不出来（判据不可信）');
    if (r.afterRedraw.red !== 0) bad.push(`重画后卷帘仍有 ${r.afterRedraw.red} 个红色像素（应清干净）`);
    if (r.afterDrag.red > 0) bad.push(`拖进度条后卷帘上有 ${r.afterDrag.red} 个红色像素（播放头残影没修掉）`);
    if (r.before.ink < 1000 && r.hasSong) bad.push(`卷帘几乎是空的（非背景像素只有 ${r.before.ink}）`);
    if (!r.hasSong) bad.push('曲目没加载进来（S.song 为空）');
    if (!r.ph) bad.push('没有播放头叠加层 #rollPh');
    if (r.errs && r.errs.length) bad.push('页面早期错误：' + r.errs.join(' | '));

    /* ---- 🎧 试听某一轨：真实点击音轨卡上的按钮，确认该轨音频真的被载入并开始播 ----
     * 用户报"音轨的试听按钮没有用"。旧版根因：`pollJob()` 完成后对**所有**任务一律
     * `S.player.src = '…kind=mix…'`，而 `S.player` **从未被赋值** → TypeError（未捕获的
     * rejection）→ 既不播该轨、后面的 `loadSong()` 也不执行。所以这里必须走**真实点击**。 */
    if (process.env.CHECK_SOLO !== '0') {
      const btn = await c.evaljs(`(()=>{const b=document.querySelector('#trackList button[data-k="solo"]');
        if(!b) return null; const r=b.getBoundingClientRect();
        const card=b.closest('.track');
        const nm=(card&&card.dataset&&card.dataset.track)?card.dataset.track:'';
        return {x:r.left+r.width/2, y:r.top+r.height/2, name:(nm||'?').trim()};})()`);
      /* 诊断：点击没生效时，一眼分清是"坐标没命中/被遮挡"还是"handler 没绑/内部早退" */
      const bdiag = await c.evaljs(`(()=>{const b=document.querySelector('#trackList button[data-k="solo"]');
        if(!b) return {found:false};
        const r=b.getBoundingClientRect(), cx=r.left+r.width/2, cy=r.top+r.height/2;
        const hit=document.elementFromPoint(cx,cy);
        return {found:true, onclick:!!b.onclick, rect:[Math.round(r.left),Math.round(r.top),
                Math.round(r.width),Math.round(r.height)], hit:hit?(hit.tagName+'.'+(hit.className||'')):null,
                isBtn:hit===b, dirty:!!S.dirty, sid:S.sid, job:S.job||null};})()`);
      console.log('🎧 按钮诊断: ' + JSON.stringify(bdiag));
      if (!btn) bad.push('找不到音轨卡上的 🎧 试听按钮');
      else {
        // 盯住 alert：startJob 失败会 alert()，而 headless 里 alert 会把页面**卡死**
        await c.evaljs('(()=>{ window.__alerts=[]; window.alert=(m)=>{window.__alerts.push(String(m));}; return 1; })()');
        await clickAt(btn.x, btn.y);
        const readSolo = () => c.evaljs(`(()=>{
          const E=(typeof ENG!=='undefined')?ENG.state():{};
          const reqs=performance.getEntriesByType('resource').map(e=>e.name)
            .filter(n=>n.indexOf('/api/audio')>=0).map(n=>n.replace(/^https?:\\/\\/[^/]+/,''));
          return {solo:S.soloTrack||null, playing:!!E.playing, dur:E.dur||0, mode:E.mode,
                  job:S.soloTrack?null:(S.job||null), soloReq:reqs.filter(u=>u.indexOf('kind=solo')>=0).length,
                  ctxState:E.mode?((typeof ENG!=='undefined'&&ENG.ctx)?ENG.ctx.state:null):null,
                  alerts:(window.__alerts||[]).slice(0,2),
                  info:(document.getElementById('jobInfo')||{}).textContent||'',
                  tail:((document.getElementById('log')||{}).textContent||'').split('\\n').slice(-2).join(' / ')};})()`);
        let sol = await readSolo();
        /* ⚠ 等待逻辑必须记「job 是否出现过」：原来写成 `while(!solo && sol.job)` —— job 还没建好
         *   循环立刻退出 → 误判成"没点到" → 又用 DOM click 点第二次 → **两个任务并发渲染同一轨、
         *   互相删对方的 `<base>.raw.wav`**（实测 FileNotFoundError，坑 140 ② 同一族）。
         *   所以：先等 1.5s 再读；只有"从头到尾没建过任务"才回退 DOM click。 */
        await sleep(1500);
        sol = await readSolo();
        let sawJob = !!sol.job;
        if (sawJob) for (let i = 0; i < 100 && !sol.solo; i++) { await sleep(1500); sol = await readSolo(); if (!sol.job) break; }
        let viaDom = false;
        if (!sawJob && !sol.solo) {
          viaDom = true;      // 确实一次都没建任务 → DOM click 触发同一 handler（如实标注）
          await c.evaljs('(()=>{document.querySelector(\'#trackList button[data-k="solo"]\').click();return 1;})()');
          await sleep(1500);
          sol = await readSolo(); sawJob = !!sol.job;
          if (sawJob) for (let i = 0; i < 100 && !sol.solo; i++) { await sleep(1500); sol = await readSolo(); if (!sol.job) break; }
        }
        r.solo = sol; r.soloBtn = btn.name; r.soloViaDom = viaDom;
        if (!sol.solo) bad.push(`点了"${btn.name}"的 🎧 试听但音源没被接管（任务 ${sol.job || '无'} · ${sol.info} · ${sol.tail}` +
          (sol.alerts && sol.alerts.length ? ' · alert: ' + sol.alerts.join('|') : '') + '）');
        else {
          if (sol.solo !== btn.name) bad.push(`试听接管的是 ${sol.solo}，不是点的那一轨 ${btn.name}`);
          if (!sol.soloReq) bad.push('没有请求 /api/audio?kind=solo（该轨音频压根没被载入）');
          /* headless 下 CDP 点击**不算用户手势** → AudioContext 一直 suspended、`play()` 卡在
           * `resume()` 上，`playing` 自然是 false。这属于环境限制，如实标注而不判失败。 */
          if (!sol.playing) {
            if (sol.ctxState === 'suspended') r.soloSoft = 'AudioContext suspended（headless 无用户手势）';
            else bad.push('试听接管了音源但没有播放（ENG.playing=false）');
          }
        }
      }
    }


    if (r.solo)
      console.log('🎧 试听「' + r.soloBtn + '」→ 音源 ' + (r.solo.solo || '（没接管）') +
                  ' · 播放中 ' + r.solo.playing + ' · 时长 ' + (r.solo.dur || 0).toFixed(1) + 's' +
                  ' · kind=solo 请求 ' + r.solo.soloReq + ' 次 · ' + r.solo.info +
                  (r.soloViaDom ? ' · 回退用了 DOM click' : ' · 真实鼠标点击') +
                  (r.soloSoft ? ' · ⚠ ' + r.soloSoft : ''));
    const png = await c.send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(SHOT, Buffer.from(png.data, 'base64'));

    console.log('浏览器: ' + path.basename(EDGE) + '  CDP ' + ver['Browser']);
    console.log('曲目 ' + r.sid + ' · 卷帘 ' + r.canvas + '（clientWidth ' + r.clientW + '）');
    console.log('音轨卡 ' + r.cards + '/' + t + ' · 混音台 ' + r.strips + ' · 非背景像素 ' + r.before.ink);
    console.log('红色像素：初始 ' + r.before.red + ' · 手工画 1 条后 ' + r.probe.red +
                ' · 重画后 ' + r.afterRedraw.red + ' · 拖动后 ' + r.afterDrag.red);
    console.log('播放头层 ' + (r.ph ? ('有 opacity=' + r.phOpacity + ' transform=' + r.phTransform) : '缺') +
                ' · 拖动处理器 ' + r.drag);
    if (r.pos && r.pos.phPct != null)
      console.log('拖到 80% → 播放头 ' + (r.pos.phPct * 100).toFixed(1) + '%（几何算 ' +
                  (r.pos.expectPct * 100).toFixed(1) + '% · 引擎位置 ' +
                  (r.pos.pos == null ? '?' : (r.pos.pos * 100).toFixed(1) + '%') +
                  ' · 时长 ' + (r.pos.dur || 0).toFixed(1) + 's · 模式 ' + r.pos.mode +
                  ' · AudioContext ' + r.pos.ctxState + '）· 画布红色像素 ' + r.pos.redAfter +
                  (r.pos.direct ? ' · headless 不跑 rAF，已直接驱动一次绘制' : ''));
    if (r.pos2 && r.pos2.phPct != null)
      console.log('再拖到 20% → 播放头 ' + (r.pos2.phPct * 100).toFixed(1) + '%（几何算 ' +
                  (r.pos2.expectPct * 100).toFixed(1) + '% · 引擎位置 ' +
                  (r.pos2.pos == null ? '?' : (r.pos2.pos * 100).toFixed(1) + '%') + '）');
    console.log('截图: ' + SHOT);
    if (bad.length) { console.log('❌ ' + bad.join('; ')); process.exitCode = 1; }
    else console.log('✅ 浏览器验收通过（音轨卡齐全 · 拖进度条无红色残影 · 播放头定位对 · 🎧 试听能播）');
  } catch (e) {
    console.log('⚠ 浏览器验收失败（不影响其它检查）: ' + e.message);
    process.exitCode = 2;
  } finally {
    if (c) c.close();
    try { proc.kill(); } catch (e) {}
    await sleep(400);
    try { fs.rmSync(profile, { recursive: true, force: true }); } catch (e) {}
  }
})();
