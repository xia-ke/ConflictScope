'use strict';
/* Isolated regression: price benchmarks remain discoverable through documented
   event links even when no Comtrade rows are present for that country. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function el(){
  return {
    children:[], className:'', innerHTML:'', textContent:'',
    append(...nodes){this.children.push(...nodes);},
    replaceChildren(...nodes){this.children=[...nodes];},
    setAttribute(){}
  };
}
const nodes=Object.fromEntries(
  ['market-status','market-details','market-prices'].map(k=>[k,el()])
);
const requested=[];
const responses = {
  '/api/trade/UKR':{rows:[]},
  '/api/prices':{series:[{
      id:'fred:PWHEAMTUSDM',benchmark:'IMF global wheat (FRED)',
      commodity_id:'wheat',observation_count:2,frequency:'monthly',
      currency:'USD',unit:'metric ton'
  }]},
  '/api/timeline?country=UKR':{events:[{
      event_type:'recovery',links:[{commodity_id:'wheat'}]
  }]},
  '/api/prices/fred%3APWHEAMTUSDM':{
      metadata:{source_name:'FRED: IMF Commodity Prices'},
      observations:[
        {observed_at:'2022-01-01',value:250},
        {observed_at:'2022-02-01',value:260}
      ]
  }
};
const fetch=async url=>{
  requested.push(url);
  if(!(url in responses))throw Error('Unexpected '+url);
  return {ok:true,json:async()=>responses[url]};
};
const original=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
const begin=original.indexOf('// P1 market panel:');
const end=original.indexOf('function formatDate(',begin);
assert.ok(begin>0&&end>begin);
const part=original.slice(begin,end)+'\nthis.testLoadMarket=loadMarket;';
const ctx={fetch,document:{
  getElementById:id=>nodes[id],
  createElement:el,
  createElementNS:el
},NS:'http://www.w3.org/2000/svg',
esc:String,
$:id=>nodes[id],
console};
vm.runInNewContext(part,ctx);
(async()=>{
  await ctx.testLoadMarket('UKR');
  assert.match(nodes['market-details'].innerHTML,/尚无已同步的贸易记录/);
  assert.equal(nodes['market-prices'].children.length,2);
  assert.deepEqual(requested,[
    '/api/trade/UKR','/api/prices','/api/timeline?country=UKR',
    '/api/prices/fred%3APWHEAMTUSDM'
  ]);
  const priceCard=nodes['market-prices'].children[1];
  assert.equal(priceCard.children.length,3);
  console.log('Market panel event-linked commodity history regression passed');
})().catch(err=>{console.error(err);process.exitCode=1;});
