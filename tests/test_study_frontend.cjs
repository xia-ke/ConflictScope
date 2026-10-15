'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

class MockElement {
  constructor(name='div'){
    this.nodeName=name;this.children=[];this.className='';this.value='';
    this.textContent='';this.listeners={};this.attrs={};
  }
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=[...nodes];}
  addEventListener(name,fn){this.listeners[name]=fn;}
  change(value){this.value=value;return this.listeners.change?.();}
  setAttribute(name,value){this.attrs[name]=value;}
}
const ids=[
  'study-event','study-series','study-basis','study-window','study-status',
  'study-results','study-chart','study-comparison'
];
const elements=Object.fromEntries(ids.map(x=>[x,new MockElement(x)]));
elements['study-basis'].value='impact';
elements['study-window'].value='20';
const handlers={};
const requests=[];
let returnData={
  events:[{
    id:'fiction-event-1',title:'Synthetic test event',event_type:'sanction',
    timeline_date:'2022-02-26',links:[{commodity_id:'oil'}]
  }]
};
let analysis={
  status:'ok',event_date:'2022-02-26',event_trading_date:'2022-02-28',
  baseline_date:'2022-02-25',baseline_price:90,window:20,
  series:{benchmark:'Brent Spot',currency:'USD',unit:'barrel',frequency:'daily'},
  event:{id:'fiction-event-1',source_url:'https://example.org/original',
        title:'Synthetic test event',research_ai_assisted:true},
  anchor_field:'effective_at',date_fallback:false,
  returns:{0:{change_pct:2.1},1:{change_pct:-1},5:{change_pct:4},20:{change_pct:5}},
  points:[
    {offset:-1,date:'2022-02-25',price:90,cumulative_pct:0},
    {offset:0,date:'2022-02-28',price:91.89,cumulative_pct:2.1},
    {offset:1,date:'2022-03-01',price:89.1,cumulative_pct:-1},
    {offset:5,date:'2022-03-07',price:93.6,cumulative_pct:4},
    {offset:20,date:'2022-03-28',price:94.5,cumulative_pct:5}
  ]
};
const fetch=async url=>{
  requests.push(url);
  const u=new URL(url,'https://localhost');
  let payload;
  if(u.pathname==='/api/timeline')payload=returnData;
  else if(u.pathname==='/api/prices')payload={series:[
    {id:'fred:DCOILBRENTEU',benchmark:'Brent Spot',commodity_id:'oil',
     observation_count:1100,frequency:'daily',currency:'USD',unit:'barrel'},
    {id:'fred:PWHEAMTUSDM',benchmark:'Wheat monthly',commodity_id:'wheat',
     observation_count:96,frequency:'monthly',currency:'USD',unit:'metric ton'}
  ]};
  else if(u.pathname==='/api/analysis/event-study')payload=analysis;
  else if(u.pathname==='/api/analysis/compare')payload={
    events:[{event:{id:'fiction-event-1',title:'Synthetic test event'},
       event_date:'2022-02-26',returns:analysis.returns}]
  };
  else throw Error('Unexpected API '+url);
  return {ok:true,json:async()=>payload};
};
const document={
  readyState:'complete',
  getElementById(id){return elements[id]},
  createElement(name){return new MockElement(name)},
  createElementNS(ns,name){return new MockElement(name)},
  addEventListener(name,fn){handlers[name]=fn}
};
const Option=(label,value)=>{const o=new MockElement('option');o.textContent=label;o.value=value;return o;};
const code=fs.readFileSync(path.join(__dirname,'../static/study.js'),'utf8');
vm.runInNewContext(code,{fetch,document,URL,URLSearchParams,Option,console});

async function main(){
  assert.equal(typeof handlers['conflictscope:country'],'function');
  await handlers['conflictscope:country']({detail:{id:'RUS'}});
  assert.equal(elements['study-event'].value,'fiction-event-1');
  assert.equal(elements['study-series'].value,'fred:DCOILBRENTEU');
  assert.match(elements['study-status'].textContent,/2022-02-28/);
  assert.ok(elements['study-results'].children.some(el=>el.className==='study-stat-grid'));
  assert.ok(elements['study-results'].children.some(el=>el.className==='study-warning'&&/AI/.test(el.textContent)));
  const chart=elements['study-chart'].children[0];
  assert.equal(chart.nodeName,'svg');
  assert.ok(chart.children.some(el=>el.attrs.class==='study-event-line'));
  assert.equal(elements['study-comparison'].children.length,3);
  assert.ok(requests.some(x=>x.includes('/api/analysis/event-study?')));
  await elements['study-window'].change('5');
  assert.ok(requests.some(x=>x.includes('window=5')));
  analysis={...analysis,status:'unsupported_frequency',points:[],returns:{},
            reason:'Monthly prices cannot resolve daily events'};
  await elements['study-basis'].change('published');
  assert.match(elements['study-status'].textContent,/Monthly prices/);
  assert.equal(elements['study-chart'].children.length,0);
  await handlers['conflictscope:country']({detail:{id:null}});
  assert.match(elements['study-status'].textContent,/未纳入/);
  assert.equal(elements['study-chart'].children.length,0);
  console.log('P3 event price study frontend tests passed');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
