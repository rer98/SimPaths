/* (C) Copyright 2026, by Ross Richardson
 * Fetch selected chart sections with bounded memory/disk caching and prefetch.
 * Cache reuse always requires fresh server authorisation and session scoping.
 * @author ross richardson
 */
import {SectionCache,CACHE_CHANNEL} from './section_cache.mjs';
export const CATALOGUE_FORMAT='simpaths.visualiser.catalogue.v1';
export const VIEW_FORMAT='simpaths.visualiser.view.v1';
const CATALOGUE_BYTES=512*1024,VIEW_BYTES=8*1024*1024,VIEW_ROWS=20000;
const CACHE_BYTES=32*1024*1024,CACHE_ENTRIES=64;
const PREFETCH_BYTES=2*1024*1024,PREFETCH_ROWS=5000,PREFETCH_TOTAL=8*1024*1024;
const kinds=new Set(['levels','wage_bin','income_bin','pyramid_bin']);
const labels=['scenario','module','variable','variable_value','stratifier','stratifier_value','metric_type'];
const numbers=['n_runs','total_sample','min_sample','mean_sample','mean_value','sd_value','lower_ci','upper_ci'];
const paired=['paired_mean_delta','paired_lower_ci','paired_upper_ci','paired_n_runs'];
const fields=['year',...labels,...numbers];
const text=v=>typeof v==='string'&&v.length>0&&v.length<=160&&!/[\u0000-\u001f]/.test(v);
const hash=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const keys=(value,expected)=>value&&typeof value==='object'&&!Array.isArray(value)&&
  Object.keys(value).sort().join('|')===[...expected].sort().join('|');
function aborted(signal){if(signal?.aborted)throw new DOMException('Chart request cancelled.','AbortError');}
export function validateRows(rows,selection,configuration){
  if(!Array.isArray(rows)||rows.length>VIEW_ROWS)throw Error('Invalid cached chart rows.');
  for(const row of rows){
    if(!(keys(row,fields)||keys(row,[...fields,...paired]))||!Number.isInteger(row.year)||
        labels.some(field=>typeof row[field]!=='string'||row[field].length>200||/[\u0000-\u001f]/.test(row[field]))||
        [...numbers,...paired].some(field=>row[field]!=null&&(typeof row[field]!=='number'||!Number.isFinite(row[field])))||
        row.scenario!==configuration.key||row.variable!==selection.variable||row.stratifier!==selection.stratifier||
        (selection.kind==='levels'?!['mean','share'].includes(row.metric_type):row.metric_type!==selection.kind))
      throw Error('Online chart sections do not match the selected chart.');
  }
  return rows;
}

export async function boundedJSON(response,maximum,signal,onBytes=()=>{}){
  aborted(signal);
  const length=Number(response.headers?.get('content-length'));
  if(Number.isFinite(length)&&length>maximum)throw Error('The chart response exceeds its size limit.');
  let content;
  if(response.body?.getReader){
    const reader=response.body.getReader(),chunks=[];let bytes=0;
    try{
      while(true){
        const {done,value}=await reader.read();aborted(signal);
        if(done)break;
        bytes+=value.byteLength;
        onBytes(value.byteLength);
        if(bytes>maximum)throw Error('The chart response exceeds its size limit.');
        chunks.push(value);
      }
      const joined=new Uint8Array(bytes);let offset=0;
      for(const piece of chunks){joined.set(piece,offset);offset+=piece.length;}
      content=new TextDecoder('utf-8',{fatal:true}).decode(joined);
    }finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
  }else{
    content=await response.text();
    const bytes=new TextEncoder().encode(content).length;onBytes(bytes);
    if(bytes>maximum)throw Error('The chart response exceeds its size limit.');
  }
  aborted(signal);
  const value=JSON.parse(content);
  if(!response.ok){const error=Error(typeof value?.error==='string'?value.error:'Online results are unavailable.');
    error.status=response.status;throw error;}
  return {value,bytes:new TextEncoder().encode(content).length};
}

