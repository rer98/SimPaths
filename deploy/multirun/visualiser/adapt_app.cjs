/* (C) Copyright 2026, by Ross Richardson
 * Adapt the maintained Visualiser application to an injected VM/local data source.
 * Guarded edits apply to a generated copy; upstream presentation stays upstream.
 * @author ross richardson
 */
function adaptApp(source) {
  // Use the reviewed upstream contribution interface; fail on incompatible code.
  for (const anchor of ['function App({ dataSource } = {})', 'normaliseAggregateRows',
    'AggregateDataPanel', 'sourceRef.current != null', 'dataSource.navigation']) {
    if (!source.includes(anchor)) throw Error('Review changed aggregate data-source interface: '+anchor);
  }
  // The hosted guidance uses its established explicit asset allowlist name.
  return source.replaceAll('/interpreting-results.html','/Interpreting-results.html');
}
function adaptDashboard(source){
  const previous='useEffect(()=>setEnabledScenarios(new Set(allScenarioNames)),[allScenarioNames]);';
  if(source.split(previous).length!==2)throw Error('Review changed scenario selection lifecycle');
  // The experiment host adds available controls without selecting them or
  // resetting choices that a user already made in this dashboard instance.
  return source.replace(previous,
    'useEffect(()=>setEnabledScenarios(previous=>new Set([...previous].filter(name=>allScenarioNames.includes(name)))),[allScenarioNames]);');
}
module.exports={adaptApp,adaptDashboard};
