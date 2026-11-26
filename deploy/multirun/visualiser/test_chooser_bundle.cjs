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
async function fixture({query='',saved=null,offline=false,total=1024,missing=false,chartsReady=false,pairsReady=false,fastPolling=false}={}){
  const errors=[],requests=[],posts=[],console=new VirtualConsole();console.on('jsdomError',e=>errors.push(e.message));
  const dom=new JSDOM(fs.readFileSync(path.join(build,'index.html'),'utf8'),{
    url:'https://localhost/visualiser/experiment/'+experiment+query,runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:console});
  const w=dom.window,doc=w.document;let denied=false,down=offline,preparation=null;
  if(fastPolling){const schedule=w.setTimeout.bind(w);w.setTimeout=(fn,ms,...args)=>schedule(fn,ms===15000?100:ms,...args);}
  w.ResizeObserver=class{observe(){}disconnect(){}};w.TextEncoder=TextEncoder;w.TextDecoder=TextDecoder;
  w.URLSearchParams=URLSearchParams;w.Blob=Blob;
  if(saved!==null)w.localStorage.setItem('simpaths-visualiser-choice:'+experiment,saved);
  const row=n=>({configuration:id(n),ordinal:n+1,name:'Policy '+(n+1),state:'succeeded',selectable:true,
    ready:true,summaries_ready:n!==8,charts_ready:n===7||n<5&&chartsReady,
    comparison_baselines:n===7?(pairsReady?[1,2,601]:[1,601]):n>0&&n<5&&pairsReady?[1]:[],
    settings:{'model.flag':String(!!(n%2)),'model.rate':String(n)}});
  const response=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'x-visualiser-cache-scope':scope}});
  w.fetch=async(url,options={})=>{
    requests.push({url:String(url),options});const parsed=new URL(url,'https://localhost'),q=parsed.searchParams;
    if(down)throw Error('Fictional connection failure');
    if(denied)return response({error:'Sign in again.'},403);
    if(parsed.pathname==='/api/session')return response({signed_in:true,csrf:'private-fixture'});
    if(q.get('opening')==='1'&&total>9)
      return response({format:'simpaths.visualiser.choose.v1',experiment});
    if(parsed.pathname.endsWith('/choices')){
      const picked=q.getAll('picked').map(key=>Number(key.split('-').at(-1))).filter(n=>n<total&&!(missing&&n===0)).map(row);
      let rows=Array.from({length:total},(_,n)=>row(n));
      if(q.get('search'))rows=rows.filter(r=>r.name.toLowerCase().includes(q.get('search').toLowerCase()));
      if(q.get('parameter'))rows=rows.filter(r=>r.settings[q.get('parameter')]===q.get('value'));
      const offset=Math.min(Number(q.get('offset')),Math.max(0,Math.floor((rows.length-1)/50))*50);
      return response({format:'simpaths.visualiser.choices.v1',experiment,name:'Large fictional sweep',total,
        matching:rows.length,offset,page_size:50,automatic_limit:9,selection_limit:9,baseline:id(0),
        defaults:Array.from({length:Math.min(9,total)},(_,n)=>id(n)),picked,configurations:rows.slice(offset,offset+50),
        comparison_baseline_names:{1:'Policy 1',2:'Policy 2',601:'Policy 601'},
        parameters:[{key:'model.flag',values:['false','true'],truncated:false}]});
    }
    if(options.method==='POST'){
      const value=JSON.parse(options.body);posts.push(value);assert.equal(options.headers['X-CSRF-Token'],'private-fixture');
      if(preparation){
        const result=await preparation();
        return result.raw?new Response(result.raw,{status:result.status}):response(result.body,result.status);
      }
      assert.ok(parsed.pathname.endsWith('/preparations'));
      return response({request:'fixture-request',email_enabled:true},202);
    }
    const chosen=q.getAll('chosen'),ids=chosen.length?chosen:Array.from({length:total},(_,n)=>id(n));
    return response({format:'simpaths.visualiser.experiment.v1',experiment,url:'/visualiser/experiment/'+experiment,
      publication:chartsReady?'a'.repeat(64):null,ready:chartsReady?ids.length:0,total:ids.length,pending:!pairsReady,retryable:false,
      configurations:ids.map(key=>({id:job(Number(key.split('-').at(-1))),configuration:key,name:key,
        state:'succeeded',charts:chartsReady?'ready':'queued',comparison:q.get('baseline')&&key!==q.get('baseline')?(pairsReady?'ready':'queued'):'not_applicable'}))});
  };
  new vm.Script(fs.readFileSync(path.join(build,'visualiser.js'),'utf8')).runInContext(dom.getInternalVMContext());
  const button=text=>Array.from(doc.querySelectorAll('button')).find(b=>(b.getAttribute('aria-label')||b.textContent.trim())===text);
  const idle=()=>!!button('Open Visualiser')&&!button('Open Visualiser').disabled;
  return {w,doc,dom,errors,requests,posts,button,idle,deny:()=>denied=true,connect:()=>down=false,
    prepare:handler=>preparation=handler,ready:(charts,pairs)=>{chartsReady=charts;pairsReady=pairs;}};
}

