/* BGM Studio 前端 —— 纯 DOM + Canvas，无框架（面板只跟本地 /api 说话） */
const GM = {0:'Acoustic Grand',4:'E.Piano 1',8:'Celesta',9:'Glockenspiel',11:'Music Box',
  14:'Tubular Bells',24:'Nylon Guitar',25:'Steel Guitar',32:'Acoustic Bass',33:'Fingered Bass',
  38:'Synth Bass 1',43:'Contrabass',46:'Harp',48:'Strings 1',49:'Strings 2',
  51:'Synth Strings 1',52:'Choir Aahs',53:'Voice Oohs',74:'Flute',81:'Lead 2',87:'Lead 8',
  95:'Pad Sweep',96:'FX Rain',99:'FX Crystal',112:'Tinkle Bell'};
const COLOR = {Melody:'#5cc8ff',Hook:'#ffc857',Piano:'#a0e57c',Arp:'#c792ea',Pad:'#7cd7d7',
  Strings:'#ff9db1',Bass:'#ff8a5c',Glock:'#e6e8ef',Perc:'#8b90a4'};
const FLAG = {Hook:'uku',Piano:'piano',Arp:'arp',Pad:'pad',Strings:'strings',Bass:'bass',
  Glock:'glock',Perc:'perc'};
const EXTRA = {Hook:['ep'],Arp:['shimmer'],Strings:['harmony'],Glock:['glock_all']};
const FLAGS_ALL = ['uku','ep','piano','pad','strings','bass','arp','glock','glock_all',
  'harmony','shimmer','perc'];

const S = {songs:[],sid:null,song:null,render:{},events:null,metrics:null,
  track:'Melody',sec:0,dirty:false,job:null,drag:null,hover:null,audioKind:'mix',
  mode:'master',stems:{},stemLoaded:false,metricWin:'',undo:[],redo:[],sel:null,
  jobKind:null,searchRows:[],previewOn:false,soloTrack:null,
  zoom:1,viewStart:0,selNote:null};

const $ = (id)=>document.getElementById(id);
const api = async (p,opt)=>{const r=await fetch(p,opt);return r.json();};
const fmt=(x,n=1)=> (x===null||x===undefined)?'-':Number(x).toFixed(n);

/* ---------------- 曲目 ---------------- */
async function loadSongs(){
  const d = await api('/api/songs');
  S.songs = d.songs||[];
  S.lib = d.lib || '';
  const sel = $('songSel'); sel.innerHTML='';
  for(const s of S.songs){
    const o=document.createElement('option'); o.value=s.id;
    o.textContent = s.id+'  · '+s.bpm+'BPM · '+s.bars+'小节 · '+(s.has_ogg?'有成品':'未渲染');
    sel.appendChild(o);
  }
  if(!S.sid && S.songs.length) S.sid = S.songs[0].id;
  sel.value = S.sid||'';
  sel.onchange = ()=>{S.sid=sel.value; loadSong();};
  $('filesHint').textContent = '曲库: '+S.lib;
  if($('btnLib')) $('btnLib').title = '更改曲库目录（当前：'+S.lib+'）\n＝含 songs/ 的父目录；改完记住，重启仍生效';
  if(S.sid) await loadSong();
}
/* **换曲库目录**（用户："要能更改目录"）：改完由服务端持久化到 studio/.libpath。
   路径用 prompt 输入（浏览器拿不到本地目录选择器的真实路径），支持粘贴。 */
async function changeLib(){
  const cur = S.lib || '';
  const p = prompt('曲库目录 = 含 songs/ 的父目录\n（例如 D:\\test\\llm_direct\\studio_lib）\n当前：'+cur, cur);
  if(p===null) return;
  const d = await api('/api/lib', {method:'POST',
    headers:{'Content-Type':'application/json'}, body:JSON.stringify({path:p})});
  if(!d.ok){ alert('换不了：'+(d.error||'未知错误')); return; }
  S.lib = d.lib; S.sid = null;
  log('曲库已切换 → '+d.lib+'（'+d.songs+' 首）');
  await loadSongs();
}
async function loadSong(){
  let d;
  try {
    d = await api('/api/song?id='+encodeURIComponent(S.sid));
  } catch (e) {
    S.loadErr = '请求失败（' + (e && e.message ? e.message : e) + '）';
    log('!! 读曲目失败：' + S.loadErr);
    renderRoll();                       // 让卷帘把失败原因画出来（而不是空着）
    return;
  }
  if(!d.ok){
    S.loadErr = d.error || '未知错误';
    log('!! 读不到曲目：' + S.loadErr);
    renderRoll();
    return;
  }
  S.loadErr = null;
  S.song=d.song; S.render=d.render||{}; S.events=d.events;
  S.dirty=false; S.sec=0;
  /* 换曲目 / 重新载入 → 之前"试听某一轨 / 某一段"的临时音源作废，按钮文字回默认 */
  S.soloTrack=null; S.previewOn=false;
  if($('btnPreview')) $('btnPreview').textContent='⚡ 试听本段';
  if(S.events && S.events.tracks && !S.events.tracks[S.track])
    S.track = Object.keys(S.events.tracks)[0];
  const info = [S.song.bpm+'BPM', (S.events?S.events.bars:'?')+'小节',
    (S.events?S.events.seconds+'s':''), S.song.style||'', 'ref='+(S.render.ref||'-')].join(' · ');
  $('songInfo').textContent = info;
  renderAll(); loadFiles();
  await prepAudio();
  loadMetrics();
}
/* 每个绘制环节**各自兜异常**：原来 renderAll 是一条直线，任何一环抛错 → 后面的全不画
 * （用户报"卷帘是空的"就是 `renderRoll()` 在没数据时抛 TypeError 把整块吃掉）。
 * 现在：单环失败不影响其它，失败原因收集到 `S.drawErrs` 并在卷帘上显示出来 —— 
 * 宁可画一句"哪里错了"，也不要画一片空白。 */
function renderAll(){
  S.drawErrs = [];
  for (const fn of [renderTracks, renderStrip, renderSecEdit, renderRoll,
                    renderStrips, drawWave, renderSearchSec]) {
    try { fn(); } catch (e) {
      S.drawErrs.push(((fn.name||'?')+': '+(e&&e.message?e.message:e)).slice(0,160));
    }
  }
  if (S.drawErrs.length) {
    log('!! 绘制报错（不影响其它区块）：' + S.drawErrs.join(' | '));
    setStatus('有 ' + S.drawErrs.length + ' 个绘制环节报错，见日志');
  }
}

/* ---------------- 候选搜索 ---------------- */
const SPEED={fast:{bars:4,budget:24,time:60},normal:{bars:8,budget:48,time:120},deep:{bars:16,budget:96,time:300}};
function renderSearchSec(){
  const sel=$('searchSec');
  sel.options = sel.options || [];
  if(sel.options.length!==(S.song.sections||[]).length){
    sel.innerHTML='';
    (S.song.sections||[]).forEach((s,i)=>{
      const o=document.createElement('option'); o.value=i;
      o.textContent=s.name+'（'+s.bars+' 小节）';
      sel.appendChild(o);
    });
    const worst=(S.song.sections||[]).map((s,i)=>[s.bars,i]).sort((a,b)=>b[0]-a[0])[0];
    if(worst) sel.value=worst[1];
  }
  const sp=SPEED[$('searchSpeed').value]||SPEED.normal;
  $('searchEta').textContent='切片最长 '+sp.bars+' 小节 · 最多 '+sp.budget+' 次渲染 / '+sp.time+'s'
    +'（实测约 0.4s/次 → 预计 '+Math.ceil(sp.budget*0.4/Math.max(1,parseInt($('searchWorkers').value||'4',10)))+' 秒）';
}
async function previewSection(){
  if(S.previewOn){ await backToFullSong('已回到整曲播放'); return; }   // 再点一次 → 回整曲
  if(S.dirty) await saveSong(true);
  const bars=SPEED[$('searchSpeed').value].bars;          // 与搜索用同一个"切片长度"档位
  const d=await api('/api/job?id='+encodeURIComponent(S.sid)+'&kind=preview',
    {method:'POST',headers:{'content-type':'application/json'},
     body:JSON.stringify({opts:{sec:S.sec,bars}})});
  if(!d.ok) return alert(d.error);
  S.job=d.job; S.jobKind='preview'; log('渲染本段切片中（约 1 秒）…'); pollJob();
}
async function playPreview(){
  const url='/api/audio?id='+encodeURIComponent(S.sid)+'&kind=preview&sec='+S.sec+'&t='+Date.now();
  try{
    await ENG.loadMaster(url);
    const dur=ENG.state().dur||0;
    ENG.setLoop([0,dur]); $('loopChk').checked=true; $('loopA').value='0';
    $('loopB').value=dur.toFixed(1);
    ENG.stop(); await ENG.play('master');
    S.previewOn=true; $('btnPreview').textContent='↩ 回整曲'; rAF();
    log('循环试听第 '+S.sec+' 段（切片 '+dur.toFixed(1)+'s）—— 改完再点一次按钮即可重听');
  }catch(e){ log('试听失败：'+e.message); }
}
/* 🎧 试听**某一轨**：把这一轨的临时渲染接进 ENG 的**完整播放通道**
 * （进度条 / 波形 / 播放头 / ⏹ 停止都能用），而不是塞进一个没有控件的 <audio>。
 *
 * ⚠ 原来这一步根本不存在：`pollJob()` 完成后对**所有**任务一律
 *   `S.player.src = '…kind=mix…'`（整曲）且**从不 play()** —— 服务端明明已经把
 *   `solo_<轨>_<时间>.ogg` 渲染好了（`jobs_dir` 就是 `TMP_AUDIO`，`/api/audio?kind=solo`
 *   也早就就绪），前端却既不取也不播 → 用户点"🎧 试听"**什么都不会响**。
 * 现在：试听 = 临时替换主音源；点 ▶ 播放或"↩ 回整曲"会换回整曲（避免"以为在听整曲"）。 */
