/* (C) Copyright 2026, by Ross Richardson
 * Build the maintained Visualiser application for VM use from a trusted checkout.
 * Original upstream notices are preserved. No public data or source maps copied.
 * @author ross richardson
 */
const fs=require('fs'), path=require('path'), crypto=require('crypto');
const {adaptApp}=require('./adapt_app.cjs');
const args={};
for(let i=2;i<process.argv.length;i+=2){
  if(!['--source','--dependencies','--output'].includes(process.argv[i])||!process.argv[i+1])throw Error('Invalid build arguments');
  args[process.argv[i].slice(2)]=path.resolve(process.argv[i+1]);
}
if(!args.source||!args.dependencies||!args.output) throw Error('Use --source CHECKOUT --dependencies NODE_MODULES --output NEW_DIRECTORY');
if(fs.existsSync(args.output)) throw Error('Choose a new build directory');
const tool=name=>{
  for(const location of [path.join(args.dependencies,name),path.join('/usr/share/nodejs',name)]){
    if(fs.existsSync(location)) return require(location);
  }
  return require(name);
};
const webpack=tool('webpack'),babel=tool('@babel/core'),reactPreset=tool('@babel/preset-react');
const sha=value=>crypto.createHash('sha256').update(value).digest('hex');
function revision(){
  let dir=path.join(args.source,'.git');
  if(fs.statSync(dir).isFile())dir=path.resolve(args.source,fs.readFileSync(dir,'utf8').trim().replace(/^gitdir: /,''));
  const head=fs.readFileSync(path.join(dir,'HEAD'),'utf8').trim();
  if(/^[a-f0-9]{40}$/.test(head))return head;
  const ref=head.replace(/^ref: /,'');
  if(fs.existsSync(path.join(dir,'commondir')))dir=path.resolve(dir,fs.readFileSync(path.join(dir,'commondir'),'utf8').trim());
  if(fs.existsSync(path.join(dir,ref)))return fs.readFileSync(path.join(dir,ref),'utf8').trim();
  const packed=fs.readFileSync(path.join(dir,'packed-refs'),'utf8').split('\n').find(line=>line.endsWith(' '+ref));
  if(!packed)throw Error('Cannot identify Visualiser source revision');
  return packed.split(' ')[0];
}
const hashes={};
const licences=new Map();
const selected=['App.js','parseCore.js','useAggregatedData.js','DashboardSection.js','localFolderParser.js',
  'AggregateDataPanel.js','aggregateDataSource.js','csvParse.js','tooltipContent.js'];
fs.mkdirSync(args.output,{recursive:true,mode:0o700});
const staging=path.join(args.output,'source');
fs.mkdirSync(staging,{mode:0o700});
for(const name of selected){
  let text=fs.readFileSync(path.join(args.source,'src',name),'utf8');
  hashes[name]=sha(text);
  if(name==='App.js')text=adaptApp(text);
  const transformed=babel.transformSync(text,{
    filename:name,presets:[[reactPreset,{runtime:'automatic'}]],
    babelrc:false,configFile:false,comments:true}).code;
  fs.writeFileSync(path.join(staging,name),transformed,{mode:0o600});
}
fs.copyFileSync(path.join(__dirname,'entry.jsx'),path.join(staging,'entry.jsx'));
fs.copyFileSync(path.join(__dirname,'comparison_data.mjs'),path.join(staging,'comparison_data.mjs'));
const entry=fs.readFileSync(path.join(staging,'entry.jsx'),'utf8');
fs.writeFileSync(path.join(staging,'entry.js'),babel.transformSync(entry,{
  filename:'entry.jsx',presets:[[reactPreset,{runtime:'automatic'}]],
  babelrc:false,configFile:false}).code);
fs.writeFileSync(path.join(staging,'server.js'),
  'export * from "./parseCore.js";export {getVariableDef,getStratifierDef} from "./useAggregatedData.js";');
