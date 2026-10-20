/* (C) Copyright 2026, by Ross Richardson
 * Bind emitted Visualiser packages to the installed dependency lock.
 * @author ross richardson
 */
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const sha=value=>crypto.createHash('sha256').update(value).digest('hex');

function dependencyReceipt(dependencies){
  const root=fs.realpathSync(dependencies),parent=path.dirname(root);
  if(path.basename(root)!=='node_modules')throw Error('Use an installed node_modules directory');
  const paths={manifest:path.join(parent,'package.json'),lock:path.join(parent,'package-lock.json')};
  const contents=Object.fromEntries(Object.entries(paths).map(([name,file])=>[name,fs.readFileSync(file)]));
  const manifest=JSON.parse(contents.manifest),lock=JSON.parse(contents.lock);
  if(!lock.packages||!lock.packages['']||lock.lockfileVersion<2)
    throw Error('A complete npm dependency lock is required');
  for(const [name,version] of Object.entries(manifest.dependencies||{})){
    const installed=JSON.parse(fs.readFileSync(path.join(root,name,'package.json')));
    const pinned=lock.packages['node_modules/'+name];
    if(!pinned||pinned.version!==installed.version||
       (/^\d+\.\d+\.\d+(?:-[\w.-]+)?$/.test(version)&&installed.version!==version)||
       lock.packages[''].dependencies?.[name]!==version)
      throw Error('Installed direct package differs from its lock: '+name);
  }
  function packageOf(resource){
    if(!resource||!resource.startsWith(root+path.sep))return null;
    if(!fs.realpathSync(resource).startsWith(root+path.sep))
      throw Error('Dependency resource escapes the installed directory');
    let directory=path.dirname(resource);
    while(directory.startsWith(root+path.sep)){
      const file=path.join(directory,'package.json');
      if(fs.existsSync(file)){
        const pkg=JSON.parse(fs.readFileSync(file));
        const relative=path.relative(root,directory),key='node_modules/'+relative.split(path.sep).join('/');
        const entry=lock.packages[key];
        if(!pkg.name||!pkg.version||!entry||entry.link||entry.version!==pkg.version)
          throw Error('Emitted package differs from the dependency lock: '+key);
        return {name:pkg.name,version:pkg.version,path:relative.split(path.sep).join('/')};
      }
      directory=path.dirname(directory);
    }
    throw Error('Cannot identify emitted dependency: '+resource);
  }
  return {
    root,packageOf,
    record:{package_json_sha256:sha(contents.manifest),package_lock_sha256:sha(contents.lock),
      lockfile_version:lock.lockfileVersion,locked_packages:Object.keys(lock.packages).length-1},
    verify(){for(const [name,file] of Object.entries(paths)){
      if(sha(fs.readFileSync(file))!==sha(contents[name]))throw Error('Dependency metadata changed during build');
    }},
  };
}

function emittedPackages(compilation,receipt){
  const found=new Map(),seen=new Set();
  function visit(module){
    if(seen.has(module))return;
    seen.add(module);
    const pkg=receipt.packageOf(module.resource);
    if(pkg)found.set(pkg.path,pkg);
    for(const child of module.modules||[])visit(child);
  }
  for(const chunk of compilation.chunks)
    for(const module of compilation.chunkGraph.getChunkModulesIterable(chunk))visit(module);
  return [...found.values()].sort((a,b)=>a.path.localeCompare(b.path));
}

module.exports={dependencyReceipt,emittedPackages};