async function playSolo(tr){
  const url='/api/audio?id='+encodeURIComponent(S.sid)+'&kind=solo&track='+encodeURIComponent(tr)
            +'&t='+Date.now();
  try{
    ENG.stop();
    const ok=await ENG.loadMaster(url);
    if(!ok){ log('试听 '+tr+' 失败：拿不到该轨音频（服务端 /api/audio?kind=solo）'); return; }
    S.soloTrack=tr; S.previewOn=true;
    $('btnPreview').textContent='↩ 回整曲';
    drawWave(); rAF();
    await ENG.play('master');
    log('🎧 试听 '+tr+'（只有这一轨，'+(ENG.state().dur||0).toFixed(1)+'s）——'
        +'点"↩ 回整曲"或按 ▶ 回到整曲');
  }catch(e){ log('试听 '+tr+' 失败：'+(e&&e.message?e.message:e)); }
}
/* 回到整曲音源（试听某轨 / 试听某段 之后共用） */
async function backToFullSong(why){
  S.soloTrack=null; S.previewOn=false; $('btnPreview').textContent='⚡ 试听本段';
  await prepAudio(); drawWave();
  if(why) log(why);
}

async function startSearch(){
  if(S.dirty) await saveSong(true);
  const sp=SPEED[$('searchSpeed').value]||SPEED.normal;
  const opts={bars:sp.bars,budget:sp.budget,time:sp.time,
              workers:parseInt($('searchWorkers').value||'4',10),
              sec:parseInt($('searchSec').value||'-1',10)};   // 默认不写回：结果给"逐项采用"表
  $('searchOut').innerHTML='搜索中…（每完成一个候选就追加一行）';
  S.searchRows=[];
  const d=await api('/api/job?id='+encodeURIComponent(S.sid)+'&kind=search',
    {method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({opts})});
  if(!d.ok) return alert(d.error);
  S.job=d.job; S.jobKind='search'; $('jobInfo').textContent='· 候选搜索运行中…';
  pollJob();
}
async function stopSearch(){
  if(!S.job) return;
  await api('/api/stop?id='+encodeURIComponent(S.job),{method:'POST'});
  log('已请求停止搜索');
}
function renderAdopt(){
  const r=S.searchResult; if(!r||!r.best) return '';
  const cur=S.song.patterns||{};
  let rows='';
  for(const k of Object.keys(r.best.params||{})){
    const v=r.best.params[k];
    if(JSON.stringify(v)===JSON.stringify(cur[k])) continue;
    const safe=(r.suggest_keys||[]).includes(k);
    const risky=(r.struct_keys||[]).includes(k);
    rows+=`<tr><td><input type="checkbox" data-k="${k}" ${safe?'checked':''}></td>
      <td>${k}</td><td class="hint">${JSON.stringify(cur[k])} → ${JSON.stringify(v)}</td>
      <td class="${risky?'bad':(safe?'ok':'')}">${risky?'性格参数：改律动/调域，先⚡试听':(safe?'织体参数':'—')}</td></tr>`;
  }
  if(!rows) return '<div class="hint">搜索结果与当前一致，无需采用。</div>';
  return `<table class="mini"><tr><th></th><th>参数</th><th>当前 → 建议</th><th>类型</th></tr>${rows}</table>
    <div class="row"><button id="btnAdopt" class="primary">采用勾选项（写回 song.json）</button>
    <span class="hint">采用后点 ⚡ 试听本段 或 🔊 渲染 对比；不满意 Ctrl+Z 撤销</span></div>`;
}
function adoptSearch(){
  const r=S.searchResult; if(!r) return;
  const picked=[...document.querySelectorAll('#searchOut input[data-k]')]
                 .filter(i=>i.checked).map(i=>i.dataset.k);
  if(!picked.length) return log('没有勾选任何参数');
  pushUndo();
  S.song.patterns=S.song.patterns||{};
  for(const k of picked) S.song.patterns[k]=r.best.params[k];
  S.dirty=true; renderSearchSec();
  saveSong(true).then(()=>log('已采用：'+picked.join(', ')+'（已写回 song.json）——点 ⚡ 试听本段 听效果'));
}
function renderSearchLog(txt){
  const box=$('searchOut');
  const lines=(txt||'').split('\n').filter(l=>/^\s*\d{3}\s+mad=|^\s*★|^RESULT |^搜索目标|^基线/.test(l));
  if(!lines.length) return;
  let rows='';
  for(const l of lines){
    const m=l.match(/^\s*(\d{3})\s+mad=([\d.]+)(?:\s+占用罚=([\d.]+))?\s+(.*)$/);
    if(m){
      const bad=parseFloat(m[2])>4?'bad':(parseFloat(m[2])>2?'warn':'ok');
      rows+=`<tr><td>${m[1]}</td><td class="${bad}">${m[2]}</td><td>${m[3]||'-'}</td><td class="hint">${m[4]}</td></tr>`;
      continue;
    }
    if(/^\s*★/.test(l)) rows+=`<tr><td colspan="4" class="ok">${l.trim()}</td></tr>`;
    else rows+=`<tr><td colspan="4" class="hint">${l.trim().slice(0,220)}</td></tr>`;
  }
  const rl=(txt||'').split('\n').find(l=>l.startsWith('RESULT '));
  if(rl){ try{ S.searchResult=JSON.parse(rl.slice(7)); }catch(e){ S.searchResult=null; } }
  box.innerHTML=`<table class="mini"><tr><th>#</th><th>mad</th><th>占用罚</th><th>参数</th></tr>${rows}</table>`
    + renderAdopt();
  const b=$('btnAdopt'); if(b) b.onclick=adoptSearch;
}

/* ---------------- 音轨（按音轨制作） ---------------- */
/* 音轨清单 / 音色 / 混音的**统一取数**（面板、混音台、段落 CC7、快捷键全用这里）。
 *
 * ⚠ 原来一律读 `S.song.programs` / `S.song.mix`，但 **song.json 里可以根本没有这两个字段**：
 *   引擎侧的真值是 `DEFAULT + 风格预设(style) + song.json 覆盖`（song_engine.py 345-351），
 *   未覆盖的曲目（如 42_gtr_tender）`song.programs === null` → `Object.keys(null||{})` = []
 *   → **一个音轨卡都不渲染**，左侧面板只剩下面的"产物/大小"表（用户报"只有产物和大小"）。
 *   实测：`/api/song` 返回的 song 键里没有 programs/mix，而 events.programs/mix 有全部 9 轨。
 *
 * 取数顺序：song.json 显式覆盖 > events 实测值 > 默认值。
 * **不**把 events 的值预写进 S.song —— 只在用户真改某一轨时才写该轨，
 * 免得"什么都没改却把 9 轨全写进 song.json"，把交付物的 diff 撑大。 */
function trackRows(){
  const ev=S.events||{}, et=ev.tracks||{}, ep=ev.programs||{}, em=ev.mix||{};
  const sp=(S.song&&S.song.programs)||{}, sm=(S.song&&S.song.mix)||{};
  const names=[];
  for(const k of Object.keys(et)) names.push(k);
  for(const k of Object.keys(ep)) if(names.indexOf(k)<0) names.push(k);
  for(const k of Object.keys(sp)) if(names.indexOf(k)<0) names.push(k);
  return names.map(t=>{
    const e1=ep[t]||[], e2=em[t]||[];
    const s1=sp[t]||[], s2=sm[t]||[];
    return {name:t, notes:(et[t]&&et[t].n)||0, hasTrack:!!et[t],
      prog:(t in sp)?(s1[0]===undefined?null:s1[0]):(t in ep?(e1[0]===undefined?null:e1[0]):null),
      chan:(t in sp&&s1[1]!==undefined)?s1[1]:(e1[1]===undefined?0:e1[1]),
      pan:(t in sm&&s2[0]!==undefined)?s2[0]:(e2[0]===undefined?64:e2[0]),
      vol:(t in sm&&s2[1]!==undefined)?s2[1]:(e2[1]===undefined?80:e2[1])};
  });
}
/* 写回 song.json 时**按需建字段**（song.json 允许没有 programs/mix）。
 * ⚠ 必须"改哪个建哪个"：原来这个函数**无条件**把 `programs`/`mix` 都建成 `{}`，
 *   于是"只拖了一下音量"也会往交付物里塞一个空的 `"programs": {}`（实测污染了
 *   42_gtr_tender/song.json）—— 写空对象没有任何语义，纯粹是噪音。 */
