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
  // Newer PR sources own the shared, accessible help. Keep it intact instead
  // of generating another version; retain the old path for pinned old builds.
  if(source.includes('import FolderStructureHelp from "./FolderStructureHelp";')){
    if(source.split('<FolderStructureHelp />').length!==2)
      throw Error('Review changed shared folder guidance');
    // The rebranded layout puts folder help in the local picker branch. The
    // hosted panel also offers a local picker, so share that same paragraph.
    const localHelp=/(\{hasDataSource \? <>\s*<AggregateDataPanel [^>]*\/>\s*<p\b[^>]*>[^<]*<\/p>\s*<\/> : <>)\s*(<p\b[^>]*>\s*Explore your own SimPaths output:[\s\S]*?<FolderStructureHelp \/>\s*<\/p>)/;
    source=source.replace(localHelp,'$2\n              $1');
    return source.replaceAll('/interpreting-results.html','/Interpreting-results.html');
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
  // The hosted chooser admits at most nine configurations. Keep every
  // alternative distinguishable, including a view without a baseline.
  const palette=source.match(/const NUMERIC_SCEN_COLOURS  = \["(#[0-9a-fA-F]{6})",/);
  if(!palette)throw Error('Review changed scenario colours');
  const first=palette[1];
  for(const [before,after] of [
    ['const SCENARIO_DASHES = ["2,2", "6,4", "8,3,2,3", "4,2,1,2"];',
     'const SCENARIO_DASHES = ["2,2", "6,4", "8,3,2,3", "4,2,1,2", "12,4", "2,5", "6,2,2,2,2,2", "10,2,2,2", "1,3,1,3,6,3"];'],
    [`const NUMERIC_SCEN_COLOURS  = ["${first}", "${first}", "${first}", "${first}"];`,
     `const NUMERIC_SCEN_COLOURS  = ["${first}", "#b85c00", "#7758a6", "#2366aa", "#b34455", "#687b22", "#815a36", "#a23e89", "#26735b"];`]
  ]){
    if(source.includes(after))continue;
    if(source.split(before).length!==2)throw Error('Review changed scenario line styles');
    source=source.replace(before,after);
  }
  const previous='useEffect(()=>setEnabledScenarios(new Set(allScenarioNames)),[allScenarioNames]);';
  const retained='useEffect(()=>setEnabledScenarios(previous=>new Set([...previous].filter(name=>allScenarioNames.includes(name)))),[allScenarioNames]);';
  const optOut='const [hiddenScenarios,setHiddenScenarios]=useState(()=>new Set());';
  const visible='const enabledScenarios=useMemo(()=>new Set(allScenarioNames.filter(name=>!hiddenScenarios.has(name))),[allScenarioNames,hiddenScenarios]);';
  if(source.includes(optOut)&&source.includes(visible))return source;
  // Older pinned dashboards store the enabled set. Their symmetric toggle can
  // operate on the hidden set once we replace the state and its refresh effect.
  const state='const [enabledScenarios,setEnabledScenarios]=useState(()=>new Set(allScenarioNames));';
  const effect=source.includes(retained)?retained:previous;
  if(source.split(state).length!==2||source.split(effect).length!==2)
    throw Error('Review changed scenario selection lifecycle');
  return source.replace(effect,'').replace(state,optOut+'\n  '+visible)
    .replaceAll('setEnabledScenarios','setHiddenScenarios');
}
module.exports={adaptApp,adaptDashboard};
