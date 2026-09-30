/* (C) Copyright 2026, by Ross Richardson
 * Adapt the maintained Visualiser application to an injected VM/local data source.
 * Guarded edits apply to a generated copy; upstream presentation stays upstream.
 * @author ross richardson
 */
function adaptApp(source) {
  let text=source;
  const replace=(old,value)=>{
    if(text.split(old).length!==2)throw Error('Review changed upstream application interface: '+old);
    text=text.replace(old,value);
  };
  const between=(start,end,value)=>{
    if(text.split(start).length!==2||text.split(end).length!==2)
      throw Error('Review changed upstream application section: '+start);
    const first=text.indexOf(start),last=text.indexOf(end,first+start.length);
    if(last<0)throw Error('Review changed upstream application section order');
    text=text.slice(0,first)+value+text.slice(last);
  };
  replace('import { useState, useEffect, useRef, useCallback } from "react";',
    'import React, { useState, useEffect, useRef } from "react";');
  replace('import * as d3 from "d3";\n','');
  replace('import { parseCsvRow } from "./useAggregatedData";\n','');
  replace('import { parseLocalFolder } from "./localFolderParser";\n','');
  replace('function App() {','function App({ dataSource }) {');
  between('  const [parsedCache,', '  const [openDomains,',
    '  const parsedCache = dataSource.rows;\n');
  // The VM adapter owns loading and source switching. No default CSV loader
  // remains in the generated application, including its reset/retry pathways.
  between('  /**\n   * Fetches + parses the bundled default CSV.',
    '  // Tracks window width', '');
  between('  /**\n   * "Visualise Your Own Data" handler',
    '  /** Expands/collapses one domain', '');
  replace('          SimPaths Policy Impacts Visualiser\n        </p>',
    '          SimPaths Policy Impacts Visualiser\n        </p>\n'+
    '        <a className="vm-return" href="/">Return to SimPaths Online</a>');
  replace('The default view displays a pre-aggregated dataset. To visualise your own simulation, select your parent folder in the Connect Data panel (data must be organised into "Baseline" and "Scenario" subfolders).',
    'The default view displays your selected online results. Use the Connect Data panel to switch to locally saved simulation output, organised into "Baseline" and "Scenario" subfolders.');
  replace('This tool is entirely JavaScript-based — all aggregation happens locally in your browser, and no data you upload is ever stored or sent anywhere.',
    'Online results are processed on the server; this page receives aggregate data only. Files selected with Visualise Locally Saved Data are processed in your browser and are never uploaded.');
  between('              <p style = {{margin: "0 0 8px",',
    '            </div>\n\n            {/* Explore Variables Card */}',
    '              {dataSource.controls}\n');
  replace('            <hr style={{ border: "none", borderTop: `1px solid ${BG_PANEL}`, marginBottom: 20 }} />',
    '            {dataSource.notice && <p className="vm-notice">{dataSource.notice}</p>}\n'+
    '            <hr style={{ border: "none", borderTop: `1px solid ${BG_PANEL}`, marginBottom: 20 }} />');
  replace('<DashboardSection parsedCache={parsedCache} targetVariable={activeVariable} bgBase={BG} bgDark={BG_DARK} bgPanel={BG_PANEL} />',
    '{parsedCache.length > 0 && <DashboardSection key={dataSource.kind + activeVariable} parsedCache={parsedCache} '+
    'targetVariable={activeVariable} vmMode={dataSource.vmMode} bgBase={BG} bgDark={BG_DARK} bgPanel={BG_PANEL} />}');
  return text;
}
module.exports={adaptApp};
