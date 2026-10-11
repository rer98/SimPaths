/* (C) Copyright 2026, by Ross Richardson
 * Exercise the compiled application with indexed, retained 52-year aggregates.
 * No whole comparison, raw CSVs, simulations or network server enter this check.
 * @author ross richardson
 */
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const build=process.env.SIMPATHS_VISUALISER_TEST_BUILD,deps=process.env.SIMPATHS_VISUALISER_DEPENDENCIES;
const evidence=process.env.SIMPATHS_VISUALISER_SECTIONS,golden=process.env.SIMPATHS_VISUALISER_GOLDEN;
if(!build||!deps||!evidence||!golden)throw Error('Set build, dependencies, sections and previous checked export directories');
const {JSDOM,VirtualConsole}=require(path.join(deps,'jsdom'));
const metadata=JSON.parse(fs.readFileSync(path.join(evidence,'catalogue.json')));
const errors=[],requests=[],downloads=[],steps=[];let denied=false;
const report={passed:false,chromium:false,no_models:true,no_raw_copies:true,full_comparison_downloaded:false};
const virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',e=>errors.push(e.message));virtualConsole.on('error',e=>errors.push(String(e)));
const dom=new JSDOM(fs.readFileSync(path.join(build,'index.html'),'utf8'),{
 url:'http://localhost/visualiser/'+'a'.repeat(64),runScripts:'outside-only',pretendToBeVisual:true,virtualConsole});
async function wait(check){const end=Date.now()+30000;while(!check()){
 if(Date.now()>end)throw Error('Selected research chart did not render');await new Promise(r=>setTimeout(r,10));}}