function songProgramsSlot(){
  if(!S.song) return null;
  if(!S.song.programs || typeof S.song.programs!=='object') S.song.programs={};
  return S.song.programs;
}
function songMixSlot(){
  if(!S.song) return null;
  if(!S.song.mix || typeof S.song.mix!=='object') S.song.mix={};
  return S.song.mix;
}
/* 从"当前显示值"补一个完整的 [prog, chan] / [pan, vol] 再改一维 —— 只改一维时另一维必须沿用
 * 真实通道号（写错通道会撞车，check_song 的 channels_and_programs 会拦）。 */
function setTrackProg(t, prog){
  const p=songProgramsSlot(); if(!p) return;
  const r=trackRows().find(x=>x.name===t)||{chan:0};
  p[t]=[prog, (p[t]&&p[t][1]!==undefined)?p[t][1]:r.chan];
}
function setTrackMix(t, i, v){
  const m=songMixSlot(); if(!m) return;
  const r=trackRows().find(x=>x.name===t)||{pan:64, vol:80};
  const cur=m[t]||[r.pan, r.vol];
  m[t] = i===0 ? [v, cur[1]] : [cur[0], v];
}
function renderTracks(){
  const ev = S.events; const box=$('trackList'); box.innerHTML='';
  if(!ev) return;
  for(const row of trackRows()){
    const t=row.name, notes=row.notes, prog=row.prog, mix=[row.pan,row.vol];
    const el=document.createElement('div'); el.className='track'+(t===S.track?' sel':'');
    el.dataset.track=t;          // 供外部脚本/验收定位这一轨（卡片文本是"Melody338 音"，粘在一起不好解析）
    el.onclick=(e)=>{ if(e.target.tagName==='INPUT'||e.target.tagName==='BUTTON')return;
      S.track=t; renderAll(); };
    el.innerHTML = `<div class="tname"><span class="swatch" style="background:${COLOR[t]||'#888'}"></span>
      ${t}<span class="n">${notes} 音${row.hasTrack?'':'（无音符）'}</span></div>
      <div class="row"><label>音色</label>
        <input type="number" min="0" max="127" value="${prog===null?'':prog}" style="width:56px"
          data-k="prog"><span class="dim">${prog===null?'鼓组':(GM[prog]||('GM'+prog))}${
          (S.song.programs&&(t in S.song.programs))?'':' · 预设'}</span></div>
      <div class="row"><label>音量</label><input type="range" min="0" max="127" value="${mix[1]}"
        data-k="vol"><span class="dim">${mix[1]}</span></div>
      <div class="row"><label>声像</label><input type="range" min="0" max="127" value="${mix[0]}"
        data-k="pan"><span class="dim">${mix[0]}</span>
        <button class="mini" data-k="solo">🎧 试听</button></div>`;
    el.querySelectorAll('input').forEach(inp=>{
      inp.onfocus = ()=>pushUndo();
      inp.oninput = ()=>{
        const k=inp.dataset.k, v=parseInt(inp.value||'0',10);
        // ⚠ 只改被编辑的那一维，另一维沿用**真实值**（通道 / 另一维混音）：
        //   原来读 S.song.programs[t] 兜底 → null 时通道退化成 0 → 会撞车；
        //   现在走 setTrackProg/setTrackMix 统一从 trackRows() 取真实值。
        if(k==='prog'){ setTrackProg(t, isNaN(v)?null:v); }
        else if(k==='vol'){ setTrackMix(t, 1, isNaN(v)?0:v); }
        else { setTrackMix(t, 0, isNaN(v)?0:v); }
        S.dirty=true; renderTracks();
      };
    });
    const b=el.querySelector('button[data-k=solo]');
    if(b) b.onclick=()=>startJob('solo:'+t, '试听 '+t);
    box.appendChild(el);
  }
}

/* ---------------- 段落编辑 ---------------- */
function renderStrip(){
  const st=$('secStrip'); st.innerHTML='';
  (S.song.sections||[]).forEach((sec,i)=>{
    const d=document.createElement('div'); d.className='sec'+(i===S.sec?' sel':'');
    d.style.width = Math.max(44, sec.bars*14)+'px';
    d.innerHTML = `<b>${sec.name}</b>${sec.bars}小节`;
    d.title = (sec.chords||[]).join(' / ');
    d.onclick=()=>{S.sec=i;renderAll();};
    st.appendChild(d);
  });
}
function renderSecEdit(){
  const sec=(S.song.sections||[])[S.sec]; const box=$('secEdit');
  if(!sec){box.innerHTML='';return;}
  const chords=Object.keys(S.song.chords||{});
  let h = `<div class="row"><b>${sec.name}</b>
    <label class="dim">小节数 <input type="number" min="1" max="32" value="${sec.bars}"
      id="secBars" style="width:52px"></label>
    <label class="dim">力度 <input type="range" min="30" max="100" value="${Math.round((sec.arr.vel||1)*100)}"
      id="secVel"></label><span class="dim">${fmt((sec.arr.vel||1),2)}</span>
    <label class="dim">旋律 <input value="${sec.melody||''}" id="secMel" style="width:70px"></label>
    </div><div class="chips">`;
  for(const f of FLAGS_ALL){
    const on = f==='perc' ? (sec.arr.perc||0)>0 : !!sec.arr[f];
    h += `<span class="chip${on?' on':''}" data-f="${f}">${f}${f==='perc'&&on?':'+sec.arr.perc:''}</span>`;
  }
  h += `</div><div class="chips dim">每轨在此段的 CC7：</div><div class="grid">`;
  for(const row of trackRows()){
    const t=row.name, v=((sec.arr.mix||{})[t]);
    h += `<div class="cell"><div class="dim">${t}</div>
      <input type="range" min="0" max="127" value="${v===undefined?row.vol:v}"
        data-mix="${t}"><span class="dim">${v===undefined?'(全局)':v}</span></div>`;
  }
  box.innerHTML=h;
  $('secBars').onfocus=()=>pushUndo();
  $('secBars').oninput=(e)=>{sec.bars=Math.max(1,parseInt(e.target.value||'1',10));S.dirty=true;renderStrip();};
  $('secVel').oninput=(e)=>{sec.arr.vel=parseInt(e.target.value,10)/100;S.dirty=true;};
  $('secMel').onchange=(e)=>{sec.melody=e.target.value;S.dirty=true;};
  box.querySelectorAll('.chip').forEach(c=>c.onclick=()=>{
    pushUndo();
    const f=c.dataset.f;
    if(f==='perc'){ const cur=(sec.arr.perc||0); sec.arr.perc = cur?0:1;
      if(sec.arr.perc && (sec.arr.mix||{}).Perc===undefined) (sec.arr.mix=sec.arr.mix||{}).Perc=100; }
    else sec.arr[f]=!sec.arr[f];
    S.dirty=true; renderSecEdit();
  });
  box.querySelectorAll('input[data-mix]').forEach(inp=>{
    inp.onfocus=()=>pushUndo();
    inp.oninput=()=>{
      const t=inp.dataset.mix; sec.arr.mix=sec.arr.mix||{}; sec.arr.mix[t]=parseInt(inp.value,10);
      S.dirty=true; renderTracks();
    };
  });
}

/* ---------------- 钢琴卷帘（和弦导引 / 强拍合规 / 增删拖 / 缩放） ---------------- */
const NOTE_PC = {C:0,'C#':1,Db:1,D:2,'D#':3,Eb:3,E:4,F:5,'F#':6,Gb:6,G:7,'G#':8,Ab:8,A:9,'A#':10,Bb:10,B:11};
const QUALITIES = ['','m','7','maj7','maj9','m7','6','m6','5','sus4','7sus4','sus2','dim','m7b5','aug','add9','m9','9'];
function totalBeats(){return (S.song.sections||[]).reduce((a,s)=>a+s.bars*4,0);}
function sectionStartBeats(i){let t=0;for(let k=0;k<i;k++)t+=S.song.sections[k].bars*4;return t;}
function sectionOfBeat(b){let t=0;const a=S.song.sections||[];
  for(let i=0;i<a.length;i++){ if(b>=t&&b<t+a[i].bars*4) return i; t+=a[i].bars*4; } return -1;}
function chordAtBeat(b){const i=sectionOfBeat(b); if(i<0)return null;
  const sec=S.song.sections[i], rel=b-sectionStartBeats(i);
  return (sec.chords||[])[Math.floor(rel/4)]||null;}
function chordTones(name){const c=(S.song.chords||{})[name]; if(!c)return [];
  return (c[1]||[]).map(m=>m%12);}
function isStrongBeat(b){ // 每小节第 1、3 拍（与 selftest 的判据一致）
  const inBar=b-Math.floor(b/4)*4; return Math.abs(inBar-0)<1e-6||Math.abs(inBar-2)<1e-6;}
