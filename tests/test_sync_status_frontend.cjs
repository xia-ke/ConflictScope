'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

const handlers={};
function elt(){
  return {children:[],textContent:'',className:'',listeners:{},
    append(...xs){this.children.push(...xs)},
    replaceChildren(...xs){this.children=[...xs]},
    addEventListener(name,fn){this.listeners[name]=fn}
  };
}
const items=Object.fromEntries(["sync-sources","sync-status","refresh-sync"].map(k=>[k,elt()]));
let response={jobs:[{
  job_id:"fred-brent",health:"current_update",last_success_at:"2026-10-15T06:00:00Z",
  latest_observation:"2026-10-14",review_pending:0
},{
  job_id:"official-events-watch",health:"current_update",last_success_at:"2026-10-15T06:00:00Z",
  latest_observation:null,review_pending:1
}],price_series:[{health:"stale_observations"}],
   price_revision_count:1,
   alerts:[{code:"source_change_requires_review"}]};
let fails=false, fetches=0;
const fetch=async path=>{
  assert.equal(path,"/api/sync/status");
  fetches++;
  return fails?{ok:false}:{ok:true,json:async()=>response};
};
const document={
  readyState:"complete",
  getElementById:id=>items[id],
  createElement:()=>elt(),
  addEventListener(name,fn){handlers[name]=fn}
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,"../static/sync_status.js"),"utf8"),
                   {document,fetch,console});
(async()=>{
  await new Promise(r=>setImmediate(r));
  assert.equal(fetches,1);
  assert.match(items["sync-status"].textContent,/异常提醒 1/);
  assert.ok(items["sync-sources"].children.some(el=>/待人工复核/.test(el.textContent)));
  assert.ok(items["sync-sources"].children.some(el=>/需核查/.test(el.textContent)));
  response={jobs:[],price_series:[],alerts:[],price_revision_count:0};
  items["refresh-sync"].listeners.click();
  await new Promise(r=>setImmediate(r));
  assert.match(items["sync-status"].textContent,/尚未运行/);
  fails=true;
  items["refresh-sync"].listeners.click();
  await new Promise(r=>setImmediate(r));
  assert.match(items["sync-status"].textContent,/暂不可用/);
  assert.equal(items["sync-sources"].children.length,0);
  console.log("P4 sync health panel frontend tests passed");
})().catch(e=>{console.error(e);process.exitCode=1});
