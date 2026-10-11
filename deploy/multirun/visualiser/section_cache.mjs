/* (C) Copyright 2026, by Ross Richardson
 * Best-effort, globally bounded IndexedDB cache for authorised aggregate sections.
 * Contains no authentication credentials. Access checks belong to SectionSource.
 * @author ross richardson
 */
export const CACHE_DATABASE='simpaths-visualiser-sections-v1';
export const CACHE_CHANNEL='simpaths-visualiser-cache-v1';
const BYTES=32*1024*1024,ENTRIES=64,AGE=12*60*60*1000,TIMEOUT=250;
const validHash=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const size=text=>new TextEncoder().encode(text).length;

export class SectionCache{
  constructor({factory,now=Date.now,bytes=BYTES,entries=ENTRIES,age=AGE,onDelete=()=>{}}={}){
    if(!Number.isSafeInteger(bytes)||bytes<0||bytes>BYTES||!Number.isSafeInteger(entries)||entries<0||entries>ENTRIES||
        !Number.isSafeInteger(age)||age<1||age>AGE)throw Error('Invalid persistent chart cache budget.');
    try{this.factory=factory===undefined?globalThis.indexedDB:factory;}catch{this.factory=null;}
    this.now=now;this.bytes=bytes;this.entries=entries;this.age=age;this.onDelete=onDelete;
    this.db=null;this.opening=null;this.disabled=!this.factory;this.closed=false;
  }
  async open(){
    if(this.disabled||this.closed)return null;
    if(this.db)return this.db;
    if(this.opening)return this.opening;
    this.opening=new Promise(resolve=>{
      let settled=false,request;
      const finish=db=>{if(settled){db?.close();return;}settled=true;clearTimeout(timer);resolve(db);};
      const timer=setTimeout(()=>{this.disabled=true;finish(null);},TIMEOUT);
      try{
        request=this.factory.open(CACHE_DATABASE,1);
        request.onupgradeneeded=()=>{
          for(const name of ['sections','lru','state'])
            if(!request.result.objectStoreNames.contains(name))request.result.createObjectStore(name,{keyPath:'id'});
        };
        request.onerror=()=>{this.disabled=true;finish(null);};
        request.onsuccess=()=>{
          const db=request.result;
          if(this.closed||this.disabled||settled){db.close();finish(null);return;}
          db.onversionchange=event=>{
            db.close();this.db=null;this.disabled=true;
            if(event.newVersion===null)this.onDelete();
          };
          this.db=db;finish(db);
        };
      }catch{this.disabled=true;finish(null);}
    });
    return this.opening;
  }
  async transaction(names,mode,work){
    const db=await this.open();if(!db)return null;
    return new Promise(resolve=>{
      let tx,result=null,finished=false;
      const finish=value=>{if(finished)return;finished=true;clearTimeout(timer);resolve(value);};
      const timer=setTimeout(()=>{try{tx?.abort();}catch{}this.disabled=true;db.close();finish(null);},TIMEOUT);
      try{
        tx=db.transaction(names,mode);
        tx.oncomplete=()=>finish(result);
        tx.onabort=tx.onerror=()=>finish(null);
        work(tx,value=>{result=value;});
      }catch{finish(null);}
    });
  }
  valid(record,now){
    return record&&typeof record.id==='string'&&record.id.length<=1200&&validHash(record.scope)&&
      validHash(record.comparison)&&Number.isSafeInteger(record.cost)&&record.cost>0&&record.cost<=this.bytes&&
      Number.isSafeInteger(record.bytes)&&record.bytes>=0&&record.bytes<=8*1024*1024&&
      Number.isFinite(record.created)&&Number.isFinite(record.used)&&record.created<=now&&
      now-record.created<this.age;
  }
  async activate(scope){
    if(!validHash(scope))return false;
    return await this.transaction(['sections','lru','state'],'readwrite',(tx,set)=>{
      const state=tx.objectStore('state'),request=state.get('active');
      request.onsuccess=()=>{
        if(request.result?.scope!==scope){
          tx.objectStore('sections').clear();tx.objectStore('lru').clear();
          state.put({id:'active',scope});
        }
        set(true);
      };
    })===true;
  }
  async get(scope,id){
    return this.transaction(['sections','lru','state'],'readwrite',(tx,set)=>{
      const state=tx.objectStore('state').get('active');
      state.onsuccess=()=>{
        if(state.result?.scope!==scope)return;
        const lru=tx.objectStore('lru'),request=lru.get(id);
        request.onsuccess=()=>{
          const meta=request.result,now=this.now();
          if(!this.valid(meta,now)||meta.scope!==scope){lru.delete(id);tx.objectStore('sections').delete(id);return;}
          const section=tx.objectStore('sections').get(id);
          section.onsuccess=()=>{
            const content=section.result?.content;
            if(typeof content!=='string'||content.length>8*1024*1024||size(content)!==meta.bytes){
              lru.delete(id);tx.objectStore('sections').delete(id);return;
            }
            lru.put({...meta,used:now});set(content);
          };
        };
      };
    });
  }
  async put(scope,comparison,id,content){
    if(!validHash(scope)||!validHash(comparison)||typeof id!=='string'||id.length>1200||typeof content!=='string')return;
    const bytes=size(content),cost=2*(bytes+size(id))+1024;
    if(bytes>8*1024*1024||cost>this.bytes||!this.entries)return;
    return this.transaction(['sections','lru','state'],'readwrite',(tx,set)=>{
      const state=tx.objectStore('state').get('active');
      state.onsuccess=()=>{
        if(state.result?.scope!==scope)return;
        const lru=tx.objectStore('lru'),sections=tx.objectStore('sections'),request=lru.getAll();
        request.onsuccess=()=>{
          const now=this.now(),kept=[];
          for(const record of request.result){
            if(record.id===id||record.scope!==scope||!this.valid(record,now)){lru.delete(record.id);sections.delete(record.id);}
            else kept.push(record);
          }
          kept.sort((a,b)=>a.used-b.used);
          let used=kept.reduce((n,item)=>n+item.cost,0);
          while(kept.length&&(used+cost>this.bytes||kept.length>=this.entries)){
            const item=kept.shift();used-=item.cost;lru.delete(item.id);sections.delete(item.id);
          }
          sections.put({id,content});lru.put({id,scope,comparison,bytes,cost,created:now,used:now});set(true);
        };
      };
    });
  }
  async remove(comparison){
    return this.transaction(['sections','lru'],'readwrite',(tx,set)=>{
      const lru=tx.objectStore('lru'),request=lru.getAll();
      request.onsuccess=()=>{
        for(const record of request.result)if(record.comparison===comparison){
          lru.delete(record.id);tx.objectStore('sections').delete(record.id);
        }
        set(true);
      };
    });
  }
  close(){this.closed=true;this.db?.close();this.db=null;}
}