async function main(){
 const w=dom.window,doc=w.document,start=Date.now();
 try{
  w.ResizeObserver=class{constructor(cb){this.cb=cb;}observe(){this.cb([{contentRect:{width:900,height:500}}]);}disconnect(){}};
  w.SVGElement.prototype.getBBox=()=>({width:40,height:12,x:0,y:0});w.SVGElement.prototype.getComputedTextLength=()=>40;
  w.TextEncoder=TextEncoder;w.TextDecoder=TextDecoder;w.URLSearchParams=URLSearchParams;
  w.addEventListener('error',e=>errors.push(e.message));
  w.fetch=async url=>{
   const began=Date.now();let value;
   if(denied)value={error:'Sign in with an approved email'};
   else if(url.endsWith('/catalogue'))value=metadata;
   else{
    assert.ok(url.includes('/view?'));const p=new URL(url,'http://localhost').searchParams;
    const selection={variable:p.get('variable'),stratifier:p.get('stratifier'),kind:p.get('kind'),configurations:p.getAll('configuration')};
    const series=selection.configurations.map(configuration=>{
     const identity=[configuration,selection.variable,selection.stratifier,selection.kind];
     const key=crypto.createHash('sha256').update(JSON.stringify(identity)).digest('hex');
     const file=path.join(evidence,'views',key+'.json');
     return {configuration,rows:fs.existsSync(file)?JSON.parse(fs.readFileSync(file,'utf8')):[]};
    });
    value={format:'simpaths.visualiser.view.v1',publication:metadata.publication,selection,series};
   }
   const body=JSON.stringify(value);assert.ok(Buffer.byteLength(body)<=8*1024**2);
   requests.push({url,bytes:Buffer.byteLength(body),fixture_read_ms:Date.now()-began});
   return new Response(body,{status:denied?403:200});
  };
  w.URL.createObjectURL=blob=>{downloads.push(blob);return 'blob:fixture';};w.URL.revokeObjectURL=()=>{};
  w.HTMLAnchorElement.prototype.click=function(){};
  new vm.Script(fs.readFileSync(path.join(build,'visualiser.js'),'utf8')).runInContext(dom.getInternalVMContext());
  const button=text=>Array.from(doc.querySelectorAll('button')).find(b=>b.textContent.trim()===text);
  const ready=()=>!doc.body.textContent.includes('Loading chart data…')&&doc.querySelector('svg path[fill="none"][stroke-dasharray]');
  await wait(ready);
  steps.push({chart:'Education — Overall',milliseconds:Date.now()-start,bytes:requests.reduce((n,r)=>n+r.bytes,0)});
  Array.from(doc.querySelectorAll('button')).find(b=>/^Health\s*▼$/.test(b.textContent.trim())).click();
  await wait(()=>button('Mental Component Summary (MCS)'));let began=Date.now();
  button('Mental Component Summary (MCS)').click();
  await wait(()=>requests.some(r=>r.url.includes('variable=Mental'))&&ready()&&button('↓ CSV'));
  steps.push({chart:'MCS — Overall',milliseconds:Date.now()-began});
  const read=blob=>new Promise((resolve,reject)=>{const reader=new w.FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsText(blob);});
  async function csv(){const count=downloads.length;button('↓ CSV').click();await wait(()=>downloads.length>count);return read(downloads.at(-1));}
  const levels=await csv();assert.equal(levels,fs.readFileSync(path.join(golden,'full-bundle-clean-levels.csv'),'utf8'));
  button('Δ Baseline → Scenario').click();await wait(()=>doc.body.textContent.includes('uncertainty intervals use paired run differences'));
  const impacts=await csv();assert.equal(impacts,fs.readFileSync(path.join(golden,'full-bundle-clean-impacts.csv'),'utf8'));
  button('〜 Line').click();await wait(ready);
  const count=requests.filter(r=>r.url.includes('/view?')).length;
  if(!button('Highest Level of Education'))Array.from(doc.querySelectorAll('button')).find(b=>b.textContent.trim().startsWith('Demographics')).click();
  await wait(()=>button('Highest Level of Education'));
  let before=requests.length;button('Highest Level of Education').click();await wait(()=>requests.length>before&&ready()&&button('↓ CSV'));
  before=requests.length;button('Mental Component Summary (MCS)').click();await wait(()=>requests.length>before&&ready()&&button('↓ CSV'));
  assert.equal(requests.filter(r=>r.url.includes('/view?')).length,count,'Previously viewed data was downloaded again');
  const select=doc.querySelector('select');select.value='Gender';select.dispatchEvent(new w.Event('change',{bubbles:true}));
  await wait(()=>requests.some(r=>r.url.includes('stratifier=Gender'))&&ready());
  assert.ok(doc.body.textContent.includes('2070')||doc.querySelectorAll('svg').length>1);
  select.value='Overall';select.dispatchEvent(new w.Event('change',{bubbles:true}));await wait(ready);
  // Reopening simulates a fresh page data source: catalogue and current view,
  // with no persistent private browser cache and no entire comparison request.
  const refreshStart=requests.length;button('View Online Results').click();await wait(()=>requests.length>refreshStart+1&&ready());
  assert.ok(requests.slice(refreshStart).every(r=>r.url.includes('/catalogue')||r.url.includes('/view?')));
  denied=true;button('View Online Results').click();await wait(()=>doc.querySelector('.vm-source-message')?.textContent.includes('approved email'));
  assert.equal(doc.querySelectorAll('svg').length,0);assert.deepEqual(errors,[]);
  assert.ok(requests.every(r=>!r.url.endsWith('/data')));
  Object.assign(report,{passed:true,level_export_identical:true,paired_export_identical:true,years:52,
   total_delivered_bytes:requests.reduce((n,r)=>n+r.bytes,0),steps,requests,
   process_peak_rss_bytes:process.resourceUsage().maxRSS*1024,elapsed_seconds:(Date.now()-start)/1000});
 }catch(error){Object.assign(report,{error:error.message,errors,requests,
  buttons:Array.from(doc.querySelectorAll('button')).map(b=>b.textContent.trim())});throw error;}
 finally{dom.window.close();fs.writeFileSync(path.join(evidence,'compiled-browser-report.json'),JSON.stringify(report,null,2)+'\n');}
 console.log(JSON.stringify(report,null,2));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
