/* 扩展 smoke_ui.js —— 覆盖第二波：WebAudio 引擎、混音台、波形、撤销/重做、快捷键 */
const fs = require('fs'), path = require('path'), vm = require('vm');
const ROOT = path.join(__dirname, '..');
/* 未捕获的异步异常（vm 里 `pollJob()` 是**不 await** 调用的，抛错会变成 unhandledRejection）：
 * 不记录的话整个 smoke 进程会直接崩掉、拿不到失败清单（实测：旧版 app.js 就是这种）。
 * 记下来还能当证据 —— 旧版 `S.player.src=` 抛的 TypeError 正是"点试听没反应"的根因。 */
const unhandled = [];
process.on('unhandledRejection', e => unhandled.push(String((e && e.message) || e)));
/* `SMOKE_APP_JS` 可指定另一份 app.js —— 用来做**反证**：
 * 拿 git 里的旧版本跑同一个 smoke，新加的断言必须在旧版上**失败**
 * （否则那些断言是空断言，"修好了"就成了自己说自己好）。
 *   cmd /c "git show HEAD:studio/web/app.js > %TEMP%\app_head.js"
 *   set SMOKE_APP_JS=%TEMP%\app_head.js && node tools/smoke_ui.js */
const APP = process.env.SMOKE_APP_JS || path.join(ROOT,'web','app.js');
const FILES = [path.join(ROOT,'web','engine.js'), APP];

let __n=0; function mkEl(id){ return fakeEl(id+('_q'+(++__n))); }
/* canvas 替身：**记录绘制**（这个 smoke 之前用 `get:()=>()=>{}` 的空壳，
 * 于是"卷帘上画了什么都测不到" —— 播放头残影这类像素级 bug 就漏过去了）。
 * 语义与真画布一致：`clearRect` 清掉该画布已记录的内容。 */
const drawLog = [];
function mkCtx(el){
  const st = {fill:'#000', stroke:'#000'};
  const own = el.__own = {items:[]};
  const push = (x,y,w,h)=>{ const r={fill:st.fill,x,y,w,h};
    own.items.push(r); drawLog.push(Object.assign({id:el.id}, r)); };
  return new Proxy({}, {
    get(t,k){
      if(k==='canvas') return el;
      if(k==='fillRect') return push;
      if(k==='clearRect') return ()=>{ own.items.length=0; };
      if(k==='strokeRect') return ()=>{};
      if(k==='createLinearGradient') return ()=>({addColorStop(){}});
      if(k==='measureText') return ()=>({width:10});
      /* 像素自检：返回"有内容"（否则 renderRoll 会以为画布是空的 → 走重试分支） */
      if(k==='getImageData') return (x,y,w,h)=>{ const d=new Uint8ClampedArray(w*h*4);
        d.fill(255); return {data:d}; };
      return ()=>{};
    },
    set(t,k,v){ if(k==='fillStyle') st.fill=String(v);
                else if(k==='strokeStyle') st.stroke=String(v); return true; },
  });
}
function fakeEl(id){
  const el = {
    id, children: [], style: {}, dataset: {}, value: '', textContent: '',
    className: '', title: '', scrollTop: 0, scrollHeight: 100, width: 1200, height: 360,
    clientWidth: 1200, paused: true, currentTime: 0, loop: false,
    /* ⚠ `innerHTML=''` 必须**真的清空子节点**：渲染函数普遍用"先清后建"，
     *   替身若只存字符串，`box.innerHTML=''; box.appendChild(...)` 会让子节点无限累积
     *   （实测：断言里数出 27 张音轨卡 = 9×3，纯属替身假象）。 */
    set innerHTML(v){ this._html = String(v); this.children.length = 0; },
    get innerHTML(){ return this._html || ''; },
    appendChild(c){ this.children.push(c); return c; },
    querySelectorAll(){ return []; }, querySelector(){ return mkEl('q'); },
    addEventListener(){}, removeEventListener(){}, focus(){}, remove(){},
    getBoundingClientRect(){ return {left:0, top:0, width:1200, height:360}; },
    getContext(){ return this.__ctx || (this.__ctx = mkCtx(this)); },
    set src(v){ this._src=v; }, get src(){ return this._src||''; },
    play(){ return Promise.resolve(); }, pause(){}, classList:{ toggle(){}, add(){}, remove(){} },
  };
  return el;
}
const els = new Map();
for(const id of ['bar','songSel','songInfo','btnSave','btnCheck','btnCompose','btnRender','btnTune',
  'btnPlay','btnStop','loopChk','loopA','loopB','posInfo','modeSeg','wave','btnExport','btnStems',
  'btnNew','btnRefresh','tracks','trackHint','trackList','filesHint','files','secStrip','secEdit',
  'rollHint','roll','rollPh','metrics','btnMetrics','logWrap','log','jobInfo','player','mixer','stems',
  'btnLoadStems','btnApplyMix','btnRenderMix','stemInfo','strips','searchOut','searchSec','searchSpeed','searchWorkers','searchEta','btnSearch','btnSearchStop','btnAdopt','searchApply']) els.set(id, fakeEl(id));
