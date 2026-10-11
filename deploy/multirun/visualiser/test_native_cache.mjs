/* (C) Copyright 2026, by Ross Richardson
 * Native IndexedDB eviction, expiry, concurrent writers and logout checks.
 * Fictional strings only; invoked by the lightweight Chromium cache proof.
 * @author ross richardson
 */
import {SectionCache,CACHE_DATABASE} from './section_cache.mjs';
const scope='d'.repeat(64),comparison='a'.repeat(64),other='e'.repeat(64);
const require=(condition,message)=>{if(!condition)throw Error(message);};
const remove=()=>new Promise((resolve,reject)=>{
  const request=indexedDB.deleteDatabase(CACHE_DATABASE);
  request.onsuccess=resolve;request.onerror=()=>reject(request.error);
});
async function metadata(cache){
  const db=await cache.open();
  return new Promise((resolve,reject)=>{
    const request=db.transaction('lru','readonly').objectStore('lru').getAll();
    request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);
  });
}
export async function runNativeCacheChecks(){
  const checks=[];await remove();
  let now=1000;
  const first=new SectionCache({now:()=>now,entries:2,bytes:5000,age:1000});
  const second=new SectionCache({now:()=>now,entries:2,bytes:5000,age:1000});
  try{
    require(await first.activate(scope),'Native IndexedDB activation failed');
    await first.put(scope,comparison,'first','fictional one');now++;
    await second.activate(scope);await second.put(scope,other,'second','fictional two');now++;
    require(await first.get(scope,'first')==='fictional one','Native cache read failed');now++;
    await Promise.all([first.put(scope,comparison,'third','fictional three'),
      second.put(scope,other,'fourth','fictional four')]);
    let records=await metadata(first);
    require(records.length<=2&&records.reduce((n,r)=>n+r.cost,0)<=5000,'Concurrent writers exceeded the global cache budget');
    require(await first.get(scope,'second')===null,'An old entry survived LRU eviction');
    checks.push('native concurrent cache writers share the global byte/entry budget and evict old sections');
    now+=1001;
    for(const record of records)require(await first.get(scope,record.id)===null,'Expired rows were returned');
    checks.push('expired native entries are deleted rather than shown or renewed on reads');
    await first.put(scope,comparison,'first','fictional one');
    await second.put(scope,other,'second','fictional two');
    await first.remove(comparison);
    require(await first.get(scope,'first')===null&&await second.get(scope,'second')==='fictional two',
      'Invalidating a comparison affected another retained comparison');
    checks.push('comparison invalidation removes only that comparison');
    await second.activate(other);
    require(await second.get(other,'second')===null,'A new session reused the previous session');
    await first.put(scope,comparison,'stale','fictional stale reply');
    require((await metadata(second)).length===0,'An old session wrote after the active cache scope changed');
    checks.push('session switching clears old entries and rejects stale writers');
    await second.put(other,comparison,'large','x'.repeat(3000));
    require((await metadata(second)).length===0,'An oversized encoded section entered the disk cache');
    await second.put(other,comparison,'damaged','fictional');
    const db=await second.open();
    await new Promise((resolve,reject)=>{
      const tx=db.transaction('sections','readwrite');tx.objectStore('sections').put({id:'damaged',content:'changed'});
      tx.oncomplete=resolve;tx.onabort=()=>reject(tx.error);
    });
    require(await second.get(other,'damaged')===null,'Corrupt encoded bytes were reused');
    checks.push('oversized and corrupt native cache entries are rejected');
  }finally{first.close();second.close();}
  const refused=new SectionCache({factory:{open(){throw new DOMException('Storage refused','SecurityError');}}});
  require(await refused.activate(scope)===false,'Denied IndexedDB access did not fall back');refused.close();
  const blocked=new SectionCache({factory:{open(){return {};}}});
  require(await blocked.activate(scope)===false,'Blocked IndexedDB opening did not time out');blocked.close();
  checks.push('unavailable and blocked storage fall back within a bounded wait');
  let deleted=0;
  const active=new SectionCache({onDelete:()=>deleted++});
  await active.activate(scope);await active.put(scope,comparison,'saved','fictional');
  await remove();require(deleted===1,'Deleting the chart database did not notify an open tab');active.close();
  checks.push('native database deletion releases open connections and signals cache invalidation');
  return checks;
}
