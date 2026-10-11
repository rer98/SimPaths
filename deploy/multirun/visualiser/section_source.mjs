/* (C) Copyright 2026, by Ross Richardson
 * Fetch selected chart sections with cancellation and an instance-scoped LRU.
 * Cache reuse requires current server authorisation. Nothing enters disk storage.
 * @author ross richardson
 */
export const CATALOGUE_FORMAT='simpaths.visualiser.catalogue.v1';
export const VIEW_FORMAT='simpaths.visualiser.view.v1';
const CATALOGUE_BYTES=512*1024,VIEW_BYTES=8*1024*1024,VIEW_ROWS=20000;
const CACHE_BYTES=32*1024*1024,CACHE_ENTRIES=64;
const kinds=new Set(['levels','wage_bin','income_bin','pyramid_bin']);
const text=v=>typeof v==='string'&&v.length>0&&v.length<=160&&!/[\u0000-\u001f]/.test(v);
const hash=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const keys=(value,expected)=>value&&typeof value==='object'&&!Array.isArray(value)&&
  Object.keys(value).sort().join('|')===[...expected].sort().join('|');
function aborted(signal){if(signal?.aborted)throw new DOMException('Chart request cancelled.','AbortError');}

export async function boundedJSON(response,maximum,signal){
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
        if(bytes>maximum)throw Error('The chart response exceeds its size limit.');
        chunks.push(value);
      }
      const joined=new Uint8Array(bytes);let offset=0;
      for(const piece of chunks){joined.set(piece,offset);offset+=piece.length;}
      content=new TextDecoder('utf-8',{fatal:true}).decode(joined);
    }finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
  }else{
    content=await response.text();
    if(new TextEncoder().encode(content).length>maximum)throw Error('The chart response exceeds its size limit.');
  }
  aborted(signal);
  const value=JSON.parse(content);
  if(!response.ok)throw Error(typeof value?.error==='string'?value.error:'Online results are unavailable.');
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
  constructor(key,{fetcher=globalThis.fetch.bind(globalThis),cacheBytes=CACHE_BYTES,cacheEntries=CACHE_ENTRIES}={}){
    if(!/^[a-f0-9]{64}$/.test(key)||!Number.isSafeInteger(cacheBytes)||cacheBytes<0||cacheBytes>CACHE_BYTES||
        !Number.isSafeInteger(cacheEntries)||cacheEntries<0||cacheEntries>CACHE_ENTRIES)throw Error('Invalid chart source.');
    this.url='/api/visualiser/'+key;this.fetcher=fetcher;this.cache=new Map();this.cacheBytes=cacheBytes;
    this.cacheEntries=cacheEntries;this.used=0;this.catalogue=null;this.generation=0;this.closed=false;
    this.stats={catalogueRequests:0,viewRequests:0,bytes:0,cacheHits:0};
  }
  clear(){this.cache.clear();this.used=0;this.generation++;}
  close(){this.clear();this.catalogue=null;this.closed=true;}
  async metadata(signal){
    aborted(signal);if(this.closed)throw new DOMException('Chart source closed.','AbortError');
    try{
      this.stats.catalogueRequests++;
      const response=await this.fetcher(this.url+'/catalogue',{credentials:'same-origin',cache:'no-store',signal});
      const {value,bytes}=await boundedJSON(response,CATALOGUE_BYTES,signal);
      const catalogue=validateCatalogue(value);this.stats.bytes+=bytes;aborted(signal);
      if(this.closed)throw new DOMException('Chart source closed.','AbortError');
      if(this.catalogue&&this.catalogue.publication!==catalogue.publication){
        this.clear();throw Error('These results have changed. Return to Results and reopen the Visualiser.');
      }
      this.catalogue??=catalogue;
      return this.catalogue;
    }catch(error){if(error.name!=='AbortError')this.clear();throw error;}
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
    try{return await this.loadSelection({variable,stratifier,kind,scenarios},signal);}
    catch(error){if(error.name!=='AbortError')this.clear();throw error;}
  }
  async loadSelection({variable,stratifier,kind,scenarios},signal){
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
    for(const c of configs){const cached=this.get(this.key(selection,c.id));
      if(cached){values.set(c.id,cached.rows);sizes.set(c.id,cached.bytes);}else missing.push(c);}
    if(missing.length){
      const params=new URLSearchParams({variable,stratifier,kind});
      missing.forEach(c=>params.append('configuration',c.id));
      this.stats.viewRequests++;
      const response=await this.fetcher(this.url+'/view?'+params,{credentials:'same-origin',cache:'no-store',signal});
      const {value,bytes}=await boundedJSON(response,VIEW_BYTES,signal);
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
        values.set(c.id,series.rows);
        sizes.set(c.id,new TextEncoder().encode(JSON.stringify(series.rows)).length);
      });
      if(count>VIEW_ROWS)throw Error('Too many chart rows. Select fewer scenarios or a smaller population breakdown.');
      aborted(signal);if(this.closed||generation!==this.generation)throw new DOMException('Chart request cancelled.','AbortError');
      this.stats.bytes+=bytes;
      value.series.forEach(series=>this.put(this.key(selection,series.configuration),series.rows,
        sizes.get(series.configuration)));
    }
    aborted(signal);
    const rows=configs.flatMap(c=>values.get(c.id));
    if(rows.length>VIEW_ROWS||configs.reduce((n,c)=>n+sizes.get(c.id),0)>VIEW_BYTES)
      throw Error('This view is too large. Select fewer scenarios or a smaller population breakdown.');
    return rows;
  }
}
