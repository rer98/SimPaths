/* (C) Copyright 2026, by Ross Richardson
 * Progressive experiment reads, unchanged-result reuse and access invalidation.
 * @author ross richardson
 */
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {ExperimentSource,validateExperiment} from './experiment_source.mjs';
const experiment='00000000-0000-4000-8000-000000000001',scope='d'.repeat(64);
const jobs=['00000000-0000-4000-8000-000000000002','00000000-0000-4000-8000-000000000003'];
const configs=[{id:'base',name:'Reference',role:'Baseline',key:'baseline'},
  {id:'alt',name:'Policy A',role:'Scenario',key:'scenario_1'}];
const selection={variable:'First',stratifier:'Overall',kind:'levels',scenarios:['scenario_1']};
const response=(value,status=200)=>new Response(JSON.stringify(value),{status,
  headers:{'content-type':'application/json','x-visualiser-cache-scope':scope}});
function fixture(options={}){
  let stage=1,denied=false,race=false;
  const requests=[],updates=[];
  const status=()=>({format:'simpaths.visualiser.experiment.v1',experiment,url:'/visualiser/experiment/'+experiment,
    publication:stage?String(stage).repeat(64):null,ready:stage,total:2,pending:stage<2,retryable:false,
    configurations:configs.map((c,i)=>({id:jobs[i],configuration:c.id,name:c.name,
      state:i===0&&stage<2?'active':'succeeded',charts:stage===2||stage===1&&i===1?'ready':'waiting',
      comparison:i===0?'not_applicable':stage<2?'waiting_baseline':'ready'}))});
  const catalogue=()=>({format:'simpaths.visualiser.catalogue.v1',publication:status().publication,
    configurations:stage===1?[configs[1]]:configs,seeds:['606'],notice:'Fictional',comparison_available:stage===2,
    limits:{response_bytes:8*1024**2,response_rows:20000},
    variables:[{name:'First',module:'Health',years:[2019],views:[{stratifier:'Overall',kind:'levels',bytes:3000,rows:2}]}]});
  const source=new ExperimentSource(experiment,{persistentCache:null,onUpdated:v=>updates.push(v),
    fetcher:async(url,init)=>{
      requests.push({url,init});
      if(denied)return response({error:'Sign in again'},403);
      const path=new URL(url,'https://localhost');
      if(path.pathname.endsWith(experiment))return response(status());
      if(path.pathname.endsWith('/catalogue'))return response(catalogue());
      if(race){race=false;stage=2;return response({error:'New results'},409);}
      const p=path.searchParams,ids=p.getAll('configuration');
      assert.equal(p.get('publication'),status().publication);
      return response({format:'simpaths.visualiser.view.v1',publication:status().publication,
        selection:{variable:p.get('variable'),stratifier:p.get('stratifier'),kind:p.get('kind'),configurations:ids},
        series:ids.map(id=>({configuration:id,rows:[{year:2019,scenario:configs.find(c=>c.id===id).role.toLowerCase(),
          module:'Health',variable:'First',variable_value:'Mean',stratifier:'Overall',stratifier_value:'Overall',metric_type:'mean',
          n_runs:1,total_sample:30,min_sample:30,mean_sample:30,mean_value:40,sd_value:null,lower_ci:null,upper_ci:null}]}))});
    },...options});
  return {source,requests,updates,status,stage:v=>stage=v,deny:()=>denied=true,race:()=>race=true};
}
test('one alternative opens first, later readiness updates the same source, and unchanged status does not reread indices',async()=>{
  const f=fixture();
  assert.deepEqual((await f.source.load(selection)).map(r=>r.scenario),['scenario_1']);
  const key=f.source.comparison;await f.source.load(selection);
  assert.equal(f.source.stats.viewRequests,1);assert.equal(f.source.stats.catalogueRequests,1);
  f.stage(2);await f.source.refresh();
  assert.equal(f.source.comparison,key);assert.equal(f.updates.length,2);
  assert.deepEqual((await f.source.load(selection)).map(r=>r.scenario),['baseline','scenario_1']);
  assert.ok(f.requests.every(r=>!r.init.method&&r.init.credentials==='same-origin'&&r.init.cache==='no-store'));
  f.source.close();
});
test('a completion between status and section reads refreshes once and returns the new consistent publication',async()=>{
  const f=fixture();f.race();
  const rows=await f.source.load(selection);
  assert.deepEqual(rows.map(r=>r.scenario),['baseline','scenario_1']);
  assert.equal(f.source.catalogue.publication,'2'.repeat(64));f.source.close();
});
test('expiry removes ready charts and access loss clears all cached data and invalidates the open page',async()=>{
  let invalidated=0;
  const f=fixture({onInvalidated:()=>invalidated++});await f.source.load(selection);f.stage(0);
  assert.equal(await f.source.refresh(),null);assert.equal(f.updates.at(-1),null);assert.equal(f.source.cache.size,0);
  f.stage(1);await f.source.load(selection);f.deny();
  await assert.rejects(f.source.refresh(),/Sign in/);assert.equal(f.source.cache.size,0);assert.equal(f.source.closed,true);
  assert.equal(invalidated,1);
});
test('initial authorisation preserves the device cache for a reload, while a changed publication retires it',async()=>{
  let removed=0;
  const f=fixture({persistentCache:{activate:async()=>true,remove:async()=>removed++,get:async()=>null,
    put:async()=>{},close:()=>{}}});
  await f.source.refresh();assert.equal(removed,0);
  await f.source.load(selection);assert.equal(removed,0);
  f.stage(2);await f.source.refresh();assert.equal(removed,1);f.source.close();
});
test('cancelling one chart cannot cancel the shared live status read used by another caller',async()=>{
  const f=fixture(),original=f.source.fetcher;let release;
  const gate=new Promise(resolve=>release=resolve);let first=true;
  f.source.fetcher=async(url,options)=>{if(first){first=false;await gate;}return original(url,options);};
  const controller=new AbortController(),oldChart=f.source.refresh(controller.signal),page=f.source.refresh();
  controller.abort();await assert.rejects(oldChart,{name:'AbortError'});release();
  assert.equal((await page).publication,'1'.repeat(64));
  assert.deepEqual((await f.source.load(selection)).map(r=>r.scenario),['scenario_1']);f.source.close();
});
test('status rejects foreign experiments, duplicate jobs, inconsistent readiness and oversized metadata',()=>{
  const f=fixture();
  for(const edit of [v=>v.experiment=jobs[0],v=>v.configurations[1].id=jobs[0],v=>v.ready=0,v=>v.total=101]){
    const value=structuredClone(f.status());edit(value);assert.throws(()=>validateExperiment(value,experiment));
  }
  f.source.close();
});
