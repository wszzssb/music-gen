/* 创作台（`/create`）真实浏览器验收 —— 零依赖：Node 自带 WebSocket + Edge 的 CDP。
 *
 * 为什么需要它：`node --check studio/web/create.js` 只证明**语法**对，证明不了
 * "页面在浏览器里真的跑起来、点一次按钮真的走通 前端→/api/ask→前端"。
 * 它加载真实 headless Edge，**真的点一次「读一下我的要求」**，再看 chips/表单有没有出现，
 * 并收集 console 异常与未捕获异常 —— 这几类错在静态检查里全都看不见。
 *
 * 用法（先让面板在跑：`studio\start.cmd` 或双击 `studio\创作台.cmd`）：
 *   node tools/browser_check_create.js
 *   STUDIO_URL=http://127.0.0.1:8791 node tools/browser_check_create.js
 * 产物：`tools/_create_check.png`（截图，肉眼复核排版用）
 */
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path'), os = require('os');

const EDGE = [
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  process.env.CHROME_PATH || '',
].find(p => p && fs.existsSync(p));
const URL_BASE = process.env.STUDIO_URL || 'http://127.0.0.1:8765';
const PORT = Number(process.env.CDP_PORT || 9341);
const SHOT = path.join(__dirname, '_create_check.png');
const bad = [];
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function waitHttp(url, ms = 20000) {
  const t0 = Date.now();
  for (;;) {
    try { const r = await fetch(url); if (r.ok) return await r.json(); } catch (e) {}
    if (Date.now() - t0 > ms) throw new Error('CDP 没起来: ' + url);
    await sleep(200);
  }
}

function cdp(wsUrl, onEvent) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    let id = 0; const pend = new Map();
    ws.addEventListener('message', ev => {
      const m = JSON.parse(ev.data);
      if (m.id && pend.has(m.id)) {
        const { res, rej } = pend.get(m.id); pend.delete(m.id);
        m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
      } else if (m.method && onEvent) { onEvent(m); }
    });
    ws.addEventListener('error', () => reject(new Error('WS 错误')));
    ws.addEventListener('open', () => resolve({
      send(method, params) {
        const myId = ++id;
        return new Promise((res, rej) => {
          pend.set(myId, { res, rej });
          ws.send(JSON.stringify({ id: myId, method, params: params || {} }));
          setTimeout(() => { if (pend.has(myId)) { pend.delete(myId); rej(new Error(method + ' 超时')); } }, 60000);
        });
      },
      async evaljs(expr, awaitPromise) {
        const r = await this.send('Runtime.evaluate',
          { expression: expr, returnByValue: true, awaitPromise: !!awaitPromise });
        if (r.exceptionDetails) {
          throw new Error('页面异常: ' +
            ((r.exceptionDetails.exception && r.exceptionDetails.exception.description) ||
              r.exceptionDetails.text));
        }
        return r.result.value;
      },
      close() { try { ws.close(); } catch (e) {} },
    }));
  });
}

