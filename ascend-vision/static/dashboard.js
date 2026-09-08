'use strict';
const $ = id => document.getElementById(id);
const ns = 'http://www.w3.org/2000/svg';
let applied = null, controller = null, timer = null, lastData = null;
function duration(value) {
  const seconds = Math.max(0, Math.floor(value));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}
function label(iso) {
  const [year, month, day] = iso.slice(0, 10).split('-').map(Number);
  return new Date(year, month-1, day).toLocaleDateString(undefined, {month:'short', day:'numeric'});
}
function svg(tag, attrs, text) {
  const node = document.createElementNS(ns, tag);
  Object.entries(attrs).forEach(([key,value]) => node.setAttribute(key, value));
  if (text !== undefined) node.textContent = text;
  return node;
}
function chartFrame(node, width, height, max) {
  node.replaceChildren();
  const left=38, top=15, bottom=height-36, right=width-14;
  const scale = Math.max(1, Math.ceil(max / 4)) * 4;
  const y = value => bottom - value/scale*(bottom-top);
  for (let step=0; step<=4; step++) {
    const value = step*scale/4;
    node.append(svg('line', {x1:left,y1:y(value),x2:right,y2:y(value),class:'grid'}));
    node.append(svg('text', {x:left-9,y:y(value)+4,'text-anchor':'end'}, value));
  }
  return {left,right,bottom,y};
}
function dailyChart(rows) {
  const node=$('daily-chart');
  const width=Math.max(320,Math.round(node.getBoundingClientRect().width));
  node.setAttribute('viewBox',`0 0 ${width} 260`);
  const {left,right,bottom,y}=chartFrame(node,width,260,Math.max(...rows.map(r=>r.pickups)));
  node.setAttribute('aria-label', `Daily pickups: ${rows.reduce((n,r)=>n+r.pickups,0)} across ${rows.length} days. Exact values in the following table.`);
  const step=(right-left)/rows.length;
  const every=Math.max(1,Math.ceil(rows.length/Math.max(3,Math.floor(width/100))));
  rows.forEach((row,index)=>{
    const x=left+index*step+step*.16, w=step*.68;
    const group=svg('g', {});
    group.append(svg('title', {}, `${row.date}: ${row.focus} focus, ${row.background} background`));
    group.append(svg('rect', {x,y:y(row.focus),width:w,height:bottom-y(row.focus),class:'bar-focus',rx:1}));
    group.append(svg('rect', {x,y:y(row.pickups),width:w,height:y(row.focus)-y(row.pickups),class:'bar-background',rx:1}));
    node.append(group);
    if(index%every===0) node.append(svg('text',{x:x+w/2,y:bottom+25,'text-anchor':'middle'}, label(row.date)));
  });
}
function weeklyChart(rows) {
  const node=$('weekly-chart');
  const width=Math.max(320,Math.round(node.getBoundingClientRect().width));
  node.setAttribute('viewBox',`0 0 ${width} 250`);
  const {left,right,bottom,y}=chartFrame(node,width,250,Math.max(...rows.map(r=>r.pickups)));
  node.setAttribute('aria-label', `Weekly pickup trend over ${rows.length} calendar weeks. Partial edge weeks are marked. Exact values in the following table.`);
  const points=rows.map((row,i)=>[rows.length===1?(left+right)/2:left+i*(right-left)/(rows.length-1),y(row.pickups)]);
  const path=points.map(([x,y],i)=>`${i?'L':'M'}${x},${y}`).join(' ');
  node.append(svg('path',{d:`${path} L${points.at(-1)[0]},${bottom} L${points[0][0]},${bottom} Z`,class:'area'}));
  node.append(svg('path',{d:path,class:'trend'}));
  const every=Math.max(1,Math.ceil(rows.length/Math.max(3,Math.floor(width/110))));
  rows.forEach((row,i)=>{
    const [x,cy]=points[i], dot=svg('circle',{cx:x,cy,r:4.5,class:'dot'});
    dot.append(svg('title',{},`Week of ${row.start}: ${row.pickups}${row.partial?' (partial week)':''}`));
    node.append(dot);
    if(i%every===0) node.append(svg('text',{x,y:bottom+25,'text-anchor':'middle'},label(row.start)+(row.partial?'*':'')));
  });
}
function cell(text) { const td=document.createElement('td'); td.textContent=text; return td; }
function table(id, rows) {
  const fragment=document.createDocumentFragment();
  rows.forEach(values=>{const tr=document.createElement('tr');values.forEach(value=>tr.append(cell(value)));fragment.append(tr);});
  $(id).replaceChildren(fragment);
}
function render(data) {
  lastData=data;
  const s=data.summary;
  $('pickups').textContent=s.pickups.toLocaleString();
  $('phone').textContent=duration(s.phone_seconds);
  $('focus').textContent=duration(s.focus_seconds);
  $('streak').textContent=duration(s.longest_streak_seconds);
  $('monitored').textContent=duration(s.monitored_seconds);
  dailyChart(data.days); weeklyChart(data.weeks);
  table('daily-data', data.days.map(r=>[r.date,r.focus,r.background,r.pickups]));
  table('weekly-data', data.weeks.map(r=>[r.start+(r.partial?' (partial)':''),r.pickups]));
  const fragment=document.createDocumentFragment();
  data.recent.forEach(row=>{
    const tr=document.createElement('tr');
    tr.append(cell(`${label(row.detected_at)} · ${row.detected_at.slice(11,19)}`));
    const mode=cell(''), badge=document.createElement('span');
    badge.className=`badge ${row.mode}`;badge.textContent=row.mode==='focus'?'Focus':'Background';mode.append(badge);
    tr.append(mode,cell(duration(row.duration_seconds)),cell(row.roast_text || '—'));fragment.append(tr);
  });
  if(!data.recent.length){const tr=document.createElement('tr'), td=cell('No pickups in this selection.');td.colSpan=4;tr.append(td);fragment.append(tr);}
  $('recent-data').replaceChildren(fragment);
  $('empty').hidden=s.pickups>0 || s.monitored_seconds>0 || s.phone_seconds>0;
  $('empty').textContent=data.database_exists ? 'No recorded monitoring in this selection. Choose another date range, or start Phone Watch to begin your journal.' : 'Your journal is ready. Start Phone Watch to create the local database and record your first session. This page will refresh automatically.';
  $('status').textContent=`${data.start} — ${data.end} · ${data.mode==='all'?'All sessions':data.mode==='focus'?'Focus only':'Background only'} · Updated ${new Date(data.updated_at).toLocaleTimeString()}`;
}
async function load(params) {
  applied=new URLSearchParams(params);
  if(controller) controller.abort();
  clearTimeout(timer);
  const current=new AbortController();controller=current;
  const timeout=setTimeout(()=>current.abort('timeout'),15000);
  $('results').setAttribute('aria-busy','true');
  try {
    const response=await fetch(`/api/stats?${params}`,{signal:current.signal,cache:'no-store'});
    const data=await response.json();
    if(!response.ok) throw new Error(data.error || 'Statistics are unavailable.');
    if(controller!==current) return;
    $('results').hidden=false; render(data); $('error').hidden=true;
  } catch(error) {
    if(controller!==current) return;
    $('error').textContent=current.signal.aborted ? 'The database request timed out. Try Refresh.' : error.message;
    $('error').hidden=false; $('results').hidden=true; $('empty').hidden=true;
    $('status').textContent='Statistics unavailable — no totals are being shown.';
  } finally {
    clearTimeout(timeout);
    if(controller===current) {
      controller=null; $('results').setAttribute('aria-busy','false');
      timer=setTimeout(()=>{if(!document.hidden) load(applied || params);},Number(document.body.dataset.refresh)*1000);
    }
  }
}
function selection() { return new URLSearchParams(new FormData($('filters'))); }
$('filters').addEventListener('submit',event=>{event.preventDefault();load(selection());});
$('refresh').addEventListener('click',()=>load(applied || selection()));
$('today').addEventListener('click',()=>{const today=$('today').dataset.today;$('start').value=today;$('end').value=today;load(selection());});
document.addEventListener('visibilitychange',()=>{if(!document.hidden && !controller) load(applied || selection());});
let resizeTimer;
window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{
  if(lastData && !$('results').hidden){dailyChart(lastData.days);weeklyChart(lastData.weeks);}
},100);});

