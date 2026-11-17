/* (C) Copyright 2026, by Ross Richardson
 * Execute the compiled chooser against fictional 1,024-configuration responses.
 * Exercise paging, independent baselines, reload, retries and privacy cleanup.
 * @author ross richardson
 */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {test}=require('node:test');
const build=process.env.SIMPATHS_VISUALISER_TEST_BUILD,deps=process.env.SIMPATHS_VISUALISER_DEPENDENCIES;
if(!build||!deps)throw Error('Set compiled build and test dependency locations');
const {JSDOM,VirtualConsole}=require(path.join(deps,'jsdom'));
const experiment='00000000-0000-4000-8000-000000000001',scope='d'.repeat(64);
const id=n=>'configuration-'+n,job=n=>'00000000-0000-4000-8000-'+String(n+2).padStart(12,'0');
async function wait(check){const until=Date.now()+8000;while(!check()){
  if(Date.now()>until)throw Error('Compiled chooser did not reach expected state');await new Promise(r=>setTimeout(r,5));}}
async function fixture({query='',saved=null,offline=false,total=1024,missing=false}={}){
  const errors=[],requests=[],posts=[],console=new VirtualConsole();console.on('jsdomError',e=>errors.push(e.message));
  const dom=new JSDOM(fs.readFileSync(path.join(build,'index.html'),'utf8'),{
    url:'https://localhost/visualiser/experiment/'+experiment+query,runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:console});
  const w=dom.window,doc=w.document;let denied=false,down=offline;
  w.ResizeObserver=class{observe(){}disconnect(){}};w.TextEncoder=TextEncoder;w.TextDecoder=TextDecoder;
  w.URLSearchParams=URLSearchParams;w.Blob=Blob;
  if(saved!==null)w.localStorage.setItem('simpaths-visualiser-choice:'+experiment,saved);
  const row=n=>({configuration:id(n),ordinal:n+1,name:'Policy '+(n+1),state:'succeeded',selectable:true,
    ready:true,summaries_ready:true,settings:{'model.flag':String(!!(n%2)),'model.rate':String(n)}});
  const response=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'x-visualiser-cache-scope':scope}});
  w.fetch=async(url,options={})=>{
    requests.push({url:String(url),options});const parsed=new URL(url,'https://localhost'),q=parsed.searchParams;
    if(down)throw Error('Fictional connection failure');
    if(denied)return response({error:'Sign in again.'},403);
    if(parsed.pathname==='/api/session')return response({signed_in:true,csrf:'private-fixture'});
    if(parsed.pathname.endsWith('/choices')){
      const picked=q.getAll('picked').map(key=>Number(key.split('-').at(-1))).filter(n=>n<total&&!(missing&&n===0)).map(row);
      let rows=Array.from({length:total},(_,n)=>row(n));
      if(q.get('search'))rows=rows.filter(r=>r.name.toLowerCase().includes(q.get('search').toLowerCase()));
      if(q.get('parameter'))rows=rows.filter(r=>r.settings[q.get('parameter')]===q.get('value'));
      const offset=Math.min(Number(q.get('offset')),Math.max(0,Math.floor((rows.length-1)/50))*50);
      return response({format:'simpaths.visualiser.choices.v1',experiment,name:'Large fictional sweep',total,
        matching:rows.length,offset,page_size:50,automatic_limit:9,selection_limit:9,baseline:id(0),
        defaults:Array.from({length:Math.min(9,total)},(_,n)=>id(n)),picked,configurations:rows.slice(offset,offset+50),
        parameters:[{key:'model.flag',values:['false','true'],truncated:false}]});
    }
    if(options.method==='POST'){
      const value=JSON.parse(options.body);posts.push(value);assert.equal(options.headers['X-CSRF-Token'],'private-fixture');
      return response({});
    }
    const chosen=q.getAll('chosen'),ids=chosen.length?chosen:Array.from({length:total},(_,n)=>id(n));
    return response({format:'simpaths.visualiser.experiment.v1',experiment,url:'/visualiser/experiment/'+experiment,
      publication:null,ready:0,total:ids.length,pending:true,retryable:false,
      configurations:ids.map(key=>({id:job(Number(key.split('-').at(-1))),configuration:key,name:key,
        state:'succeeded',charts:'queued',comparison:'not_applicable'}))});
  };
  new vm.Script(fs.readFileSync(path.join(build,'visualiser.js'),'utf8')).runInContext(dom.getInternalVMContext());
  const button=text=>Array.from(doc.querySelectorAll('button')).find(b=>b.textContent.trim()===text);
  const idle=()=>!!button('Open Visualiser')&&!button('Open Visualiser').disabled;
  return {w,doc,dom,errors,requests,posts,button,idle,deny:()=>denied=true,connect:()=>down=false};
}

