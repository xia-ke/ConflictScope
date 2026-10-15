'use strict';
/* P4 health summary: read-only; no periodic network polling / automatic writes. */
(() => {
  const $=id=>document.getElementById(id);
  let current=0;
  const messages={
    current_update:'最近更新成功',failed_last_attempt:'最近更新失败',
    never_succeeded:'尚无成功更新',stale_update:'超过十天未成功更新',
    missing:'没有行情',stale_observations:'行情观测日期过旧',
    current_observations:'行情观测日期较新'
  };
  function make(tag,classname,text){
    const el=document.createElement(tag);
    el.className=classname;
    el.textContent=String(text??'');
    return el;
  }
  async function load(){
    const token=++current,root=$('sync-sources'),status=$('sync-status');
    root.replaceChildren();status.textContent='正在读取本地同步审计记录…';
    try{
      const r=await fetch('/api/sync/status');
      if(!r.ok)throw Error('Unavailable');
      const doc=await r.json();
      if(token!==current)return;
      if(!Array.isArray(doc.jobs)||!Array.isArray(doc.price_series)||!Array.isArray(doc.alerts))
        throw Error('Unexpected health payload');
      if(!doc.jobs.length){
        status.textContent='尚未运行同步任务。请配置 sync_tasks.py 和系统定时任务。';
      }else status.textContent='任务 '+doc.jobs.length+' 个 · 异常提醒 '+doc.alerts.length+' 项 · 已记价格修订 '+(doc.price_revision_count||0)+' 次';
      const pending=doc.jobs.filter(j=>j.review_pending);
      if(pending.length){
        root.append(make('p','sync-attention',
          '官方事件文件有变更待人工复核：'+pending.map(j=>j.job_id).join('、')+
          '；未自动写入事件库。'));
      }
      const list=doc.jobs.slice(0,10);
      for(const job of list){
        const row=make('div','sync-record','');
        row.append(make('span','sync-record-name',job.job_id));
        row.append(make('span','sync-record-state',messages[job.health]||job.health));
        row.append(make('small','sync-record-date',
          '上次成功：'+(job.last_success_at?.slice(0,16).replace('T',' ')||'尚无')+
          ' · 最新观测：'+(job.latest_observation||'未知')));
        root.append(row);
      }
      const stale=doc.price_series.filter(s=>s.health!=='current_observations');
      if(stale.length){
        root.append(make('p','sync-attention',
          '需核查 '+stale.length+' 条行情序列：最近有效观测日与当前日期间隔较长。历史专项序列可能有意停止更新。'));
      }
    }catch(_err){
      if(token!==current)return;
      status.textContent='同步监控未初始化或暂不可用；请先运行 python sync_tasks.py once。';
      root.replaceChildren();
    }
  }
  function boot(){
    $('refresh-sync').addEventListener('click',load);
    load();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);
  else boot();
})();