export function validateCatalogue(value){
  if(!keys(value,['format','publication','configurations','seeds','notice','comparison_available','limits','variables'])||
      value.format!==CATALOGUE_FORMAT||!hash(value.publication)||!Array.isArray(value.configurations)||
      value.configurations.length<1||value.configurations.length>100||typeof value.notice!=='string'||value.notice.length>500||
      typeof value.comparison_available!=='boolean'||!Array.isArray(value.seeds)||value.seeds.length>1000||
      value.seeds.some(s=>typeof s!=='string'||!/^[0-9]{1,20}$/.test(s))||
      !keys(value.limits,['response_bytes','response_rows'])||value.limits.response_bytes!==VIEW_BYTES||
      value.limits.response_rows!==VIEW_ROWS||!Array.isArray(value.variables)||value.variables.length>256)
    throw Error('Online chart metadata is unavailable.');
  const ids=new Set(),roles=new Set();
  value.configurations.forEach(c=>{
    if(!keys(c,['id','name','role','key'])||!text(c.id)||typeof c.name!=='string'||!c.name.length||c.name.length>200||!text(c.key)||ids.has(c.id)||roles.has(c.key)||
        !['Baseline','Scenario'].includes(c.role)||
        (c.role==='Baseline'?c.key!=='baseline':!/^scenario(?:_[1-9][0-9]*)?$/.test(c.key)))
      throw Error('Online configuration metadata is unavailable.');
    ids.add(c.id);roles.add(c.key);
  });
  const variables=new Set();
  value.variables.forEach(v=>{
    if(!keys(v,['name','module','years','views'])||!text(v.name)||!text(v.module)||variables.has(v.name)||
        !Array.isArray(v.years)||v.years.length>1000||v.years.some(y=>!Number.isInteger(y))||
        !Array.isArray(v.views)||v.views.length>100)
      throw Error('Online variable metadata is unavailable.');
    const views=new Set();
    v.views.forEach(view=>{
      const key=JSON.stringify([view.stratifier,view.kind]);
      if(!keys(view,['stratifier','kind','bytes','rows'])||!text(view.stratifier)||!kinds.has(view.kind)||views.has(key)||
          !Number.isSafeInteger(view.bytes)||view.bytes<0||!Number.isSafeInteger(view.rows)||view.rows<0)
        throw Error('Online chart metadata is unavailable.');
      views.add(key);
    });variables.add(v.name);
  });
  return value;
}

