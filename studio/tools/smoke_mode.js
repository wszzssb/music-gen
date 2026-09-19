/* smoke_mode.js —— 真浏览器验收：**切播放模式**的行为（零依赖，Node 自带 WebSocket + Edge CDP）
 *
 * 盯的是两个"一按就怪"的老毛病（2026-09-19 用户口径："分轨播放操作很奇怪"）：
 *   ① 没在播放时点「主混音 / 分轨混音 / 参考曲」→ **不许自动出声**。
 *      旧版 `setMode()` 无条件 `togglePlay()`，只想切个模式看看就会突然放起来；
 *      切到还没载入分轨的"分轨混音"更是立刻弹"还没有音源"。
 *   ② 播放中切模式 → **继续播，且位置不许归零**。旧版用 `ENG.stop()`，而它会把 `st.offset`
 *      清 0，于是"切个模式"= 回到开头。现在是 `ENG.pause()`（保留位置）后再接着放。
 *
 * 跑法（需要服务在 STUDIO_URL，默认 8765）：
 *   node studio/tools/smoke_mode.js
 *   STUDIO_URL=http://127.0.0.1:8791 node studio/tools/smoke_mode.js
 *
 * ⚠ 无头浏览器必须带 `--autoplay-policy=no-user-gesture-required`（脚本已加）：否则
 *   AudioContext 一直 suspended，CDP 的点击不算用户手势，▶ 根本播不起来 —— 那是环境限制，
 *   不是产品问题（`browser_check.js` 里对同一现象有说明）。
 */
'use strict';
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path'), os = require('os');

const EDGE = [
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
].find(p => fs.existsSync(p));

const BASE = process.env.STUDIO_URL || 'http://127.0.0.1:8765';
const CDP_PORT = Number(process.env.CDP_PORT || 9337);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const fails = [];
function ck(what, cond, extra) {
  console.log((cond ? '   ok   ' : '   FAIL ') + what + (extra !== undefined ? ' → ' + extra : ''));
  if (!cond) fails.push(what);
}
async function waitJson(url, ms = 20000) {
  const t0 = Date.now();
  for (;;) {
    try { const r = await fetch(url); if (r.ok) return await r.json(); } catch (e) {}
    if (Date.now() - t0 > ms) throw new Error('CDP 没起来');
    await sleep(200);
  }
}
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
    ws.addEventListener('error', () => reject(new Error('WS 错误')));
    ws.addEventListener('open', () => resolve({
      send(method, params) {
        const myId = ++id;
        return new Promise((res, rej) => {
          pend.set(myId, { res, rej });
          ws.send(JSON.stringify({ id: myId, method, params: params || {} }));
          setTimeout(() => { if (pend.has(myId)) { pend.delete(myId); rej(new Error(method + ' 超时')); } }, 30000);
        });
      },
      async evaljs(expr) {
        const r = await this.send('Runtime.evaluate', { expression: expr, returnByValue: true });
        if (r.exceptionDetails) {
          const d = r.exceptionDetails.exception;
          throw new Error('页面异常: ' + ((d && d.description) || r.exceptionDetails.text));
        }
        return r.result.value;
      },
      close() { try { ws.close(); } catch (e) {} },
    }));
  });
}

