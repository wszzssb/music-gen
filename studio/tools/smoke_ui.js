/* 扩展 smoke_ui.js —— 覆盖第二波：WebAudio 引擎、混音台、波形、撤销/重做、快捷键 */
const fs = require('fs'), path = require('path'), vm = require('vm');
const ROOT = path.join(__dirname, '..');
const FILES = [path.join(ROOT,'web','engine.js'), path.join(ROOT,'web','app.js')];

let __n=0; function mkEl(id){ return fakeEl(id+('_q'+(++__n))); }
function fakeEl(id){
  const el = {
    id, children: [], style: {}, dataset: {}, value: '', textContent: '', innerHTML: '',
    className: '', title: '', scrollTop: 0, scrollHeight: 100, width: 1200, height: 360,
    clientWidth: 1200, paused: true, currentTime: 0, loop: false,
    appendChild(c){ this.children.push(c); return c; },
    querySelectorAll(){ return []; }, querySelector(){ return mkEl('q'); },
    addEventListener(){}, removeEventListener(){}, focus(){}, remove(){},
    getBoundingClientRect(){ return {left:0, top:0, width:1200, height:360}; },
    getContext(){ return new Proxy({}, { get: ()=>()=>{} }); },
    set src(v){ this._src=v; }, get src(){ return this._src||''; },
    play(){ return Promise.resolve(); }, pause(){}, classList:{ toggle(){}, add(){}, remove(){} },
  };
  return el;
}
const els = new Map();
for(const id of ['bar','songSel','songInfo','btnSave','btnCheck','btnCompose','btnRender','btnTune',
  'btnPlay','btnStop','loopChk','loopA','loopB','posInfo','modeSeg','wave','btnExport','btnStems',
  'btnNew','btnRefresh','tracks','trackHint','trackList','filesHint','files','secStrip','secEdit',
  'rollHint','roll','metrics','btnMetrics','logWrap','log','jobInfo','player','mixer','stems',
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
const apiCalls = [];
async function fakeFetch(url){
  apiCalls.push(url.split('?')[0]);
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
  else if(url.startsWith('/api/job')) body={ok:true,job:'j1'};
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

  if(bad.length) { console.log('❌ '+bad.join('; ')); process.exit(1); }
  console.log('API:', [...new Set(apiCalls.filter(c=>c.startsWith('/api')))].join(' '));
  console.log('✅ 引擎/混音台/写回/撤销/分轨播放 全部跑通（无浏览器）');
  process.exit(0);
}, 400);