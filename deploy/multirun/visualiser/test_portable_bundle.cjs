/* (C) Copyright 2026, by Ross Richardson
 * Reopen a real server-exported comparison in the compiled Visualiser.
 * Match level/paired CSVs, preserve names and avoid network/raw-file processing.
 * @author ross richardson
 */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {webcrypto,createHash}=require('node:crypto');
const {test}=require('node:test');
const build=process.env.SIMPATHS_VISUALISER_TEST_BUILD,deps=process.env.SIMPATHS_VISUALISER_DEPENDENCIES,folder=process.env.SIMPATHS_PORTABLE_TEST_FOLDER;
if(!build||!deps||!folder)throw Error('Set build, dependency and exported-folder test locations');
const {JSDOM,VirtualConsole}=require(path.join(deps,'jsdom'));
const manifest=JSON.parse(fs.readFileSync(path.join(folder,'manifest.json'))),metadata=manifest.catalogue;
const hash=v=>createHash('sha256').update(v).digest('hex');
async function wait(check){const until=Date.now()+7000;while(!check()){
  if(Date.now()>until)throw Error('Portable bundle did not become ready');await new Promise(r=>setTimeout(r,10));}}

test('the compiled app reopens a server ZIP locally with identical level and paired CSVs after access expires',async()=>{
  const errors=[],requests=[],reads=[],downloads=[],key='f'.repeat(64);
  const console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error.message));console.on('error',error=>errors.push(String(error)));
  const dom=new JSDOM(fs.readFileSync(path.join(build,'index.html'),'utf8'),{
    url:'https://localhost/visualiser/'+key,runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:console});
  try{
    const w=dom.window,doc=w.document;
    w.ResizeObserver=class{constructor(cb){this.cb=cb;}observe(){this.cb([{contentRect:{width:900,height:500}}]);}disconnect(){}};
    w.SVGElement.prototype.getBBox=()=>({width:40,height:12,x:0,y:0});w.SVGElement.prototype.getComputedTextLength=()=>40;
    w.TextEncoder=TextEncoder;w.TextDecoder=TextDecoder;w.URLSearchParams=URLSearchParams;w.Blob=Blob;
    Object.defineProperty(w,'crypto',{value:webcrypto});
    w.URL.createObjectURL=blob=>{downloads.push(blob);return 'blob:fixture';};w.URL.revokeObjectURL=()=>{};
    w.HTMLAnchorElement.prototype.click=function(){};
    function directory(root){return {getDirectoryHandle:async name=>directory(path.join(root,name)),
      getFileHandle:async name=>({getFile:async()=>{
        const file=path.join(root,name),size=fs.statSync(file).size;
        return {size,arrayBuffer:async()=>{reads.push(path.relative(folder,file));return Uint8Array.from(fs.readFileSync(file)).buffer;}};
      }})};}
    w.showDirectoryPicker=async()=>directory(folder);
    let denied=false;
    w.fetch=async url=>{
      requests.push(String(url));if(denied)throw Error('The saved-data reader must not call the server');
      assert.ok(url.startsWith('/api/visualiser/'+key+'/'));
      if(url.endsWith('/catalogue'))return new Response(JSON.stringify(metadata));
      const p=new URL(url,'https://localhost').searchParams,ids=p.getAll('configuration');
      return new Response(JSON.stringify({format:'simpaths.visualiser.view.v1',publication:metadata.publication,
        selection:{variable:p.get('variable'),stratifier:p.get('stratifier'),kind:p.get('kind'),configurations:ids},
        series:ids.map(id=>{
          const part=manifest.parts.find(part=>part.configurations.includes(id));
          const index=JSON.parse(fs.readFileSync(path.join(folder,part.directory,'index.json')));
          const key=hash(JSON.stringify([id,p.get('variable'),p.get('stratifier'),p.get('kind')]));
          return {configuration:id,rows:index.sections[key]?JSON.parse(fs.readFileSync(path.join(folder,part.directory,key+'.json'))):[]};
        })}));
    };
    new vm.Script(fs.readFileSync(path.join(build,'visualiser.js'),'utf8')).runInContext(dom.getInternalVMContext());
    const button=text=>Array.from(doc.querySelectorAll('button')).find(b=>b.textContent.trim()===text);
    const ready=()=>!!button('↓ CSV')&&!doc.body.textContent.includes('Loading chart');
    await wait(()=>doc.body.textContent.includes('Policy 2'));
    assert.equal(doc.querySelector('a[href$="/archive"]').textContent,'Download comparison data');
    Array.from(doc.querySelectorAll('button')).find(b=>/^Health\s*▼$/.test(b.textContent.trim())).click();
    await wait(()=>button('Mental Component Summary (MCS)'));button('Mental Component Summary (MCS)').click();
    await wait(()=>ready()&&doc.querySelectorAll('svg path[fill="none"][stroke-dasharray]:not([stroke-dasharray="none"])').length===2);
    async function csv(){const prior=downloads.length;button('↓ CSV').click();await wait(()=>downloads.length>prior);return downloads.at(-1).text();}
    const levels=await csv();
    button('Δ Baseline → Scenario').click();await wait(()=>ready()&&doc.body.textContent.includes('uncertainty intervals use paired run differences'));
    const paired=await csv();assert.ok(paired.includes('Scenario minus Baseline'));
    const before=requests.length;denied=true;button('Open saved comparison').click();
    await wait(()=>doc.querySelector('[aria-label="Displayed data source"]')?.textContent.includes('saved comparison')&&ready()&&
      doc.querySelectorAll('svg path[fill="none"][stroke-dasharray]:not([stroke-dasharray="none"])').length===2);
    assert.equal(await csv(),levels);
    button('Δ Baseline → Scenario').click();await wait(()=>ready()&&doc.body.textContent.includes('uncertainty intervals use paired run differences'));
    assert.equal(await csv(),paired);
    assert.equal(requests.length,before);
    assert.equal(doc.querySelector('a[href$="/archive"]'),null);
    assert.ok(doc.body.textContent.includes('Policy 2 <plain text>'));
    assert.equal(doc.querySelector('plain'),null);
    const unused=hash(JSON.stringify(['alt2','Physical Component Summary (PCS)','Overall','levels']))+'.json';
    assert.ok(!reads.some(p=>p.endsWith(unused)),'Unselected chart sections must stay unread');
    assert.deepEqual(errors,[]);
  }catch(error){error.message+='; state='+JSON.stringify({errors,requests,reads,
    message:dom.window.document.querySelector('.vm-source-message')?.textContent,
    buttons:Array.from(dom.window.document.querySelectorAll('button')).map(b=>b.textContent.trim()).slice(-25)});throw error;}
  finally{dom.window.close();}
});