const camBtn = $('launch-camera');
const camText = $('camera-btn-text');
async function checkCameraStatus() {
  if (!camBtn) return;
  try {
    const res = await fetch('/api/camera/status', {cache: 'no-store'});
    if (res.ok) {
      const data = await res.json();
      if (data.running) {
        camText.textContent = 'Camera Active';
        camBtn.classList.add('camera-running');
      } else {
        camText.textContent = 'Open Live Camera';
        camBtn.classList.remove('camera-running');
      }
    }
  } catch(e) {}
}

if (camBtn) {
  camBtn.addEventListener('click', async () => {
    camBtn.disabled = true;
    camText.textContent = 'Launching…';
    try {
      const res = await fetch('/api/camera/start', {method: 'POST'});
      if (res.ok) {
        setTimeout(checkCameraStatus, 2000);
      }
    } catch (e) {
      alert('Could not start live camera.');
    } finally {
      setTimeout(() => { if (camBtn) camBtn.disabled = false; checkCameraStatus(); }, 1500);
    }
  });
  setInterval(checkCameraStatus, 4000);
  checkCameraStatus();
}

load(selection());

const handoffForm=$('vision-handoff');
if(handoffForm){handoffForm.addEventListener('submit',async event=>{event.preventDefault();const status=$('vision-handoff-status');const data=new FormData(handoffForm);try{const response=await fetch('/api/auth/vision-handoff',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({identifier:data.get('identifier'),password:data.get('password')})});const body=await response.json();if(!response.ok)throw new Error(body.error||'Core sign-in could not be completed.');handoffForm.reset();status.textContent='Automation sign-in enabled for the next 15 minutes.';status.hidden=false;}catch(error){status.textContent=error.message;status.hidden=false;}})}

