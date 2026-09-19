/* smoke_i18n.js —— 真浏览器验收：面板的中/英切换（零依赖，Node 自带 WebSocket + Edge CDP）
 *
 * 为什么不能在 `smoke_ui.js` 里测：那个 smoke 跑在 vm 的 DOM 替身里，**不加载 i18n.js**，
 * 也没有真实的 `navigator.language` / `localStorage` —— 而语言选择恰好全靠这两样。
 * 覆盖率检查（`scripts/i18n_check.py`）只证明"字典里有这一条"，证明不了"浏览器里真的换了"。
 *
 * 跑法（需要服务先在 --port 8791 上）：
 *   node studio/tools/smoke_i18n.js
 *   STUDIO_URL=http://127.0.0.1:8765 node studio/tools/smoke_i18n.js
 *   I18N_STRICT=1 node studio/tools/smoke_i18n.js    # 把"英文态仍见短中文"也算失败
 *
 * 两个清单分开看（实测出来的口径）：
 *   · **短文案**（≤24 字）= 按钮/标签/下拉项这一类，应该全绿；
 *   · **长句** = app.js / ed.js 运行时拼进日志区的句子，本次不翻（见 i18n.js 头部注释）。
 *
 * 收尾会把 localStorage 里的 bgmLang 清掉，不把用户的界面留在英文。
 */
'use strict';
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path'), os = require('os');

const EDGE = [
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  process.env.CHROME_PATH || '',
].find(p => p && fs.existsSync(p));

const BASE = process.env.STUDIO_URL || 'http://127.0.0.1:8791';
const CDP_PORT = Number(process.env.CDP_PORT || 9334);
const STRICT = !!process.env.I18N_STRICT;
const SHORT = 24;                       // 与 PROBE 里的口径保持一致
const SHOT = process.env.I18N_SHOT || '';   // 设了目录 → 顺带存"中文态/英文态"两张截图

const sleep = ms => new Promise(r => setTimeout(r, ms));
const fails = [];

function eq(what, got, want) {
  if (got !== want) {
    fails.push(what + '\n     得到：' + JSON.stringify(got) + '\n     期望：' + JSON.stringify(want));
    console.log('   FAIL ' + what + ' → ' + JSON.stringify(got));
  } else {
    console.log('   ok   ' + what);
  }
}

async function waitJson(url, ms = 20000) {
  const t0 = Date.now();
  for (;;) {
    try { const r = await fetch(url); if (r.ok) return await r.json(); } catch (e) { /* 还没起 */ }
    if (Date.now() - t0 > ms) throw new Error('CDP 没起来：' + url);
    await sleep(200);
  }
}

/* 最小 CDP 客户端（同 browser_check.js，直连页面级 ws，不需要 sessionId） */
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
    ws.addEventListener('error', () => reject(new Error('WS 错误：' + wsUrl)));
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

/* 页面里跑的探针：找"用户能直接看到、却仍是汉字"的文本。
 * 只看元素的**直接文本子节点**（不含子元素的），跳过日志区与语言按钮自己。
 * 按长度分两档：短 = 按钮/标签/下拉项（应全绿）；长 = 运行时拼的句子（本次不翻）。 */
const PROBE = `(function(){
  var RE = /[\\u4e00-\\u9fff]/;
  var short = [], long = [];
  var all = document.body ? document.body.querySelectorAll('*') : [];
  for (var i = 0; i < all.length; i++) {
    var el = all[i];
    if (el.tagName === 'SCRIPT' || el.tagName === 'STYLE') continue;
    if (el.id === 'langBtn') continue;
    if (el.closest && el.closest('#log')) continue;
    for (var j = 0; j < el.childNodes.length; j++) {
      var n = el.childNodes[j];
      if (n.nodeType !== 3) continue;
      var t = (n.nodeValue || '').trim();
      if (!t || !RE.test(t)) continue;
      var tag = (el.id || el.tagName.toLowerCase());
      (t.length <= ${SHORT} ? short : long).push(tag + ' :: ' + t);
    }
  }
  return { short: short, long: long };
})()`;

async function openPage(url) {
  const r = await fetch('http://127.0.0.1:' + CDP_PORT + '/json/new?' + encodeURIComponent(url),
    { method: 'PUT' });
  if (!r.ok) throw new Error('/json/new 失败：' + r.status);
  const t = await r.json();
  const c = await cdp(t.webSocketDebuggerUrl);
  await c.send('Page.enable');
  await c.send('Runtime.enable');
  for (let i = 0; i < 80; i++) {
    if (await c.evaljs('document.readyState') === 'complete') break;
    await sleep(150);
  }
  await sleep(700);                    // i18n.js 的 start() 与首次 walk（含 app.js 首轮渲染）
  c.__target = t.id;
  return c;
}

async function closePage(c) {
  c.close();
  try { await fetch('http://127.0.0.1:' + CDP_PORT + '/json/close/' + c.__target); } catch (e) {}
}

/* 截图（`I18N_SHOT=<目录>` 才存）：断言能证明"文本换了"，证明不了"没被浮层挡住" ——
 * 实测 🌐 按钮原来贴在右上角，把顶栏最右的 ⏹ 停止按钮整个盖住了，而所有断言照样全绿。 */
async function shot(c, tag) {
  try {
    const r = await c.send('Page.captureScreenshot', { format: 'png' });
    const p = path.join(SHOT, tag + '.png');
    fs.writeFileSync(p, Buffer.from(r.data, 'base64'));
    console.log('   📷 ' + p);
  } catch (e) { console.log('   ⚠ 截图失败：' + e.message); }
}