els.get('modeSeg').querySelectorAll = ()=>[];

/* --- WebAudio 替身 --- */
class FakeNode { constructor(){ this.gain={value:1}; this.pan={value:0}; this.fftSize=512; }
  connect(){ return this; } disconnect(){} start(){} stop(){}
  getFloatTimeDomainData(a){ for(let i=0;i<a.length;i++) a[i]=0.1; } }
class FakeAC { constructor(){ this.currentTime=0; this.state='running'; this.destination=new FakeNode(); }
  createGain(){ return new FakeNode(); } createStereoPanner(){ return new FakeNode(); }
  createAnalyser(){ return new FakeNode(); } createBufferSource(){ const n=new FakeNode(); n.buffer=null; return n; }
  resume(){ return Promise.resolve(); }
  decodeAudioData(){ const ch=new Float32Array(4410); for(let i=0;i<ch.length;i++) ch[i]=Math.sin(i/20)*0.3;
    return Promise.resolve({ duration: 100, getChannelData: ()=>ch }); } }

const SONG = JSON.parse(fs.readFileSync(path.join(ROOT,'fixtures','song.json'),'utf8'));
const EVENTS = JSON.parse(fs.readFileSync(path.join(ROOT,'fixtures','events.json'),'utf8'));
const apiCalls = [];      // 只记路径（输出用）
const fullCalls = [];     // 记完整 URL（断言用：`?kind=solo&track=…` 这类参数在 query 里）
let jobSeq = 0; const jobKindOf = {};
async function fakeFetch(url, opt){
  apiCalls.push(url.split('?')[0]); fullCalls.push(url);
  const method = (opt && opt.method) || 'GET';
  let body = {ok:true};
  if(url.startsWith('/api/songs')) body={ok:true,root:'R',songs:[{id:'demo',bpm:150,bars:65,seconds:104,has_ogg:true}]};
  else if(url.startsWith('/api/song?')) body={ok:true,song:JSON.parse(JSON.stringify(SONG)),render:{ref:'REF'},events:JSON.parse(JSON.stringify(EVENTS))};
  else if(url.startsWith('/api/metrics')) body={ok:true,mine:{rms:-16.8,width:.51,centroid:3000,
      rel:{'20-40':-19.7,'40-80':-1.5,'80-160':-2.6,'160-315':-1.5,'315-630':0,'630-1250':-2,'1250-2500':-4.4,
           '2500-5000':-8.3,'5000-10000':-15.8,'10000-18000':-19},
      occ:{'5000-10000':70,'10000-18000':44,'40-80':58,'160-315':94}},
    ref:{name:'X',rms:-16.9,width:.51,centroid:3250,bands:{'20-40':-18.8,'40-80':-1.6,'80-160':0,'160-315':-2,
      '315-630':-3.8,'630-1250':-2.5,'1250-2500':-4.2,'2500-5000':-7.1,'5000-10000':-12.4,'10000-18000':-19.6},
      occ:{'5000-10000':91,'10000-18000':70,'40-80':62,'160-315':90}}, diff_rel:{'20-40':-0.9}};
  else if(url.startsWith('/api/files')) body={ok:true,files:[{name:'a.ogg',size:1,url:'/x'}]};
  else if(url.startsWith('/api/stems')) body={ok:true,cached:true,tracks:{Melody:'/api/stem?track=Melody',Bass:'/api/stem?track=Bass'}};
  /* 任务生命周期：POST 建任务（记住 kind）→ GET 查询直接回"已完成"（smoke 不等真渲染）。
   * 这样 pollJob 的**分派逻辑**（preview / solo / 其它）才测得到 —— 原来一律回字符串 'j1'，
   * `d.job.state` 是 undefined，任何"完成后的动作"都不会被触发。 */
  else if(url.startsWith('/api/job')){
    if(method === 'POST'){
      const m = /[?&]kind=([^&]+)/.exec(url);
      const jid = 'job' + (++jobSeq);
      jobKindOf[jid] = decodeURIComponent(m ? m[1] : '');
      body = {ok:true, job:jid};
    } else {
      const jid = (/[?&]id=([^&]+)/.exec(url) || [])[1] || '';
      body = {ok:true, job:{id:jid, kind:jobKindOf[jid] || '', state:'done', rc:0, out:'X'},
              log:'（smoke：任务直接给 done）'};
    }
  }
  return { json: async()=>body, ok:true,
           arrayBuffer: async()=>new ArrayBuffer(2048) };
}
const sandbox = { console, setTimeout:(fn)=>0, clearTimeout(){}, requestAnimationFrame:()=>0,
  devicePixelRatio:1, alert:(m)=>apiCalls.push('ALERT:'+m), fetch:fakeFetch,
  AudioContext: FakeAC, webkitAudioContext: FakeAC,
  document:{ getElementById:(id)=>els.get(id)||fakeEl(id), createElement:(t)=>fakeEl(t),
    querySelectorAll:()=>[], addEventListener(){}, body: fakeEl('body') },
  window:{ addEventListener(){}, devicePixelRatio:1, AudioContext:FakeAC } };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for(const f of FILES) vm.runInContext(fs.readFileSync(f,'utf8'), sandbox, {filename:path.basename(f)});
