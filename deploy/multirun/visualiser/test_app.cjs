/* (C) Copyright 2026, by Ross Richardson
 * Verify the maintained application renders safely with injected aggregate data.
 * Fictional source labels only; no private simulation files are read.
 * @author ross richardson
 */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const vm=require('node:vm'),{test}=require('node:test');
const {adaptApp}=require('./adapt_app.cjs');
const source=process.env.SIMPATHS_VISUALISER_SOURCE;
const dependencies=process.env.SIMPATHS_VISUALISER_DEPENDENCIES;
if(!source||!dependencies)throw Error('Set SIMPATHS_VISUALISER_SOURCE and SIMPATHS_VISUALISER_DEPENDENCIES');
const React=require(path.join(dependencies,'react'));
const {renderToStaticMarkup}=require(path.join(dependencies,'react-dom/server'));
const babel=require('/usr/share/nodejs/@babel/core');
const input=fs.readFileSync(path.join(source,'src/App.js'),'utf8');
const adapted=adaptApp(input);
// Load reviewed reusable components, leaving chart drawing and raw-folder I/O
// to the full Chromium/Jest checks. Static rendering never starts a fetch.
const cache=new Map();
function load(file){
  const absolute=path.join(source,'src',file);
  if(cache.has(absolute))return cache.get(absolute);
  const exports={};cache.set(absolute,exports);
  const original=fs.readFileSync(absolute,'utf8');
  const code=babel.transformSync(file==='App.js'?adaptApp(original):original,{
    filename:file,babelrc:false,configFile:false,
    presets:[[require('/usr/share/nodejs/@babel/preset-react'),{runtime:'automatic'}]],
    plugins:[require('/usr/share/nodejs/@babel/plugin-transform-modules-commonjs')]}).code;
  vm.runInNewContext(code,{exports,process:{env:{PUBLIC_URL:'/visualiser-assets'}},require:name=>{
    if(name==='react'||name.startsWith('react/'))return require(path.join(dependencies,name));
    if(name==='d3')return require(path.join(dependencies,'d3'));
    if(name==='./DashboardSection')return ()=>React.createElement('svg',{'data-chart':'fixture'});
    if(name==='./localFolderParser')return {parseLocalFolder:()=>{throw Error('No raw files in static check');}};
    if(name.startsWith('./'))return load(name.slice(2).replace(/\.js$/,'')+'.js');
    throw Error('Unexpected dependency in data-source adapter: '+name);
  }});
  return exports;
}
const App=load('App.js').default;
const row={year:2019,scenario:'baseline',module:'Health',variable:'Mental Component Summary (MCS)',
  variable_value:'Continuous Mean',stratifier:'Overall',stratifier_value:'Overall',metric_type:'mean',
  n_runs:3,total_sample:300,min_sample:100,mean_sample:100,mean_value:50,sd_value:1,lower_ci:49,upper_ci:51};
const render=rows=>renderToStaticMarkup(React.createElement(App,{dataSource:{rows,
  names:{baseline:'<script>fictional</script>'},label:'Online results',
  navigation:React.createElement('a',{href:'/results'},'Return to SimPaths Online'),notice:'Development preview'}}));

test('original application presents its topics, explanations, credits and local assets',()=>{
  const html=render([row]);
  for(const label of ['SimPaths Policy Impacts Visualiser','Connect Data','Explore Variables',
    'Demographics','Activity status','Income','Health','Highest Level of Education',
    'Getting Started','Limitations &amp; Interpretation','Credit &amp; Citation','Send Feedback',
    '/visualiser-assets/pmh_logo.png','/visualiser-assets/UKRILogo.png','Return to SimPaths Online',
    'Baseline — &lt;script&gt;fictional&lt;/script&gt;'])assert.ok(html.includes(label),label);
  assert.ok(!html.includes('<script>fictional</script>'));
  assert.ok(render([row]).includes('data-chart="fixture"'));
});

test('unavailable or malformed connected aggregates render no charts',()=>{
  assert.ok(!render([]).includes('<svg'));
  assert.ok(!render([{...row,id_Person:'private'}]).includes('<svg'));
});

test('the build guard requires the reviewed source interface and maps only local guidance',()=>{
  assert.throws(()=>adaptApp('function App() {}'));
  assert.ok(adapted.includes('/Interpreting-results.html'));
  assert.ok(!adapted.includes('/interpreting-results.html'));
});
