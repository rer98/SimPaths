/* (C) Copyright 2026, by Ross Richardson
 * Reject mismatched installed packages and retain nested emitted membership.
 * @author ross richardson
 */
const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {dependencyReceipt,emittedPackages}=require('./build_metadata.cjs');

function fixture(t){
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'visualiser-lock-'));
  t.after(()=>fs.rmSync(directory,{recursive:true}));
  const root=path.join(directory,'node_modules');fs.mkdirSync(root);
  const write=(file,value)=>{
    fs.mkdirSync(path.dirname(file),{recursive:true});
    fs.writeFileSync(file,typeof value==='string'?value:JSON.stringify(value));
  };
  const packages={'':{dependencies:{example:'1.0.0'}},
    'node_modules/example':{version:'1.0.0'},
    'node_modules/example/node_modules/child':{version:'2.0.0'}};
  write(path.join(directory,'package.json'),{dependencies:{example:'1.0.0'}});
  write(path.join(directory,'package-lock.json'),{lockfileVersion:3,packages});
  write(path.join(root,'example/package.json'),{name:'example',version:'1.0.0'});
  write(path.join(root,'example/index.js'),'fictional package');
  write(path.join(root,'example/node_modules/child/package.json'),{name:'child',version:'2.0.0'});
  write(path.join(root,'example/node_modules/child/index.js'),'fictional nested package');
  return {directory,root,write,packages};
}

test('concatenated and nested modules retain their distinct locked package identities',t=>{
  const f=fixture(t),receipt=dependencyReceipt(f.root);
  const child={resource:path.join(f.root,'example/node_modules/child/index.js')};
  const parent={resource:path.join(f.root,'example/index.js')};
  const joined={modules:[parent,child,parent]};
  const compilation={chunks:[1,2],chunkGraph:{getChunkModulesIterable:()=>[joined,parent]}};
  assert.deepEqual(emittedPackages(compilation,receipt),[
    {name:'example',version:'1.0.0',path:'example'},
    {name:'child',version:'2.0.0',path:'example/node_modules/child'}]);
  assert.equal(receipt.record.locked_packages,2);
  receipt.verify();
});

test('changed installed versions cannot inherit a clean lock inventory',t=>{
  const f=fixture(t);
  f.write(path.join(f.root,'example/package.json'),{name:'example',version:'9.0.0'});
  assert.throws(()=>dependencyReceipt(f.root),/differs from its lock/);
});

test('changed transitive versions are rejected when their modules are emitted',t=>{
  const f=fixture(t),receipt=dependencyReceipt(f.root);
  f.write(path.join(f.root,'example/node_modules/child/package.json'),{name:'child',version:'3.0.0'});
  assert.throws(()=>receipt.packageOf(path.join(f.root,'example/node_modules/child/index.js')),/differs from/);
});

test('a matching installed version and lock cannot override an exact manifest pin',t=>{
  const f=fixture(t);
  f.write(path.join(f.root,'example/package.json'),{name:'example',version:'9.0.0'});
  f.packages['node_modules/example'].version='9.0.0';
  f.write(path.join(f.directory,'package-lock.json'),{lockfileVersion:3,packages:f.packages});
  assert.throws(()=>dependencyReceipt(f.root),/differs from its lock/);
});

test('symlink escapes cannot be attributed to a locked package',t=>{
  const f=fixture(t),receipt=dependencyReceipt(f.root);
  const outside=path.join(f.directory,'outside.js');f.write(outside,'fictional outside resource');
  const link=path.join(f.root,'example/link.js');fs.symlinkSync(outside,link);
  assert.throws(()=>receipt.packageOf(link),/escapes/);
});

test('changing lock metadata during compilation fails the final check',t=>{
  const f=fixture(t),receipt=dependencyReceipt(f.root);
  f.write(path.join(f.directory,'package-lock.json'),{lockfileVersion:3,packages:{}});
  assert.throws(()=>receipt.verify(),/changed during build/);
});