function melodyNotes(){
  const out=[]; let t0=0;
  (S.song.sections||[]).forEach((sec,i)=>{
    const key=sec.melody, arr=(S.song.melody||{})[key];
    if(Array.isArray(arr)) for(const n of arr) out.push({sec:i,beat:t0+n[0]*4+(n[1]||0),
      dur:n[2]||1,pitch:n[3],vel:96*((sec.arr.vel)||1),editable:true,raw:n});
    t0+=sec.bars*4;
  });
  return out;
}
function allNotes(){
  const ev=S.events; const list=[];
  if(ev) for(const t of Object.keys(ev.tracks))
    for(const n of ev.tracks[t].notes){
      if(t==='Melody' && n[3]>80) continue;          // 主奏"作者音"由本地编辑版接管
      list.push({track:t,beat:n[0],dur:n[1],pitch:n[2],vel:n[3],editable:false});
    }
  for(const n of melodyNotes()) list.push({track:'Melody',beat:n.beat,dur:n.dur,pitch:n.pitch,
    vel:n.vel,editable:true,sec:n.sec,raw:n.raw});
  return list;
}
function rollView(){                                  // 缩放/滚动窗口（单位：拍）
  const tb=totalBeats()||1, z=S.zoom||1, span=tb/z;
  let st=S.viewStart||0; st=Math.max(0,Math.min(tb-span,st));
  return {st, span, tb, z};
}
function rollGeom(){
  const c=$('roll');
  /* ⚠ 尺寸必须**兜底**：`clientWidth` 在布局未完成/被隐藏时可能是 0，
   *   此时 `c.width = c.clientWidth * dpr = 0` → 画得再对也**看不见**（用户报"卷帘是空的"，
   *   而左侧音轨卡与波形都正常，就是这个：canvas 尺寸被置 0 了）。
   *   另外**只在尺寸真的变了才重设**（每帧改写 canvas 尺寸会清空画布 + 掉帧）。 */
  const cssW = Math.max(320, c.clientWidth || (c.parentElement && c.parentElement.clientWidth) || 1200);
  const cssH = 360;
  const dpr = devicePixelRatio || 1;
  const wantW = Math.round(cssW * dpr), wantH = Math.round(cssH * dpr);
  if (c.width !== wantW) { c.width = wantW; c.style.width = cssW + 'px'; }
  if (c.height !== wantH) { c.height = wantH; c.style.height = cssH + 'px'; }
  const W = c.width, H = c.height;
  const notes=allNotes();
  let lo=48,hi=88;
  if(notes.length){lo=Math.min(...notes.map(n=>n.pitch))-2;hi=Math.max(...notes.map(n=>n.pitch))+2;}
  const v=rollView(), tb=v.tb||1;
  return {c,W,H,ctx:c.getContext('2d'),tb,lo,hi,v,
    x:(b)=>(b-v.st)/v.span*W, y:(p)=>H-(p-lo+1)*H/(hi-lo+1),
    px:(x)=>v.st+x/vW(x)*v.span, ppy:(y)=>hi-(y/(H/(hi-lo+1)))-1};
  function vW(){return c.width;}
}
/* ⚠ 画完**自检**：canvas 上到底有没有东西，肉眼和日志都靠不住（用户报"卷帘是空的"
 *   而所有数据/音轨卡都正常）。这里画完直接数一次非背景像素；若是空的就
 *   **按当前布局重置尺寸再重画一次**（最多 3 轮）—— 尺寸为 0/被改小是这类症状的常见根因，
 *   重画之后仍为空则把原因记进 `S.rollEmptyWhy`，由页面自检显示出来。 */
function rollPainted(c, W, H) {
  try {
    const d = c.getContext('2d').getImageData(0, 0, Math.min(W, 600), Math.min(H, 300)).data;
    let n = 0;
    for (let i = 0; i < d.length; i += 4) {
      if (d[i + 3] === 0) continue;
      if (Math.abs(d[i] - 15) + Math.abs(d[i + 1] - 17) + Math.abs(d[i + 2] - 23) > 24) n++;
    }
    return n;
  } catch (e) { S.rollEmptyWhy = '像素读取失败: ' + e.message; return -1; }
}
function renderRoll(){
  let tries = 0;
  for (;;) {
    const g = rollGeom();
    paintRoll(g);
    if (rollPainted(g.c, g.W, g.H) !== 0 || tries >= 3) break;
    // 画了但画布是空的 → 强制按布局重置尺寸再试（尺寸为 0/被改小是常见根因）
    const dpr = devicePixelRatio || 1;
    const cssW = Math.max(320, g.c.clientWidth ||
                          (g.c.parentElement && g.c.parentElement.clientWidth) || 1200);
    S.rollEmptyWhy = '画布尺寸 ' + g.c.width + '×' + g.c.height + '（clientWidth ' +
                     g.c.clientWidth + '）绘制后为空，已第 ' + (tries + 1) + ' 次重置重画';
    g.c.width = Math.round(cssW * dpr);
    g.c.height = Math.round(360 * dpr);
    g.c.style.width = cssW + 'px';
    g.c.style.height = '360px';
    tries++;
  }
  if (tries === 0) { S.rollEmptyWhy = null; window.__rollWhy = null; }
  else { window.__rollWhy = S.rollEmptyWhy; }   // 暴露给页面自检（S 是模块作用域，外部读不到）
}
function paintRoll(g){
  const {ctx,W,H,v}=g;
  ctx.clearRect(0,0,W,H); ctx.fillStyle='#0f1117'; ctx.fillRect(0,0,W,H);
  /* ⚠ 曲目还没加载进来时必须**自己说清楚**（用户报"卷帘是空的"）：
   *   原来这里直接 `S.song.sections` → 未加载时抛 TypeError → 整个重绘中断 →
   *   画布保持空白、也没有任何提示，看起来就是"功能坏了"。
   *   所以：没数据就画一句人话（并说明怎么修），而不是让异常把画面吃掉。 */
  if(!S.song){
    S.rollG=null;                                   // 没数据 → 播放头也不该停在旧位置
    ctx.fillStyle='#8b90a4'; ctx.font=(13*(devicePixelRatio||1))+'px sans-serif';
    ctx.fillText(S.loadErr?('曲目加载失败：'+S.loadErr):'还没加载曲目 —— 左上角选一首（或按 ↻ 刷新）',
                 14, 26);
    if(S.loadErr){
      ctx.fillStyle='#ffb454';
      ctx.fillText('若服务刚重启：刷新页面（Ctrl+F5）后重试', 14, 48);
    }
    return;
  }
  // ① 和弦内音导引带（按小节的和弦）
  const rowH=H/(g.hi-g.lo+1);
  for(let b=Math.floor(v.st/4)*4; b<v.st+v.span+4; b+=4){
    const cn=chordAtBeat(b+0.01); if(!cn) continue;
    const tones=chordTones(cn);
    ctx.fillStyle='rgba(87,209,139,.07)';
    for(const pc of tones){ for(let p=g.lo;p<=g.hi;p++) if(p%12===pc) ctx.fillRect(g.x(b),g.y(p),g.x(b+4)-g.x(b),rowH); }
  }
  // ② 段落底色 + 边界 + 和弦名
  let t0=0;
  (S.song.sections||[]).forEach((sec,i)=>{
    const x0=g.x(t0), x1=g.x(t0+sec.bars*4);
    ctx.fillStyle=(i%2)?'rgba(255,255,255,.02)':'rgba(255,255,255,.05)';
    ctx.fillRect(x0,0,Math.max(0,x1-x0),H);
    ctx.strokeStyle='#5cc8ff'; ctx.globalAlpha=.5; ctx.beginPath();
    ctx.moveTo(x0,0); ctx.lineTo(x0,H); ctx.stroke(); ctx.globalAlpha=1;
    ctx.fillStyle='#8b90a4'; ctx.font=(11*devicePixelRatio)+'px sans-serif';
    if(x1-x0>26) ctx.fillText(sec.name,x0+3,12*devicePixelRatio);
    for(let b=0;b<sec.bars;b++){
      const xb=g.x(t0+b*4);
      ctx.strokeStyle='rgba(255,255,255,.08)'; ctx.beginPath(); ctx.moveTo(xb,0); ctx.lineTo(xb,H); ctx.stroke();
      const ch=(sec.chords||[])[b];
      if(ch && (x1-x0)>60) { ctx.fillStyle='#6f7690'; ctx.fillText(ch,xb+3,H-4*devicePixelRatio); }
    }
    t0+=sec.bars*4;
  });
  // ③ 循环区间
  const [la,lb]=ENG.state().loop||[null,null];
  if(la!=null&&lb!=null){ const spb=60/(S.song.bpm||120);
    ctx.fillStyle='rgba(255,107,107,.10)';
    ctx.fillRect(g.x(la/spb),0,g.x(lb/spb)-g.x(la/spb),H); }
  // ④ 音符（强拍违规描红）
  for(const n of allNotes()){
    const sel=n.track===S.track;
    ctx.fillStyle=COLOR[n.track]||'#888';
    ctx.globalAlpha=sel?0.95:0.3;
    const x=g.x(n.beat), w=Math.max(2,g.x(n.beat+n.dur)-x), y=g.y(n.pitch), h=Math.max(2,rowH-1);
    ctx.fillRect(x,y,w,h);
    if(n.editable){
      const cn=chordAtBeat(n.beat), pc=n.pitch%12;
      const bad = cn && isStrongBeat(n.beat) && !chordTones(cn).includes(pc);
      ctx.globalAlpha=1; ctx.lineWidth=bad?2:1;
      ctx.strokeStyle = bad ? '#ff6b6b' : (S.selNote===n ? '#5cc8ff' : 'rgba(255,255,255,.5)');
      ctx.strokeRect(x,y,w,h);
      if(S.selNote===n){ ctx.fillStyle='#fff'; ctx.fillRect(x+w-3,y,3,h); }   // 右边缘=改时值
    }
    ctx.globalAlpha=1;
  }
  // 左上角自证一行：**音符数与范围**（用户报"卷帘是空的"时，一眼就能分清是
  // "没数据"还是"画不出来" —— 有这行数字就不用再来回猜）
  try {
    // ⚠ 用 `allNotes()` 现取，别引用 `notes`：`renderRoll` 里音符是直接
    //   `for(const n of allNotes())` 遍历的，**没有** `notes` 变量 ——
    //   写成 `notes.length` 会抛 ReferenceError，被这里的 try 吞掉，
    //   结果就是"加了自证行却什么也没显示"（实测踩过）。
    const ns = allNotes();
    const tot = ns.length, ed = ns.filter(n=>n.editable).length;
    ctx.globalAlpha=1;
    ctx.fillStyle='rgba(15,17,23,.72)';
    ctx.fillRect(0,0,240,18);
    ctx.fillStyle = tot ? '#7f8a9a' : '#ffb454';
    ctx.font=(11*(devicePixelRatio||1))+'px sans-serif';
    ctx.fillText((S.sid||S.cur||'')+' · 音符 '+tot+'（可编辑 '+ed+'）', 6, 13);
    // 有绘制错误就在卷帘上写出来（否则用户只能看到"空的"）
    if (S.drawErrs && S.drawErrs.length) {
      ctx.fillStyle='#ff6b6b';
      ctx.fillText('绘制错误：' + S.drawErrs[0], 6, 30);
    }
  } catch(e) { /* 自证信息不该影响主绘制 */ }
  S.rollG=g;                       // 播放头用**同一次绘制**的几何（保证与音符严格对齐）
  drawPlayhead(g);
}
/* 播放头：**独立 DOM 叠加层**（#rollPh），不再画在卷帘画布上。
 * 原实现每帧往画布上补一条 2px 红线而**从不擦除上一帧的** —— 播放时残影叠成红带，
 * 拖进度条时位置大跨度跳跃、旧线全部留下，看起来就是"满屏红色竖条"（用户报的
 * "拖动进度条之后还有显示bug"）。现在只改 transform：不重绘画布、不触发排版、
 * 也不可能有残影（canvas 上零个播放头像素）。 */
