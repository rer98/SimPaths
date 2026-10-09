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
module.exports={adaptApp};