test('all 21 pages preserve chosen members and enforce nine including the baseline',async()=>{
  const f=await fixture();try{
    await wait(f.idle);assert.equal(f.posts.length,0);
    assert.ok(f.doc.body.textContent.includes('Selected: 5 / 9'));
    for(let page=1;page<21;page++){
      f.button('Next configurations').click();await wait(()=>f.doc.querySelector('tbody th')?.textContent.startsWith((page*50+1)+'.')&&f.idle());
    }
    assert.equal(f.doc.querySelectorAll('tbody tr').length,24);assert.equal(f.button('Next configurations').disabled,true);
    const boxes=Array.from(f.doc.querySelectorAll('tbody input'));
    boxes.slice(0,4).forEach(box=>box.click());await wait(()=>f.doc.body.textContent.includes('Selected: 9 / 9'));
    assert.equal(f.doc.querySelectorAll('tbody input:disabled').length,20);
    f.button('Open Visualiser').click();await wait(()=>f.button('Change configurations'));
    assert.equal(f.posts.length,1);assert.equal(f.posts[0].configurations.length,9);assert.equal(f.posts[0].baseline,id(0));
    assert.ok(f.posts[0].configurations.includes(id(1003)));
    assert.deepEqual(new URL(f.w.location.href).searchParams.getAll('chosen'),f.posts[0].configurations);
    const saved=f.w.localStorage.getItem('simpaths-visualiser-choice:'+experiment);
    assert.ok(!saved.includes('Policy'));assert.ok(!saved.includes('private-fixture'));
    f.button('Change configurations').click();await wait(f.idle);
    assert.ok(f.doc.body.textContent.includes('Selected: 9 / 9'));assert.equal(f.posts.length,1);
    assert.deepEqual(f.errors,[]);
  }finally{f.dom.window.close();}
});
test('a reload reopens the exact view without a preparation POST, while sign-in loss clears chooser details',async()=>{
  const query='?chosen=configuration-600&chosen=configuration-700&baseline=configuration-700';
  const f=await fixture({query});try{
    await wait(()=>f.button('Change configurations'));assert.equal(f.posts.length,0);
    assert.ok(f.requests.filter(r=>new URL(r.url,'https://localhost').pathname.endsWith(experiment))
      .every(r=>new URL(r.url,'https://localhost').searchParams.get('baseline')===id(700)));
    f.button('Change configurations').click();await wait(f.idle);
    assert.ok(f.doc.body.textContent.includes('Policy 601'));f.deny();f.button('Next configurations').click();
    await wait(()=>f.doc.body.textContent.includes('Sign in again.'));
    assert.equal(f.doc.querySelector('table'),null);assert.ok(!f.doc.body.textContent.includes('Policy 601'));
    assert.deepEqual(f.errors,[]);
  }finally{f.dom.window.close();}
});
test('initial failure retries the normal small-experiment opening without enqueueing work',async()=>{
  const f=await fixture({offline:true,total:9});try{
    await wait(()=>f.button('Retry connection'));f.connect();f.button('Retry connection').click();
    await wait(()=>f.button('Change configurations'));assert.equal(f.posts.length,0);
    assert.equal(f.doc.querySelector('.vm-chooser'),null);assert.deepEqual(f.errors,[]);
  }finally{f.dom.window.close();}
});
test('unavailable saved baselines are removed and a revised valid selection can be saved',async()=>{
  const f=await fixture({saved:JSON.stringify({configurations:[id(0),id(600)],baseline:id(0)}),missing:true});try{
    await wait(f.idle);assert.ok(f.doc.body.textContent.includes('Some saved configurations are unavailable'));
    assert.equal(f.doc.querySelector('section select').value,'');
    f.button('Open Visualiser').click();await wait(()=>f.button('Change configurations'));
    assert.deepEqual(f.posts,[{configurations:[id(600)],baseline:null}]);assert.deepEqual(f.errors,[]);
  }finally{f.dom.window.close();}
});