const localAutoConnect=document.body.dataset.localAutoConnect==='true';
const localCoreUrl=document.body.dataset.coreUrl;
const localConnectStatus=$('vision-handoff-status');
const localConnectState=$('local-vision-state');
const localVisionExpiry=$('local-vision-expiry');
const localCoreApiState=$('local-core-api-state');
function setLocalConnectStatus(message){
  if(localConnectState)localConnectState.textContent=message;
  if(localConnectStatus){localConnectStatus.textContent=message;localConnectStatus.hidden=false;}
}
function setLocalConnectionDetails(status){
  const character=status&&status.character;
  const expiresAt=status&&status.expiresAt;
  const expiresIn=Math.max(0,Math.ceil((Date.parse(expiresAt)-Date.now())/60000));
  setLocalConnectStatus(`CONNECTED · Character: ${character.name} · Authorization active`);
  if(localVisionExpiry)localVisionExpiry.textContent=Number.isFinite(expiresIn)?`Expires in: ${expiresIn}m`:'Expires in: unavailable';
  if(localCoreApiState)localCoreApiState.textContent=`Core API: ${status.coreApi==='reachable'?'Reachable':'Unreachable'}`;
}
function hasStoredLocalAuthorization(status){
  return Boolean(status&&status.status==='connected'&&status.character&&typeof status.character.id==='string'
    &&typeof status.character.name==='string'&&typeof status.expiresAt==='string'
    &&Date.parse(status.expiresAt)>Date.now());
}
async function localVisionStatus(){
  const response=await fetch('/api/auth/local-vision-status',{cache:'no-store'});
  const payload=await response.json().catch(()=>null);
  if(!response.ok)throw new Error('Local Vision status is unavailable.');
  return payload;
}
async function coreSessionRequest(path,options={}){
  const response=await fetch(`${localCoreUrl}${path}`,{...options,credentials:'include',cache:'no-store'});
  const payload=await response.json().catch(()=>null);
  if(!response.ok)throw new Error('Core session is unavailable.');
  return payload;
}
function authenticatedCharacter(context){
  const character=context&&context.character;
  if(!character||typeof character.id!=='string'||!character.id.trim()||typeof character.name!=='string')throw new Error('Core did not provide a character.');
  return {id:character.id,name:character.name};
}
async function connectLocalCore({forceHandoff=false}={}){
  if(!localAutoConnect||!localCoreUrl)return;
  try{
    if(!forceHandoff){
      setLocalConnectStatus('Checking saved Vision authorization…');
      const localStatus=await localVisionStatus();
      if(hasStoredLocalAuthorization(localStatus)){
        setLocalConnectionDetails(localStatus);
        return;
      }
    }
    setLocalConnectStatus('Checking your local Ascend Core session…');
    const character=authenticatedCharacter(await coreSessionRequest('/api/auth/me'));
    let visionToken;
    try{
      visionToken=await coreSessionRequest('/api/auth/vision-token',{method:'POST'});
      if(!visionToken||typeof visionToken.accessToken!=='string'||typeof visionToken.expiresAt!=='string')throw new Error('Core did not provide Vision authorization.');
      const handoff=await fetch('/api/auth/local-vision-handoff',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({accessToken:visionToken.accessToken,expiresAt:visionToken.expiresAt,character})});
      const result=await handoff.json().catch(()=>null);
      if(!handoff.ok)throw new Error((result&&result.error)||'Vision could not store authorization.');
      setLocalConnectionDetails({...result,coreApi:'reachable'});
    }finally{
      visionToken=undefined;
    }
  }catch(error){
    setLocalConnectStatus('NOT CONNECTED · Open Ascend Core and enter Guest Mode first.');
    if(localVisionExpiry)localVisionExpiry.textContent='Expires in: unavailable';
    if(localCoreApiState)localCoreApiState.textContent='Core API: Unreachable';
  }
}
if(localAutoConnect){
  $('retry-local-vision')?.addEventListener('click',()=>connectLocalCore({forceHandoff:true}));
  $('reconnect-local-vision')?.addEventListener('click',()=>connectLocalCore({forceHandoff:true}));
  connectLocalCore();
}
