'use strict';
const $ = id => document.getElementById(id);
const NS = 'http://www.w3.org/2000/svg';
const state = {countries: [], byId: new Map(), features: [], names: new Map(), selected: 'UKR', category: 'all', news: null, request: 0, zoom: 1, tx: 0, ty: 0};
const names = new Intl.DisplayNames(['zh-CN'], {type: 'region'});
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
const safeURL = value => {try {const u = new URL(value); return ['http:','https:'].includes(u.protocol) && !u.username && !u.password ? u.href : '#';} catch {return '#';}};
function svg(tag, attrs, parent) {const e = document.createElementNS(NS, tag); for (const [k,v] of Object.entries(attrs)) e.setAttribute(k,v); if(parent) parent.append(e); return e;}
function project(lon, lat) {
  const l = lon * Math.PI / 180, p = lat * Math.PI / 180, p2 = p*p, p4=p2*p2;
  const x = l*(.8707-.131979*p2 + p4*p4*p4*(-.013791+p2*(.003971*p2-.001529)));
  const y = p*(1.007226+p2*(.015085+p4*(-.044475+.028874*p2-.005916*p4)));
  return [550+x*190, 315-y*190];
}
function featureID(f) {const id=f.properties['ISO3166-1-Alpha-3'];return id && id !== '-99' ? id : f.properties.name;}
function featureName(f) {
  const id=featureID(f), tracked=state.byId.get(id); if(tracked) return tracked.name;
  const a2=f.properties['ISO3166-1-Alpha-2'];
  try {if(a2 && /^[A-Z]{2}$/.test(a2)) return names.of(a2);} catch {}
  return ({France:'法国',Norway:'挪威',Kosovo:'科索沃',Somaliland:'索马里兰','Northern Cyprus':'北塞浦路斯'})[f.properties.name] || f.properties.name;
}
function shapePath(f) {
  const ps=f.geometry.type==='Polygon' ? [f.geometry.coordinates] : f.geometry.coordinates;
  return ps.map(poly=>poly.map(ring=>ring.map((point,i)=> {
    const [x,y]=project(point[0],point[1]);
    const split=i && Math.abs(point[0]-ring[i-1][0])>180;
    return `${!i || split?'M':'L'}${x.toFixed(2)},${y.toFixed(2)}`;
  }).join('')+'Z').join('')).join('');
}
function drawMap() {
  for(let lat=-60;lat<=75;lat+=30){const points=[];for(let lon=-180;lon<=180;lon+=5)points.push(project(lon,lat));svg('path',{d:points.map((p,i)=>(i?'L':'M')+p.join(',')).join(''),class:'graticule-line'},$('graticule'));}
  for(let lon=-150;lon<=180;lon+=30){const points=[];for(let lat=-60;lat<=85;lat+=5)points.push(project(lon,lat));svg('path',{d:points.map((p,i)=>(i?'L':'M')+p.join(',')).join(''),class:'graticule-line'},$('graticule'));}
  for(const f of state.features){
    if(f.properties.name==='Antarctica')continue;
    const id=featureID(f), name=featureName(f);state.names.set(id,{name,en:f.properties.name,id});
    const path=svg('path',{d:shapePath(f),class:'country','data-id':id,'aria-label':name,'fill-rule':'evenodd',tabindex:state.byId.has(id)?'0':'-1',role:'button'},$('countries'));
    const title=svg('title',{},path);title.textContent=name;
    path.addEventListener('pointermove',e=>showTooltip(e,id));path.addEventListener('pointerleave',hideTooltip);
    path.addEventListener('click',()=>{if(!ignoreMapClick)selectCountry(id);});
    path.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();selectCountry(id);}});
  }
  // Small countries remain selectable at world scale through sized point markers and the list.
  for(const c of state.countries){
    const [x,y]=project(...c.center);
    const marker=svg('circle',{cx:x,cy:y,r:3.8,class:'map-marker '+c.category,'data-id':c.id,'aria-label':c.name,tabindex:'-1',role:'button'},$('markers'));
    marker.addEventListener('pointermove',e=>showTooltip(e,c.id));marker.addEventListener('pointerleave',hideTooltip);
    marker.addEventListener('click',()=>{if(!ignoreMapClick)selectCountry(c.id);});
    marker.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();selectCountry(c.id);}});
  }
  $('map-loading').hidden=true;updateColors();drawSelection();
}
function showTooltip(e,id){
  const c=state.byId.get(id),n=state.names.get(id);if(!n)return;
  const t=$('tooltip');t.innerHTML=`${esc(n.name)}<small>${esc(c?c.label:'未纳入当前观察清单')}</small>`;t.hidden=false;
  const rect=$('map-stage').getBoundingClientRect();
  t.style.left=Math.max(8,Math.min(rect.width-t.offsetWidth-8,e.clientX-rect.left+12))+'px';
  t.style.top=Math.max(8,Math.min(rect.height-t.offsetHeight-8,e.clientY-rect.top-45))+'px';
}
function hideTooltip(){$('tooltip').hidden=true;}
function updateColors(){
  for(const e of document.querySelectorAll('[data-id].country, [data-id].map-marker')){
    const id=e.dataset.id,c=state.byId.get(id), marker=e.classList.contains('map-marker');
    e.setAttribute('class',(marker?'map-marker':'country')+(c?' '+c.category:'')+(id===state.selected?' selected':'')+(c && state.category!=='all' && c.category!==state.category?' dimmed':''));
  }
}
function drawSelection(){
  const g=$('selected-marker');g.replaceChildren();const c=state.byId.get(state.selected);if(!c)return;
  const [x,y]=project(...c.center);svg('circle',{cx:x,cy:y,r:10,class:'selection-halo'},g);
  const text=svg('text',{x:x+15,y:y+5,class:'selection-label'},g);text.textContent=c.name;
}
function renderWatchlist(){
  const cs=state.countries.filter(c=>state.category==='all'||c.category===state.category);
  $('watchlist').replaceChildren();for(const c of cs){const b=document.createElement('button');b.className='country-chip'+(c.id===state.selected?' active':'');b.innerHTML=`<i class="swatch ${c.category}"></i>${esc(c.name)}`;b.addEventListener('click',()=>selectCountry(c.id));$('watchlist').append(b);}
  $('list-count').textContent=cs.length+' 个';$('clear-filter').hidden=state.category==='all';
}
function setCategory(category,updateURL=true){state.category=category;for(const b of document.querySelectorAll('.filter')){b.classList.toggle('active',b.dataset.category===category);b.setAttribute('aria-pressed',String(b.dataset.category===category));}renderWatchlist();updateColors();if(updateURL)syncURL();}
function syncURL(){
  try{
    const url=new URL(window.location.href);url.searchParams.set('country',state.selected);if(state.category==='all')url.searchParams.delete('category');else url.searchParams.set('category',state.category);
    history.replaceState(null,'',url.pathname+url.search+url.hash);
  }catch{}
}
function focusCountry(id=state.selected,targetZoom=2.7){
  const c=state.byId.get(id);if(!c)return;
  const [x,y]=project(...c.center);state.zoom=Math.max(1,Math.min(8,targetZoom));state.tx=550-x*state.zoom;state.ty=325-y*state.zoom;applyView();hideTooltip();
}
function resetView(){state.zoom=1;state.tx=state.ty=0;applyView();hideTooltip();}
function initialSelection(){
  try{
    const url=new URL(window.location.href);const country=url.searchParams.get('country');const category=url.searchParams.get('category');
    return {country:state.names.has(country)?country:'UKR',category:['all','persistent','localized','spillover'].includes(category)?category:'all'};
  }catch{return {country:'UKR',category:'all'};}
}
function selectCountry(id,scroll=true){
  const c=state.byId.get(id), n=state.names.get(id);if(!n && !c)return;
  state.selected=id;state.request++;state.news=null;syncURL();
  if(c && state.category!=='all' && state.category!==c.category)setCategory('all');
  $('search-results').hidden=true;$('search').value='';hideTooltip();updateColors();drawSelection();renderWatchlist();
  $('source-kind').value='all';
  if(c){
    $('country-detail').innerHTML=`<div class="detail-heading"><div><h2>${esc(c.name)}</h2><p class="country-en">${esc(c.en)} · ${esc(c.region)}</p></div><button class="focus-country" type="button" data-focus-country="${esc(c.id)}" aria-label="在地图上聚焦${esc(c.name)}">⌖ 聚焦地图</button></div><span class="category-badge ${c.category}"><i class="swatch ${c.category}"></i>${esc(c.label)}</span><p class="country-summary">${esc(c.summary)}</p><div class="area-block"><small>主要波及地区</small>${esc(c.areas)}</div><div class="basis"><span class="basis-title">核实依据</span>${c.sources.map(s=>`<a href="${esc(safeURL(s.url))}" target="_blank" rel="noopener noreferrer">${esc(s.name)} ↗</a><span>来源日期 ${esc(s.date)}</span>`).join('')}</div>`;
    $('refresh-news').disabled=false;$('days').disabled=false;$('source-kind').disabled=false;loadNews();loadMarket(c.id);
  }else{
    $('country-detail').innerHTML=`<h2>${esc(n.name)}</h2><p class="country-en">${esc(n.en)}</p><span class="category-badge">未纳入当前观察清单</span><p class="country-summary">当前没有为这个国家维护冲突状态。未着色不表示没有冲突或安全风险。</p>`;
    $('news-status').textContent='可以通过新闻检索入口继续了解该地区。';$('news-status').className='news-status';$('provider-status').replaceChildren();$('articles').innerHTML=`<div class="empty-news">暂无维护中的地区简报。<br><a class="source-link" href="${esc(safeURL('https://www.bing.com/news/search?q='+encodeURIComponent(n.en+' conflict')))}" target="_blank" rel="noopener noreferrer">打开相关新闻检索 ↗</a></div>`;
    ++marketToken;$('market-details').replaceChildren();$('market-prices').replaceChildren();$('market-status').textContent='未纳入当前冲突地区观察清单。';
    $('refresh-news').disabled=true;$('days').disabled=true;$('source-kind').disabled=true;
  }
  if(scroll && window.matchMedia('(max-width:920px)').matches)$('country-detail').scrollIntoView({behavior:'smooth',block:'start'});
}
// Market P1 is read-only, explicitly incomplete, and never drives conflict coloring.
let marketToken=0;
const money = v => Number(v).toLocaleString('zh-CN',{maximumFractionDigits:0});
async function marketJSON(url){
  const response=await fetch(url);
  if(!response.ok)throw new Error('Market HTTP '+response.status);
  return response.json();
}
async function loadMarket(country){
  const token=++marketToken;
  const status=$('market-status'),details=$('market-details'),prices=$('market-prices');
  status.textContent='正在查询本地市场数据库…';details.replaceChildren();prices.replaceChildren();
  try{
    const [trade, list]=await Promise.all([
      marketJSON('/api/trade/'+encodeURIComponent(country)),
      marketJSON('/api/prices')
    ]);
    if(token!==marketToken)return;
    if(!Array.isArray(trade.rows)||!Array.isArray(list.series))throw new Error('Invalid market API');
    status.textContent='本地数据库 · 已跟踪 HS6 子集（非国家贸易全量）';
    if(trade.rows.length){
      const heading=document.createElement('div');heading.className='market-caption';heading.textContent='已同步商品贸易 · 对世界合计';
      details.append(heading);
      for(const item of trade.rows.slice(0,4)){
        const row=document.createElement('div');row.className='market-record';
        row.innerHTML='<span>'+esc(item.commodity)+'<small>'+esc(item.period_start?.slice(0,4))+' · '+esc(item.flow==='export'?'出口':'进口')+' · HS '+esc(item.hs_code)+'</small></span>'+
          '<strong>if(!value)return '—';const d=new Date(value);return Number.isNaN(d.valueOf())?'—':d.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});}
async function loadNews(force=false){
  const id=state.selected;if(!state.byId.has(id))return;
  const token=++state.request;state.news=null;$('refresh-news').disabled=true;$('refresh-news').textContent='↻ 获取中';
  $('news-status').className='news-status';$('news-status').textContent='正在获取新闻订阅源，通常需要几秒…';$('articles').innerHTML='';
  try{
    const response=await fetch(`/api/news?country=${encodeURIComponent(id)}&days=${$('days').value}${force?'&refresh=1':''}`);
    if(!response.ok)throw new Error('HTTP '+response.status);
    const news=await response.json();if(token!==state.request)return;state.news=news;renderNews();
  }catch(error){
    if(token!==state.request)return;
    $('news-status').className='news-status stale';$('news-status').textContent='获取失败，请确认本地服务仍在运行，稍后重试。';
    $('articles').innerHTML='<div class="empty-news">暂时无法加载新闻。<br>点击右上方“刷新”重新获取。</div>';
  }finally{if(token===state.request){$('refresh-news').disabled=false;$('refresh-news').textContent='↻ 刷新';}}
}
function renderNews(){
  const news=state.news;if(!news)return;
  const kind=$('source-kind').value;const articles=news.articles.filter(x=>kind==='all'||x.kind===kind);
  const status=$('news-status');status.className='news-status '+news.status;
  if(news.status==='live')status.textContent=`${news.from_cache?'缓存':'订阅源已更新'} · 获取于 ${formatDate(news.fetched_at)}（北京时间） · ${articles.length} 条`;
  else status.textContent=news.message+(news.status==='snapshot'?` 快照日期 ${news.snapshot_date}`:` 获取于 ${formatDate(news.fetched_at)}`);
  if(news.failed_providers?.length)status.textContent+=' · 部分订阅源未响应';
  const providers=$('provider-status');providers.replaceChildren();
  const known=['Bing News','BBC News','UN News'];for(const name of known){const item=document.createElement('span');const failed=news.failed_providers?.includes(name);const ok=news.providers?.includes(name);item.className='provider-pill '+(failed?'failed':ok?'ok':'idle');item.textContent=(failed?'× ':ok?'● ':'○ ')+name;providers.append(item);}
  const node=$('articles');node.replaceChildren();
  for(const article of articles){const a=document.createElement('a');a.className='article';a.href=safeURL(article.url);a.target='_blank';a.rel='noopener noreferrer';
    a.innerHTML=`<div class="article-meta"><span class="source">${esc(article.source)}</span><span class="kind-badge">${article.kind==='official'?'官方':'媒体'}</span><span>·</span><time datetime="${esc(article.published)}">${esc(formatDate(article.published))}</time></div><h4 class="article-title"><span>${esc(article.title)}</span><span>↗</span></h4>`;node.append(a);}
  if(!articles.length){const c=state.byId.get(state.selected);node.innerHTML=`<div class="empty-news">当前时间范围 / 来源没有取得相关新闻。<br>可切换时间范围，或打开新闻检索。<br><a class="source-link" href="${esc(safeURL('https://www.bing.com/news/search?q='+encodeURIComponent(c.query)))}" target="_blank" rel="noopener noreferrer">检索 ${esc(c.name)} 相关新闻 ↗</a></div>`;}
}
function applyView(){
  const maxX=550*(state.zoom-1),maxY=325*(state.zoom-1);state.tx=Math.max(-maxX*2,Math.min(0,state.tx));state.ty=Math.max(-maxY*2,Math.min(0,state.ty));
  $('map-content').setAttribute('transform',`translate(${state.tx},${state.ty}) scale(${state.zoom})`);
}
function zoom(factor,point=[550,325]){const previous=state.zoom;state.zoom=Math.max(1,Math.min(8,state.zoom*factor));const k=state.zoom/previous;state.tx=point[0]-(point[0]-state.tx)*k;state.ty=point[1]-(point[1]-state.ty)*k;applyView();hideTooltip();}
function svgPoint(event){const p=$('world-map').createSVGPoint();p.x=event.clientX;p.y=event.clientY;const m=$('world-map').getScreenCTM();return m?p.matrixTransform(m.inverse()):{x:550,y:325};}
let drag=null,ignoreMapClick=false;
function wireEvents(){
  $('filters').addEventListener('click',e=>{const b=e.target.closest('button[data-category]');if(b)setCategory(b.dataset.category);});$('clear-filter').addEventListener('click',()=>setCategory('all'));
  $('country-detail').addEventListener('click',e=>{const b=e.target.closest('[data-focus-country]');if(b)focusCountry(b.dataset.focusCountry);});
  $('search').addEventListener('input',()=>{const q=$('search').value.trim().toLowerCase(),box=$('search-results');box.hidden=!q;box.replaceChildren();if(!q)return;
    const found=[...state.names.values()].filter(n=>(n.name+' '+n.en+' '+n.id).toLowerCase().includes(q)).sort((a,b)=>Number(state.byId.has(b.id))-Number(state.byId.has(a.id))).slice(0,8);
    for(const n of found){const c=state.byId.get(n.id),b=document.createElement('button');b.innerHTML=`<i class="swatch ${c?c.category:'neutral'}"></i>${esc(n.name)}<small>${c?'已纳入观察':'未纳入观察'}</small>`;b.addEventListener('click',()=>selectCountry(n.id));box.append(b);}
    if(!found.length)box.innerHTML='<div class="search-empty">没有找到该国家或地区</div>';
  });
  $('search').addEventListener('keydown',e=>{if(e.key==='Enter'){const first=$('search-results').querySelector('button');if(first)first.click();}if(e.key==='Escape')$('search-results').hidden=true;});
  document.addEventListener('click',e=>{if(!e.target.closest('.search')&&!e.target.closest('.search-results'))$('search-results').hidden=true;});
  document.addEventListener('keydown',e=>{if(e.key==='/'&&!['INPUT','SELECT','TEXTAREA'].includes(document.activeElement.tagName)){e.preventDefault();$('search').focus();}});
  $('refresh-news').addEventListener('click',()=>loadNews(true));$('days').addEventListener('change',()=>loadNews());$('source-kind').addEventListener('change',renderNews);
  $('about-button').addEventListener('click',()=>$('about').showModal());$('close-about').addEventListener('click',()=>$('about').close());
  $('about').addEventListener('click',e=>{if(e.target===$('about')){const r=e.target.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)e.target.close();}});
  $('zoom-in').addEventListener('click',()=>zoom(1.35));$('zoom-out').addEventListener('click',()=>zoom(1/1.35));$('reset-map').addEventListener('click',resetView);
  const map=$('world-map');map.addEventListener('wheel',e=>{e.preventDefault();const p=svgPoint(e);zoom(e.deltaY<0?1.15:1/1.15,[p.x,p.y]);},{passive:false});
  map.addEventListener('pointerdown',e=>{if(e.button!==0)return;const p=svgPoint(e);drag={id:e.pointerId,x:p.x,y:p.y,tx:state.tx,ty:state.ty,moved:false};ignoreMapClick=false;});
  map.addEventListener('pointermove',e=>{if(!drag||e.pointerId!==drag.id)return;const p=svgPoint(e),dx=p.x-drag.x,dy=p.y-drag.y;
    if(Math.abs(dx)+Math.abs(dy)>4){drag.moved=true;ignoreMapClick=true;map.setPointerCapture(e.pointerId);map.classList.add('dragging');hideTooltip();}
    if(drag.moved){state.tx=drag.tx+dx;state.ty=drag.ty+dy;applyView();}
  });
  const endDrag=e=>{if(drag?.id!==e.pointerId)return;drag=null;map.classList.remove('dragging');if(map.hasPointerCapture(e.pointerId))map.releasePointerCapture(e.pointerId);setTimeout(()=>{ignoreMapClick=false;},0);};
  map.addEventListener('pointerup',endDrag);map.addEventListener('pointercancel',endDrag);
}
async function init(){
  const updateClock=()=>{$('clock').textContent=new Date().toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false})+' CST';};updateClock();setInterval(updateClock,60000);
  try{
    const responses=await Promise.all([fetch('/api/conflicts'),fetch('/world.json')]);if(responses.some(r=>!r.ok))throw new Error('地图请求失败');
    const [config,world]=await Promise.all(responses.map(r=>r.json()));state.countries=config.countries;state.byId=new Map(config.countries.map(c=>[c.id,c]));state.features=world.features;
    $('coverage-count').textContent=state.countries.length;$('snapshot-date').textContent=config.updated;
    for(const type of ['all','persistent','localized','spillover'])$('count-'+type).textContent=type==='all'?state.countries.length:state.countries.filter(c=>c.category===type).length;
    drawMap();wireEvents();const initial=initialSelection();setCategory(initial.category,false);selectCountry(initial.country,false);
  }catch(error){$('map-loading').textContent='地图加载失败。请使用 Python 服务启动，然后刷新页面。';$('country-detail').innerHTML='<p class="error-banner">本地服务未连接</p>';}
}
init();
+esc(money(item.value_usd))+'</strong>';
        details.append(row);
      }
    }else details.innerHTML='<p class="market-empty">该地区尚无已同步的贸易记录；不能将缺失解释为零贸易。</p>';
    const traded=new Set(trade.rows.map(x=>x.commodity_id));
    const related=list.series.filter(x=>traded.has(x.commodity_id)&&Number(x.observation_count)>0);
    if(!related.length){
      prices.innerHTML='<p class="market-empty">暂未建立此地区与已导入价格序列的对应关系。</p>';
      return;
    }
    const caption=document.createElement('div');caption.className='market-caption';caption.textContent='相关商品的全球市场基准价格（独立数据序列）';prices.append(caption);
    for(const series of related.slice(0,2)){
      const doc=await marketJSON('/api/prices/'+encodeURIComponent(series.id));
      if(token!==marketToken)return;
      const points=doc.observations;
      if(!Array.isArray(points)||!points.length)continue;
      const wrapper=document.createElement('div');wrapper.className='market-chart';
      const subtitle=document.createElement('div');subtitle.className='market-record';
      subtitle.innerHTML='<span>'+esc(series.benchmark)+'<small>'+esc(series.frequency)+' · '+esc(series.currency)+' / '+esc(series.unit)+'</small></span>'+
        '<strong>'+esc(Number(points[points.length-1].value).toLocaleString('zh-CN',{maximumFractionDigits:2}))+'</strong>';
      wrapper.append(subtitle);
      const numeric=points.slice(-80).map(p=>Number(p.value)).filter(Number.isFinite);
      if(numeric.length>1){
        const lo=Math.min(...numeric),hi=Math.max(...numeric),span=hi-lo||1;
        const path=numeric.map((v,i)=>(i?'L':'M')+(i*280/(numeric.length-1)).toFixed(1)+','+(42-38*(v-lo)/span).toFixed(1)).join(' ');
        const plot=document.createElementNS(NS,'svg');plot.setAttribute('viewBox','0 0 280 48');
        plot.setAttribute('role','img');plot.setAttribute('aria-label',series.benchmark+'商品历史价格趋势，非因果分析');
        const line=document.createElementNS(NS,'path');line.setAttribute('d',path);line.setAttribute('class','market-sparkline');plot.append(line);wrapper.append(plot);
      }
      const foot=document.createElement('div');foot.className='market-caption';
      foot.textContent=points[0].observed_at+' — '+points[points.length-1].observed_at+' · '+doc.metadata.source_name;
      wrapper.append(foot);prices.append(wrapper);
    }
  }catch(_error){
    if(token!==marketToken)return;
    status.textContent='市场数据尚未就绪或无法读取；先运行 python sync_market.py init 并导入来源数据。';
    details.replaceChildren();prices.replaceChildren();
  }
}