(async () => {
  const prof = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-mode-'));
  const proc = spawn(EDGE, ['--headless=new', '--remote-debugging-port=' + CDP_PORT,
    '--user-data-dir=' + prof, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--autoplay-policy=no-user-gesture-required',
    '--remote-allow-origins=*', 'about:blank'], { stdio: 'ignore' });
  let c;
  try {
    await waitJson('http://127.0.0.1:' + CDP_PORT + '/json/version');
    const t = await (await fetch('http://127.0.0.1:' + CDP_PORT + '/json/new?about:blank',
      { method: 'PUT' })).json();
    c = await cdp(t.webSocketDebuggerUrl);
    await c.send('Page.enable');
    await c.send('Runtime.enable');
    await c.send('Page.navigate', { url: BASE + '/' });
    for (let i = 0; i < 80; i++) {
      if (await c.evaljs('document.readyState') === 'complete') break;
      await sleep(150);
    }
    await sleep(3000);

    /* ⚠ 读 `S.mode`（面板状态）而不是 `ENG.state().mode`：引擎的 st.mode 只在 `ENG.play(mode)`
     *   时才更新，而"没在播就只切样式"正是本次要验的行为 → 读引擎那个值永远停在旧模式。
     *   headless 还得加 `--autoplay-policy=no-user-gesture-required`，否则 AudioContext 一直
     *   suspended、点 ▶ 播不起来（CDP 的点击在无头下不算用户手势）。 */
    const st = () => c.evaljs(`({playing:!!ENG.state().playing, mode:S.mode,
      engMode:ENG.state().mode, ctx:ENG.state().ctx?ENG.state().ctx.state:null,
      pos:ENG.position(), posInfo:(document.getElementById('posInfo')||{}).textContent})`);
    const clickMode = (m) => c.evaljs(`(()=>{document.querySelector('#modeSeg button[data-mode="${m}"]').click();return 1;})()`);

    console.log('== 起始 ==');
    let s = await st();
    console.log('  ' + JSON.stringify(s));
    ck('初始未播放', s.playing === false, s.playing);

    console.log('\n== ① 没在播放时点"分轨混音"（未载入分轨）==');
    await clickMode('stems');
    await sleep(1200);
    s = await st();
    console.log('  ' + JSON.stringify(s));
    ck('切模式后**没有**自动出声', s.playing === false, 'playing=' + s.playing);
    ck('模式确实切到了 stems', s.mode === 'stems', s.mode);
    const logTxt = await c.evaljs("document.getElementById('log').textContent");
    ck('日志说明了模式已切', /播放模式 → stems/.test(logTxt), (logTxt || '').slice(-120));

    console.log('\n== ② 没在播放时点"主混音" ==');
    await clickMode('master');
    await sleep(1200);
    s = await st();
    ck('切回 master 也没自动出声', s.playing === false, 'playing=' + s.playing);

    console.log('\n== ③ 按 ▶ 播放（master 有成品）==');
    await c.evaljs("(()=>{document.getElementById('btnPlay').click();return 1;})()");
    await sleep(2500);
    s = await st();
    console.log('  ' + JSON.stringify(s));
    ck('▶ 能播起来', s.playing === true, 'playing=' + s.playing);

    console.log('\n== ④ 播放中点"参考曲"：要继续播 + 位置不归零 ==');
    const before = (await st()).pos;
    await clickMode('ref');
    await sleep(2000);
    s = await st();
    console.log('  切换前 pos=' + before.toFixed(2) + ' → 切换后 ' + JSON.stringify(s));
    ck('切模式后仍在播放', s.playing === true, 'playing=' + s.playing);
    ck('播放位置没被归零', s.pos > 0.2, 'pos=' + s.pos.toFixed(2));
    ck('位置是接着走而不是重来（≥ 切换前）', s.pos >= before - 0.5,
      before.toFixed(2) + ' → ' + s.pos.toFixed(2));

    console.log('\n== ⑤ 再切回主混音：同样接着播 ==');
    await clickMode('master');
    await sleep(2000);
    s = await st();
    ck('仍在播放', s.playing === true, 'playing=' + s.playing);
    ck('位置仍未归零', s.pos > 0.2, 'pos=' + s.pos.toFixed(2));
  } catch (e) {
    fails.push('异常：' + (e && e.message));
    console.error('异常：' + (e && e.stack || e));
  } finally {
    if (c) c.close();
    try { proc.kill(); } catch (e) {}
    await sleep(300);
    try { fs.rmSync(prof, { recursive: true, force: true }); } catch (e) {}
  }
  console.log('\n==== ' + (fails.length ? 'FAIL ' + fails.length + ' 项: ' + fails.join(' / ') : 'PASS 全部通过') + ' ====');
  process.exit(fails.length ? 1 : 0);
})();
