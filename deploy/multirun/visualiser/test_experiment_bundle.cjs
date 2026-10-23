/* (C) Copyright 2026, by Ross Richardson
 * Exercise the actual compiled experiment interface as ready publications grow.
 * Fictional, matched aggregates verify preserved selections and lazy chart reads.
 * @author ross richardson
 */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {test}=require('node:test');
const build=process.env.SIMPATHS_VISUALISER_TEST_BUILD,deps=process.env.SIMPATHS_VISUALISER_DEPENDENCIES;
if(!build||!deps)throw Error('Set compiled build and test dependency locations');
const {JSDOM,VirtualConsole}=require(path.join(deps,'jsdom'));
const experiment='00000000-0000-4000-8000-000000000001';
const ids=[2,3,4].map(n=>'00000000-0000-4000-8000-'+String(n).padStart(12,'0'));
async function wait(check){const until=Date.now()+7000;while(!check()){
  if(Date.now()>until)throw Error('Compiled experiment did not become ready');await new Promise(r=>setTimeout(r,10));}}

test('an already open experiment adds controls without resetting chart or scenario choices',async()=>{
  const {fixture,row}=await import('./portable_fixture.mjs'),f=fixture(),errors=[],requests=[],downloads=[],polls=[];
  const console=new VirtualConsole();console.on('jsdomError',e=>errors.push(e.message));
  const dom=new JSDOM(fs.readFileSync(path.join(build,'index.html'),'utf8'),{
    url:'https://localhost/visualiser/experiment/'+experiment,runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:console});
  let stage=1,denied=false;
  try{
    const w=dom.window,doc=w.document;
    w.ResizeObserver=class{constructor(cb){this.cb=cb;}observe(){this.cb([{contentRect:{width:900,height:500}}]);}disconnect(){}};
    w.SVGElement.prototype.getBBox=()=>({width:40,height:12,x:0,y:0});w.SVGElement.prototype.getComputedTextLength=()=>40;
    w.TextEncoder=TextEncoder;w.TextDecoder=TextDecoder;w.URLSearchParams=URLSearchParams;w.Blob=Blob;
    w.URL.createObjectURL=blob=>{downloads.push(blob);return 'blob:fixture';};w.URL.revokeObjectURL=()=>{};
    w.HTMLAnchorElement.prototype.click=function(){};
    const timeout=w.setTimeout.bind(w);w.setTimeout=(callback,delay,...args)=>{
      if(delay===15000||delay===60000){polls.push(callback);return 0;}return timeout(callback,delay,...args);
    };
    const catalogue=()=>({...f.catalogue,publication:String(stage).repeat(64),comparison_available:stage>1,
      configurations:stage===1?[f.catalogue.configurations[1]]:f.catalogue.configurations.slice(0,stage)});
    w.fetch=async(url,options)=>{
      requests.push({url:String(url),options});const parsed=new URL(url,'https://localhost');
      if(denied)return new Response(JSON.stringify({error:'Sign in again to view online results.'}),{status:403});
      if(parsed.pathname.endsWith(experiment))return new Response(JSON.stringify({
        format:'simpaths.visualiser.experiment.v1',experiment,url:'/visualiser/experiment/'+experiment,
        publication:catalogue().publication,ready:stage,total:3,pending:stage<3,retryable:false,
        configurations:f.catalogue.configurations.map((c,i)=>({id:ids[i],configuration:c.id,name:c.name,
          state:stage===1&&i!==1||stage===2&&i===2?'active':'succeeded',
          charts:stage===1?i===1?'ready':'waiting':i<stage?'ready':'waiting',
          comparison:i===0?'not_applicable':stage>1&&i<stage?'ready':'waiting_baseline'}))}));
      if(parsed.pathname.endsWith('/catalogue'))return new Response(JSON.stringify(catalogue()));
      const p=parsed.searchParams,selected=p.getAll('configuration');
      assert.equal(p.get('publication'),catalogue().publication);
      return new Response(JSON.stringify({format:'simpaths.visualiser.view.v1',publication:catalogue().publication,
        selection:{variable:p.get('variable'),stratifier:p.get('stratifier'),kind:p.get('kind'),configurations:selected},
        series:selected.map(id=>{
          const c=f.catalogue.configurations.find(c=>c.id===id),r=row(c.role.toLowerCase(),id==='base'?40:45,p.get('variable'));
          if(stage===1||id==='base')for(const k of Object.keys(r).filter(k=>k.startsWith('paired_')))delete r[k];
          return {configuration:id,rows:[r]};
        })}));
    };
    new vm.Script(fs.readFileSync(path.join(build,'visualiser.js'),'utf8')).runInContext(dom.getInternalVMContext());
    const buttons=()=>Array.from(doc.querySelectorAll('button'));
    const button=text=>buttons().find(b=>b.textContent.trim()===text);
    const scenario=name=>buttons().find(b=>b.textContent.trim().endsWith(name));
    const ready=()=>!!button('↓ CSV')&&!doc.body.textContent.includes('Loading chart')&&
      !!doc.querySelector('svg path[fill="none"][d]');
    await wait(()=>doc.body.textContent.includes('1 of 3 configurations'));
    buttons().find(b=>/^Health\s*▼$/.test(b.textContent.trim())).click();
    await wait(()=>button('Mental Component Summary (MCS)'));button('Mental Component Summary (MCS)').click();
    await wait(()=>ready()&&scenario('Policy A')&&doc.querySelector('svg path[fill="none"][stroke-dasharray="2,2"]'));
    async function csv(){
      await new Promise(resolve=>w.requestAnimationFrame(()=>w.requestAnimationFrame(resolve)));
      await wait(ready);const before=downloads.length;button('↓ CSV').click();
      await wait(()=>downloads.length>before);return downloads.at(-1).text();
    }
    assert.ok((await csv()).includes('45'));const first=scenario('Policy A'),enabledStyle=first.style.cssText;first.click();
    await wait(()=>scenario('Policy A').style.cssText!==enabledStyle);
    const disabledStyle=scenario('Policy A').style.cssText;
    const publicationPoll=async()=>{await wait(()=>polls.length>0);await polls.shift()();};
    stage=2;await publicationPoll();await wait(()=>doc.body.textContent.includes('2 of 3 configurations')&&ready());
    assert.equal(scenario('Policy A').style.cssText,disabledStyle);
    assert.equal(doc.querySelector('a[href$="/archive"]').getAttribute('href'),'/api/visualiser-experiments/'+experiment+'/archive');
    assert.ok(button('Mental Component Summary (MCS)'));
    const before=await csv();assert.ok(before.includes('40'));assert.ok(!before.includes('Policy A'));
    stage=3;await publicationPoll();await wait(()=>doc.body.textContent.includes('3 of 3 configurations')&&scenario('Policy B <img src="unsafe">')&&ready());
    assert.equal(await csv(),before,'New alternatives must be offered without enabling them');
    assert.equal(doc.querySelector('img[src="unsafe"]'),null);
    scenario('Policy A').click();await wait(()=>ready());assert.ok((await csv()).includes('45'));
    assert.deepEqual(errors,[]);assert.ok(requests.every(r=>!r.options.method),'Live updates must never enqueue preparation');
    denied=true;await publicationPoll();
    await wait(()=>doc.body.textContent.includes('Sign in again to view online results.'));
    assert.equal(doc.querySelector('.vm-experiment-status'),null,'Sign-out must clear the private configuration progress list');
    assert.ok(!doc.body.textContent.includes('Charts now available:'));
    assert.ok(!doc.body.textContent.includes('Policy B <img src="unsafe">'));
    await wait(()=>!doc.querySelector('svg path[fill="none"][d]'));
  }catch(error){error.message+='; state='+JSON.stringify({errors,stage,requests:requests.map(r=>r.url),
    text:dom.window.document.body.textContent.slice(-2000)});throw error;}
  finally{dom.window.close();}
});
