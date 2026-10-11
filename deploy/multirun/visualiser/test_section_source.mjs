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
const scope='d'.repeat(64);
const response=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'content-type':'application/json',
 'x-visualiser-cache-scope':scope}});
const selection={variable:'First',stratifier:'Overall',kind:'levels',scenarios:['scenario_1','scenario_2']};
const row=(changes={})=>({year:2019,scenario:'baseline',module:'Health',variable:'First',variable_value:'Mean',
 stratifier:'Overall',stratifier_value:'Overall',metric_type:'mean',n_runs:1,total_sample:30,min_sample:30,
 mean_sample:30,mean_value:40,sd_value:null,lower_ci:null,upper_ci:null,...changes});
function fixture(options={}){
 const requests=[];let denied=false,changed=false;
 const fetcher=async(url,init)=>{
  requests.push({url,init});
  if(denied)return response({error:'Sign in with an approved email'},403);
  if(url.endsWith('/catalogue'))return response({...catalogue,publication:changed?'c'.repeat(64):publication});
  const query=new URL(url,'http://localhost').searchParams,ids=query.getAll('configuration');
  const selected={variable:query.get('variable'),stratifier:query.get('stratifier'),kind:query.get('kind'),configurations:ids};
  return response({format:'simpaths.visualiser.view.v1',publication,selection:selected,
   series:ids.map(id=>({configuration:id,rows:[row({scenario:configs.find(c=>c.id===id).role.toLowerCase(),
    variable:selected.variable,stratifier:selected.stratifier,metric_type:'mean',mean_value:id==='base'?40:45})]}))});
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

class MemoryDisk{
 constructor(){this.entries=new Map();this.scope=null;this.reads=0;this.removals=0;}
 async activate(value){if(this.scope!==value){this.entries.clear();this.scope=value;}return true;}
 async get(value,key){this.reads++;return value===this.scope?this.entries.get(key)?.content??null:null;}
 async put(value,comparison,key,content){if(value===this.scope)this.entries.set(key,{comparison,content});}
 async remove(comparison){this.removals++;for(const [key,value]of this.entries)if(value.comparison===comparison)this.entries.delete(key);}
 close(){}
}
const dwell=()=>new Promise(resolve=>setTimeout(resolve,350));
test('refresh reauthorises before reading disk and avoids downloading already stored sections',async()=>{
 const disk=new MemoryDisk(),first=fixture({persistentCache:disk});
 const expected=await first.source.load(selection);first.source.close();
 const again=fixture({persistentCache:disk});
 assert.deepEqual(await again.source.load(selection),expected);
 assert.equal(again.source.stats.catalogueRequests,1);assert.equal(again.source.stats.viewRequests,0);
 assert.equal(again.source.stats.diskHits,3);assert.equal(again.requests.length,1);
 const denied=fixture({persistentCache:disk});denied.deny();const reads=disk.reads;
 await assert.rejects(denied.source.load(selection),/approved email/);
 assert.equal(disk.reads,reads);assert.equal(disk.entries.size,0);
});
test('a changed publication and a different comparison cannot reuse persistent rows',async()=>{
 const disk=new MemoryDisk(),f=fixture({persistentCache:disk});await f.source.load(selection);
 f.change();await assert.rejects(f.source.load(selection),/results have changed/);assert.equal(disk.entries.size,0);
 await fixture({persistentCache:disk}).source.load(selection);
 const other=fixture({persistentCache:disk});other.source.comparison='e'.repeat(64);
 await other.source.load(selection);assert.equal(other.source.stats.viewRequests,1);
});
test('corrupt or raw cached fields are discarded and recovered from approved section reads',async()=>{
 const disk=new MemoryDisk();await fixture({persistentCache:disk}).source.load(selection);
 for(const value of disk.entries.values()){const rows=JSON.parse(value.content);rows[0].id_Person='PRIVATE';value.content=JSON.stringify(rows);}
 const f=fixture({persistentCache:disk});await f.source.load(selection);
 assert.equal(f.source.stats.diskHits,0);assert.equal(f.source.stats.viewRequests,1);
 assert.ok([...disk.entries.values()].every(e=>!e.content.includes('id_Person')));
});
test('a session change invalidates both caches and the opaque scope never widens permission',async()=>{
 const disk=new MemoryDisk(),f=fixture({persistentCache:disk});await f.source.load(selection);
 const original=f.source.fetcher;
 f.source.fetcher=async(...args)=>{const result=await original(...args);result.headers.set('x-visualiser-cache-scope','e'.repeat(64));return result;};
 await assert.rejects(f.source.load(selection),/sign-in has changed/);
 assert.equal(f.source.cache.size,0);assert.equal(disk.entries.size,0);
});
test('hover/focus prefetch prepares one section after a dwell and a subsequent click reuses it',async()=>{
 const f=fixture({persistentCache:null});await f.source.load(selection);
 f.source.hint({variable:'Next'});await dwell();
 assert.equal(f.source.stats.prefetchRequests,1);
 const views=f.source.stats.viewRequests;
 await f.source.load({...selection,variable:'Next'});
 assert.equal(f.source.stats.viewRequests,views);assert.equal(f.source.stats.catalogueRequests,3);
 assert.ok(f.source.stats.prefetchBytes>0);assert.ok(f.source.stats.prefetchBytes<=2*1024**2);
});
test('transient hints, hidden pages, data-saving and oversized sections suppress background transfers',async()=>{
 const env={document:{hidden:false},navigator:{connection:{saveData:false,effectiveType:'4g'}}};
 const f=fixture({environment:env,persistentCache:null});await f.source.load(selection);
 f.source.hint({variable:'Next'});f.source.cancelHint();await dwell();
 env.document.hidden=true;f.source.hint({variable:'Next'});await dwell();
 env.document.hidden=false;env.navigator.connection.saveData=true;f.source.hint({variable:'Next'});await dwell();
 env.navigator.connection.saveData=false;env.navigator.connection.effectiveType='2g';f.source.hint({variable:'Next'});await dwell();
 env.navigator.connection.effectiveType='4g';f.source.catalogue.variables[1].views[0].bytes=3*1024**2;
 f.source.hint({variable:'Next'});await dwell();
 assert.equal(f.source.stats.prefetchRequests,0);assert.equal(f.source.stats.viewRequests,1);
});
test('foreground navigation cancels background work and late replies cannot repopulate a closed source',async()=>{
 const f=fixture({persistentCache:null});await f.source.load(selection);
 const original=f.source.fetcher;let release,backgroundSignal;
 f.source.fetcher=async(url,init)=>{
  if(url.includes('variable=Next')){backgroundSignal=init.signal;return new Promise(resolve=>{release=async()=>resolve(await original(url,init));});}
  return original(url,init);
 };
 f.source.hint({variable:'Next'});await dwell();assert.ok(release);
 await f.source.load(selection);assert.equal(backgroundSignal.aborted,true);
 f.source.close();await release();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(f.source.cache.size,0);
});
test('visibility and connection changes cancel a pending hint and release their listeners on close',async()=>{
 const documentListeners=new Map(),connectionListeners=new Map();
 const document={hidden:false,addEventListener:(type,fn)=>documentListeners.set(type,fn),
  removeEventListener:(type,fn)=>{assert.equal(documentListeners.get(type),fn);documentListeners.delete(type);}};
 const connection={saveData:false,effectiveType:'4g',addEventListener:(type,fn)=>connectionListeners.set(type,fn),
  removeEventListener:(type,fn)=>{assert.equal(connectionListeners.get(type),fn);connectionListeners.delete(type);}};
 const f=fixture({environment:{document,navigator:{connection}},persistentCache:null});await f.source.load(selection);
 f.source.hint({variable:'Next'});document.hidden=true;documentListeners.get('visibilitychange')();
 await dwell();assert.equal(f.source.stats.prefetchRequests,0);
 document.hidden=false;f.source.hint({variable:'Next'});connection.saveData=true;connectionListeners.get('change')();
 await dwell();assert.equal(f.source.stats.prefetchRequests,0);
 f.source.close();assert.equal(documentListeners.size,0);assert.equal(connectionListeners.size,0);
});
test('background attempts and downloaded bytes remain bounded and unavailable storage falls back to network',async()=>{
 const f=fixture({persistentCache:null});await f.source.load(selection);
 f.source.prefetchCount=20;f.source.hint({variable:'Next'});await dwell();assert.equal(f.source.stats.prefetchRequests,0);
 f.source.prefetchCount=0;f.source.prefetchedBytes=8*1024**2;f.source.hint({variable:'Next'});await dwell();
 assert.equal(f.source.stats.prefetchRequests,0);
 const disabled=new MemoryDisk();disabled.activate=async()=>false;
 const network=fixture({persistentCache:disabled});await network.source.load(selection);
 assert.equal(network.source.stats.viewRequests,1);assert.equal(disabled.entries.size,0);
});
