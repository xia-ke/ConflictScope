// Logic checks with a minimal DOM fixture; not a substitute for browser visual QA.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const base = path.resolve(__dirname, '..');
class Element {
  constructor(){this.value='all';this.children=[];this.innerHTML='';this.textContent='';this.style={};this.disabled=false;}
  append(e){this.children.push(e);}
  replaceChildren(){this.children=[];this.innerHTML='';}
  setAttribute(k,v){this[k]=v;}
}
const elements = new Map();
const document = {
  getElementById(id){if(!elements.has(id))elements.set(id,new Element());return elements.get(id);},
  createElement(){return new Element();},
};
const pending=[];
const context=vm.createContext({document,Intl,URL,Date,Map,Math,String,console,fetch:(url)=>new Promise(resolve=>pending.push({url,resolve}))});
const script=fs.readFileSync(path.join(base,'static/app.js'),'utf8').replace(/init\(\);\s*$/, '');
vm.runInContext(script,context);
const run=s=>vm.runInContext(s,context);
const config=JSON.parse(fs.readFileSync(path.join(base,'data/conflicts.json')));
context.config=config;
run("state.countries=config.countries;state.byId=new Map(config.countries.map(c=>[c.id,c]));");
document.getElementById('days').value='30';
const world=JSON.parse(fs.readFileSync(path.join(base,'static/world.json')));
context.world=world;
assert.equal(run("world.features.every(f=>!/[Nn]a[Nn]|Infinity/.test(shapePath(f)))"),true);
assert.equal(run("safeURL('javascript:alert(1)')"),'#');
assert.equal(run("safeURL('https://user:pass@example.org/story')"),'#');
assert.equal(run("esc('<img src=x onerror=bad>')"),'&lt;img src=x onerror=bad&gt;');
(async()=>{
  run("state.selected='UKR'");
  const first=run('loadNews()');
  run("state.selected='SDN'");
  const second=run('loadNews(true)');
  assert.equal(pending.length,2);
  assert.match(pending[1].url,/country=SDN.*refresh=1/);
  const sudan={country:'SDN',status:'live',articles:[],fetched_at:new Date().toISOString(),failed_providers:[]};
  pending[1].resolve({ok:true,json:async()=>sudan});await second;
  pending[0].resolve({ok:true,json:async()=>({country:'UKR',status:'live',articles:[]})});await first;
  assert.equal(run('state.news.country'),'SDN','An old request must not replace the newly selected country');
  const malicious={title:'<script>bad()</script>',url:'javascript:bad()',source:'<img>',kind:'media',published:new Date().toISOString()};
  context.malicious=malicious;run("state.news.articles=[malicious];renderNews()");
  const link=document.getElementById('articles').children[0];
  assert.equal(link.href,'#');
  assert.ok(!link.innerHTML.includes('<script>'));
  assert.ok(!link.innerHTML.includes('<img>'));
  console.log('Frontend logic: geometry, link sanitization, text escaping, refresh, request races OK');
})().catch(e=>{console.error(e);process.exitCode=1;});