const compile=(entry,filename,target)=>new Promise((resolve,reject)=>{
  webpack({mode:'production',target,entry,devtool:false,
    output:{path:args.output,filename,...(target==='node'?{library:{type:'commonjs2'}}:{})},
    resolve:{modules:[args.dependencies,'node_modules'],extensions:['.js','.jsx']},
    optimization:{minimize:true},
    plugins:[new webpack.DefinePlugin({'process.env.PUBLIC_URL':JSON.stringify('/visualiser-assets')})],
  },(error,stats)=>{
    if(error||stats.hasErrors())reject(error||Error(stats.toString({all:false,errors:true})));
    else {
      for(const module of stats.compilation.modules){
        if(!module.resource||!module.resource.startsWith(args.dependencies+path.sep))continue;
        let directory=path.dirname(module.resource);
        while(directory.startsWith(args.dependencies+path.sep)&&!fs.existsSync(path.join(directory,'package.json')))
          directory=path.dirname(directory);
        const pkg=JSON.parse(fs.readFileSync(path.join(directory,'package.json')));
        for(const name of fs.readdirSync(directory).filter(n=>/^(LICENSE|LICENCE)(\..*)?$/i.test(n))){
          const file=path.join(directory,name);if(!fs.statSync(file).isFile())continue;
          licences.set(pkg.name+' '+pkg.version+' / '+name,fs.readFileSync(file,'utf8'));
        }
      }
      resolve();
    }
  });
});
(async()=>{
  await compile(path.join(staging,'entry.js'),'visualiser.js','web');
  await compile(path.join(staging,'server.js'),'calculation.cjs','node');
  fs.copyFileSync(path.join(__dirname,'runner.cjs'),path.join(args.output,'runner.cjs'));
  const baseCss=fs.readFileSync(path.join(args.source,'src/index.css'),'utf8');
  hashes['index.css']=sha(baseCss);
  fs.writeFileSync(path.join(args.output,'visualiser.css'),baseCss+'\n'+fs.readFileSync(path.join(__dirname,'visualiser.css'),'utf8'));
  fs.copyFileSync(path.join(__dirname,'index.html'),path.join(args.output,'index.html'));
  for(const name of ['pmh_logo.png','UKRILogo.png','SimPaths-logo-transparent.png']){
    const content=fs.readFileSync(path.join(args.source,'public',name));
    hashes['public/'+name]=sha(content);
    fs.writeFileSync(path.join(args.output,name),content);
  }
  for(const name of ['Interpreting-results','citation']){
    const sourceName=name==='Interpreting-results'?'interpreting-results':name;
    let text=fs.readFileSync(path.join(args.source,'public',sourceName+'.html'),'utf8');
    hashes['public/'+sourceName+'.html']=sha(text);
    const style=text.match(/<style>([\s\S]*?)<\/style>/);
    if(!style||text.split('<style>').length!==2)throw Error('Review changed guidance styles');
    fs.writeFileSync(path.join(args.output,name+'.css'),style[1]);
    text=text.replace(style[0],'<link rel="stylesheet" href="/visualiser-assets/'+name+'.css">')
      .replace(/<link[^>]+href="https:\/\/fonts\.(?:googleapis|gstatic)\.com[^" ]*"[^>]*>\s*/g,'')
      .replace(/ onerror="[^"]*"/g,'')
      .replaceAll('src="/pmh_logo.png"','src="/visualiser-assets/pmh_logo.png"')
      .replaceAll('href="/citation.html"','href="/visualiser-assets/citation.html"')
      .replaceAll('href="/interpreting-results.html"','href="/visualiser-assets/Interpreting-results.html"')
      .replace(/← Back to (?:Dashboard|Visualiser)/,'← Return to SimPaths Online');
    if(/<script\b|\son\w+\s*=|<link[^>]+href="https?:|<img[^>]+src="https?:/i.test(text))
      throw Error('Review changed guidance resources before hosting');
    fs.writeFileSync(path.join(args.output,name+'.html'),text);
  }
  fs.writeFileSync(path.join(args.output,'dependencies.LICENSE.txt'),[...licences].sort().map(
    ([name,text])=>name+'\n\n'+text).join('\n\n'));
  fs.writeFileSync(path.join(args.output,'COPYRIGHT.md'),
    '<!-- (C) Copyright 2026, by Ross Richardson\nGenerated VM Visualiser build attribution.\n@author ross richardson\n-->\n\n'+
    '# Build attribution\n\nIntegration wrappers and generated build metadata: (C) Copyright 2026, by Ross Richardson.\n\n'+
    'The maintained Policy Impacts Visualiser application, guidance and logos retain their upstream attribution. The generated App adapter changes data sourcing, not ownership of the interface or scientific methods. Third-party packages retain their own licences, included in dependencies.LICENSE.txt and bundle licence notices. This attribution does not relicense upstream code.\n');
  const files={};
  for(const name of fs.readdirSync(args.output).filter(name=>fs.statSync(path.join(args.output,name)).isFile())){
    fs.chmodSync(path.join(args.output,name),0o600);
    files[name]=sha(fs.readFileSync(path.join(args.output,name)));
  }
  const dependencies={};
  for(const name of ['react','react-dom','d3']){
    const pkg=JSON.parse(fs.readFileSync(path.join(args.dependencies,name,'package.json')));
    if(({react:'19.2.7','react-dom':'19.2.7',d3:'7.9.0'})[name]!==pkg.version) throw Error('Unexpected dependency');
    dependencies[name]=pkg.version;
  }
  const identity={format:'simpaths.visualiser.build.v1',
    revision:revision(),
    mode:'development-paired',source_hashes:hashes,dependencies,
    toolchain:{node:process.version,webpack:webpack.version,babel:babel.version},files};
  fs.writeFileSync(path.join(args.output,'build.json'),JSON.stringify(identity,null,2),{mode:0o600});
  fs.rmSync(staging,{recursive:true});
  console.log('Built pinned development Visualiser: '+args.output);
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
