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
  // Reuse the maintained folder tree in the common Getting Started guidance,
  // which remains visible when the host supplies the Connect Data controls.
  const sidebarStart=source.indexOf('subfolders <span\n');
  const sidebarEnd=source.indexOf('</span></p>',sidebarStart);
  if(sidebarStart<0||sidebarEnd<0)throw Error('Review changed folder-structure help');
  const sidebar=source.slice(sidebarStart,sidebarEnd);
  const tree=sidebar.match(/\{(`Your Data Folder\/[\s\S]*?`)\}/);
  if(!tree)throw Error('Review changed folder-structure example');
  source=source.slice(0,sidebarStart)+'subfolders.'+source.slice(sidebarEnd+'</span>'.length);
  const guidance=/<p style=\{\{[^\n]+\}\}>\n      To use your own data,[\s\S]*?folders sit within\.\n    <\/p>/g;
  if([...source.matchAll(guidance)].length!==1)throw Error('Review changed Getting Started folder guidance');
  source=source.replace(guidance,paragraph=>paragraph.replace('<p style=',
    '<p className="vm-folder-guidance" style=').replace('runs from your model output.',`runs from your model output.{" "}
      <span className="vm-folder-help"
        onMouseEnter={() => setShowFolderStructure(true)}
        onMouseLeave={() => setShowFolderStructure(false)}>
        <button type="button" aria-expanded={showFolderStructure}
          aria-controls="vm-folder-tree" aria-describedby={showFolderStructure ? "vm-folder-tree" : undefined}
          onFocus={() => setShowFolderStructure(true)} onBlur={() => setShowFolderStructure(false)}
          onClick={() => setShowFolderStructure(true)}
          onKeyDown={event => { if(event.key === "Escape") setShowFolderStructure(false); }}>
          View folder structure
        </button>
        {showFolderStructure && <span id="vm-folder-tree" className="vm-folder-tree" role="tooltip">
          <code>{${tree[1].replace(/\n {10}/g,'\n')}}</code>
        </span>}
      </span>{" "}`));
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