function drawPlayhead(g0){
  const ph=$('rollPh'); if(!ph) return;
  const g=g0||S.rollG;
  if(!g||!S.events||!S.song){ ph.style.opacity='0'; return; }
  const dpr=devicePixelRatio||1;
  const x=g.x(ENG.position()/(60/(S.song.bpm||120)));
  if(!(x>=0&&x<=g.W)){ ph.style.opacity='0'; return; }   // 视野外 → 隐藏（NaN 也走这里）
  ph.style.opacity='1';
  ph.style.transform='translateX('+(x/dpr).toFixed(1)+'px)';
}
function rollHit(mx,my){
  const g=rollGeom(), beat=g.v.st+mx/g.W*g.v.span, pitch=Math.round(g.ppy(my));
  let near=null;
  for(const n of allNotes().filter(n=>n.track===S.track)){
    if(beat>=n.beat-0.06 && beat<=n.beat+n.dur+0.06 && pitch===n.pitch){
      if(near===null || Math.abs(beat-n.beat)<Math.abs(beat-near.beat)) near=n;
    }
  }
  if(near) return {note:near, edge:Math.abs(beat-(near.beat+near.dur))<g.v.span*0.03, beat, pitch};
  return {miss:true, beat, pitch};
}
function rollDown(e){
  const rect=$('roll').getBoundingClientRect();
  const mx=(e.clientX-rect.left)*devicePixelRatio, my=(e.clientY-rect.top)*devicePixelRatio;
  const hit=rollHit(mx,my);
  if(!hit.miss){
    S.selNote=hit.note;
    if(!hit.note.editable){ renderRoll(); return; }
    pushUndo();
    S.drag={note:hit.note, mode: hit.edge?'resize':(e.altKey?'vel':'move'),
            b0:hit.note.beat, p0:hit.note.pitch, d0:hit.note.dur, v0:hit.note.vel||80};
  }else{
    if(S.track!=='Melody') { renderRoll(); return; }
    const secIdx=sectionOfBeat(hit.beat); if(secIdx<0) return;
    pushUndo();
    const snapped=Math.round(hit.beat*2)/2;
    const secStart=sectionStartBeats(secIdx), secBars=S.song.sections[secIdx].bars;
    const rel=Math.max(0,Math.min(secBars*4-0.5,snapped-secStart));
    const mel=(S.song.sections[secIdx].melody)||'';
    if(!Array.isArray(S.song.melody[mel])) S.song.melody[mel]=[];
    const raw=[Math.floor(rel/4), +(rel-Math.floor(rel/4)*4).toFixed(2), 1, hit.pitch];
    S.song.melody[mel].push(raw); S.dirty=true;
    const note={beat:snapped,pitch:hit.pitch,dur:1,vel:96,editable:true,raw,sec:secIdx};
    S.selNote=note; S.drag={note, mode:'resize', b0:snapped, p0:hit.pitch, d0:1, v0:96};
  }
  renderRoll();
}
function rollMove(e){
  const d=S.drag; if(!d) return;
  const rect=$('roll').getBoundingClientRect();
  const mx=(e.clientX-rect.left)*devicePixelRatio, my=(e.clientY-rect.top)*devicePixelRatio;
  const g=rollGeom();
  const beat=g.v.st+mx/g.W*g.v.span, pitch=Math.round(g.ppy(my));
  const raw=d.note.raw; if(!raw) return;
  const start=sectionStartBeats(d.note.sec), secBars=S.song.sections[d.note.sec].bars;
  if(d.mode==='move'){
    let b=Math.round(beat*2)/2; b=Math.max(start,Math.min(start+secBars*4-0.5,b));
    const rel=b-start;
    raw[0]=Math.floor(rel/4); raw[1]=+(rel-Math.floor(rel/4)*4).toFixed(2);
    raw[3]=Math.max(0,Math.min(127,pitch));
    d.note.beat=b; d.note.pitch=raw[3];
  }else if(d.mode==='resize'){
    let dur=Math.round((beat-d.note.beat)*2)/2; dur=Math.max(0.5,Math.min(secBars*4,dur));
    raw[2]=dur; d.note.dur=dur;
  }else{                                        // Alt=力度
    const dv=(d.p0-pitch)*4; d.note.vel=Math.max(1,Math.min(127,(d.v0||80)+dv));
  }
  S.dirty=true; renderRoll();
}
function rollUp(){ if(S.drag){S.drag=null;} renderRoll(); }
function rollContext(e){
  e.preventDefault();
  const rect=$('roll').getBoundingClientRect();
  const hit=rollHit((e.clientX-rect.left)*devicePixelRatio,(e.clientY-rect.top)*devicePixelRatio);
  if(hit.miss||!hit.note.editable) return;
  pushUndo();
  for(const k in S.song.melody){const a=S.song.melody[k];const i=a.indexOf(hit.note.raw);if(i>=0)a.splice(i,1);}
  S.selNote=null; S.dirty=true; renderRoll();
}
function rollWheel(e){
  const g=rollGeom();
  if(e.ctrlKey){ e.preventDefault();
    S.zoom=Math.max(1,Math.min(16,(S.zoom||1)*(e.deltaY<0?1.25:0.8)));
  }else{ S.viewStart=Math.max(0,(S.viewStart||0)+ (e.deltaY>0?1:-1)*g.v.span*0.15); }
  renderRoll();
}

/* ---------------- 指标 ---------------- */
async function loadMetrics(){
  const w=S.metricWin?('&win='+encodeURIComponent(S.metricWin)):'';
  $('metrics').innerHTML='<div class="dim">测量中…（读音频 + 画像）</div>';
  const m=await api('/api/metrics?id='+encodeURIComponent(S.sid)+w);
  S.metrics=m; renderMetrics();
}
function bar(v,min,max){return Math.max(0,Math.min(100,(v-min)/(max-min)*100));}
function renderMetrics(){
  const box=$('metrics'); const m=S.metrics;
  if(!m||!m.ok){box.innerHTML='<div class="dim">'+((m&&m.error)||'还没渲染')+'</div>';return;}
  const mine=m.mine, ref=m.ref;
  let h=`<div class="kv"><span>RMS</span><b>${fmt(mine.rms)} dB<span class="dim"> / 参考 ${ref?fmt(ref.rms):'-'}</span></b></div>
    <div class="kv"><span>宽度</span><b>${fmt(mine.width,3)}<span class="dim"> / ${ref?fmt(ref.width,3):'-'}</span></b></div>
    <div class="kv"><span>质心</span><b>${Math.round(mine.centroid)} Hz<span class="dim"> / ${ref?Math.round(ref.centroid):'-'}</span></b></div>
    <div class="dim" style="margin:6px 0 2px">倍频程（上=本曲 下=参考，-25~+3dB 归一）</div>`;
  for(const k of Object.keys(mine.rel)){
    const rv = ref&&ref.bands? ref.bands[k] : null;
    h+=`<div class="bandrow"><span class="dim">${k}</span>
      <span class="bandbar"><i class="mine" style="width:${bar(mine.rel[k],-25,3)}%"></i>
      ${rv!==null?`<i class="ref" style="width:${bar(rv,-25,3)}%"></i>`:''}</span>
      <span class="${Math.abs((m.diff_rel||{})[k]||0)>3?'bad':'ok'}">${(m.diff_rel||{})[k]!==undefined?((m.diff_rel[k]>0?'+':'')+m.diff_rel[k]):'-'}</span></div>`;
  }
  h+=`<div class="dim" style="margin:8px 0 2px">占用率（"连续的墙" vs "点"，见坑 103）</div>`;
  for(const k of ['5000-10000','10000-18000','40-80','160-315']){
    const rv=ref&&ref.occ?ref.occ[k]:null;
    h+=`<div class="bandrow"><span class="dim">${k}</span>
      <span class="bandbar"><i class="mine" style="width:${bar(mine.occ[k],0,100)}%"></i>
      ${rv!==null?`<i class="ref" style="width:${bar(rv,0,100)}%"></i>`:''}</span>
      <span class="dim">${mine.occ[k]}%</span></div>`;
  }
  box.innerHTML=h;
}