function formatDate(value){if(!value)return '—';const d=new Date(value);return Number.isNaN(d.valueOf())?'—':d.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});}
async function loadNews(force=false){
  const id=state.selected;if(!state.byId.has(id))return;
  const token=++state.request;state.news=null;$('refresh-news').disabled=true;$('refresh-news').textContent='↻ 获取中';
  $('news-status').className='news-status';$('news-status').textContent='正在获取新闻订阅源，通常需要几秒…';$('articles').innerHTML='';
  try{
    const response=await fetch(`/api/news?country=${encodeURIComponent(id)}&days=${$('days').value}${force?'&refresh=1':''}`);
    if(!response.ok)throw new Error('HTTP '+response.status);
    const news=await response.json();if(token!==state.request)return;state.news=news;renderNews();
  }catch(error){
    if(token!==state.request)return;
    $('news-status').className='news-status stale';$('news-status').textContent='获取失败，请确认本地服务仍在运行，稍后重试。';
    $('articles').innerHTML='<div class="empty-news">暂时无法加载新闻。<br>点击右上方“刷新”重新获取。</div>';
  }finally{if(token===state.request){$('refresh-news').disabled=false;$('refresh-news').textContent='↻ 刷新';}}
}
function renderNews(){
  const news=state.news;if(!news)return;
  const kind=$('source-kind').value;const articles=news.articles.filter(x=>kind==='all'||x.kind===kind);
  const status=$('news-status');status.className='news-status '+news.status;
  if(news.status==='live')status.textContent=`${news.from_cache?'缓存':'订阅源已更新'} · 获取于 ${formatDate(news.fetched_at)}（北京时间） · ${articles.length} 条`;
  else status.textContent=news.message+(news.status==='snapshot'?` 快照日期 ${news.snapshot_date}`:` 获取于 ${formatDate(news.fetched_at)}`);
  if(news.failed_providers?.length)status.textContent+=' · 部分订阅源未响应';
  const providers=$('provider-status');providers.replaceChildren();
  const known=['Bing News','BBC News','UN News'];for(const name of known){const item=document.createElement('span');const failed=news.failed_providers?.includes(name);const ok=news.providers?.includes(name);item.className='provider-pill '+(failed?'failed':ok?'ok':'idle');item.textContent=(failed?'× ':ok?'● ':'○ ')+name;providers.append(item);}
  const node=$('articles');node.replaceChildren();
  for(const article of articles){const a=document.createElement('a');a.className='article';a.href=safeURL(article.url);a.target='_blank';a.rel='noopener noreferrer';
    a.innerHTML=`<div class="article-meta"><span class="source">${esc(article.source)}</span><span class="kind-badge">${article.kind==='official'?'官方':'媒体'}</span><span>·</span><time datetime="${esc(article.published)}">${esc(formatDate(article.published))}</time></div><h4 class="article-title"><span>${esc(article.title)}</span><span>↗</span></h4>`;node.append(a);}
  if(!articles.length){const c=state.byId.get(state.selected);node.innerHTML=`<div class="empty-news">当前时间范围 / 来源没有取得相关新闻。<br>可切换时间范围，或打开新闻检索。<br><a class="source-link" href="${esc(safeURL('https://www.bing.com/news/search?q='+encodeURIComponent(c.query)))}" target="_blank" rel="noopener noreferrer">检索 ${esc(c.name)} 相关新闻 ↗</a></div>`;}
}
function applyView(){
  const maxX=550*(state.zoom-1),maxY=325*(state.zoom-1);state.tx=Math.max(-maxX*2,Math.min(0,state.tx));state.ty=Math.max(-maxY*2,Math.min(0,state.ty));
  $('map-content').setAttribute('transform',`translate(${state.tx},${state.ty}) scale(${state.zoom})`);
}
function zoom(factor,point=[550,325]){const previous=state.zoom;state.zoom=Math.max(1,Math.min(8,state.zoom*factor));const k=state.zoom/previous;state.tx=point[0]-(point[0]-state.tx)*k;state.ty=point[1]-(point[1]-state.ty)*k;applyView();hideTooltip();}
function svgPoint(event){const p=$('world-map').createSVGPoint();p.x=event.clientX;p.y=event.clientY;const m=$('world-map').getScreenCTM();return m?p.matrixTransform(m.inverse()):{x:550,y:325};}
let drag=null,ignoreMapClick=false;
function wireEvents(){
  $('filters').addEventListener('click',e=>{const b=e.target.closest('button[data-category]');if(b)setCategory(b.dataset.category);});$('clear-filter').addEventListener('click',()=>setCategory('all'));
  $('country-detail').addEventListener('click',e=>{const b=e.target.closest('[data-focus-country]');if(b)focusCountry(b.dataset.focusCountry);});
  $('search').addEventListener('input',()=>{const q=$('search').value.trim().toLowerCase(),box=$('search-results');box.hidden=!q;box.replaceChildren();if(!q)return;
    const found=[...state.names.values()].filter(n=>(n.name+' '+n.en+' '+n.id).toLowerCase().includes(q)).sort((a,b)=>Number(state.byId.has(b.id))-Number(state.byId.has(a.id))).slice(0,8);
    for(const n of found){const c=state.byId.get(n.id),b=document.createElement('button');b.innerHTML=`<i class="swatch ${c?c.category:'neutral'}"></i>${esc(n.name)}<small>${c?'已纳入观察':'未纳入观察'}</small>`;b.addEventListener('click',()=>selectCountry(n.id));box.append(b);}
    if(!found.length)box.innerHTML='<div class="search-empty">没有找到该国家或地区</div>';
  });
  $('search').addEventListener('keydown',e=>{if(e.key==='Enter'){const first=$('search-results').querySelector('button');if(first)first.click();}if(e.key==='Escape')$('search-results').hidden=true;});
  document.addEventListener('click',e=>{if(!e.target.closest('.search')&&!e.target.closest('.search-results'))$('search-results').hidden=true;});
  document.addEventListener('keydown',e=>{if(e.key==='/'&&!['INPUT','SELECT','TEXTAREA'].includes(document.activeElement.tagName)){e.preventDefault();$('search').focus();}});
  $('refresh-news').addEventListener('click',()=>loadNews(true));$('days').addEventListener('change',()=>loadNews());$('source-kind').addEventListener('change',renderNews);
  $('about-button').addEventListener('click',()=>$('about').showModal());$('close-about').addEventListener('click',()=>$('about').close());
  $('about').addEventListener('click',e=>{if(e.target===$('about')){const r=e.target.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)e.target.close();}});
  $('zoom-in').addEventListener('click',()=>zoom(1.35));$('zoom-out').addEventListener('click',()=>zoom(1/1.35));$('reset-map').addEventListener('click',resetView);
  const map=$('world-map');map.addEventListener('wheel',e=>{e.preventDefault();const p=svgPoint(e);zoom(e.deltaY<0?1.15:1/1.15,[p.x,p.y]);},{passive:false});
  map.addEventListener('pointerdown',e=>{if(e.button!==0)return;const p=svgPoint(e);drag={id:e.pointerId,x:p.x,y:p.y,tx:state.tx,ty:state.ty,moved:false};ignoreMapClick=false;});
  map.addEventListener('pointermove',e=>{if(!drag||e.pointerId!==drag.id)return;const p=svgPoint(e),dx=p.x-drag.x,dy=p.y-drag.y;
    if(Math.abs(dx)+Math.abs(dy)>4){drag.moved=true;ignoreMapClick=true;map.setPointerCapture(e.pointerId);map.classList.add('dragging');hideTooltip();}
    if(drag.moved){state.tx=drag.tx+dx;state.ty=drag.ty+dy;applyView();}
  });
  const endDrag=e=>{if(drag?.id!==e.pointerId)return;drag=null;map.classList.remove('dragging');if(map.hasPointerCapture(e.pointerId))map.releasePointerCapture(e.pointerId);setTimeout(()=>{ignoreMapClick=false;},0);};
  map.addEventListener('pointerup',endDrag);map.addEventListener('pointercancel',endDrag);
}
async function init(){
  const updateClock=()=>{$('clock').textContent=new Date().toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false})+' CST';};updateClock();setInterval(updateClock,60000);
  try{
    const responses=await Promise.all([fetch('/api/conflicts'),fetch('/world.json')]);if(responses.some(r=>!r.ok))throw new Error('地图请求失败');
    const [config,world]=await Promise.all(responses.map(r=>r.json()));state.countries=config.countries;state.byId=new Map(config.countries.map(c=>[c.id,c]));state.features=world.features;
    $('coverage-count').textContent=state.countries.length;$('snapshot-date').textContent=config.updated;
    for(const type of ['all','persistent','localized','spillover'])$('count-'+type).textContent=type==='all'?state.countries.length:state.countries.filter(c=>c.category===type).length;
    drawMap();wireEvents();const initial=initialSelection();setCategory(initial.category,false);selectCountry(initial.country,false);
  }catch(error){$('map-loading').textContent='地图加载失败。请使用 Python 服务启动，然后刷新页面。';$('country-detail').innerHTML='<p class="error-banner">本地服务未连接</p>';}
}
init();
