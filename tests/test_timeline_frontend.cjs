'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function element() {
  const listeners = {};
  const node = {
    children: [], textContent: '', innerHTML: '', className: '',
    append(...items) { this.children.push(...items); },
    replaceChildren(...items) { this.children = [...items]; },
    addEventListener(name, handler) { listeners[name] = handler; },
    emit(name) { return listeners[name]?.(); }
  };
  return node;
}
const ids = Object.fromEntries([
  'timeline-basis','timeline-type','timeline-events','timeline-status'
].map(id => [id,element()]));
ids['timeline-basis'].value='impact';
ids['timeline-type'].value='';
const handlers={};
const document = {
  readyState:'complete',
  getElementById: id => ids[id],
  createElement: () => element(),
  addEventListener: (name,fn) => {handlers[name]=fn;}
};
const observed=[];
let pending=null;
const fetch = url => {
  observed.push(url);
  if(pending) return pending(url);
  return Promise.resolve({ok:true,json:async()=>({events:[{
    id:'fixture-event',title:'Test-only event',event_type:'embargo',
    verification_status:'official',track:'policy',timeline_date:'2026-01-05',
    occurred_at:'2026-01-01',effective_at:'2026-01-05',
    evidence:[
      {source_name:'Fixture',evidence_role:'primary',url:'https://example.org/proof'},
      {source_name:'Invalid',evidence_role:'context',url:'javascript:alert(1)'}
    ]
  }]})});
};
const script=fs.readFileSync(path.join(__dirname,'../static/timeline.js'),'utf8');
vm.runInNewContext(script,{document,URL,URLSearchParams,fetch,console});
assert.equal(typeof handlers['conflictscope:country'],'function');
handlers['conflictscope:country']({detail:{id:'UKR'}});
function walk(node) {
  return [node,...node.children.flatMap(walk)];
}
(async()=>{
  await new Promise(resolve=>setImmediate(resolve));
  assert.match(observed[0],/country=UKR/);
  assert.match(observed[0],/basis=impact/);
  assert.match(ids['timeline-status'].textContent,/1 条/);
  const all=walk(ids['timeline-events']);
  assert.ok(all.some(n=>n.textContent==='制裁 / 贸易政策 · 1'));
  assert.ok(all.some(n=>n.textContent==='Test-only event'));
  const links=all.filter(n=>n.href);
  assert.equal(links.length,1);
  assert.equal(links[0].href,'https://example.org/proof');
  ids['timeline-basis'].value='effective';
  ids['timeline-basis'].emit('change');
  await new Promise(resolve=>setImmediate(resolve));
  assert.match(observed.at(-1),/basis=effective/);
  handlers['conflictscope:country']({detail:{id:null}});
  assert.match(ids['timeline-status'].textContent,/未纳入/);
  assert.equal(ids['timeline-events'].children.length,0);
  pending=async()=>({ok:false});
  handlers['conflictscope:country']({detail:{id:'SDN'}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.match(ids['timeline-status'].textContent,/暂不可用/);
  console.log('P2 timeline frontend checks passed');
})().catch(err=>{console.error(err);process.exitCode=1;});