(async () => {
  if (!EDGE) { console.log('⚠ 没找到 Edge/Chrome，跳过'); process.exit(0); }
  const profile = path.join(os.tmpdir(), 'bgm-create-check-' + Date.now());
  spawn(EDGE, ['--headless=new', '--disable-gpu', '--no-first-run', '--mute-audio',
    '--no-default-browser-check', '--hide-scrollbars',
    '--remote-debugging-port=' + PORT, '--user-data-dir=' + profile,
    '--window-size=1400,1000', 'about:blank'], { stdio: 'ignore' });

  const errs = [];
  let c = null;
  try {
    await waitHttp('http://127.0.0.1:' + PORT + '/json/version');
    const list = await waitHttp('http://127.0.0.1:' + PORT + '/json/list');
    const page = list.find(t => t.type === 'page');
    c = await cdp(page.webSocketDebuggerUrl, m => {
      if (m.method === 'Runtime.exceptionThrown') {
        const d = m.params.exceptionDetails;
        errs.push('未捕获异常: ' + ((d.exception && d.exception.description) || d.text));
      }
      if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
        errs.push('console.error: ' +
          m.params.args.map(a => a.value || a.description || '').join(' '));
      }
      if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error') {
        // 带上 URL：不然"404"看不出是谁 —— 实测引擎面板一直在要 /favicon.ico
        errs.push('log.error: ' + m.params.entry.text +
                  ' @ ' + (m.params.entry.url || '(无 url)'));
      }
    });
    await c.send('Page.enable'); await c.send('Runtime.enable'); await c.send('Log.enable');
    await c.send('Page.navigate', { url: URL_BASE + '/create' });

    // ① 等就绪：**等引擎状态落到终态**（"就绪"或"连不上"）。
    //    ⚠ 别拿"主题下拉填好了"当就绪判据（第一版就是那样，实测假 FAIL）：`/api/songs`
    //    要对全库逐个读 song.json，是首屏最慢的一步，主题填好时它往往还没回来 ——
    //    那时读 `#songList` 得到 0 条，会冤枉成"曲库列表是空的"。
    let ready = false;
    for (let i = 0; i < 60; i++) {
      await sleep(500);
      try {
        ready = await c.evaljs(
          '(()=>{const t=document.getElementById("engineInfo");' +
          'return !!(t && /就绪|连不上/.test(t.textContent));})()');
      } catch (e) {}
      if (ready) break;
    }
    if (!ready) bad.push('页面 30 秒内没落到终态（引擎状态一直停在中间态）');

    // ② 静态结构 + 引擎状态
    const stat = await c.evaljs(`(()=>{
      const q=s=>document.querySelector(s);
      return {
        title: document.title,
        themes: q('#genTheme') ? q('#genTheme').options.length : 0,
        songs: document.querySelectorAll('#songList .song-item').length,
        modes: document.querySelectorAll('.mode').length,
        engine: (q('#engineInfo')||{}).textContent,
        dot: (q('#engineDot')||{}).className,
        bpm: q('#genBpm') ? q('#genBpm').value : null,
        bpmHint: (q('#bpmHint')||{}).textContent,
        meta: (q('#themeMeta')||{}).textContent,
      };})()`);
    console.log('  标题:', stat.title);
    console.log('  主题选项:', stat.themes, '· 曲库条目:', stat.songs, '· 提取档位:', stat.modes);
    console.log('  引擎状态:', stat.engine, '（' + stat.dot + '）');
    console.log('  BPM 框:', stat.bpm, '· 提示:', stat.bpmHint);
    console.log('  主题元信息:', stat.meta);
    if (stat.themes < 10) bad.push('主题下拉只有 ' + stat.themes + ' 项');
    if (stat.songs < 1) bad.push('曲库列表是空的');
    if (stat.modes !== 2) bad.push('提取档位不是 2 个（实际 ' + stat.modes + '）');
    if (!/就绪/.test(stat.engine || '')) bad.push('引擎状态没变成「就绪」：' + stat.engine);
    // 三个"可见可改"的维度（BPM / 风格 / 时长）必须真的在界面上有依据地填出来
    if (!stat.bpm) bad.push('BPM 没被主题画像的实测值填上（拿到 ' + stat.bpm + '）');
    if (!/引擎预设/.test(stat.meta || '')) bad.push('主题元信息里没有「引擎预设」：' + stat.meta);
    if (!/预计时长/.test(stat.meta || '')) bad.push('主题元信息里没有「预计时长」：' + stat.meta);

    // ③ **真的点一次「读一下我的要求」**（前端→/api/ask→前端 全链路）
    const asked = await c.evaljs(`(async ()=>{
      const out={};
      document.getElementById('askText').value='来一首欢快的钢琴曲，90秒';
      document.getElementById('btnAsk').click();
      for(let i=0;i<60;i++){
        await new Promise(r=>setTimeout(r,500));
        const el=document.getElementById('askChips');
        if(el && !el.classList.contains('hidden') && el.children.length){
          out.chips=[...el.children].map(x=>x.textContent);
          break;
        }
      }
      out.formShown=!document.getElementById('genForm').classList.contains('hidden');
      out.theme=(document.getElementById('genTheme')||{}).value;
      out.inst=(document.getElementById('genInst')||{}).value;
      out.bpmAfter=(document.getElementById('genBpm')||{}).value;
      out.metaAfter=(document.getElementById('themeMeta')||{}).textContent;
      // 再手动触发一次 change：若元信息因此变了，说明"解析赋值主题"没把元信息同步过去
      document.getElementById('genTheme').dispatchEvent(new Event('change'));
      out.metaRecheck=(document.getElementById('themeMeta')||{}).textContent;
      return out;})()`, true);
    console.log('  解析 chips:', JSON.stringify(asked.chips || []));
    console.log('  表单出现:', asked.formShown, '· 主题=' + asked.theme, '· 乐器=' + asked.inst);
    console.log('  解析后 BPM:', asked.bpmAfter, '· 元信息:', asked.metaAfter);
    if (!asked.chips || !asked.chips.length) bad.push('点「读一下我的要求」后没有 chips（链路断了）');
    if (!asked.formShown) bad.push('解析后生成表单没出现');
    if (asked.theme !== 'cheerful') bad.push('主题没被预填成 cheerful（实际 ' + asked.theme + '）');
    // 主题被解析改掉后，BPM 默认值与元信息（拍号/小节数/时长）必须跟着换 ——
    // 换个说法验：再触发一次 change，如果元信息因此变化，就说明前面没同步（2026-10-07 真踩过）
    if (asked.metaAfter !== asked.metaRecheck) {
      bad.push('主题被解析赋值后元信息没同步：解析后是「' + asked.metaAfter +
               '」，再触发一次 change 变成「' + asked.metaRecheck + '」');
    }
    // 说了时长 → 界面必须给出"用 BPM 怎么接近"的说法（填上了要显示建议值，超出区间要明说）
    if (!(asked.chips || []).some(x => /秒/.test(x) && /BPM|区间/.test(x))) {
      bad.push('解析出的时长没在界面上给出可执行的说法（chips：' +
               JSON.stringify(asked.chips || []) + '）');
    }

    // ④ 产物区：点一个曲目 → 必须出现「📦 合并导出」（目标里"导出"这条路的唯一入口，
    //    而且它**不能依赖有没有音频** —— 只有 MIDI 的曲目也要能导）
    const outs = await c.evaljs(`(async ()=>{
      const first=document.querySelector('#songList .song-item');
      if(!first) return {err:'曲库列表是空的'};
      first.click();
      for(let i=0;i<40;i++){
        await new Promise(r=>setTimeout(r,500));
        if(document.querySelector('#outs .out')) break;
      }
      return {cards: document.querySelectorAll('#outs .out').length,
              exportBtn: !!document.querySelector('#outs [data-export]'),
              hint: (document.getElementById('outHint')||{}).textContent};
    })()`, true);
    console.log('  产物卡:', outs.cards, '· 导出按钮:', outs.exportBtn, '· 曲目:', outs.hint);
    if (outs.err) bad.push('点曲目后读产物失败：' + outs.err);
    else if (!outs.cards) bad.push('点曲目后没有产物卡（showOuts 没渲染）');
    else if (!outs.exportBtn) bad.push('产物区没有「合并导出」按钮');

    // ⑤ **导航闭环**：创作台 → 引擎面板 → 回创作台。
    //    用户 2026-10-07 报的正是这条："点了引擎面板这些没办法切换回来" ——
    //    创作台侧栏能进两个老页面，而老页面当时**没有回创作台的入口**（断头路）。
    await c.evaljs('(()=>{const a=document.querySelector(\'a[href="/"]\');if(a)a.click();return !!a;})()');
    await sleep(2500);
    const onEngine = await c.evaljs('({path: location.pathname, hasBack: !!document.querySelector("#lnkCreate")})');
    console.log('  跳引擎面板后:', onEngine.path, '· 有回创作台入口:', onEngine.hasBack);
    if (onEngine.path !== '/' && onEngine.path !== '/index.html') {
      bad.push('从创作台点「引擎面板」没跳过去（当前 ' + onEngine.path + '）');
    }
    if (!onEngine.hasBack) bad.push('引擎面板上没有回创作台的入口（用户报的就是这条）');
    if (onEngine.hasBack) {
      await c.evaljs('(()=>{document.querySelector("#lnkCreate").click();return true;})()');
      await sleep(2500);
      const back = await c.evaljs('location.pathname');
      console.log('  点「创作台」回到:', back);
      if (back !== '/create') bad.push('从引擎面板点「创作台」没回到 /create（当前 ' + back + '）');
    }

    try {
      const shot = await c.send('Page.captureScreenshot', { format: 'png' });
      fs.writeFileSync(SHOT, Buffer.from(shot.data, 'base64'));
      console.log('  截图:', SHOT);
    } catch (e) { console.log('  截图失败:', e.message); }

    if (errs.length) {
      bad.push('页面报错 ' + errs.length + ' 条');
      errs.slice(0, 6).forEach(e => console.log('  !! ' + e));
    } else {
      console.log('  console/异常: 干净');
    }
  } catch (e) {
    bad.push('验收脚本异常: ' + e.message);
  } finally {
    if (c) c.close();
  }

  console.log(bad.length ? '\n结果: FAIL\n  - ' + bad.join('\n  - ')
                         : '\n结果: PASS（创作台在真实浏览器里可用）');
  process.exit(bad.length ? 1 : 0);
})();
