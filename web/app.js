const $ = id => document.getElementById(id);
let sources = [], current = null, catalogItems = [];

async function api(url, options = {}) {
  const response = await fetch(url, {headers: {'Content-Type': 'application/json'}, ...options});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}
function toast(message, bad=false) {
  $('toast').textContent = message; $('toast').className = bad ? 'show bad' : 'show';
  setTimeout(() => $('toast').className = '', 3500);
}
function busy(button, state, text='Загрузка…') {
  if (state) { button.dataset.label = button.textContent; button.textContent = text; }
  else button.textContent = button.dataset.label || button.textContent;
  button.disabled = state;
}
function esc(value='') { const div=document.createElement('div'); div.textContent=value; return div.innerHTML; }
function attr(value='') { return String(value).replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll("'",'&#39;').replaceAll('<','&lt;').replaceAll('>','&gt;'); }
function statusLabel(status) { return ({working:'Работает',partial:'Частично',unknown:'Не проверен',blocked_or_unavailable:'Сайт не пускает / недоступен',broken_parser:'Парсер устарел'})[status] || status; }

async function loadSources() {
  sources = await api('/api/sources');
  $('source').innerHTML = sources.map(s => `<option value="${esc(s.id)}">${esc(s.title)} — ${statusLabel(s.status)}</option>`).join('');
  sourceChanged();
}
function sourceChanged() {
  const s = sources.find(x => x.id === $('source').value); if (!s) return;
  $('sourceInfo').innerHTML = `<span class="pill ${attr(s.status)}">${statusLabel(s.status)}</span><span>${s.catalog?'каталог доступен':'только прямая ссылка/каталог недоступен'}</span><a href="${attr(s.host)}" target="_blank" rel="noreferrer">${esc(s.host)}</a>`;
  $('loadCatalog').disabled = !s.catalog;
  if (!s.catalog) setTab('url');
}
function setTab(name) {
  $('catalogPane').hidden=name!=='catalog'; $('urlPane').hidden=name!=='url';
  $('catalogTab').classList.toggle('active',name==='catalog'); $('urlTab').classList.toggle('active',name==='url');
}
function renderCatalog() {
  const query=$('search').value.trim().toLowerCase();
  const items=catalogItems.filter(x => (x.title||x.uniq||'').toLowerCase().includes(query));
  $('catalog').className='catalog';
  $('catalog').innerHTML=items.length ? items.map((x,i)=>`<button class="catalog-item" data-index="${i}"><span>${esc(x.title||x.uniq||x.link)}</span><small>${esc(x.link)}</small></button>`).join('') : '<div class="empty">Ничего не найдено</div>';
  document.querySelectorAll('.catalog-item').forEach(button => button.onclick=()=>inspect(items[Number(button.dataset.index)].link,button));
}
async function loadCatalog() {
  busy($('loadCatalog'),true);
  try { catalogItems=await api(`/api/catalog?source=${encodeURIComponent($('source').value)}&max_pages=3`); renderCatalog(); toast(`Тайтлов: ${catalogItems.length}`); }
  catch(e){toast(e.message,true)} finally{busy($('loadCatalog'),false)}
}
async function inspect(url, button=$('loadUrl')) {
  if(!url.trim()) return toast('Вставьте ссылку на тайтл',true);
  busy(button,true,'Открываю…');
  try {
    current=await api('/api/title',{method:'POST',body:JSON.stringify({source:$('source').value,url})});
    renderTitle(); $('titlePanel').scrollIntoView({behavior:'smooth'});
  } catch(e){toast(e.message,true)} finally{busy(button,false)}
}
function renderTitle() {
  $('titlePanel').hidden=false; $('title').textContent=current.title||current.uniq||'Без названия';
  $('titleSource').textContent=current.source; $('summary').textContent=(current.summary||'').replace(/<[^>]*>/g,' ').replace(/\s+/g,' ').trim();
  $('cover').src=current.cover||''; $('cover').hidden=!current.cover;
  $('chapterCount').textContent=`Главы · ${current.chapters.length}`;
  $('chapters').innerHTML=current.chapters.map((c,i)=>`<label class="chapter${c.downloadable===false?' unavailable':''}" title="${attr(c.availability||'')}"><input type="checkbox" value="${i}" ${c.downloadable===false?'disabled':''}><span>${esc(c.title||`Глава ${i+1}`)}${c.downloadable===false?' · недоступна':''}</span><small>${esc(c.availability||c.link)}</small></label>`).join('');
  document.querySelectorAll('#chapters input').forEach(x=>x.onchange=selectedCount); selectedCount();
}
function selectedCount(){ $('selectedCount').textContent=`Выбрано: ${document.querySelectorAll('#chapters input:checked').length}`; }
function select(mode){ document.querySelectorAll('#chapters input:not(:disabled)').forEach(x=>x.checked=mode==='all'||(mode==='invert'&&!x.checked)); selectedCount(); }
async function downloadSelected(){
  const chapters=[...document.querySelectorAll('#chapters input:checked')].map(x=>Number(x.value));
  if(!chapters.length)return toast('Выберите главы',true); busy($('download'),true,'Добавляю…');
  try{await api('/api/downloads',{method:'POST',body:JSON.stringify({token:current.token,chapters})}); toast('Добавлено в очередь'); await loadJobs();}
  catch(e){toast(e.message,true)}finally{busy($('download'),false)}
}
async function loadJobs(){
  try{const jobs=await api('/api/jobs'); $('jobs').className='jobs'; $('jobs').innerHTML=jobs.length?jobs.map(j=>{
    const done=j.completed+j.failed, pct=j.total?Math.round(done/j.total*100):0;
    const state=({queued:'В очереди',running:'Скачивается',done:'Готово',done_with_errors:'Готово с ошибками',failed:'Ошибка'})[j.state]||j.state;
    return `<article class="job"><div><strong>${esc(j.title)}</strong><span class="pill ${j.state}">${state}</span></div><p>${j.current?`Сейчас: ${esc(j.current)}${j.pages?` · стр. ${j.page}/${j.pages}`:''}`:`Глав: ${j.completed}/${j.total}${j.failed?` · ошибок ${j.failed}`:''}`}</p><div class="progress"><i style="width:${pct}%"></i></div>${j.error?`<small class="error">${esc(j.error)}</small>`:''}</article>`}).join(''):'<div class="empty">Очередь пуста</div>';
  }catch(e){toast(e.message,true)}
}

$('source').onchange=sourceChanged; $('catalogTab').onclick=()=>setTab('catalog'); $('urlTab').onclick=()=>setTab('url');
$('loadCatalog').onclick=loadCatalog; $('search').oninput=renderCatalog;
$('search').onkeydown=e=>{if(e.key==='Enter'&&!e.isComposing&&!$('loadCatalog').disabled){e.preventDefault();loadCatalog()}};
$('loadUrl').onclick=()=>inspect($('titleUrl').value);
$('titleUrl').onkeydown=e=>{if(e.key==='Enter')inspect($('titleUrl').value)};
$('all').onclick=()=>select('all'); $('none').onclick=()=>select('none'); $('invert').onclick=()=>select('invert');
$('download').onclick=downloadSelected; $('refreshJobs').onclick=loadJobs;
loadSources().catch(e=>toast(e.message,true)); loadJobs(); setInterval(loadJobs,2000);