export class SectionSource{
  constructor(key,{fetcher=globalThis.fetch.bind(globalThis),cacheBytes=CACHE_BYTES,cacheEntries=CACHE_ENTRIES,
      persistentCache,onInvalidated=()=>{},prefetch=true,environment=globalThis}={}){
    if(!/^[a-f0-9]{64}$/.test(key)||!Number.isSafeInteger(cacheBytes)||cacheBytes<0||cacheBytes>CACHE_BYTES||
        !Number.isSafeInteger(cacheEntries)||cacheEntries<0||cacheEntries>CACHE_ENTRIES)throw Error('Invalid chart source.');
    this.comparison=key;this.url='/api/visualiser/'+key;this.fetcher=fetcher;this.cache=new Map();this.cacheBytes=cacheBytes;
    this.cacheEntries=cacheEntries;this.used=0;this.catalogue=null;this.generation=0;this.closed=false;
    this.scope=null;this.diskActive=false;this.onInvalidated=onInvalidated;
    this.disk=persistentCache===null?null:persistentCache||new SectionCache({onDelete:()=>this.invalidate()});
    this.environment=environment;this.prefetchEnabled=prefetch;this.foreground=0;this.background=null;
    this.hintTimer=null;this.hintSelection=null;this.lastSelection=null;this.prefetchCount=0;this.prefetchedBytes=0;
    this.stats={catalogueRequests:0,viewRequests:0,bytes:0,cacheHits:0,diskHits:0,prefetchRequests:0,prefetchBytes:0};
    this.channel=null;
    try{if(typeof window!=='undefined'&&globalThis.BroadcastChannel){
      this.channel=new BroadcastChannel(CACHE_CHANNEL);
      this.channel.onmessage=event=>{if(event.data?.type==='clear')this.invalidate();};
    }}catch{}
    this.visibility=()=>{
      const connection=this.environment.navigator?.connection;
      if(this.environment.document?.hidden||connection?.saveData||['slow-2g','2g'].includes(connection?.effectiveType))this.cancelHint();
    };
    this.environment.document?.addEventListener?.('visibilitychange',this.visibility);
    this.environment.navigator?.connection?.addEventListener?.('change',this.visibility);
  }
  clear({persistent=true}={}){this.cancelHint();this.cache.clear();this.used=0;this.generation++;
    return persistent?this.disk?.remove(this.comparison):undefined;}
  close(){this.clear({persistent:false});this.catalogue=null;this.closed=true;this.disk?.close();
    this.channel?.close();this.environment.document?.removeEventListener?.('visibilitychange',this.visibility);
    this.environment.navigator?.connection?.removeEventListener?.('change',this.visibility);}
  invalidate(){if(this.closed)return;this.close();this.onInvalidated('Sign in again to view online results.');}
  async metadata(signal){
    aborted(signal);if(this.closed)throw new DOMException('Chart source closed.','AbortError');
    try{
      this.stats.catalogueRequests++;
      const response=await this.fetcher(this.url+'/catalogue',{credentials:'same-origin',cache:'no-store',signal});
      const {value,bytes}=await boundedJSON(response,CATALOGUE_BYTES,signal);
      const catalogue=validateCatalogue(value);this.stats.bytes+=bytes;aborted(signal);
      if(this.closed)throw new DOMException('Chart source closed.','AbortError');
      if(this.catalogue&&this.catalogue.publication!==catalogue.publication){
        await this.clear();throw Error('These results have changed. Return to Results and reopen the Visualiser.');
      }
      const scope=response.headers?.get('x-visualiser-cache-scope');
      if(this.scope&&scope!==this.scope){await this.clear();throw Error('Your sign-in has changed. Return to Results and reopen the Visualiser.');}
      this.scope=hash(scope)?scope:null;
      this.diskActive=!!(this.scope&&this.disk&&await this.disk.activate(this.scope));
      aborted(signal);
      if(this.closed)throw new DOMException('Chart source closed.','AbortError');
      this.catalogue??=catalogue;
      return this.catalogue;
    }catch(error){if(error.name!=='AbortError'){
      await this.clear();if([401,403,404].includes(error.status))this.invalidate();
    }throw error;}
  }
  key(selection,id){return JSON.stringify([this.catalogue.publication,selection.variable,selection.stratifier,selection.kind,id]);}
  get(key){const entry=this.cache.get(key);if(!entry)return null;
    this.cache.delete(key);this.cache.set(key,entry);this.stats.cacheHits++;return entry;}
  put(key,rows,bytes){
    // A conservative accounting allowance includes decoded object overhead;
    // it is a cache budget, not a claim about a browser's exact heap usage.
    const cost=bytes*4+rows.length*768;
    if(cost>this.cacheBytes||this.cacheEntries===0)return;
    const previous=this.cache.get(key);if(previous){this.used-=previous.cost;this.cache.delete(key);}
    while(this.cache.size&&(this.used+cost>this.cacheBytes||this.cache.size>=this.cacheEntries)){
      const first=this.cache.keys().next().value;this.used-=this.cache.get(first).cost;this.cache.delete(first);
    }
    this.cache.set(key,{rows,bytes,cost});this.used+=cost;
  }
  async load({variable,stratifier,kind,scenarios},signal){
    this.cancelHint();this.foreground++;
    try{
      const selection={variable,stratifier,kind,scenarios:[...scenarios]};
      const rows=await this.loadSelection(selection,signal);
      this.lastSelection=selection;return rows;
    }catch(error){if(error.name!=='AbortError')await this.clear();throw error;}
    finally{this.foreground--;}
  }
  async loadSelection({variable,stratifier,kind,scenarios},signal,{background=false}={}){
    // Even a cache hit checks ownership, source availability and expiry.
    const catalogue=await this.metadata(signal),generation=this.generation;
    if(!text(variable)||!text(stratifier)||!kinds.has(kind)||!Array.isArray(scenarios)||
        new Set(scenarios).size!==scenarios.length||scenarios.some(s=>!catalogue.configurations.some(c=>c.key===s)))
      throw Error('Invalid chart selection.');
    if(!catalogue.variables.some(v=>v.name===variable))return [];
    const selection={variable,stratifier,kind};
    const configs=catalogue.configurations.filter(c=>c.key==='baseline'||scenarios.includes(c.key));
    if(!configs.length)return [];
    const values=new Map(),sizes=new Map(),missing=[];
    for(const c of configs){
      const key=this.key(selection,c.id);let cached=this.get(key);
      if(!cached&&this.diskActive){
        const content=await this.disk.get(this.scope,JSON.stringify([this.scope,this.comparison,key]));aborted(signal);
        if(this.closed||generation!==this.generation)throw new DOMException('Chart request cancelled.','AbortError');
        if(content!=null){
          try{const rows=validateRows(JSON.parse(content),selection,c),bytes=new TextEncoder().encode(content).length;
            cached={rows,bytes};this.put(key,rows,bytes);this.stats.diskHits++;
          }catch{await this.disk.remove(this.comparison);}
        }
      }
      if(cached){values.set(c.id,cached.rows);sizes.set(c.id,cached.bytes);}else missing.push(c);}
    if(missing.length){
      const params=new URLSearchParams({variable,stratifier,kind});
      if(this.pinPublication)params.set('publication',catalogue.publication);
      missing.forEach(c=>params.append('configuration',c.id));
      this.stats.viewRequests++;
      if(background)this.stats.prefetchRequests++;
      const response=await this.fetcher(this.url+'/view?'+params,{credentials:'same-origin',cache:'no-store',signal});
      const {value,bytes}=await boundedJSON(response,background?Math.min(PREFETCH_BYTES,PREFETCH_TOTAL-this.prefetchedBytes):VIEW_BYTES,signal,
        count=>{if(background){this.prefetchedBytes+=count;this.stats.prefetchBytes+=count;}});
      if(!keys(value,['format','publication','selection','series'])||value.format!==VIEW_FORMAT||
          value.publication!==catalogue.publication||!keys(value.selection,['variable','stratifier','kind','configurations'])||
          value.selection.variable!==variable||value.selection.stratifier!==stratifier||value.selection.kind!==kind||
          JSON.stringify(value.selection.configurations)!==JSON.stringify(missing.map(c=>c.id))||
          !Array.isArray(value.series)||value.series.length!==missing.length)
        throw Error('Online chart sections do not match this comparison.');
      let count=0;
      value.series.forEach((series,i)=>{
        const c=missing[i];
        if(!keys(series,['configuration','rows'])||series.configuration!==c.id||!Array.isArray(series.rows))
          throw Error('Online chart sections do not match their configurations.');
        count+=series.rows.length;
        for(const row of series.rows){
          if(!row||row.scenario!==c.role.toLowerCase()||row.variable!==variable||row.stratifier!==stratifier||
              (kind==='levels'?!['mean','share'].includes(row.metric_type):row.metric_type!==kind))
            throw Error('Online chart sections do not match the selected chart.');
          row.scenario=c.key;
        }
        validateRows(series.rows,selection,c);
        values.set(c.id,series.rows);
        sizes.set(c.id,new TextEncoder().encode(JSON.stringify(series.rows)).length);
      });
      if(count>(background?PREFETCH_ROWS:VIEW_ROWS))throw Error('Too many chart rows. Select fewer scenarios or a smaller population breakdown.');
      aborted(signal);if(this.closed||generation!==this.generation)throw new DOMException('Chart request cancelled.','AbortError');
      this.stats.bytes+=bytes;
      value.series.forEach(series=>this.put(this.key(selection,series.configuration),series.rows,
        sizes.get(series.configuration)));
      if(this.diskActive)for(const series of value.series){
        aborted(signal);if(this.closed||generation!==this.generation)throw new DOMException('Chart request cancelled.','AbortError');
        await this.disk.put(this.scope,this.comparison,JSON.stringify([this.scope,this.comparison,this.key(selection,series.configuration)]),
          JSON.stringify(series.rows));
      }
    }
    aborted(signal);
    if(this.closed||generation!==this.generation)throw new DOMException('Chart request cancelled.','AbortError');
    const rows=configs.flatMap(c=>values.get(c.id));
    if(rows.length>VIEW_ROWS||configs.reduce((n,c)=>n+sizes.get(c.id),0)>VIEW_BYTES)
      throw Error('This view is too large. Select fewer scenarios or a smaller population breakdown.');
    return rows;
  }
  mayPrefetch(){
    const connection=this.environment.navigator?.connection;
    return this.prefetchEnabled&&!this.closed&&!this.foreground&&!this.environment.document?.hidden&&
      !connection?.saveData&&!['slow-2g','2g'].includes(connection?.effectiveType)&&
      this.prefetchCount<20&&this.prefetchedBytes+PREFETCH_BYTES<=PREFETCH_TOTAL;
  }
  cancelHint(){clearTimeout(this.hintTimer);this.hintTimer=null;this.hintSelection=null;
    this.background?.abort();this.background=null;}
  hint({variable}){
    this.cancelHint();if(!this.mayPrefetch()||!this.lastSelection||variable===this.lastSelection.variable)return;
    const pyramid=variable==='Population Pyramid';
    const selection={...this.lastSelection,variable:pyramid?'Age':variable,
      ...(pyramid?{stratifier:'Gender',kind:'pyramid_bin'}:{kind:'levels'})};
    const definition=this.catalogue?.variables.find(v=>v.name===selection.variable);
    let view=definition?.views.find(v=>v.stratifier===selection.stratifier&&v.kind===selection.kind);
    if(!view&&!pyramid){selection.stratifier='Overall';view=definition?.views.find(v=>v.stratifier==='Overall'&&v.kind==='levels');}
    if(!view||view.bytes>PREFETCH_BYTES||view.rows>PREFETCH_ROWS)return;
    const configs=this.catalogue.configurations.filter(c=>c.key==='baseline'||selection.scenarios.includes(c.key));
    if(configs.every(c=>this.cache.has(this.key(selection,c.id))))return;
    this.hintSelection=selection;
    // A dwell avoids downloads while a pointer simply passes over the menu.
    this.hintTimer=setTimeout(()=>this.prefetch(selection),300);
  }
  async prefetch(selection){
    if(!this.mayPrefetch()||this.hintSelection!==selection)return;
    const abort=new AbortController();this.background=abort;this.prefetchCount++;
    const deadline=setTimeout(()=>abort.abort(),4000);
    try{await this.loadSelection(selection,abort.signal,{background:true});}
    catch(error){if([401,403,404].includes(error.status)){await this.clear();this.invalidate();}}
    finally{clearTimeout(deadline);if(this.background===abort)this.background=null;}
  }
}
