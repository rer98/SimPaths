/* (C) Copyright 2026, by Ross Richardson
 * Verify selected delivery, authorisation, cancellation and bounded cache reuse.
 * @author ross richardson
 */
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {SectionSource,validateCatalogue,boundedJSON} from './section_source.mjs';
const key='a'.repeat(64),publication='b'.repeat(64);
const configs=[{id:'base',name:'Reference',role:'Baseline',key:'baseline'},
 {id:'a',name:'Policy A',role:'Scenario',key:'scenario_1'},
 {id:'b',name:'Policy B',role:'Scenario',key:'scenario_2'}];
const catalogue={format:'simpaths.visualiser.catalogue.v1',publication,configurations:configs,
 seeds:['606'],notice:'Fictional',comparison_available:true,limits:{response_bytes:8*1024**2,response_rows:20000},
 variables:['First','Next'].map(name=>({name,module:'Health',years:[2019,2070],
  views:[{stratifier:'Overall',kind:'levels',bytes:1000,rows:6}]}))};
const response=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'content-type':'application/json'}});
const selection={variable:'First',stratifier:'Overall',kind:'levels',scenarios:['scenario_1','scenario_2']};
function fixture(options={}){
 const requests=[];let denied=false,changed=false;
 const fetcher=async(url,init)=>{
  requests.push({url,init});
  if(denied)return response({error:'Sign in with an approved email'},403);
  if(url.endsWith('/catalogue'))return response({...catalogue,publication:changed?'c'.repeat(64):publication});
  const query=new URL(url,'http://localhost').searchParams,ids=query.getAll('configuration');
  const selected={variable:query.get('variable'),stratifier:query.get('stratifier'),kind:query.get('kind'),configurations:ids};
  return response({format:'simpaths.visualiser.view.v1',publication,selection:selected,
   series:ids.map(id=>({configuration:id,rows:[{scenario:configs.find(c=>c.id===id).role.toLowerCase(),
    variable:selected.variable,stratifier:selected.stratifier,metric_type:'mean',mean_value:id==='base'?40:45}]}))});
 };
 return {source:new SectionSource(key,{fetcher,...options}),requests,deny:()=>denied=true,change:()=>changed=true};
}
test('the default native browser fetch keeps its global receiver',async()=>{
 const previous=globalThis.fetch;
 try{
  globalThis.fetch=async function(){
   assert.equal(this,globalThis,'Native Window.fetch rejects a loader object as its receiver');
   return response(catalogue);
  };
  const source=new SectionSource(key);
  assert.equal((await source.metadata()).publication,publication);
 }finally{globalThis.fetch=previous;}
});
test('a selected view never downloads the full package; repeated views reauthorise and reuse rows',async()=>{
 const f=fixture(),rows=await f.source.load(selection);
 assert.deepEqual(rows.map(r=>r.scenario),['baseline','scenario_1','scenario_2']);
 assert.equal(f.source.stats.viewRequests,1);
 assert.ok(f.requests[1].url.includes('variable=First'));
 const again=await f.source.load(selection);
 assert.deepEqual(again,rows);assert.equal(f.source.stats.viewRequests,1);
 assert.equal(f.source.stats.catalogueRequests,2);
 assert.ok(f.requests.every(r=>r.init.credentials==='same-origin'&&r.init.cache==='no-store'&&!r.url.endsWith('/data')));
});
test('adding scenarios reuses the baseline and requests only missing configurations',async()=>{
 const f=fixture();await f.source.load({...selection,scenarios:['scenario_1']});
 await f.source.load(selection);
 const view=f.requests.filter(r=>r.url.includes('/view?'));
 assert.deepEqual(new URL(view[1].url,'http://localhost').searchParams.getAll('configuration'),['b']);
});
test('cache accounting and entry count stay bounded through repeated navigation',async()=>{
 const f=fixture({cacheBytes:1200,cacheEntries:1});
 for(let i=0;i<8;i++)await f.source.load({...selection,variable:i%2?'First':'Next'});
 assert.ok(f.source.used<=1200);assert.ok(f.source.cache.size<=1);
 assert.ok(f.source.stats.viewRequests>=8);
 f.source.close();assert.equal(f.source.used,0);assert.equal(f.source.cache.size,0);
});
test('revoked access clears cached data and cannot return a previous chart',async()=>{
 const f=fixture();await f.source.load(selection);f.deny();
 await assert.rejects(f.source.load(selection),/approved email/);
 assert.equal(f.source.cache.size,0);assert.equal(f.source.used,0);
});
test('a changed publication invalidates the cache and asks to reopen results',async()=>{
 const f=fixture();await f.source.load(selection);f.change();
 await assert.rejects(f.source.load(selection),/results have changed/);
 assert.equal(f.source.cache.size,0);
});
test('late cancelled responses never enter the cache',async()=>{
 const abort=new AbortController();let release;
 const original=fixture();
 const source=new SectionSource(key,{fetcher:async(url,init)=>{
  if(url.endsWith('/catalogue'))return response(catalogue);
  return new Promise(resolve=>{release=()=>resolve(response({}));});
 }});
 const work=source.load(selection,abort.signal);
 while(!release)await new Promise(resolve=>setTimeout(resolve,0));
 abort.abort();release();await assert.rejects(work,e=>e.name==='AbortError');
 assert.equal(source.cache.size,0);assert.equal(original.source.cache.size,0);
});
test('catalogues reject duplicate roles, foreign fields and oversized selections',()=>{
 for(const invalid of [{...catalogue,raw_rows:[]},{...catalogue,configurations:[configs[0],configs[0]]},
   {...catalogue,limits:{response_bytes:1024**3,response_rows:20000}}])
  assert.throws(()=>validateCatalogue(invalid),/metadata/);
 const many={...catalogue,configurations:[configs[0],...Array.from({length:12},(_,i)=>({
  id:'p'+i,role:'Scenario',name:'Policy '+i,key:'scenario_'+(i+1)}))]};
 assert.equal(validateCatalogue(many).configurations.length,13);
});
test('oversized streaming responses are cancelled before JSON parsing',async()=>{
 let cancelled=false;
 const body=new ReadableStream({start(controller){controller.enqueue(new Uint8Array(2048));},cancel(){cancelled=true;}});
 await assert.rejects(boundedJSON(new Response(body),1024),/size limit/);
 assert.ok(cancelled);
});
test('combining separately cached sections still enforces the current-view byte bound',async()=>{
 const source=new SectionSource(key,{fetcher:async()=>response(catalogue)});
 source.catalogue=catalogue;
 source.put(source.key(selection,'base'),[{scenario:'baseline'}],3*1024**2);
 source.put(source.key(selection,'a'),[{scenario:'scenario_1'}],3*1024**2);
 // Force three cache hits without generating an oversized network fixture.
 const original=source.get.bind(source);
 source.get=k=>k===source.key(selection,'b')?{rows:[{scenario:'scenario_2'}],bytes:3*1024**2}:original(k);
 await assert.rejects(source.load(selection),/view is too large/);
 assert.equal(source.cache.size,0);assert.equal(source.stats.viewRequests,0);
});
