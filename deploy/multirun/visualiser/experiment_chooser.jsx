/* (C) Copyright 2026, by Ross Richardson
 * Search and page large owned experiments before preparing a bounded chart view.
 * @author ross richardson
 */
import React,{useEffect,useLayoutEffect,useRef,useState} from 'react';
import {boundedJSON} from './section_source.mjs';
import {preparationLabel,validateExperiment} from './experiment_source.mjs';
import {validateSelection,fromQuery,restoreSelection,saveSelection,selectionQuery} from './experiment_choices.mjs';

const api='/api/visualiser-experiments/';
async function read(url,signal){
  const response=await fetch(url,{credentials:'same-origin',cache:'no-store',signal});
  return {value:(await boundedJSON(response,512*1024,signal)).value,scope:response.headers.get('x-visualiser-cache-scope')};
}
function SelectedProgress({status,base,requested}){
  const isBase=base?.configuration===status.configuration;
  const comparison=base&&!isBase&&preparationLabel(status,true,requested);
  return <><div>{preparationLabel(status,false,requested)}</div>
    {isBase&&<div>Selected baseline</div>}
    {comparison&&<div>{comparison} · baseline {base.ordinal}. {base.name}</div>}
  </>;
}
const AvailableResults=React.memo(function AvailableResults({row,baselineNames}){
  return <td>
    {row.ready&&row.summaries_ready&&<div>Summaries ready{row.historical_reader?' · calculator '+row.historical_reader.slice(0,12):''}</div>}
    {row.charts_ready&&<div>Charts ready</div>}
    {row.comparison_baselines?.map(ordinal=><div key={ordinal}>Comparison with baseline #{ordinal}
      {baselineNames?.[ordinal]&&' ('+baselineNames[ordinal]+')'}</div>)}
  </td>;
});
export function ExperimentChooser({experiment,current=null,force=false,onOpen}){
  const [data,setData]=useState(null),[chosen,setChosen]=useState([]),[baseline,setBaseline]=useState(null);
  const [search,setSearch]=useState(''),[parameter,setParameter]=useState(''),[value,setValue]=useState(''),[ready,setReady]=useState(false);
  const [message,setMessage]=useState('Loading configurations…'),[busy,setBusy]=useState(false);
  const generation=useRef(0),controller=useRef(null),scope=useRef(null),dead=useRef(false);
  const [reviewing,setReviewing]=useState(()=>force||!new URLSearchParams(location.search).has('chosen'));
  const pageFocus=useRef(null),retryAction=useRef(null);
  const [pageURL,setPageURL]=useState(null);
  function cancel(){++generation.current;controller.current?.abort();}
  async function load(offset=0,selection=chosen,filters={search,parameter,value,ready}){
    cancel();const serial=generation.current,abort=new AbortController();controller.current=abort;
    retryAction.current=null;setBusy(true);setMessage('Loading configurations…');
    const query=new URLSearchParams({offset:String(offset),search:filters.search,parameter:filters.parameter,
      value:filters.value,ready:filters.ready?'1':'0'});
    selection.forEach(row=>query.append('picked',row.configuration));
    try{
      const url=api+experiment+'/choices?'+query;
      const result=await read(url,abort.signal);
      if(serial!==generation.current)return;
      if(scope.current&&result.scope!==scope.current)throw Object.assign(Error('Your sign-in changed. Return to My Jobs.'),{status:403});
      scope.current=result.scope;
      const next=result.value;
      if(next.format!=='simpaths.visualiser.choices.v1'||next.experiment!==experiment||next.page_size!==50||
          !Array.isArray(next.configurations)||next.configurations.length>50||next.selection_limit!==9)
        throw Error('Configuration choices are unavailable.');
      setData(next);setPageURL(url);setChosen(next.picked);
      setBaseline(previous=>next.picked.some(row=>row.configuration===previous)?previous:null);setMessage('');
      return next;
    }catch(error){
      if(serial!==generation.current||error.name==='AbortError')return;
      if([401,403,404].includes(error.status)){dead.current=true;setData(null);setChosen([]);setBaseline(null);}
      setMessage(error.message);
    }finally{if(serial===generation.current)setBusy(false);}
  }
  async function initialise(){
    let selected=current;
    const linked=!force&&!current&&new URLSearchParams(location.search).has('chosen');
    if(!selected&&linked){
      try{selected=fromQuery(new URLSearchParams(location.search));}catch(error){setMessage(error.message);}
    }
    if(!selected&&!linked){try{selected=restoreSelection(experiment,localStorage);}catch{}}
    const first=await load(0,(selected?.configurations||[]).map(configuration=>({configuration})));
    if(!first||dead.current)return;
    if(selected){
      setBaseline(first.picked.some(row=>row.configuration===selected.baseline)?selected.baseline:null);
      if(first.picked.length!==selected.configurations.length){
        setMessage('Some saved configurations are unavailable. Review your selection.');
      }else if(linked&&first.picked.every(row=>row.selectable)){
        onOpen(validateSelection(selected));return;
      }
    }else{
      const ids=first.defaults.slice(0,first.total<=first.automatic_limit?9:5);
      const rows=ids.map(id=>first.configurations.find(row=>row.configuration===id));
      if(rows.every(Boolean))setChosen(rows);
      else if(!await load(0,ids.map(configuration=>({configuration}))))return;
      setBaseline(ids.includes(first.baseline)?first.baseline:null);
    }
    setReviewing(true);
  }
  useEffect(()=>{
    initialise();
    return()=>{dead.current=true;cancel();};
  },[]);
  useLayoutEffect(()=>{
    if(busy||!data||!pageFocus.current)return;
    const {position,label}=pageFocus.current;pageFocus.current=null;
    const bar=document.querySelector('nav[data-configuration-position="'+position+'"]');
    if(!bar||document.activeElement!==document.body&&!bar.contains(document.activeElement))return;
    const buttons=Array.from(bar.querySelectorAll('button')).filter(button=>!button.disabled);
    const opposite=/^(Next|Last) /.test(label)?'Previous configurations':'Next configurations';
    (buttons.find(button=>button.getAttribute('aria-label')===label)||
      buttons.find(button=>button.getAttribute('aria-label')===opposite)||buttons[0])?.focus({preventScroll:true});
  },[busy,data]);
  useEffect(()=>{
    let channel;
    try{channel=new BroadcastChannel('simpaths-visualiser-cache-v1');channel.onmessage=event=>{
      if(event.data?.type==='clear'){
        cancel();dead.current=true;setData(null);setChosen([]);setBaseline(null);setBusy(false);
        setMessage('Your sign-in changed. Return to My Jobs.');
      }
    };}catch{}
    return()=>channel?.close();
  },[]);
  const [readiness,setReadiness]=useState(null),[accepted,setAccepted]=useState(null);
  const [submitting,setSubmitting]=useState(false);
  useEffect(()=>{
    if(!pageURL)return;
    const abort=new AbortController();
    let timer;
    async function refreshPage(){
      if(dead.current)return;
      const serial=generation.current;
      try{
        const result=await read(pageURL,abort.signal);
        if(abort.signal.aborted||dead.current||serial!==generation.current)return;
        if(result.scope!==scope.current)throw Object.assign(Error('Your sign-in changed. Return to My Jobs.'),{status:403});
        if(result.value.format==='simpaths.visualiser.choices.v1'&&result.value.experiment===experiment){
          setData(result.value);
          const fresh=new Map([...result.value.picked,...result.value.configurations].map(row=>[row.configuration,row]));
          setChosen(previous=>previous.map(row=>fresh.get(row.configuration)||row));
        }
      }catch(error){
        if(abort.signal.aborted||serial!==generation.current)return;
        if([401,403,404].includes(error.status)){
          cancel();dead.current=true;setData(null);setChosen([]);setBaseline(null);setBusy(false);setMessage(error.message);
        }
      }finally{
        if(!abort.signal.aborted&&!dead.current)timer=setTimeout(refreshPage,document.hidden?60000:15000);
      }
    }
    timer=setTimeout(refreshPage,15000);
    return ()=>{abort.abort();clearTimeout(timer);};
  },[pageURL,experiment]);
  const selectionKey=data&&chosen.length&&chosen.every(row=>row.selectable)&&
    (!baseline||chosen.some(row=>row.configuration===baseline))?
    selectionQuery({configurations:chosen.map(row=>row.configuration),baseline}).toString():'';
  const saved=accepted?.key===selectionKey?accepted:null;
  useEffect(()=>{
    if(!selectionKey||dead.current)return;
    const abort=new AbortController();
    let timer;
    async function check(){
      try{
        const result=await read(api+experiment+'?'+selectionKey,abort.signal);
        if(abort.signal.aborted||dead.current)return;
        if(result.scope!==scope.current)throw Object.assign(Error('Your sign-in changed. Return to My Jobs.'),{status:403});
        const status=validateExperiment(result.value,experiment);
        const query=new URLSearchParams(selectionKey);
        const ids=query.getAll('chosen'),base=query.get('baseline');
        if(status.total!==ids.length||status.configurations.some(row=>!ids.includes(row.configuration)))return;
        const paired=status.configurations.filter(row=>row.configuration!==base&&row.comparison==='ready').length;
        const ready=status.ready===ids.length&&(!base||paired===ids.length-1);
        const label=ready?'Ready to open · '+ids.length+' selected':
          status.ready+'/'+ids.length+' charts ready'+(base&&ids.length>1?' · '+paired+'/'+(ids.length-1)+' comparisons ready':'');
        const requested=status.preparation_requests?.some(request=>request.baseline===(base||null)&&
          request.configurations.length===ids.length&&ids.every(id=>request.configurations.includes(id)));
        const incompatible=status.configurations.some(row=>row.charts==='incompatible'||row.comparison==='incompatible');
        setReadiness({key:selectionKey,label,ready,rows:status.configurations,requested,email:status.preparation_email_enabled,incompatible,
          failed:status.configurations.some(row=>[row.charts,row.comparison].some(state=>['failed','evicted'].includes(state)))});
        if(!ready&&(!incompatible||status.pending))timer=setTimeout(check,15000);
      }catch(error){
        if(abort.signal.aborted)return;
        setReadiness(previous=>({...(previous?.key===selectionKey?previous:{}),key:selectionKey,ready:false,
          label:'Could not refresh chart progress. Checking again shortly.',
          rows:previous?.key===selectionKey?previous.rows:[]}));
        timer=setTimeout(check,15000);
        if([401,403,404].includes(error.status)){
          clearTimeout(timer);cancel();dead.current=true;setData(null);setChosen([]);setBaseline(null);setBusy(false);setMessage(error.message);
        }
      }
    }
    timer=setTimeout(check,300);
    return ()=>{abort.abort();clearTimeout(timer);};
  },[experiment,selectionKey,saved]);
  function toggle(row){
    setChosen(previous=>previous.some(item=>item.configuration===row.configuration)?
      previous.filter(item=>item.configuration!==row.configuration):[...previous,row]);
    if(row.configuration===baseline)setBaseline(null);
  }
  const progress=readiness?.key===selectionKey?readiness:null;
  const outstanding=saved||progress?.requested;
  const canRetry=progress?.failed;
  const base=chosen.find(row=>row.configuration===baseline);
  async function submit(openView){
    cancel();const serial=generation.current,abort=new AbortController();controller.current=abort;
    let timedOut=false;
    const timeout=setTimeout(()=>{timedOut=true;abort.abort();},20000);
    retryAction.current=openView;setSubmitting(true);setBusy(true);
    setMessage('Sending your chart preparation request…');
    try{
      const selected=validateSelection({configurations:chosen.map(row=>row.configuration),baseline});
      let receipt=saved||(progress?.requested?{key:selectionKey,email:progress.email}:null);
      if(!progress?.ready&&!progress?.incompatible&&(!outstanding||canRetry)){
        const session=(await read('/api/session',abort.signal)).value;
        if(serial!==generation.current)return;
        if(!session.signed_in)throw Object.assign(Error('Sign in again from My Jobs.'),{status:403});
        // The server checks ownership, current grants and its fixed selection limit again.
        const response=await fetch(api+experiment+'/preparations',{method:'POST',credentials:'same-origin',cache:'no-store',signal:abort.signal,
          headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf},body:JSON.stringify(selected)});
        const {value}=await boundedJSON(response,256*1024,abort.signal);
        if(serial!==generation.current)return;
        if(typeof value?.request!=='string'||typeof value.email_enabled!=='boolean')
          throw Error('Could not confirm your preparation request. Please retry.');
        receipt={key:selectionKey,email:value.email_enabled};setAccepted(receipt);
      }
      try{saveSelection(experiment,selected,localStorage);}catch{}
      retryAction.current=null;setMessage('');
      if(openView){
        history.replaceState(null,'',location.pathname+'?'+selectionQuery(selected));
        onOpen(selected,receipt);
      }
    }catch(error){
      if(serial!==generation.current||abort.signal.aborted&&!timedOut)return;
      if([401,403,404].includes(error.status)){dead.current=true;setData(null);setChosen([]);setBaseline(null);}
      setMessage(timedOut?'The server has not confirmed your request yet. Check the preparation status or retry.':error.message);
    }finally{
      clearTimeout(timeout);
      if(serial===generation.current){setBusy(false);setSubmitting(false);}
    }
  }
  const option=data?.parameters.find(item=>item.key===parameter);
  const pageControls=position=>{
    if(!data||data.matching<=50)return null;
    const last=Math.floor((data.matching-1)/50)*50;
    return <nav aria-label="Configuration pages" data-configuration-position={position}>
      {[
        ['First',0,'M6 5v14 M18 5l-7 7 7 7',data.offset===0],
        ['Previous',Math.max(0,data.offset-50),'M15 5l-7 7 7 7',data.offset===0],
        ['Next',Math.min(last,data.offset+50),'M9 5l7 7-7 7',data.offset>=last],
        ['Last',last,'M18 5v14 M6 5l7 7-7 7',data.offset>=last]
      ].map(([action,offset,path,disabled])=><button key={action} type="button" className="vm-configuration-page-button"
        aria-label={action+' configurations'} title={action+' page'} disabled={busy||disabled}
        onClick={()=>{pageFocus.current={position,label:action+' configurations'};load(offset);}}>
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2"
          strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d={path}/></svg>
      </button>)}
    </nav>;
  };
  const feedback=<>
    {message&&<p role="status">{message}</p>}
    {!busy&&!dead.current&&message&&<button onClick={()=>retryAction.current!==null?submit(retryAction.current):data?load(data.offset):initialise()}>Retry connection</button>}
  </>;
  if(!reviewing)return <main className="vm-chooser"><a href="/">Return to SimPaths Online</a>
    {message?<p role="status">{message}</p>:<p role="status">Opening online results…</p>}
    {!busy&&!dead.current&&message&&<button onClick={initialise}>Retry connection</button>}
  </main>;
  return <main className="vm-chooser">
    <a href="/">Return to SimPaths Online</a><h1>Choose configurations</h1>
    {data&&<><h2>{data.name}</h2><p>{data.total} configurations. Choose up to 9, including an optional baseline.
      Cached charts open immediately; new comparisons may take time to prepare.</p>
      <section aria-label="Selected configurations"><h2>Selected: {chosen.length} / 9</h2>
        <label>Baseline <select value={baseline||''} disabled={busy} onChange={event=>setBaseline(event.target.value||null)}>
          <option value="">No baseline — levels only</option>
          {chosen.map(row=><option key={row.configuration} value={row.configuration}>{row.ordinal}. {row.name}</option>)}
        </select></label>
        <ul>{chosen.map(row=>{
          const state=progress?.rows?.find(item=>item.configuration===row.configuration);
          return <li key={row.configuration}>{row.ordinal}. {row.name}
            {!row.selectable&&' — unavailable'} <button disabled={busy} onClick={()=>toggle(row)} aria-label={'Remove '+row.name}>Remove</button>
            {state&&<SelectedProgress status={state} base={base} requested={!!outstanding}/>}
          </li>;
        })}</ul>
        <div><button className="vm-source-button" disabled={busy||!selectionKey||!!outstanding&&!canRetry||progress?.ready||progress?.incompatible}
          onClick={()=>submit(false)}>{baseline?'Prepare charts and comparisons':'Prepare charts'}</button></div>
        <button className="vm-source-button" disabled={busy||!selectionKey} onClick={()=>submit(true)}>Open Visualiser</button>
        {feedback}
        {progress?.incompatible&&<p role="status">A compatible calculator is needed for this selection. Contact the service operator.</p>}
        {!submitting&&outstanding&&!progress?.ready&&!canRetry&&!progress?.incompatible&&<p role="status">Your preparation request is saved. You can leave this page or wait in the Visualiser.
          {(saved?.email||progress?.email)&&' We’ll email you when your selection is ready.'}</p>}
        {!message&&progress&&<p aria-live="polite">{progress.label}</p>}
      </section>
      <form className="vm-choice-filters" onSubmit={event=>{event.preventDefault();load(0);}}>
        <label>Search name, number or setting <input maxLength={200} value={search} onChange={event=>setSearch(event.target.value)}/></label>
        <label>Parameter <select value={parameter} onChange={event=>{setParameter(event.target.value);setValue('');}}>
          <option value="">Any parameter</option>{data.parameters.map(item=><option key={item.key} value={item.key}>{item.key}</option>)}
        </select></label>
        {parameter&&<label>Value <select value={value} onChange={event=>setValue(event.target.value)}><option value="">Choose value</option>
          {option?.values.map(item=><option key={item} value={item}>{item}</option>)}</select>
          {option?.truncated&&<span>Use search for other values.</span>}</label>}
        <label><input type="checkbox" checked={ready} onChange={event=>setReady(event.target.checked)}/> Completed and available only</label>
        <button disabled={busy}>Apply filters</button>
      </form>
      <p>{data.matching?data.offset+1:0}–{Math.min(data.offset+50,data.matching)} of {data.matching} matching configurations</p>
      {pageControls('top')}
      <div className="vm-choice-table"><table><thead><tr><th>Select</th><th>Configuration</th><th>Available results</th><th>Varying settings</th></tr></thead>
        <tbody>{data.configurations.map(row=>{
          const checked=chosen.some(item=>item.configuration===row.configuration);
          return <tr key={row.configuration}><td><input type="checkbox" aria-label={'Select '+row.name} checked={checked}
            disabled={busy||!row.selectable&&!checked||!checked&&chosen.length>=9} onChange={()=>toggle(row)}/></td>
            <th scope="row">{row.ordinal}. {row.name}</th>
            <AvailableResults row={row} baselineNames={data.comparison_baseline_names}/>
            <td>{Object.entries(row.settings).map(([key,value])=><div key={key}>{key}: {value}</div>)}</td></tr>;
        })}</tbody></table></div>
      {pageControls('bottom')}
    </>}
    {!data&&feedback}
  </main>;
}
