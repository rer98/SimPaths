/* (C) Copyright 2026, by Ross Richardson
 * Boot the actual hosted bundle with fictional aggregates and no network server.
 * Catch packaging/runtime failures before the native PostgreSQL/Chromium proof.
 * @author ross richardson
 */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {test}=require('node:test');
const build=process.env.SIMPATHS_VISUALISER_TEST_BUILD,dependencies=process.env.SIMPATHS_VISUALISER_DEPENDENCIES;
if(!build||!dependencies)throw Error('Set SIMPATHS_VISUALISER_TEST_BUILD and SIMPATHS_VISUALISER_DEPENDENCIES');
const {JSDOM,VirtualConsole}=require(path.join(dependencies,'jsdom'));
const row=(scenario,value)=>({year:2019,scenario,module:'Health',variable:'Mental Component Summary (MCS)',
  variable_value:'Continuous Mean',stratifier:'Overall',stratifier_value:'Overall',metric_type:'mean',
  n_runs:3,total_sample:300,min_sample:100,mean_sample:100,mean_value:value,sd_value:1,lower_ci:value-1,upper_ci:value+1,
  paired_mean_delta:scenario==='baseline'?null:value-40,paired_lower_ci:scenario==='baseline'?null:value-41,
  paired_upper_ci:scenario==='baseline'?null:value-39,paired_n_runs:scenario==='baseline'?0:3});
async function waitFor(check){
  const until=Date.now()+5000;
  while(!check()){
    if(Date.now()>until)throw Error('Fictional bundle did not render in time');
    await new Promise(resolve=>setTimeout(resolve,10));
  }
}
test('the compiled connected application boots, loads every alternative and draws paired charts',async()=>{
  const errors=[],requests=[],key='a'.repeat(64);
  const console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error.message));
  console.on('error',error=>errors.push(String(error)));
  const dom=new JSDOM(fs.readFileSync(path.join(build,'index.html'),'utf8'),{
    url:'http://localhost/visualiser/'+key,runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:console,
  });
  try{
    const w=dom.window,doc=w.document;
    w.ResizeObserver=class {constructor(cb){this.cb=cb;}observe(){this.cb([{contentRect:{width:900,height:500}}]);}disconnect(){}};
    w.SVGElement.prototype.getBBox=()=>({width:40,height:12,x:0,y:0});
    w.SVGElement.prototype.getComputedTextLength=()=>40;
    w.TextEncoder=TextEncoder;w.TextDecoder=TextDecoder;w.URLSearchParams=URLSearchParams;
    w.addEventListener('error',event=>errors.push(event.message));
    const configs=[{id:'base',role:'Baseline',name:'Reference'},{id:'a',role:'Scenario',name:'Policy A'},
      {id:'b',role:'Scenario',name:'Policy B <img src="canary">'}];
    let denied=false;
    const metadata={format:'simpaths.visualiser.catalogue.v1',publication:'b'.repeat(64),
      configurations:configs.map((c,i)=>({...c,key:i?'scenario_'+i:'baseline'})),seeds:['606','607','608'],
      comparison_available:true,notice:'Fictional paired results',limits:{response_bytes:8*1024**2,response_rows:20000},
      variables:[{name:row('baseline',40).variable,module:'Health',years:[2019],
        views:[{stratifier:'Overall',kind:'levels',bytes:2000,rows:3}]}]};
    w.fetch=async function(url){
      if(this?.document!==doc)throw new TypeError("Failed to execute 'fetch' on 'Window': Illegal invocation");
      requests.push(url);assert.ok(url.startsWith('/api/visualiser/'+key+'/'));
      if(denied)return new Response(JSON.stringify({error:'Sign in with an approved email'}),{status:403});
      if(url.endsWith('/catalogue'))return new Response(JSON.stringify(metadata));
      const params=new URL(url,'http://localhost').searchParams;
      const ids=params.getAll('configuration');
      return new Response(JSON.stringify({format:'simpaths.visualiser.view.v1',publication:metadata.publication,
        selection:{variable:params.get('variable'),stratifier:params.get('stratifier'),kind:params.get('kind'),configurations:ids},
        series:ids.map(id=>{const i=configs.findIndex(c=>c.id===id);return{configuration:id,rows:[row(i?'scenario':'baseline',40+5*i)]};})}));
    };
    new vm.Script(fs.readFileSync(path.join(build,'visualiser.js'),'utf8')).runInContext(dom.getInternalVMContext());
    await waitFor(()=>doc.body.textContent.includes('Policy B'));
    const button=text=>Array.from(doc.querySelectorAll('button')).find(b=>b.textContent.trim()===text);
    Array.from(doc.querySelectorAll('button')).find(b=>/^Health\s*▼$/.test(b.textContent.trim())).click();
    await waitFor(()=>button('Mental Component Summary (MCS)'));
    button('Mental Component Summary (MCS)').click();
    await waitFor(()=>doc.querySelectorAll('svg path[fill="none"][stroke-dasharray]:not([stroke-dasharray="none"])').length===2);
    assert.ok(doc.body.textContent.includes('Policy A'));
    assert.ok(doc.querySelector('a[href="/visualiser-assets/Interpreting-results.html"]'));
    assert.equal(doc.querySelector('img[src="canary"]'),null);
    button('Δ Baseline → Scenario').click();
    await waitFor(()=>doc.body.textContent.includes('uncertainty intervals use paired run differences'));
    assert.equal(requests.filter(url=>url.includes('/view?')).length,1);
    assert.ok(requests.every(url=>!url.endsWith('/data')));
    denied=true;
    button('View Online Results').click();
    await waitFor(()=>doc.querySelector('.vm-source-message')?.textContent.includes('Sign in with an approved email'));
    assert.equal(doc.querySelectorAll('svg').length,0);
    assert.equal(requests.at(-1),'/api/visualiser/'+key+'/catalogue');
    assert.deepEqual(errors,[]);
  }catch(error){
    error.message+='; fictional fixture state: '+JSON.stringify({errors,
      sourceMessages:Array.from(dom.window.document.querySelectorAll('.vm-source-message')).map(p=>p.textContent),
      paths:dom.window.document.querySelectorAll('svg path[fill="none"][stroke-dasharray]:not([stroke-dasharray="none"])').length,
      buttons:Array.from(dom.window.document.querySelectorAll('button')).map(b=>b.textContent.trim())});
    throw error;
  }finally{dom.window.close();}
});