const SHOW_ALL = !!process.env.I18N_ALL;

/* 一个页面走两态：中文（原文）→ 英文（切换后） */
async function checkPage(name, url, zhCase, enCase) {
  console.log('\n== ' + name + ' (' + url + ')');
  const c = await openPage(url);
  try {
    await c.evaljs("try{localStorage.removeItem('bgmLang')}catch(e){}");
    await c.send('Page.reload');
    await sleep(1500);
    eq(name + ' 中文态 语言按钮', await c.evaljs("document.getElementById('langBtn').textContent"), '🌐 EN');
    for (const k in zhCase) eq(name + ' 中文态 ' + k, await c.evaljs(zhCase[k][0]), zhCase[k][1]);
    if (SHOT) await shot(c, name + '-zh');

    await c.evaljs("try{localStorage.setItem('bgmLang','en')}catch(e){}");
    await c.send('Page.reload');
    await sleep(1500);
    eq(name + ' 英文态 语言按钮', await c.evaljs("document.getElementById('langBtn').textContent"), '🌐 中文');
    for (const k in enCase) eq(name + ' 英文态 ' + k, await c.evaljs(enCase[k][0]), enCase[k][1]);
    if (SHOT) await shot(c, name + '-en');

    const left = await c.evaljs(PROBE);
    if (left.short.length) {
      console.log('   ⚠ 英文态仍有 ' + left.short.length + ' 处**短**中文（按钮/标签类，应翻掉）：');
      (SHOW_ALL ? left.short : left.short.slice(0, 15)).forEach(s => console.log('       ' + s));
      if (!SHOW_ALL && left.short.length > 15) {
        console.log('       … 另有 ' + (left.short.length - 15) + ' 处（I18N_ALL=1 全列）');
      }
      if (STRICT) fails.push(name + ' 英文态仍有 ' + left.short.length + ' 处短中文');
    } else {
      console.log('   ok   英文态短文案已无汉字');
    }
    if (left.long.length) {
      console.log('   ℹ 另有 ' + left.long.length + ' 处长句仍是中文（运行时日志，本次不翻）：');
      (SHOW_ALL ? left.long : left.long.slice(0, 6)).forEach(s => console.log('       ' + s));
      if (!SHOW_ALL && left.long.length > 6) {
        console.log('       … 另有 ' + (left.long.length - 6) + ' 处（I18N_ALL=1 全列）');
      }
    }
    await c.evaljs("try{localStorage.removeItem('bgmLang')}catch(e){}");   // 还原，别留在英文
  } finally {
    await closePage(c);
  }
}

(async () => {
  if (!EDGE) { console.error('找不到 Edge/Chrome，设 CHROME_PATH 指定'); process.exit(2); }
  try { await fetch(BASE + '/'); } catch (e) {
    console.error('服务没起：' + BASE + '（先跑 studio/server.py --port 8791）');
    process.exit(2);
  }
  const prof = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-i18n-'));
  const proc = spawn(EDGE, ['--headless=new', '--remote-debugging-port=' + CDP_PORT,
    '--user-data-dir=' + prof, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--remote-allow-origins=*', 'about:blank'], { stdio: 'ignore' });
  try {
    await waitJson('http://127.0.0.1:' + CDP_PORT + '/json/version');

    await checkPage('引擎面板', BASE + '/', {
      '标签页标题': ['document.title', 'BGM Studio — 按音轨制作 / 可视化编曲 / 合并导出'],
      '作曲按钮': ["document.querySelector('#btnCompose').textContent.trim()", '🎼 作曲'],
      '分轨导出按钮': ["document.querySelector('#btnStems').textContent.trim()", '🎚 分轨导出'],
    }, {
      '标签页标题': ['document.title', 'BGM Studio — track-based production / visual arrangement / export'],
      '作曲按钮': ["document.querySelector('#btnCompose').textContent.trim()", '🎼 Compose'],
      '分轨导出按钮': ["document.querySelector('#btnStems').textContent.trim()", '🎚 Export stems'],
      '载入分轨按钮': ["document.querySelector('#btnLoadStems').textContent.trim()", '⬇ Load stems'],
    });

    await checkPage('MIDI 编辑器', BASE + '/editor', {
      '标签页标题': ['document.title', 'MIDI Studio — 导入 / 编辑 / 导出'],
      '量化按钮': ["document.querySelector('#btnQuant').textContent.trim()", '⚡ 量化'],
      '渐变按钮': ["document.querySelector('#btnRamp').textContent.trim()", '📈 渐变'],
    }, {
      '标签页标题': ['document.title', 'MIDI Studio — import / edit / export'],
      '量化按钮': ["document.querySelector('#btnQuant').textContent.trim()", '⚡ Quantize'],
      '渐变按钮': ["document.querySelector('#btnRamp').textContent.trim()", '📈 Ramp'],
      '去重按钮': ["document.querySelector('#btnClean').textContent.trim()", '🧹 Dedupe'],
    });
  } catch (e) {
    fails.push('异常：' + (e && e.message));
    console.error('异常：' + (e && e.stack || e));
  } finally {
    try { proc.kill(); } catch (e) {}
    await sleep(300);
    try { fs.rmSync(prof, { recursive: true, force: true }); } catch (e) {}
  }

  console.log('\n==== ' + (fails.length ? 'FAIL ' + fails.length + ' 项' : 'PASS 全部通过')
    + (STRICT ? '（严格模式）' : '') + ' ====');
  fails.forEach(f => console.log('  · ' + f));
  process.exit(fails.length ? 1 : 0);
})();