test('all 21 pages preserve chosen members and enforce nine including the baseline',async()=>{
  const f=await fixture();try{
    await wait(f.idle);assert.equal(f.posts.length,0);
    assert.ok(f.doc.body.textContent.includes('Selected: 5 / 9'));
    const pagers=()=>Array.from(f.doc.querySelectorAll('nav[aria-label="Configuration pages"]'));
    const controls=()=>{
      const bars=pagers();assert.equal(bars.length,2);
      assert.equal(bars[0].dataset.configurationPosition,'top');assert.equal(bars[1].dataset.configurationPosition,'bottom');
      for(let i=0;i<4;i++)assert.equal(bars[0].querySelectorAll('button')[i].disabled,bars[1].querySelectorAll('button')[i].disabled);
      return bars;
    };
    assert.equal(controls()[0].querySelector('button').disabled,true);
    const jumps=f.requests.length;f.button('Last configurations').focus();f.button('Last configurations').click();
    await wait(()=>f.doc.querySelector('tbody th')?.textContent.startsWith('1001.')&&f.idle());
    assert.equal(f.requests.length,jumps+1);assert.equal(f.button('Last configurations').disabled,true);
    await wait(()=>f.doc.activeElement===f.button('Previous configurations'));
    f.button('First configurations').click();await wait(()=>f.doc.querySelector('tbody th')?.textContent.startsWith('1.')&&f.idle());
    assert.equal(f.button('First configurations').disabled,true);
    for(const button of controls()[0].querySelectorAll('button')){
      assert.equal(button.textContent,'');assert.ok(button.title);assert.equal(button.querySelector('svg').getAttribute('aria-hidden'),'true');
    }

    for(let page=1;page<21;page++){
      const next=controls()[page%2].querySelectorAll('button')[2];next.focus();next.click();
      await wait(()=>f.doc.querySelector('tbody th')?.textContent.startsWith((page*50+1)+'.')&&f.idle());
      await wait(()=>f.doc.activeElement===(page===20?controls()[page%2].querySelectorAll('button')[1]:next));controls();
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
  for(const total of [50,51,100]){
    const boundary=await fixture({total});try{
      await wait(boundary.idle);
      assert.equal(boundary.doc.querySelectorAll('nav[aria-label="Configuration pages"]').length,total>50?2:0);
      if(total>50){boundary.button('Last configurations').click();
        await wait(()=>boundary.doc.querySelector('tbody th')?.textContent.startsWith('51.')&&boundary.idle());
        assert.equal(boundary.button('Last configurations').disabled,true);
      }
      assert.equal(boundary.posts.length,0);assert.deepEqual(boundary.errors,[]);
    }finally{boundary.dom.window.close();}
  }
});
test('one through nine configurations open without entering the chooser; ten get a bounded first page',async()=>{
  for(const total of [1,9,10]){
    const f=await fixture({total});try{
      if(total<=9)await wait(()=>f.button('Change configurations'));
      else await wait(f.idle);
      const urls=f.requests.map(r=>new URL(r.url,'https://localhost'));
      assert.equal(urls.filter(u=>u.searchParams.get('opening')==='1').length,1);
      assert.equal(urls.filter(u=>u.pathname.endsWith('/choices')).length,total>9?1:0);
      assert.equal(f.doc.body.textContent.includes('Choose configurations'),total>9);
      assert.equal(f.posts.length,0);
      if(total<=9){
        f.button('Change configurations').click();await wait(f.idle);
        assert.ok(f.doc.body.textContent.includes('Choose configurations'));
      }
    }finally{f.dom.window.close();}
  }
});

test('unavailable saved baselines are removed and a revised valid selection can be saved',async()=>{
  const f=await fixture({saved:JSON.stringify({configurations:[id(0),id(600)],baseline:id(0)}),missing:true});try{
    await wait(f.idle);assert.ok(f.doc.body.textContent.includes('Some saved configurations are unavailable'));
    assert.equal(f.doc.querySelector('section select').value,'');
    f.button('Open Visualiser').click();await wait(()=>f.button('Change configurations'));
    assert.deepEqual(f.posts,[{configurations:[id(600)],baseline:null}]);assert.deepEqual(f.errors,[]);
  }finally{f.dom.window.close();}
});
test('opening progress and errors stay below the button, with a single message after access is revoked',async()=>{
  const f=await fixture();try{
    await wait(f.idle);let release;
    f.prepare(()=>new Promise(resolve=>{release=resolve;}));f.button('Open Visualiser').click();
    await wait(()=>release);
    const feedback=()=>{
      const messages=f.doc.querySelectorAll('[role="status"]');assert.equal(messages.length,1);
      assert.equal(f.button('Open Visualiser').nextElementSibling,messages[0]);return messages[0];
    };
    assert.equal(feedback().textContent,'Sending your chart preparation request…');
    assert.equal(f.button('Open Visualiser').disabled,true);
    release({body:{error:'Preparation is temporarily unavailable. Please try again.'},status:503});
    await wait(f.idle);
    assert.equal(feedback().textContent,'Preparation is temporarily unavailable. Please try again.');
    assert.equal(feedback().nextElementSibling,f.button('Retry connection'));
    f.prepare(null);f.button('Open Visualiser').click();await wait(()=>f.button('Change configurations'));
    f.button('Change configurations').click();await wait(f.idle);f.deny();f.button('Open Visualiser').click();
    await wait(()=>!f.doc.querySelector('table'));
    assert.equal(f.doc.querySelectorAll('[role="status"]').length,1);
    assert.equal(f.doc.querySelector('[role="status"]').textContent,'Sign in again.');
    assert.equal(f.button('Open Visualiser'),undefined);assert.equal(f.button('Retry connection'),undefined);
    assert.ok(!f.doc.body.textContent.includes('Policy 1'));assert.deepEqual(f.errors,[]);
  }finally{f.dom.window.close();}
});

test('selection readiness counts only the chosen charts and baseline comparisons without enqueueing',async()=>{
  for(const pairsReady of [false,true]){
    const f=await fixture({chartsReady:true,pairsReady});
    try{
      await wait(()=>f.doc.body.textContent.includes(pairsReady?'Ready to open · 5 selected':'5/5 charts ready · 0/4 comparisons ready'));
      const select=f.doc.querySelector('section select');select.value='';
      select.dispatchEvent(new f.w.Event('change',{bubbles:true}));
      await wait(()=>f.doc.body.textContent.includes('Ready to open · 5 selected'));
      f.button('Open Visualiser').click();await wait(()=>f.button('Change configurations'));
      assert.equal(f.posts.length,0);
      assert.ok(!f.requests.some(r=>r.url==='/api/session'));
    }finally{f.dom.window.close();}
  }
});

test('Prepare saves the selection in place and Open follows it without a second request',async()=>{
  const f=await fixture();
  try{
    await wait(f.idle);
    const prepare=f.button('Prepare charts and comparisons');
    assert.ok(prepare.compareDocumentPosition(f.button('Open Visualiser')) & f.w.Node.DOCUMENT_POSITION_FOLLOWING);
    const unchecked=f.doc.querySelectorAll('tbody tr')[7].cells[2];
    const entries=()=>Array.from(unchecked.children,line=>line.textContent);
    const available=['Summaries ready','Charts ready','Comparison with baseline #1 (Policy 1)','Comparison with baseline #601 (Policy 601)'];
    const pageReads=()=>f.requests.filter(r=>r.url.includes('/choices?')).length;
    const reads=pageReads();
    assert.equal(f.doc.querySelectorAll('thead th')[2].textContent,'Available results');
    assert.deepEqual(entries(),available);
    assert.equal(f.doc.querySelectorAll('tbody tr')[8].cells[2].textContent,'');
    const base=f.doc.querySelector('section select');
    base.value=id(1);base.dispatchEvent(new f.w.Event('change',{bubbles:true}));
    await wait(()=>f.requests.some(r=>r.url.includes('baseline='+id(1))));
    assert.deepEqual(entries(),available);
    base.value='';base.dispatchEvent(new f.w.Event('change',{bubbles:true}));
    await wait(()=>f.button('Prepare charts'));
    assert.deepEqual(entries(),available);
    assert.equal(pageReads(),reads);assert.equal(f.posts.length,0);
    f.button('Prepare charts').click();
    await wait(()=>f.doc.body.textContent.includes('We’ll email you when your selection is ready.'));
    assert.ok(f.doc.querySelector('table'));assert.equal(f.posts.length,1);
    f.button('Open Visualiser').click();await wait(()=>f.button('Change configurations'));
    assert.equal(f.posts.length,1);assert.deepEqual(f.errors,[]);
  }finally{f.dom.window.close();}
});

test('a gateway error can be retried in place and progress changes as the worker finishes',async()=>{
  const f=await fixture({fastPolling:true});
  try{
    await wait(f.idle);
    f.prepare(()=>({raw:'<html>Gateway timeout</html>',status:504}));
    f.button('Prepare charts and comparisons').click();
    await wait(()=>f.button('Retry connection'));
    assert.match(f.doc.body.textContent,/Please retry/);
    assert.doesNotMatch(f.doc.body.textContent,/JSON.parse|Gateway timeout|request is saved/);
    f.prepare(null);f.button('Retry connection').click();
    await wait(()=>f.doc.body.textContent.includes('request is saved'));
    const cell=f.doc.querySelectorAll('tbody tr')[1].cells[2];
    const selected=f.doc.querySelector('section[aria-label="Selected configurations"] li:nth-child(2)');
    f.ready(true,false);
    await wait(()=>cell.textContent.includes('Charts ready')&&selected.textContent.includes('Comparison queued'));
    assert.ok(!cell.textContent.includes('Comparison'));
    f.ready(true,true);
    await wait(()=>cell.textContent.includes('Comparison with baseline #1 (Policy 1)'));
    const accumulated=f.doc.querySelectorAll('tbody tr')[7].cells[2];
    assert.deepEqual(Array.from(accumulated.children,e=>e.textContent).slice(2),[
      'Comparison with baseline #1 (Policy 1)','Comparison with baseline #2 (Policy 2)','Comparison with baseline #601 (Policy 601)']);
    await wait(()=>f.doc.body.textContent.includes('Ready to open · 5 selected'));
    assert.equal(f.posts.length,2);
    assert.ok(f.doc.querySelector('table'));
  }finally{f.dom.window.close();}
});
