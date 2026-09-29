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
const code=babel.transformSync(adapted,{filename:'App.js',babelrc:false,configFile:false,
  presets:[[require('/usr/share/nodejs/@babel/preset-react'),{runtime:'classic'}]],
  plugins:[require('/usr/share/nodejs/@babel/plugin-transform-modules-commonjs')]}).code;
const applicationExports={};
vm.runInNewContext(code,{exports:applicationExports,process:{env:{PUBLIC_URL:'/visualiser-assets'}},require:name=>{
  if(name==='react')return React;
  if(name==='./DashboardSection')return ({vmMode})=>React.createElement('svg',{'data-mode':vmMode?'vm':'local'});
  throw Error('Unexpected dependency in data-source adapter: '+name);
}});
const App=applicationExports.default;
const render=(rows,vmMode=true)=>renderToStaticMarkup(React.createElement(App,{dataSource:{rows,vmMode,
  kind:vmMode?'vm':'local',controls:React.createElement('p',null,'Baseline - <script>fictional</script>'),
  notice:'Development preview'}}));

test('original application presents its topics, explanations, credits and local assets',()=>{
  const html=render([{}]);
  for(const label of ['SimPaths Policy Impacts Visualiser','Connect Data','Explore Variables',
    'Demographics','Activity status','Income','Health','Highest Level of Education',
    'Getting Started','Limitations &amp; Interpretation','Credit &amp; Citation','Send Feedback',
    '/visualiser-assets/pmh_logo.png','/visualiser-assets/UKRILogo.png','Return to MultiRun']){
    assert.ok(html.includes(label),label);
  }
  assert.ok(html.includes('Baseline - &lt;script&gt;fictional&lt;/script&gt;'));
  assert.ok(!html.includes('<script>fictional</script>'));
});

test('unavailable aggregates leave no charts and no path to bundled default data',()=>{
  assert.ok(!render([]).includes('<svg'));
  assert.ok(render([{}]).includes('data-mode="vm"'));
  assert.ok(render([{}],false).includes('data-mode="local"'));
  for(const forbidden of ['d3.csv','SimPaths_All_Aggregated_Outputs.csv','loadDefaultDataset','handleSelectFolder'])
    assert.ok(!adapted.includes(forbidden),forbidden);
});
