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
    let denied=false,connectionDown=false;
    w.fetch=async url=>{
      if(connectionDown)throw new TypeError('Connection unavailable');
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
    assert.equal(button('Clear saved data for these charts'),undefined);
    assert.equal(button('View Online Results'),undefined);
    assert.ok(button('Reload charts from server'));
    assert.equal(doc.querySelector('details.vm-troubleshooting').open,false);
    Array.from(doc.querySelectorAll('button')).find(b=>/^Health\s*▼$/.test(b.textContent.trim())).click();
    await wait(()=>button('Mental Component Summary (MCS)'));button('Mental Component Summary (MCS)').click();
    await wait(()=>ready()&&doc.querySelectorAll('svg path[fill="none"][stroke-dasharray]:not([stroke-dasharray="none"])').length===2);
    async function csv(){const prior=downloads.length;button('↓ CSV').click();await wait(()=>downloads.length>prior);return downloads.at(-1).text();}
    const levels=await csv();
    button('Δ Baseline → Scenario').click();await wait(()=>ready()&&doc.body.textContent.includes('uncertainty intervals use paired run differences'));
    const paired=await csv();assert.ok(paired.includes('Scenario minus Baseline'));
    async function cancelledPickersKeepView(){
      const description=doc.querySelector('[aria-label="Displayed data source"]').textContent;
      const archive=doc.querySelector('a[href$="/archive"]')?.href;
      const reload=!!button('Reload charts from server');
      for(const label of ['Open saved comparison','Visualise Locally Saved Data']){
        let reject;
        w.showDirectoryPicker=()=>new Promise((_resolve,fail)=>{reject=fail;});
        button(label).click();await wait(()=>button(label).disabled);
        assert.equal(doc.querySelector('[aria-label="Displayed data source"]').textContent,description);
        assert.ok(ready(),'The current chart stays available while choosing a folder');
        reject(new w.DOMException('User cancelled','AbortError'));
        await wait(()=>!button(label).disabled);
        assert.equal(doc.querySelector('[aria-label="Displayed data source"]').textContent,description);
        assert.equal(doc.querySelector('a[href$="/archive"]')?.href,archive);
        assert.equal(!!button('Reload charts from server'),reload);
        assert.ok(!doc.body.textContent.includes('selection cancelled'));
        assert.equal(await csv(),paired,'Cancellation preserves the selected chart and paired values');
      }
      w.showDirectoryPicker=async()=>directory(folder);
    }
    await cancelledPickersKeepView();
    const viewRequests=requests.filter(url=>url.includes('/view?')).length;
    connectionDown=true;button('Reload charts from server').click();
    await wait(()=>button('Retry connection'));
    assert.ok(doc.body.textContent.includes('Connection unavailable'));
    connectionDown=false;button('Retry connection').click();
    await wait(()=>!button('Retry connection')&&!doc.querySelector('.vm-source-message')&&ready());
    assert.equal(await csv(),paired,'Retry retains the selected variable and paired chart');
    assert.ok(requests.filter(url=>url.includes('/view?')).length>viewRequests,'Reload fetches chart rows from the server');
    const before=requests.length;denied=true;button('Open saved comparison').click();
    await wait(()=>doc.querySelector('[aria-label="Displayed data source"]')?.textContent.includes('saved comparison')&&ready()&&
      doc.querySelectorAll('svg path[fill="none"][stroke-dasharray]:not([stroke-dasharray="none"])').length===2);
    button('〜 Line').click();await wait(()=>ready()&&!doc.body.textContent.includes('uncertainty intervals use paired run differences'));
    assert.equal(await csv(),levels);
    button('Δ Baseline → Scenario').click();await wait(()=>ready()&&doc.body.textContent.includes('uncertainty intervals use paired run differences'));
    assert.equal(await csv(),paired);
    await cancelledPickersKeepView();
    assert.equal(requests.length,before);
    assert.equal(doc.querySelector('a[href$="/archive"]'),null);
    assert.ok(button('View Online Results'));
    assert.equal(doc.querySelector('details.vm-troubleshooting'),null);
    assert.ok(doc.body.textContent.includes('Policy 2 <plain text>'));
    assert.equal(doc.querySelector('plain'),null);
    const unused=hash(JSON.stringify(['alt2','Physical Component Summary (PCS)','Overall','levels']))+'.json';
    assert.ok(!reads.some(p=>p.endsWith(unused)),'Unselected chart sections must stay unread');
    assert.deepEqual(errors,[]);
    denied=false;button('View Online Results').click();
    await wait(()=>doc.querySelector('a[href$="/archive"]')&&ready());
    assert.equal(button('View Online Results'),undefined);
  }catch(error){error.message+='; state='+JSON.stringify({errors,requests,reads,
    message:dom.window.document.querySelector('.vm-source-message')?.textContent,
    buttons:Array.from(dom.window.document.querySelectorAll('button')).map(b=>b.textContent.trim()).slice(-25)});throw error;}
  finally{dom.window.close();}
});