/* ---------------- 任务 ---------------- */
async function startJob(kind,label){
  if(S.dirty){ await saveSong(true); }
  const d=await api('/api/job?id='+encodeURIComponent(S.sid)+'&kind='+encodeURIComponent(kind),
    {method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({song:S.song})});
  if(!d.ok) return alert(d.error);
  S.job=d.job; $('jobInfo').textContent='· '+label+' 运行中…'; $('log').textContent='';
  pollJob();
}
async function pollJob(){
  if(!S.job) return;
  const d=await api('/api/job?id='+encodeURIComponent(S.job));
  if(!d.ok) return;
  $('log').textContent=d.log||''; $('log').scrollTop=$('log').scrollHeight;
  if(S.jobKind==='search') renderSearchLog(d.log||'');
  $('jobInfo').textContent='· '+d.job.kind+' '+d.job.state+(d.job.rc!==null?(' rc='+d.job.rc):'');
  if(d.job.state==='running'){ setTimeout(pollJob,800); }
  else{ const wasKind=S.jobKind; const kind=(d.job&&d.job.kind)||wasKind||'';
    S.job=null; S.jobKind=null;
    if(wasKind==='preview' && d.job.state==='done'){ await playPreview(); return; }
    if(d.job.state==='done'){
      if(d.job.rc!==null && d.job.rc!==0){ log('任务失败（rc='+d.job.rc+'）：'+(d.log||'').split('\n').slice(-3).join(' / ')); }
      /* **按任务类型分派**：`solo:` 的产物必须**真的播出来**。
       * 原来这里不分类型，一律 `S.player.src='…kind=mix…'` 且从不 play() ——
       * 对 solo 试听来说等于"渲染完了没人听"（用户报"试听按钮没有用"）；
       * 而整曲音源本来就会由下面的 `loadSong() → prepAudio()` 重新载入，那行是死代码
       * （顺带还有个雷：用户先点过 ⏹ 之后 `S.player` 是 null，`S.player.src=` 会抛 TypeError）。 */
      if(kind.indexOf('solo:')===0){
        await loadSong();                       // 先回整曲（清掉上一次试听的状态）
        await playSolo(kind.slice(5));
        return;
      }
      await loadSong(); loadMetrics();
    }
  }
}
async function saveSong(quiet){
  const d=await api('/api/song?id='+encodeURIComponent(S.sid),
    {method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({song:S.song})});
  if(!d.ok){alert('保存失败：'+d.error);return false;}
  S.dirty=false; if(!quiet)$('log').textContent=d.log||'已保存';
  return true;
}
async function loadFiles(){
  const d=await api('/api/files?id='+encodeURIComponent(S.sid));
  if(!d.ok)return;
  let h='<table class="mini"><tr><th>产物</th><th>大小</th><th></th></tr>';
  for(const f of d.files) h+=`<tr><td>${f.name}</td><td>${(f.size/1048576).toFixed(2)}MB</td>
    <td><a href="${f.url}">下载</a></td></tr>`;
  $('files').innerHTML=h+'</table>';
}