vm.runInContext('globalThis.__S=S; globalThis.__ENG=ENG;', sandbox);

setTimeout(async ()=>{
  const S = sandbox.__S, ENG = sandbox.__ENG;
  const bad = [];
  if(!S||!S.song) bad.push('初始化没有 song');
  if(!ENG) bad.push('没有 ENG');
  if(!els.get('trackList').children.length) bad.push('音轨列表没渲染');
  if(!els.get('strips').children.length) bad.push('混音台 strips 没渲染');
  // 混音台：改推子 → 写回 song.json
  const before = JSON.stringify(S.song.mix.Melody);
  if(ENG){ ENG.setTrack('Melody',{gain:0.5,pan:-0.3}); }
  await sandbox.applyMixToSong();
  if(JSON.stringify(S.song.mix.Melody)===before) bad.push('写回 song.json 没生效');
  // 撤销/重做
  const undoLen = S.undo.length;
  sandbox.undo();
  if(S.undo.length>=undoLen && undoLen) bad.push('撤销没起作用');
  // 播放模式切换 + 播放
  sandbox.setMode('stems');
  await sandbox.loadStems();
  const played = await ENG.play('stems');
  if(!played) bad.push('分轨模式播放失败');

  // ---- 卷帘：改时值 / 改力度 / 缩放 / 删除 ----
  const roll = els.get('roll');
  S.track='Melody';
  const geom = vm.runInContext('rollGeom()', sandbox);
  const notes = vm.runInContext('allNotes()', sandbox).filter(n=>n.track==='Melody' && n.raw);
  if(!notes.length) bad.push('卷帘里没有可编辑的 Melody 音符');
  else {
    const n0 = notes[0];
    const pxPerBeat = geom.W/geom.v.span;
    const cxEdge = (n0.beat+n0.dur-geom.v.st)*pxPerBeat - 2;
    const cy = geom.y(n0.pitch);
    const before = JSON.stringify(S.song.melody);
    vm.runInContext('rollDown', sandbox)({clientX:cxEdge, clientY:cy, altKey:false, preventDefault(){}});
    vm.runInContext('rollMove', sandbox)({clientX:cxEdge+30, clientY:cy, altKey:false, preventDefault(){}});
    vm.runInContext('rollUp', sandbox)();
    if(JSON.stringify(S.song.melody)===before) bad.push('拖拽没有改到 song.json 的 melody');
    const beforeDel = JSON.stringify(S.song.melody);
    vm.runInContext('rollContext', sandbox)({clientX:cxEdge, clientY:cy, preventDefault(){}});
    if(JSON.stringify(S.song.melody)===beforeDel) bad.push('右键删除没有改到 song.json 的 melody');
    vm.runInContext('rollWheel', sandbox)({ctrlKey:true, deltaY:-100, preventDefault(){}});
    if((S.zoom||1)<=1) bad.push('Ctrl+滚轮没有缩放');
  }
  // ---- 真实数据形状：song.json **没有** programs/mix（靠 style 预设生效）----
  /* 42_gtr_tender 实测就是这种形状（song 的键里没有 programs/mix，真值在 events 里），
   * 而 renderTracks 原来读 `Object.keys(S.song.programs||{})` = [] → **一张音轨卡都不渲染**，
   * 左侧面板只剩下面的"产物/大小"表（用户报"只有产物和大小"）。
   * fixture 自带 programs/mix，所以这条必须**先删字段**再渲染，否则永远测不出来。 */
  delete S.song.programs; delete S.song.mix;
  sandbox.renderAll();
  const nWant = Object.keys(S.events.tracks).length;
  const nGot = els.get('trackList').children.length;
  if(nGot !== nWant) bad.push('song.json 缺 programs/mix 时音轨卡只有 '+nGot+'/'+nWant+' 张');
  if(els.get('strips').children.length !== nWant)
    bad.push('混音台 strips 同样塌成 '+els.get('strips').children.length+' 条');
  // 只改一维时另一维必须沿用**真实值**（通道写错会撞车，check_song 会拦）
  /* 守卫式断言：换旧版 app.js 跑反证时不能因为"函数不存在"整脚本崩掉，
   * 否则前面的失败项打不出来（实测：旧版直接 TypeError 退出，反证拿不到清单）。 */
  if(typeof sandbox.setTrackMix !== 'function' || typeof sandbox.setTrackProg !== 'function'){
    bad.push('缺 setTrackMix/setTrackProg（旧版读 S.song.mix[t] 兜底 → 缺字段时不建字段、通道退化成 0）');
  } else {
    const ch0 = ((S.events.programs||{}).Melody||[0,0])[1];
    sandbox.setTrackMix('Melody', 1, 100);
    /* 改一维不许**顺手**建另一个空对象：实测"只拖了一下音量"就往交付物里塞了
     * `"programs": {}`（42_gtr_tender/song.json 被污染）—— 空对象没有任何语义。
     * 这里字段是**刚被删掉**的，所以此刻 programs 必须仍然是 undefined。 */
    if(S.song.programs) bad.push('改音量时建了 programs（应只建 mix）：'+JSON.stringify(S.song.programs));
    sandbox.setTrackProg('Melody', 0);
    if(!S.song.mix || S.song.mix.Melody[1] !== 100) bad.push('音量写回没按需建 mix 字段');
    if(!S.song.programs || S.song.programs.Melody[1] !== ch0)
      bad.push('改音色把通道号改掉了（会撞车）：'+JSON.stringify(S.song.programs && S.song.programs.Melody));
  }
  // 混音台 → 写回：老代码 `if(!(t in (S.song.mix||{}))) continue;` 在缺字段时**全跳过且静默**
  ENG.setTrack('Bass',{gain:0.5,pan:-0.5});
  await sandbox.applyMixToSong();
  if(!S.song.mix || !S.song.mix.Bass) bad.push('缺 mix 字段时"应用到曲目"什么都没写（静默失效）');

  // ---- 拖进度条：卷帘画布上**不许**留下播放头残影 ----
  /* 老实现把播放头画在卷帘画布上、**从不擦除上一帧** → 拖进度条时位置大跨度跳跃，
   * 旧线全部留下 = "满屏红色竖条"（用户报的"拖动进度条之后还有显示bug"）。 */
  const rollOwn = ()=>els.get('roll').__own.items;
  const redFull = ()=>rollOwn().filter(p=>/ff6b6b/i.test(p.fill) && p.h>=300).length;
  sandbox.renderRoll();
  for(const t of [3,17,42,66,90]){ ENG.seek(t); sandbox.drawPlayhead(); }
  if(redFull() !== 0) bad.push('拖进度条后卷帘上留下 '+redFull()+' 条红色满高残影');
  // 探针自证：手工画 2 条必须被数出来（否则上面的 0 是"测不到"而不是"没有"）
  const rc = els.get('roll').getContext('2d');
  rc.fillStyle='#ff6b6b'; rc.fillRect(10,0,2,360); rc.fillRect(50,0,2,360);
  if(redFull() !== 2) bad.push('探针自证失败：手工红色满高线没被数出来（'+redFull()+'≠2）');
  // 播放头落在独立叠加层并写出位置
  sandbox.renderRoll(); ENG.seek(1); sandbox.drawPlayhead();
  const ph = els.get('rollPh');
  if(!ph || ph.style.opacity !== '1' || !/translateX\(/.test(ph.style.transform||''))
    bad.push('播放头叠加层没被驱动：'+(ph?JSON.stringify(ph.style):'缺 #rollPh'));
  if(redFull() !== 0) bad.push('画播放头又污染了卷帘画布');

  // ---- 和弦表编辑器：能打开、能通过校验 ----
  try { sandbox.openChordEditor(); } catch(e){ bad.push('和弦表打不开: '+e.message); }
  if(sandbox.chordParseable && (!sandbox.chordParseable('Bbadd9') || sandbox.chordParseable('Bbzz')))
    bad.push('和弦名校验反了');
  // 搜索结果 → 逐项采用表（本次修的 UX：默认不写回，由人勾选）
  S.jobKind = 'search';
  const fakeLog = [
    '  001    mad=2.90  {"arpeggio": "…"}',
    'RESULT {"ok":true,"best":{"mad":1.94,"params":{"arpeggio":[0,3,2,3],"bass_style":"offbeat","staccato":1.0}},' +
    '"changed":{"arpeggio":[0,3,2,3],"bass_style":"offbeat","staccato":1.0},' +
    '"suggest_keys":["arpeggio","staccato"],"struct_keys":["bass_style"]}'
  ].join(String.fromCharCode(10));
  sandbox.renderSearchLog(fakeLog);
  if (!S.searchResult || !(S.searchResult.suggest_keys || []).length)
    bad.push('搜索 RESULT 没解析出 suggest_keys');
  const sHtml = els.get('searchOut').innerHTML || '';
  if (sHtml.indexOf('采用勾选项') < 0) bad.push('没渲染"逐项采用"按钮');
  if (sHtml.indexOf('性格参数') < 0) bad.push('没标出性格参数警示');

  // ---- index.html 静态结构：内联自检脚本的语法、播放头叠加层、资源版本戳 ----
  /* 页面里的启动自检脚本是**独立一段 JS**，它自己坏掉时整页看起来"正常但没结论"
   * （之前 `window.renderRoll` 那条分支就一直是死的）。这里做静态检查：
   * 内联脚本必须能通过语法解析，播放头层必须存在，静态资源必须带版本戳（防浏览器吃旧的）。 */
  const html = fs.readFileSync(path.join(ROOT,'web','index.html'),'utf8');
  const inl = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
  if(!inl.length) bad.push('index.html 里没有内联自检脚本');
  inl.forEach((b,i)=>{ try { new vm.Script(b); }
    catch(e){ bad.push('index.html 内联脚本#'+(i+1)+' 语法错误: '+e.message); } });
  if(!/id="rollPh"/.test(html)) bad.push('index.html 缺播放头叠加层 #rollPh');
  if(!/<canvas id="roll"[^>]*>/.test(html)) bad.push('index.html 里 #roll 不再是 canvas');
  for(const f of ['app.js','engine.js','style.css'])
    if(!new RegExp(f.replace('.','\\.')+'\\?v=\\d+').test(html))
      bad.push('静态资源 '+f+' 没带版本戳（浏览器可能吃旧文件）');
  // 页面自检依赖的两个 window 接口必须真的挂上（否则自检静默少一半）
  if(typeof sandbox.__studioProbe !== 'function' && typeof (sandbox.window||{}).__studioProbe !== 'function'
     && !vm.runInContext('typeof window.__studioProbe', sandbox).includes('function'))
    bad.push('没挂 window.__studioProbe（页面自检读不到真值）');

  // ---- 🎧 试听某一轨：渲染出来的那轨必须**真的被播** ----
  /* 用户报"音轨的试听按钮没有用"。根因：`pollJob()` 完成后对**所有**任务一律把
   * （已经不存在的）`S.player.src` 指向整曲 mix 且**从不 play()** —— 服务端早就把
   * `solo_<轨>_<ts>.ogg` 渲染好了、`/api/audio?kind=solo&track=` 也早就就绪，
   * 前端既不取也不播。断言要点：音源被换成该轨 + 真的开始播 + 请求里确实带了 kind=solo。 */
  if(typeof sandbox.startJob !== 'function') {
    bad.push('缺 startJob（旧版：试听只改 <audio>.src 且从不 play()）');
  } else {
    await sandbox.startJob('solo:Melody', '试听 Melody');
    await new Promise(r=>setTimeout(r,60));            // 让 pollJob 走完
    /* 下面三条是**行为**断言（新旧版都会跑到）：旧版在这里必然失败 ——
     * 它把（已不存在的）`S.player.src` 指向整曲且从不 play()，压根不会请求该轨音频。 */
    if(!fullCalls.some(c=>c.indexOf('kind=solo')>=0 && c.indexOf('track=Melody')>=0))
      bad.push('没有请求 /api/audio?kind=solo&track=Melody（该轨音频压根没被载入）');
    if(S.soloTrack !== 'Melody') bad.push('试听没有接管音源（S.soloTrack=' + S.soloTrack + '）');
    if(!ENG.state().playing) bad.push('试听没有开始播放（ENG.playing=false）');
    // 按 ▶ 必须换回整曲音源（否则用户以为在听整曲，其实只有一轨）
    ENG.stop();
    await sandbox.togglePlay();
    if(S.soloTrack) bad.push('按 ▶ 之后仍在试听某一轨（S.soloTrack=' + S.soloTrack + '）');
    if(!fullCalls.some(c=>c.indexOf('kind=mix')>=0)) bad.push('按 ▶ 没有重新载入整曲音源');
  }

  await new Promise(r=>setTimeout(r,120));         // 给未捕获的 rejection 一点时间冒出来
  if(unhandled.length) bad.push('未捕获的异步异常：' + unhandled.slice(0,3).join(' | '));

  if(bad.length) { console.log('❌ '+bad.join('; ')); process.exit(1); }
  console.log('API:', [...new Set(apiCalls.filter(c=>c.startsWith('/api')))].join(' '));
  console.log('✅ 引擎/混音台/写回/撤销/分轨播放 全部跑通（无浏览器）');
  process.exit(0);
}, 400);