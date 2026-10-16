/* (C) Copyright 2026, by Ross Richardson
 * Portable chart sections: lazy reads, exact values, integrity and input bounds.
 * @author ross richardson
 */
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {webcrypto} from 'node:crypto';
import {PortableSource,validatePackage} from './portable_source.mjs';
import {fixture,bytes,digest} from './portable_fixture.mjs';
globalThis.crypto??=webcrypto;
const selection={variable:'Mental Component Summary (MCS)',stratifier:'Overall',kind:'levels',scenarios:['scenario_1','scenario_2']};

test('opening reads metadata only; selection reads the matching sections with stable names and paired values',async()=>{
  const f=fixture(),source=await PortableSource.open(f.directory);
  assert.deepEqual(f.reads,['manifest.json']);
  const rows=await source.load(selection);
  assert.deepEqual(rows.map(r=>[r.scenario,r.mean_value,r.paired_mean_delta]),[
    ['baseline',40,null],['scenario_1',45,5],['scenario_2',45,5]]);
  assert.equal(source.stats.sectionReads,3);
  assert.equal(source.catalogue.configurations[2].name,'Policy B <img src="unsafe">');
  const physical=Array.from(f.files.keys()).filter(k=>!k.endsWith('index.json')&&k.includes('/'));
  assert.ok(physical.some(path=>!f.reads.includes(path)),'Unused variables must not be read');
  const next=await source.load({...selection,scenarios:[]});assert.equal(next.length,1);
  assert.equal(source.stats.sectionReads,4);
});

test('saved values work without network and retain suppressed estimates',async()=>{
  const f=fixture(),part=f.manifest.parts[0];
  const key=digest(JSON.stringify(['base',selection.variable,'Overall','levels'])),path=part.directory+'/'+key+'.json';
  const rows=JSON.parse(f.files.get(path));rows[0].mean_value=null;rows[0].lower_ci=null;rows[0].upper_ci=null;
  const body=bytes(rows);f.files.set(path,body);
  const index=JSON.parse(f.files.get(part.directory+'/index.json'));
  index.sections[key]={bytes:body.length,sha256:digest(body),rows:1};
  const raw=bytes(index);f.files.set(part.directory+'/index.json',raw);part.index={bytes:raw.length,sha256:digest(raw)};
  f.files.set('manifest.json',bytes(f.manifest));
  const prior=globalThis.fetch;globalThis.fetch=()=>{throw Error('No network allowed');};
  try{const source=await PortableSource.open(f.directory);const result=await source.load(selection);assert.equal(result[0].mean_value,null);}
  finally{globalThis.fetch=prior;}
});

test('changed index or selected section fails checksum verification',async()=>{
  for(const index of [true,false]){
    const f=fixture(),source=await PortableSource.open(f.directory);
    const part=f.manifest.parts[0],key=digest(JSON.stringify(['base',selection.variable,'Overall','levels']));
    const path=part.directory+'/'+(index?'index':key)+'.json',body=Buffer.from(f.files.get(path));body[0]=88;f.files.set(path,body);
    await assert.rejects(source.load(selection),/checksum/);
  }
});

test('unsupported versions, missing alternatives, traversal and extra manifest fields are rejected',()=>{
  for(const edit of [m=>m.format='future',m=>m.parts.pop(),m=>m.parts[0].directory='../private',
    m=>m.parts[0].configurations=['alt1','base'],m=>m.private_token='secret',m=>m.provenance.configurations.pop()]){
    const m=structuredClone(fixture().manifest);edit(m);assert.throws(()=>validatePackage(m));
  }
});

test('file byte limits apply before reading or parsing; cancellation and closed readers cannot return rows',async()=>{
  let reads=0;
  await assert.rejects(PortableSource.open({getFileHandle:async()=>({getFile:async()=>({size:1024**2+1,arrayBuffer:()=>{reads++;}})})}),/limit/);
  assert.equal(reads,0);
  const f=fixture(),source=await PortableSource.open(f.directory),controller=new AbortController();controller.abort();
  await assert.rejects(source.load(selection,controller.signal),{name:'AbortError'});
  source.close();await assert.rejects(source.load(selection),{name:'AbortError'});
});

test('extra raw fields are rejected even when a modified package has matching hashes',async()=>{
  const f=fixture(),part=f.manifest.parts[0],key=digest(JSON.stringify(['base',selection.variable,'Overall','levels']));
  const path=part.directory+'/'+key+'.json',rows=JSON.parse(f.files.get(path));rows[0].id_Person='PRIVATE';
  const body=bytes(rows),index=JSON.parse(f.files.get(part.directory+'/index.json'));
  f.files.set(path,body);index.sections[key]={bytes:body.length,sha256:digest(body),rows:1};
  const raw=bytes(index);f.files.set(part.directory+'/index.json',raw);part.index={bytes:raw.length,sha256:digest(raw)};
  f.files.set('manifest.json',bytes(f.manifest));
  const source=await PortableSource.open(f.directory);await assert.rejects(source.load(selection),/sections/);
});

test('a standalone configuration reopens without requiring a baseline-alternative pair',async()=>{
  const f=fixture(),m=f.manifest,part=m.parts[0];
  m.parts=[part];part.configurations=['base'];m.catalogue.configurations=m.catalogue.configurations.slice(0,1);
  m.provenance.configurations=m.provenance.configurations.slice(0,1);
  const index=JSON.parse(f.files.get(part.directory+'/index.json'));
  index.catalogue.configurations=index.catalogue.configurations.slice(0,1);
  const body=bytes(index);f.files.set(part.directory+'/index.json',body);part.index={bytes:body.length,sha256:digest(body)};
  f.files.set('manifest.json',bytes(m));
  const source=await PortableSource.open(f.directory);
  assert.equal((await source.load({...selection,scenarios:[]})).length,1);
});