/* ---------------- 事件绑定 ---------------- */
function bind(){
  $('btnLib').onclick=()=>changeLib();
  $('btnSave').onclick=()=>saveSong();
  $('btnCheck').onclick=async()=>{ if(S.dirty) await saveSong(true);
    const d=await api('/api/check?id='+encodeURIComponent(S.sid),{method:'POST'});
    $('log').textContent=(d.ok?'✅ 数据契约通过\n':'⚠ 未通过\n')+d.log; $('jobInfo').textContent='· 校验'; };
  $('btnCompose').onclick=()=>startJob('compose','作曲');
  $('btnRender').onclick=()=>startJob('render','渲染（不调参）');
  $('btnTune').onclick=()=>startJob('render-tune','渲染+自动调参');
  $('btnExport').onclick=()=>startJob('export','合并导出');
  $('btnStems').onclick=()=>startJob('export:stems','导出（含分轨）');
  $('btnMetrics').onclick=loadMetrics;
  $('winSel').onchange=()=>{ S.metricWin=$('winSel').value; loadMetrics(); };
  $('btnChords').onclick=openChordEditor;
  $('btnNew').onclick=async()=>{
    const id=prompt('新曲目名（字母/数字/下划线，例：24_my_song）'); if(!id) return;
    const theme=prompt('主题模板包（daily/seaside/night/tender/battle/gorgeous… 见 --list-themes）','daily');
    if(!theme) return;
    const ref=prompt('频谱对齐画像名（与模板依据无关，可留空用默认）','')||'';
    const d=await api('/api/new?id='+encodeURIComponent(id),
      {method:'POST',headers:{'content-type':'application/json'},
       body:JSON.stringify({id,theme,ref})});
    if(!d.ok){ alert('新建失败：'+d.error+'\n'+(d.log||'')); return; }
    S.sid=id; await loadSongs();
  };
  $('btnRefresh').onclick=()=>{loadSong();};
  $('btnPlay').onclick=togglePlay;
  $('btnStop').onclick=()=>{ ENG.stop(); drawWave(); };
  $('loopChk').onchange=applyLoop; $('loopA').onchange=applyLoop; $('loopB').onchange=applyLoop;
  document.querySelectorAll('#modeSeg button').forEach(b=>b.onclick=()=>setMode(b.dataset.mode));
  $('btnLoadStems').onclick=loadStems; $('btnApplyMix').onclick=applyMixToSong;
  $('btnRenderMix').onclick=async()=>{ if(await saveSong(true)) startJob('render','渲染（合并成品）'); };
  $('btnMixfit').onclick=()=>startJob('mixfit','自动配平（解方程 + 实测校验）');
  $('btnPreview').onclick=previewSection;
  $('searchSpeed').onchange=()=>renderSearchSec();
  $('btnSearch').onclick=startSearch;
  $('btnSearchStop').onclick=stopSearch;
  bindWave();
  window.addEventListener('keydown',onKey);
  const c=$('roll');
  c.onmousedown=rollDown; c.onmousemove=rollMove; c.onmouseup=rollUp;
  c.oncontextmenu=rollContext; c.onmouseleave=rollUp; c.onwheel=rollWheel;
  window.addEventListener('resize',()=>renderRoll());
}
async function togglePlay(){
  const s=ENG.state();
  if(s.playing){ ENG.pause(); drawWave(); return; }
  /* 试听某一轨/某段是**临时替换主音源**：这时按 ▶ 必须换回整曲 ——
   * 否则用户会以为在听整曲，其实只有一轨（或只有那一段循环）。 */
  if(S.soloTrack || S.previewOn){
    const why=S.soloTrack?('已回到整曲（'+S.soloTrack+' 试听结束）'):'已回到整曲（分段试听结束）';
    await backToFullSong(why);
  }
  ENG.play(S.mode).then(ok=>{ if(!ok) log('这一模式还没有音源：'+S.mode+'（分轨要先"载入分轨"）'); });
  rAF();
}
function rAF(){
  let tick = 0;
  const step=()=>{
    const st=ENG.state();
    /* **卷帘自维持**：谁把 canvas 尺寸改回去（浏览器 reflow / dpr 变化 / 别的绘制路径），
     * 这里下一帧就发现并重画 —— 用户报"卷帘是空的"而手工 fillRect 也画不上去，
     * 就是"画完被清空"这一类；靠"只在数据变更时绘制"防不住。
     * 只在尺寸真的变了才重画（不是每帧全量重绘）。 */
    const c=$('roll');
    if(c){
      const dpr=devicePixelRatio||1;
      const cssW=Math.max(320, c.clientWidth || (c.parentElement&&c.parentElement.clientWidth) || 1200);
      const wantW=Math.round(cssW*dpr), wantH=Math.round(360*dpr);
      if(c.width!==wantW || c.height!==wantH){
        c.width=wantW; c.height=wantH; c.style.width=cssW+'px'; c.style.height='360px';
        try{ renderRoll(); }catch(e){ /* 由 renderAll 的兜底统一报 */ }
      }
    }
    drawPlayhead();
    /* 波形/电平表降频到 ~12fps：这个循环现在常驻（播放与否都跑），
     * 而 `drawWave` 每次都重设 canvas 尺寸并重画 1200 根柱子 —— 60fps 白烧 CPU。 */
    tick++;
    if(st.playing || (tick % 5) === 0) { drawWave(true); drawMeters(); }
    const d=st.dur||0, p=ENG.position();
    $('posInfo').textContent=num(p).toFixed(1)+' / '+num(d).toFixed(1)+'s';
    requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}
async function prepAudio(){
  try{
    await ENG.loadMaster('/api/audio?id='+encodeURIComponent(S.sid)+'&kind=mix&t='+Date.now());
    await ENG.loadRef('/api/ref-audio?id='+encodeURIComponent(S.sid)+'&t='+Date.now());
  }catch(e){ log('音源加载失败：'+e.message); }
  const st=ENG.state();
  /* ⚠ 这里必须用 `Number.isFinite` 兜底：`st.dur` 可能是 NaN（解码失败/替身环境），
   *   而 `NaN || 0` 仍然是 NaN → `.toFixed()` 抛异常 → **`loadSong()` 在这里断掉**，
   *   后面的 `renderAll()` 虽然是先调用的，但切歌流程整体失败（用户侧表现为"卷帘不更新"）。
   *   数字格式化一律走 `num()`，任何环节的脏数据都不许中断渲染。 */
  if(! $('loopB').value) { $('loopB').value = num(st.dur).toFixed(1); }
  drawWave();
}
/* 任何"可能不是有限数"的值 → 安全数字（NaN/undefined/null/Infinity 一律 0） */
const num = (x, dflt) => (Number.isFinite(Number(x)) ? Number(x) : (dflt === undefined ? 0 : dflt));
function setMode(m){
  S.mode=m;
  document.querySelectorAll('#modeSeg button').forEach(b=>b.classList.toggle('on',b.dataset.mode===m));
  const was=ENG.state().playing; ENG.stop();
  if(was) togglePlay();
  drawWave();
  log('播放模式 → '+m);
}
function applyLoop(){
  const a=parseFloat($('loopA').value), b=parseFloat($('loopB').value);
  const on=$('loopChk').checked;
  ENG.setLoop(on&&!isNaN(a)&&!isNaN(b)&&b>a ? [a,b] : [null,null]);
}
function bindWave(){
  const c=$('wave'); let dragging=false, shift=false;
  const pos=(e)=>{ const r=c.getBoundingClientRect();
    return Math.max(0,Math.min(1,(e.clientX-r.left)/r.width)) * (ENG.state().dur||0); };
  c.onmousedown=(e)=>{ shift=e.shiftKey; dragging=true; if(shift){$('loopA').value=pos(e).toFixed(1);} else ENG.seek(pos(e)); };
  c.onmousemove=(e)=>{ if(!dragging) return; if(shift){ $('loopB').value=pos(e).toFixed(1); $('loopChk').checked=true; applyLoop(); } else ENG.seek(pos(e)); drawWave(); };
  window.addEventListener('mouseup',()=>{dragging=false;});
}
function log(msg){ const l=$('log'); l.textContent=(msg+'\n'+l.textContent).slice(0,4000); }
function drawWave(light){
  const c=$('wave'); if(!c) return;
  const W=c.width=c.clientWidth*devicePixelRatio, H=c.height=56*devicePixelRatio;
  const g=c.getContext('2d'); g.clearRect(0,0,W,H); g.fillStyle='#0f1117'; g.fillRect(0,0,W,H);
  const p=ENG.peaks(1200);
  if(p){
    g.fillStyle='#3a4152';
    for(let i=0;i<p.length;i++){ const h=p[i]*H*0.9; g.fillRect(i*W/p.length, (H-h)/2, 1, h); }
  }
  const d=ENG.state().dur||0;
  if(d){
    const [a,b]=ENG.state().loop;
    if(a!=null&&b!=null){ g.fillStyle='rgba(92,200,255,.12)'; g.fillRect(a/d*W,0,(b-a)/d*W,H); }
    const x=ENG.position()/d*W;
    g.fillStyle='#ff6b6b'; g.fillRect(x-1,0,2,H);
  }
  if(!light) g.fillStyle='#6f7690';
}

/* ---------------- 混音台（分轨实时混音） ---------------- */
async function loadStems(){
  const info=$('stemInfo');
  let d=await api('/api/stems?id='+encodeURIComponent(S.sid));
  if(!d.cached){
    info.textContent='· 生成分轨中（逐轨渲染，约 10~60 秒）…';
    await startJob('stems-cache','生成分轨');
    d=await api('/api/stems?id='+encodeURIComponent(S.sid));
  }
  if(!d.cached){ info.textContent='· 分轨生成失败'; return; }
  S.stems=d.tracks;
  await ENG.loadStems(d.tracks);
  S.stemLoaded=true;
  for(const t of Object.keys(d.tracks)) if(!ENG.mix[t]) ENG.mix[t]={gain:1,pan:0,mute:false,solo:false};
  info.textContent='· 已载入 '+Object.keys(d.tracks).length+' 轨（切到"分轨混音"就能边听边调）';
  renderStrips(); drawWave();
}
function renderStrips(){
  const box=$('strips'); box.innerHTML='';
  for(const row of trackRows()){
    const t=row.name;
    const m=ENG.mix[t]||{gain:1,pan:0,mute:false,solo:false};
    const has=!!S.stems[t];
    const el=document.createElement('div'); el.className='strip'+(has?'':' off');
    el.innerHTML=`<div class="nm"><span class="swatch" style="background:${COLOR[t]||'#888'}"></span>${t}
        <span class="dim" style="margin-left:auto">${row.prog===null?'鼓组':(GM[row.prog]||('GM'+row.prog))}</span></div>
      <div class="meter"><i data-m="${t}"></i></div>
      <div class="rowc"><label>vol</label><input type="range" min="0" max="200" value="${Math.round((m.gain??1)*100)}" data-g="${t}"></div>
      <div class="rowc"><label>pan</label><input type="range" min="-100" max="100" value="${Math.round((m.pan??0)*100)}" data-p="${t}"></div>
      <div class="btns"><button data-mute="${t}" class="${m.mute?'on':''}">M</button>
        <button data-solo="${t}" class="solo ${m.solo?'on':''}">S</button>
        <button data-aud="${t}" title="单独试听这一轨（渲染一版临时 OGG）">🎧</button></div>`;
    box.appendChild(el);
  }
  box.querySelectorAll('input[data-g]').forEach(i=>i.oninput=()=>{
    ENG.setTrack(i.dataset.g,{gain:parseInt(i.value,10)/100}); S.mixDirty=true;});
  box.querySelectorAll('input[data-p]').forEach(i=>i.oninput=()=>{
    ENG.setTrack(i.dataset.p,{pan:parseInt(i.value,10)/100}); S.mixDirty=true;});
  box.querySelectorAll('button[data-mute]').forEach(b=>b.onclick=()=>{
    const t=b.dataset.mute, m=ENG.mix[t]||{}; ENG.setTrack(t,{mute:!m.mute}); renderStrips(); S.mixDirty=true;});
  box.querySelectorAll('button[data-solo]').forEach(b=>b.onclick=()=>{
    const t=b.dataset.solo, m=ENG.mix[t]||{}; ENG.setTrack(t,{solo:!m.solo}); renderStrips(); S.mixDirty=true;});
  box.querySelectorAll('button[data-aud]').forEach(b=>b.onclick=()=>startJob('solo:'+b.dataset.aud,'试听 '+b.dataset.aud));
}
function drawMeters(){
  const lv=ENG.levels();
  document.querySelectorAll('#strips .meter i').forEach(i=>{
    const v=lv.tracks[i.dataset.m]||0;
    i.style.width=Math.min(100, Math.round(Math.sqrt(v)*140))+'%';
  });
}
async function applyMixToSong(){
  let n=0;
  const rows=trackRows();
  for(const t of Object.keys(ENG.mix)){
    /* ⚠ 原先这里 `if(!(t in (S.song.mix||{}))) continue;` —— song.json 没有 mix 字段时
     *   **每一轨都被跳过**，于是"混音台调完 → 应用到曲目"静默什么也没做（用户看不到任何反馈）。
     *   现在以 trackRows()（= events 实测值）为准，任何面板上存在的轨都可写回。 */
    const row=rows.find(x=>x.name===t); if(!row) continue;
    const m=ENG.mix[t]; const old=[row.pan,row.vol];
    const vol=Math.max(0,Math.min(127,Math.round((m.gain??1)*100)));
    const pan=Math.max(0,Math.min(127,Math.round(((m.pan??0)+1)/2*127)));
    if(vol!==old[1]||pan!==old[0]){ setTrackMix(t,0,pan); setTrackMix(t,1,vol); n++; }
  }
  S.dirty=true; renderTracks();
  log('已写回 song.json 的 mix：'+n+' 轨有改动（记得💾保存 / 🔊以当前混音渲染）');
}

function onKey(e){
  const tag=(e.target.tagName||'').toLowerCase();
  if(tag==='input'||tag==='select'||tag==='textarea') return;
  if(e.code==='Space'){ e.preventDefault(); togglePlay(); }
  else if(e.key==='s'&&(e.ctrlKey||e.metaKey)){ e.preventDefault(); saveSong(); }
  else if(e.key==='z'&&(e.ctrlKey||e.metaKey)){ e.preventDefault(); undo(); }
  else if(e.key==='y'&&(e.ctrlKey||e.metaKey)){ e.preventDefault(); redo(); }
  else if(e.key==='Delete'&&S.selNote){ delSelNote(); }
  else if(e.key==='['&&S.selNote){ S.selNote.vel=Math.max(1,(S.selNote.vel||80)-8); syncVel(S.selNote); renderRoll(); }
  else if(e.key===']'&&S.selNote){ S.selNote.vel=Math.min(127,(S.selNote.vel||80)+8); syncVel(S.selNote); renderRoll(); }
  else if(e.key>='1'&&e.key<='9'){ const r=trackRows()[+e.key-1]; if(r){S.track=r.name;renderAll();} }
}
function pushUndo(){ S.undo.push(JSON.stringify(S.song)); if(S.undo.length>60) S.undo.shift(); S.redo=[]; }
function undo(){ if(!S.undo.length) return log('没有可撤销的步骤');
  S.redo.push(JSON.stringify(S.song)); S.song=JSON.parse(S.undo.pop()); S.dirty=true; renderAll(); log('↶ 撤销'); }
function redo(){ if(!S.redo.length) return log('没有可重做的步骤');
  S.undo.push(JSON.stringify(S.song)); S.song=JSON.parse(S.redo.pop()); S.dirty=true; renderAll(); log('↷ 重做'); }
function delSelNote(){
  const n=S.selNote; if(!n||!n.raw) return;
  pushUndo();
  for(const k in S.song.melody){const a=S.song.melody[k];const i=a.indexOf(n.raw);if(i>=0)a.splice(i,1);}
  S.selNote=null; S.dirty=true; renderRoll(); log('删除音符');
}
function syncVel(n){ /* 力度不写回 song.json（引擎按 mel_vel/段落 vel 生成力度），这里只做视觉提示 */ }


/* ---------------- 和弦表编辑器（含"名字可解析"校验，规则同 selftest.QUALITY） ---------------- */
function chordParseable(name){
  let base=name, slash=null;
  if(base.endsWith('6/9')){ const r=base.slice(0,-3); return !!NOTE_PC[r]; }
  if(base.includes('/')){ const p=base.split('/'); base=p[0]; slash=p[1]; if(!(slash in NOTE_PC)) return false; }
  for(const q of QUALITIES.slice().sort((a,b)=>b.length-a.length)){
    if(q && base.endsWith(q)){ const r=base.slice(0,-q.length); if(r in NOTE_PC) return true; }
  }
  return base in NOTE_PC;
}
function openChordEditor(){
  const m=document.createElement('div'); m.className='modal';
  const names=Object.keys(S.song.chords||{});
  let rows='';
  for(const n of names){
    const c=S.song.chords[n];
    rows+=`<tr data-n="${n}"><td><input value="${n}" data-f="name" style="width:96px"></td>
      <td><input type="number" value="${c[0]}" data-f="bass" style="width:62px"></td>
      <td><input value="${(c[1]||[]).join(',')}" data-f="tones" style="width:220px"></td>
      <td class="hint" data-msg></td></tr>`;
  }
  m.innerHTML=`<div class="box"><h3>和弦表</h3>
    <div class="hint">名字必须能被 song_engine / check_song 解析（如 <code>Bbadd9</code>、<code>F/A</code>、<code>C7sus4</code>、<code>D#6/9</code>）；
      低音写 MIDI 号（如 46=Bb2），音集写 MIDI 音高列表（低→高）。改动"小节里的和弦名"要在段落编辑器里选。</div>
    <table class="mini" id="ctab"><tr><th>和弦名</th><th>低音</th><th>音集</th><th></th></tr>${rows}</table>
    <div class="row" style="margin-top:8px"><button id="ckAdd" class="mini">＋ 新增</button>
      <button id="ckSave" class="primary">保存到 song.json</button>
      <button id="ckClose" class="mini">关闭</button><span class="hint" id="ckMsg"></span></div></div>`;
  document.body.appendChild(m);
  const close=()=>m.remove();
  m.querySelector('#ckClose').onclick=close;
  m.onclick=(e)=>{ if(e.target===m) close(); };
  m.querySelector('#ckAdd').onclick=()=>{
    const tr=document.createElement('tr');
    tr.innerHTML=`<td><input value="C" data-f="name" style="width:96px"></td>
      <td><input type="number" value="36" data-f="bass" style="width:62px"></td>
      <td><input value="55,60,64" data-f="tones" style="width:220px"></td><td class="hint" data-msg></td>`;
    m.querySelector('#ctab').appendChild(tr);
  };
  m.querySelectorAll('tr[data-n]').forEach(tr=>{
    const n0=tr.dataset.n;
    const check=()=>{
      const name=tr.querySelector('[data-f=name]').value.trim();
      const msg=tr.querySelector('[data-msg]');
      const c=S.song.chords[n0];
      const tones=tr.querySelector('[data-f=tones]').value.split(',').map(x=>parseInt(x,10)).filter(x=>!isNaN(x));
      const bass=parseInt(tr.querySelector('[data-f=bass]').value,10);
      const pcs=new Set(tones.map(t=>(t%12+12)%12));
      const root=(name.replace(/[/].*$/,'').match(/^[A-G](#|b)?/)||[''])[0];
      const okName=chordParseable(name);
      const okRoot=(root in NOTE_PC) && (pcs.size===0 || pcs.has(NOTE_PC[root]||-1) || (bass%12)===NOTE_PC[root]);
      msg.textContent = !okName ? '✗ 名字不可解析' : (isNaN(bass) ? '✗ 低音' : (tones.some(t=>t<0||t>127) ? '✗ 音高越界' : '✓'));
      msg.className = 'hint ' + (okName && !isNaN(bass) && !tones.some(t=>t<0||t>127) ? 'ok' : 'bad');
    };
    tr.querySelectorAll('input').forEach(i=>i.oninput=check);
    check();
  });
  m.querySelector('#ckSave').onclick=()=>{
    const out={};
    let bad=null;
    m.querySelectorAll('#ctab tr[data-n], #ctab tr:not([data-n])').forEach(tr=>{
      const nameEl=tr.querySelector('[data-f=name]'); if(!nameEl) return;
      const name=nameEl.value.trim();
      const bass=parseInt(tr.querySelector('[data-f=bass]').value,10);
      const tones=tr.querySelector('[data-f=tones]').value.split(',').map(x=>parseInt(x,10)).filter(x=>!isNaN(x));
      if(!chordParseable(name)) bad=bad||name;
      if(isNaN(bass)||tones.some(t=>t<0||t>127)) bad=bad||name;
      out[name]=[bass,tones];
    });
    if(bad){ m.querySelector('#ckMsg').textContent='✗ 有问题：'+bad; return; }
    pushUndo();
    // 旧名 → 新名：小节里的引用要跟着改（按位置映射，改了名字就同步）
    const oldNames=[...m.querySelectorAll('#ctab tr[data-n]')].map(tr=>tr.dataset.n);
    const newNames=[...m.querySelectorAll('#ctab tr[data-f=name]')].map(i=>i.value.trim());
    S.song.chords=out;
    (S.song.sections||[]).forEach(sec=>{
      sec.chords=(sec.chords||[]).map(cn=>{
        const k=oldNames.indexOf(cn); return (k>=0 && newNames[k]) ? newNames[k] : cn;
      });
    });
    S.dirty=true; close(); renderAll(); log('和弦表已更新（'+Object.keys(out).length+' 个）——记得💾保存');
  };
}

bind(); loadSongs();

/* 给 index.html 的启动自检 / tools/browser_check.js 用的显式接口。
 * 说明（之前这里写过一句不准确的话）：app.js 是**普通脚本**，顶层 `const ENG/S` 进的是
 * **全局词法环境**，页面上其它 <script> 与 CDP 的 Runtime.evaluate 都读得到 ——
 * 不需要 `window.ENG` 这种写法（用了反而读不到）。这里挂 window 只是**写明契约**：
 * 将来若把 app.js 改成 type="module"，自检与验收脚本不会静默失效。 */
window.renderRoll = renderRoll;
window.__studioProbe = () => {
  let rows=[], st={};
  try { rows = trackRows(); } catch (e) { rows = []; }
  try { st = (typeof ENG !== 'undefined' && ENG.state) ? ENG.state() : {}; } catch (e) { st = {}; }
  return {tracks: rows.length, events: !!S.events, song: !!S.song,
          rollG: !!S.rollG, ph: !!$('rollPh'), track: S.track,
          notes: (S.events && S.events.tracks && S.events.tracks[S.track]) ?
                 (S.events.tracks[S.track].n || 0) : 0,
          /* 播放/定位真值：验收脚本要判断"seek 到底有没有生效"（headless 里 dur 可能是 0） */
          dur: st.dur || 0, pos: (typeof ENG !== 'undefined' && ENG.position) ? ENG.position() : null,
          mode: st.mode || S.mode, playing: !!st.playing,
          ctxState: (ENG && ENG.ctx && ENG.ctx.state) || null};
};