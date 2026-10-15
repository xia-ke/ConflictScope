'use strict';
/* P2: a standalone, read-only event timeline. Do not assign price causality. */
(() => {
  const $ = id => document.getElementById(id);
  const escapeText = value => String(value ?? '').replace(/[&<>"']/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const safeUrl = value => {
    try {
      const u = new URL(value);
      return u.protocol === 'https:' && !u.username && !u.password ? u.href : null;
    } catch {return null;}
  };
  const labelType = {
    armed_conflict:'军事冲突',ceasefire:'停火',sanction:'国际制裁',
    embargo:'禁运',production_disruption:'生产中断',
    logistics_disruption:'物流中断',trade_policy:'贸易政策',
    recovery:'生产/物流恢复',other:'其他事件'
  };
  const tracks = [
    ['conflict','冲突 / 停火'],['policy','制裁 / 贸易政策'],
    ['supply','生产 / 供应'],['logistics','物流 / 运输'],['other','其他']
  ];
  const dateFields = [
    ['occurred_at','发生'],['announced_at','公告'],
    ['effective_at','生效'],['published_at','来源发布']
  ];
  let selected = null;
  let token = 0;
  let initialized = false;

  function dateLabel(value){
    return value ? String(value).slice(0,16).replace('T',' ') : '未记录';
  }
  function renderEvent(event){
    const card=document.createElement('article');
    card.className='timeline-event';
    const top=document.createElement('div');
    top.className='timeline-event-top';
    const when=document.createElement('time');
    when.className='timeline-event-date';
    when.textContent=dateLabel(event.timeline_date);
    if(event.timeline_date)when.dateTime=event.timeline_date;
    const tag=document.createElement('span');
    tag.className='timeline-event-tag';
    tag.textContent=labelType[event.event_type]||event.event_type;
    top.append(when,tag);
    const heading=document.createElement('h4');
    heading.className='timeline-event-title';
    heading.textContent=String(event.title||'未命名事件');
    const notes=document.createElement('div');
    notes.className='timeline-event-dates';
    for(const [field,label] of dateFields){
      if(event[field]){
        const span=document.createElement('span');
        span.textContent=label+' '+dateLabel(event[field]);
        notes.append(span);
      }
    }
    const description=document.createElement('p');
    description.className='timeline-event-desc';
    description.textContent=event.description||'原始记录未提供补充说明';
    const stamp=document.createElement('span');
    stamp.className='timeline-evidence-status';
    stamp.textContent='已编辑审核 · '+({
      reported:'来源报道',corroborated:'多来源交叉核对',
      official:'标注为官方来源',disputed:'存在争议'
    }[event.verification_status]||'来源待核对');
    card.append(top,heading,notes,description,stamp);
    const proof=document.createElement('div');
    proof.className='timeline-evidence';
    for(const item of (event.evidence||[]).slice(0,8)){
      const url=safeUrl(item.url);
      if(!url)continue;
      const a=document.createElement('a');
      a.href=url;a.target='_blank';a.rel='noopener noreferrer';
      a.textContent=(item.evidence_role==='primary'?'原始证据':'补充来源')+
        ' · '+String(item.source_name||'来源')+' ↗';
      proof.append(a);
    }
    card.append(proof);
    return card;
  }
  function render(data){
    const events = Array.isArray(data.events) ? data.events : [];
    const area=$('timeline-events');
    area.replaceChildren();
    $('timeline-status').textContent=events.length ?
      '共 '+events.length+' 条已审阅记录 · 按所选日期基准排序' :
      '当前筛选下没有已审核事件。新闻条目不会自动成为事件记录。';
    if(!events.length)return;
    for(const [track,name] of tracks){
      const matched=events.filter(e=>e.track===track);
      if(!matched.length)continue;
      const block=document.createElement('section');
      block.className='timeline-track track-'+track;
      const head=document.createElement('h4');
      head.className='timeline-track-label';
      head.textContent=name+' · '+matched.length;
      block.append(head);
      for(const e of matched)block.append(renderEvent(e));
      area.append(block);
    }
  }
  async function load(){
    const current=++token;
    const node=$('timeline-events');
    node.replaceChildren();
    if(!selected){
      $('timeline-status').textContent='未纳入当前观察清单';
      return;
    }
    $('timeline-status').textContent='正在读取本地已审阅事件…';
    const params=new URLSearchParams({country:selected,basis:$('timeline-basis').value,limit:'100'});
    if($('timeline-type').value)params.set('type',$('timeline-type').value);
    try{
      const response=await fetch('/api/timeline?'+params.toString());
      if(!response.ok)throw Error('HTTP '+response.status);
      const data=await response.json();
      if(current!==token)return;
      render(data);
    }catch(err){
      if(current!==token)return;
      $('timeline-status').textContent='事件数据库暂不可用；可运行 python sync_market.py init，审核并导入事件批次。';
      node.replaceChildren();
    }
  }
  function setup(){
    if(initialized)return;
    initialized=true;
    $('timeline-basis').addEventListener('change',load);
    $('timeline-type').addEventListener('change',load);
    document.addEventListener('conflictscope:country',e=>{
      selected=e.detail?.id||null;
      load();
    });
  }
  if(document.readyState==='loading'){
    document.addEventListener('DOMContentLoaded',setup);
  }else setup();
})();
