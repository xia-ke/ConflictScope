'use strict';
/* P3: graph factual spot-price returns around a documented event.
   No data fetch except local read-only API; no interpolated prices. */
(() => {
  const $=id=>document.getElementById(id);
  const NS='http://www.w3.org/2000/svg';
  const mk=(name,cls='',text='')=>{
    const node=document.createElement(name);
    if(cls)node.className=cls;
    if(text!==undefined)node.textContent=String(text);
    return node;
  };
  const fmt=x=>Number(x).toLocaleString('zh-CN',{maximumFractionDigits:2});
  const pct=x=>x===null||x===undefined?'—':(x>0?'+':'')+Number(x).toFixed(2)+'%';
  const safeSource=url=>{
    try{const u=new URL(url);return u.protocol==='https:'&&!u.username&&!u.password?u.href:null;}
    catch{return null;}
  };
  const distinct =items=>[...new Set(items)];
  let active=null,seq=0,catalog={events:[],series:[]};
  let started=false;

  async function getJSON(url){
    const response=await fetch(url);
    if(!response.ok)throw Error('HTTP '+response.status);
    return response.json();
  }
  function setOptions(id,entries,placeholder){
    const select=$(id),chosen=select.value;
    select.replaceChildren(new Option(placeholder,''));
    for(const [value,label] of entries)select.append(new Option(label,value));
    if(entries.some(([value])=>value===chosen))select.value=chosen;
    else select.value=entries.length?entries[0][0]:'';
  }
  function matchedSeries(event){
    const commodities=new Set((event?.links||[]).map(x=>x.commodity_id).filter(Boolean));
    return catalog.series.filter(s=>commodities.has(s.commodity_id)&&Number(s.observation_count)>0);
  }
  function syncSeries(){
    const event=catalog.events.find(x=>x.id===$('study-event').value);
    const matches=matchedSeries(event);
    setOptions('study-series',matches.map(s=>[s.id, s.benchmark+' · '+s.frequency]),'没有关联行情');
  }
  function clear(){
    $('study-results').replaceChildren();
    $('study-chart').replaceChildren();
    $('study-comparison').replaceChildren();
  }
  function statistic(key,value){
    const box=mk('div','study-stat');
    box.append(mk('span','study-stat-label',key),mk('strong','study-stat-value',value));
    return box;
  }
  function makeChart(points){
    const holder=$('study-chart');
    holder.replaceChildren();
    if(!Array.isArray(points)||points.length<2)return;
    const sorted=points.filter(p=>Number.isFinite(Number(p.cumulative_pct))&&Number.isInteger(p.offset))
                       .sort((a,b)=>a.offset-b.offset);
    if(sorted.length<2)return;
    const values=sorted.map(p=>Number(p.cumulative_pct));
    const low=Math.min(0,...values),high=Math.max(0,...values);
    const pad=Math.max(0.2,(high-low)*0.12);
    const min=low-pad,max=high+pad,span=max-min||1;
    const offMin=sorted[0].offset,offMax=sorted[sorted.length-1].offset;
    const x=off=>42+(off-offMin)*302/Math.max(offMax-offMin,1);
    const y=value=>165-(value-min)*135/span;
    const svg=document.createElementNS(NS,'svg');
    svg.setAttribute('viewBox','0 0 370 200');
    svg.setAttribute('role','img');
    svg.setAttribute('aria-label','商品行情累计价格变动；事件基准日标记在0，非因果估计');
    for(const fraction of [0,0.5,1]){
      const val=min+span*fraction;
      const line=document.createElementNS(NS,'line');
      line.setAttribute('x1','42');line.setAttribute('x2','344');
      line.setAttribute('y1',String(y(val)));line.setAttribute('y2',String(y(val)));
      line.setAttribute('class','study-grid-line');svg.append(line);
      const t=document.createElementNS(NS,'text');
      t.setAttribute('x','38');t.setAttribute('y',String(y(val)+3));
      t.setAttribute('text-anchor','end');t.setAttribute('class','study-axis');
      t.textContent=pct(val);svg.append(t);
    }
    if(offMin<=0&&offMax>=0){
      const cursor=document.createElementNS(NS,'line');
      cursor.setAttribute('x1',String(x(0)));cursor.setAttribute('x2',String(x(0)));
      cursor.setAttribute('y1','18');cursor.setAttribute('y2','170');
      cursor.setAttribute('class','study-event-line');svg.append(cursor);
      const mark=document.createElementNS(NS,'text');
      mark.setAttribute('x',String(x(0)+4));mark.setAttribute('y','15');
      mark.setAttribute('class','study-event-label');mark.textContent='事件日 0';
      svg.append(mark);
    }
    const path=document.createElementNS(NS,'path');
    path.setAttribute('d',sorted.map((p,i)=>(i?'L':'M')+x(p.offset).toFixed(2)+','+
                      y(Number(p.cumulative_pct)).toFixed(2)).join(' '));
    path.setAttribute('class','study-data-line');
    svg.append(path);
    for(const point of sorted){
      if(point.offset!==0&&point.offset!==offMin&&point.offset!==offMax)continue;
      const circle=document.createElementNS(NS,'circle');
      circle.setAttribute('cx',String(x(point.offset)));
      circle.setAttribute('cy',String(y(Number(point.cumulative_pct))));
      circle.setAttribute('r',point.offset===0?'3.8':'2.5');
      circle.setAttribute('class','study-data-dot');
      const tip=document.createElementNS(NS,'title');
      tip.textContent=point.date+' · '+pct(point.cumulative_pct)+' · '+fmt(point.price);
      circle.append(tip);svg.append(circle);
    }
    for(const offset of distinct([offMin,0,offMax])){
      if(offset<offMin||offset>offMax)continue;
      const tick=document.createElementNS(NS,'text');
      tick.setAttribute('x',String(x(offset)));tick.setAttribute('y','192');
      tick.setAttribute('text-anchor','middle');tick.setAttribute('class','study-axis');
      tick.textContent=(offset>0?'+':'')+offset;svg.append(tick);
    }
    holder.append(svg);
  }
  function showResults(data){
    clear();
    if(data.status!=='ok'){
      $('study-status').textContent=data.reason||'缺少符合条件的日度报价。';
      return;
    }
    $('study-status').textContent='事件 '+data.event_date+' · 行情对齐 '+data.event_trading_date+
      ' · 价格基线 '+data.baseline_date+' · '+data.series.frequency;
    const stats=mk('div','study-stat-grid');
    stats.append(
      statistic('事件日 / t+0',pct(data.returns['0']?.change_pct)),
      statistic('后 1 个交易日',pct(data.returns['1']?.change_pct)),
      statistic('后 5 个交易日',pct(data.returns['5']?.change_pct)),
      statistic('后 20 个交易日',pct(data.returns['20']?.change_pct))
    );
    $('study-results').append(stats);
    const source=safeSource(data.event.source_url);
    if(source){
      const link=mk('a','study-source','事件原始证据 ↗');
      link.href=source;link.target='_blank';link.rel='noopener noreferrer';
      $('study-results').append(link);
    }
    const line=mk('p','study-meta',data.series.benchmark+' · '+
      data.series.currency+'/'+data.series.unit+' · 基准价 '+fmt(data.baseline_price));
    $('study-results').append(line);
    if(data.date_fallback)$('study-results').append(
      mk('p','study-warning','所选日期类型缺失；分析已标注使用的回退日期字段：'+data.anchor_field));
    if(data.event.research_ai_assisted)$('study-results').append(
      mk('p','study-warning','该事件是 AI 辅助核查的官方来源记录，尚未完成人工复核。'));
    makeChart(data.points);
  }
  function renderComparison(payload){
    const root=$('study-comparison');
    root.replaceChildren();
    if(!Array.isArray(payload.events)||!payload.events.length)return;
    root.append(mk('h4','study-comparison-heading','同地区、同商品的其他事件对比'));
    const table=mk('table','study-comparison-table');
    const head=mk('tr');
    for(const name of ['事件（选定日期）','t+5','t+20'])head.append(mk('th','',name));
    table.append(head);
    for(const row of payload.events){
      const tr=mk('tr');
      tr.append(mk('td','',row.event.title+' · '+row.event_date));
      for(const k of ['5','20'])tr.append(mk('td','',pct(row.returns?.[k]?.change_pct)));
      table.append(tr);
    }
    root.append(table);
    root.append(mk('p','study-meta','相邻/重叠事件不能视为独立样本；这些仅是各自窗口的价格变化。'));
  }
  async function runStudy(){
    const current=++seq, country=active;
    clear();
    const eventId=$('study-event').value;
    const series=$('study-series').value;
    if(!country||!eventId||!series){
      $('study-status').textContent='需要先导入官方来源事件和对应商品的历史价格序列。';
      return;
    }
    const basis=$('study-basis').value,windowSize=$('study-window').value;
    $('study-status').textContent='正在对齐事件时间与本地报价…';
    const query=new URLSearchParams({event_id:eventId,series,basis,window:windowSize});
    const compareQuery=new URLSearchParams({country,series,basis,window:windowSize,limit:'30'});
    try{
      const [out,related]=await Promise.all([
        getJSON('/api/analysis/event-study?'+query),
        getJSON('/api/analysis/compare?'+compareQuery)
      ]);
      if(current!==seq)return;
      showResults(out);
      renderComparison(related);
    }catch(_err){
      if(current!==seq)return;
      clear();
      $('study-status').textContent='分析不可用，请检查事件与历史价格覆盖范围和本地数据库。';
    }
  }
  async function loadCountry(country){
    const current=++seq;
    active=country;catalog={events:[],series:[]};clear();
    if(!country){
      setOptions('study-event',[],'请选择事件');
      setOptions('study-series',[],'请选择价格序列');
      $('study-status').textContent='未纳入观察地区';
      return;
    }
    $('study-status').textContent='正在检查关联事件与可用商品价格…';
    try{
      const [events,prices]=await Promise.all([
        getJSON('/api/timeline?'+new URLSearchParams({country,limit:'200'})),
        getJSON('/api/prices')
      ]);
      if(current!==seq)return;
      if(!Array.isArray(events.events)||!Array.isArray(prices.series))throw Error('Bad response');
      catalog.events=events.events;catalog.series=prices.series;
      const applicable=events.events.filter(e=>matchedSeries(e).length);
      setOptions('study-event',applicable.map(e=>[e.id,e.title+' · '+String(e.timeline_date).slice(0,10)]),'没有对应的事件与价格数据');
      syncSeries();
      await runStudy();
    }catch(_err){
      if(current!==seq)return;
      $('study-status').textContent='本地事件或价格数据库尚未就绪。';
    }
  }
  function boot(){
    if(started)return;started=true;
    $('study-event').addEventListener('change',()=>{syncSeries();runStudy();});
    for(const id of ['study-series','study-basis','study-window'])
      $(id).addEventListener('change',runStudy);
    document.addEventListener('conflictscope:country',e=>loadCountry(e.detail?.id||null));
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);
  else boot();
})();
