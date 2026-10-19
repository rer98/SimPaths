"""(C) Copyright 2026, by Ross Richardson

Bounded file-size histories and labelled population/year resource reports.
Only metadata and existing numeric observations are used; no scientific rows.
@author ross richardson
"""
import csv
from html import escape
import io
import json
import os
from pathlib import Path

from deploy.multirun import research_year_profile as years

GIB=1024**3
MAX_SAMPLES=10000
SIZE_FIELDS={'work_bytes','input_bytes','csv_bytes','snapshot_bytes','other_bytes',
    'request_bytes','work_and_request_bytes'}
CONTEXT={'sequence','elapsed_ms','year_sample_sequence','model_elapsed_ms','repetition','year'}


def append_workspace(rows,measurement,elapsed_ms,year_sample=None):
    """Reuse an existing stat-only workspace walk, linked to its latest year sample."""
    context=dict(year_sample_sequence=-1,model_elapsed_ms=-1,repetition=0,year=0)
    if year_sample:
        context=dict(year_sample_sequence=year_sample['sequence'],model_elapsed_ms=year_sample['elapsed_ms'],
            repetition=year_sample['repetition'],year=year_sample['year'])
    row=dict(measurement,sequence=len(rows),elapsed_ms=elapsed_ms,**context)
    validate_workspace_row(row,rows[-1] if rows else None)
    if len(rows)>=MAX_SAMPLES:raise ValueError('Workspace observations exceed their sample budget')
    rows.append(row)


def validate_workspace_row(row,previous=None):
    if (type(row) is not dict or set(row)!=SIZE_FIELDS|CONTEXT
            or any(type(row[k]) is not int or not -1<=row[k]<2**63 for k in row)
            or min(row[k] for k in SIZE_FIELDS|{'sequence','elapsed_ms','repetition','year'})<0
            or row['work_and_request_bytes']!=row['work_bytes']+row['request_bytes']
            or row['repetition'] not in range(3) or row['year'] not in (0,*range(2019,2071))
            or (row['year']==0)!=(row['repetition']==0)
            or (row['year_sample_sequence']==-1)!=(row['model_elapsed_ms']==-1)
            or row['year_sample_sequence']==-1 and row['year']!=0
            or previous is None and row['sequence']!=0
            or previous is not None and (row['sequence']!=previous['sequence']+1
                or row['elapsed_ms']<previous['elapsed_ms']
                or row['year_sample_sequence']<previous['year_sample_sequence']
                or row['model_elapsed_ms']<previous['model_elapsed_ms'])):
        raise ValueError('Invalid or reordered workspace observation')


def write_workspace(path,rows):
    if not rows:raise ValueError('Missing workspace observations')
    with Path(path).open('x',encoding='utf-8') as target:
        os.chmod(path,0o600)
        previous=None
        for row in rows:
            validate_workspace_row(row,previous);previous=row
            target.write(json.dumps(row,sort_keys=True,separators=(',',':'))+'\n')


def read_workspace(path):
    from deploy.multirun.research_benchmarks import ordinary
    from deploy.multirun.research_storage_audit import stable_file
    from deploy.multirun.artifacts import fingerprint
    path=ordinary(path);expected=fingerprint(path)
    if expected['bytes']>16*1024**2:raise ValueError('Workspace evidence exceeds its byte budget')
    rows=[]
    with stable_file(path,expected) as (source,_):
        while line:=source.readline(4097):
            if len(line)>4096 or len(rows)>=MAX_SAMPLES:raise ValueError('Workspace evidence exceeds its bounds')
            row=years.decode(line)
            validate_workspace_row(row,rows[-1] if rows else None);rows.append(row)
    if not rows:raise ValueError('Missing workspace observations')
    return rows


def workspace_summary(rows):
    previous=None
    for row in rows:validate_workspace_row(row,previous);previous=row
    if not rows:raise ValueError('Missing workspace observations')
    differences=[]
    for first,last in zip(rows,rows[1:]):
        delta=last['work_and_request_bytes']-first['work_and_request_bytes']
        ms=last['elapsed_ms']-first['elapsed_ms']
        differences.append(dict(growth_bytes=max(0,delta),signed_change_bytes=delta,elapsed_ms=ms,
            bytes_per_second=max(0,delta)*1000/ms if ms>0 else None,
            first_sequence=first['sequence'],last_sequence=last['sequence'],
            first_year=first['year'],last_year=last['year']))
    return dict(samples=len(rows),sampled_peak={k:max(row[k] for row in rows) for k in SIZE_FIELDS},
        maximum_sample_gap_ms=max((d['elapsed_ms'] for d in differences),default=None),
        largest_observed_growth=max(differences,key=lambda d:d['growth_bytes'],default=None),
        limitation='Allocated-byte file-size snapshots, normally about ten seconds apart. '
            'They can miss temporary files and shorter bursts. Year context is the last received Java observation, '
            'not an atomic disk/year measurement. This is not kernel I/O bandwidth or a hard quota.')


def number(value,digits=2):
    return 'Unavailable' if value is None else f'{value:,.{digits}f}'


def table(headers,rows):
    return '<table><thead><tr>'+''.join('<th scope="col">'+escape(h)+'</th>' for h in headers)+\
        '</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+escape(str(v))+'</td>' for v in row)+'</tr>'
        for row in rows)+'</tbody></table>'


def scatter(profiles,metric,label):
    """Small offline SVG scatter plot; tooltips identify the year and horizon."""
    points=[];colours=('#155e9c','#b45309','#527a28','#8d3888','#146d65','#964c3d')
    legends=[]
    for index,profile in enumerate(profiles):
        colour=colours[index%len(colours)]
        name=f"{profile['population_requested']:,} initial; ends {profile['end_year']}; "\
            f"{profile['heap_limit_bytes']/GIB:g}/{profile['container_limit_bytes']/GIB:g} GiB heap/container"
        legends.append(f'<span style="color:{colour}">● {escape(name)}</span>')
        for rep in profile['repetitions']:
            for row in rep['annual']:
                x=row['max_observed_person_count']
                if metric=='memory':
                    values=[('heap',row['sampled_peaks']['heap_used_bytes']/GIB),
                        ('working RAM',row['sampled_peaks']['working_set_bytes']/GIB)]
                else:values=[('annual elapsed',row['elapsed_ms']/1000)]
                for kind,y in values:
                    points.append((x,y,colour,f"{name}; seed {rep['seed']}; year {row['year']}; {kind}: {y:.3f}",kind))
    if not points:return ''
    width,height=800,350;left,top,right,bottom=76,28,24,60
    xmax=max(max(p[0] for p in points)*1.05,1);ymax=max(max(p[1] for p in points)*1.10,1)
    plotw=width-left-right;ploth=height-top-bottom
    svg=[f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(label)}">',
        f'<title>{escape(label)}</title>']
    for i in range(6):
        x=left+plotw*i/5;y=top+ploth-ploth*i/5
        svg += [f'<path d="M{x:.2f} {top} V{top+ploth} M{left} {y:.2f} H{left+plotw}" stroke="#ddd"/>',
            f'<text x="{x:.2f}" y="{top+ploth+22}" text-anchor="middle">{xmax*i/5:,.0f}</text>',
            f'<text x="{left-10}" y="{y+5:.2f}" text-anchor="end">{ymax*i/5:.2f}</text>']
    for x,y,colour,title,kind in points:
        px=left+plotw*x/xmax;py=top+ploth-ploth*y/ymax
        if kind=='heap':
            svg.append(f'<circle cx="{px:.2f}" cy="{py:.2f}" r="3" fill="white" stroke="{colour}"><title>{escape(title)}</title></circle>')
        else:svg.append(f'<circle cx="{px:.2f}" cy="{py:.2f}" r="3" fill="{colour}" opacity=".7"><title>{escape(title)}</title></circle>')
    svg += [f'<text x="{left+plotw/2}" y="{height-8}" text-anchor="middle">Observed model persons, annual maximum</text>',
        f'<text x="18" y="{top+ploth/2}" text-anchor="middle" transform="rotate(-90 18 {top+ploth/2})">{escape(label)}</text>','</svg>']
    return ''.join(svg)+'<p class="legend">'+' · '.join(legends)+'</p>'


def write_reports(output,profiles):
    """All tables identify population, actual finishing year, repetition and units."""
    output=Path(output)
    fields=['population_requested','simulation_end_year','seed','year','max_observed_person_count','exported_person_rows',
        'elapsed_seconds','heap_peak_gib','working_peak_gib','average_vcpus','cpu_allowance_percent',
        'gc_counter_wall_percent','throttled_period_percent','annual_csv_gib','cumulative_csv_gib']
    data=[]
    for p in profiles:
        for rep in p['repetitions']:
            for row in rep['annual']:
                data.append(dict(population_requested=p['population_requested'],simulation_end_year=p['end_year'],
                    seed=rep['seed'],year=row['year'],max_observed_person_count=row['max_observed_person_count'],
                    exported_person_rows=row.get('exported_person_rows'),elapsed_seconds=row['elapsed_ms']/1000,
                    heap_peak_gib=row['sampled_peaks']['heap_used_bytes']/GIB,
                    working_peak_gib=row['sampled_peaks']['working_set_bytes']/GIB,**row['utilisation'],
                    annual_csv_gib=row.get('annual_csv_bytes',0)/GIB,cumulative_csv_gib=row.get('cumulative_csv_bytes',0)/GIB))
    text=io.StringIO();writer=csv.DictWriter(text,fieldnames=fields);writer.writeheader();writer.writerows(data)
    (output/'population-annual.csv').write_text(text.getvalue());os.chmod(output/'population-annual.csv',0o600)
    body=['<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<!-- (C) Copyright 2026, by Ross Richardson. Labelled population/year resource report. @author ross richardson -->',
        '<meta name="viewport" content="width=device-width, initial-scale=1"><title>Population and year resource profiles</title>',
        '<style>body{font:15px system-ui,sans-serif;max-width:1500px;margin:2em auto;padding:0 1em;color:#182332}',
        'table{border-collapse:collapse;width:100%;margin:1em 0;font-size:13px}th,td{padding:.5em;border:1px solid #ccd3dc;text-align:right}',
        'th{background:#e9eff7}thead{display:table-header-group}td:first-child,th:first-child{text-align:left}',
        'svg{max-width:850px;width:100%;background:#fff}svg text{font:13px system-ui,sans-serif}.scroll{overflow-x:auto}',
        '.legend{font-size:13px}section{margin-top:2.5em}h2{border-bottom:2px solid #ccd3dc;padding-bottom:.3em}',
        '@media print{body{margin:0;font-size:11px}tr{break-inside:avoid}h2,h3{break-after:avoid}}</style></head><body>',
        '<h1>Population and simulation-year resource profiles</h1>',
        '<p>Each case is a real simulation with the stated ending year. Rows show sampled demand under the tested allowance, '
        'not the minimum safe heap or a validated concurrency recommendation. Annual tables include output work after the model’s year-finish message.</p>',
        '<p><a href="population-annual.csv">Annual CSV</a> · <a href="population-resources.csv">Milestone CSV</a> · <a href="comparison.md">Milestone table</a></p>',
        '<h2>Memory and runtime versus observed population</h2>',
        '<p>Points are individual years; hover for year, seed and ending horizon. Open circles: heap. Filled circles: working RAM. '
        'Population counts are model collection sizes observed at annual boundaries, not exact maximum live populations. Association does not establish a sizing formula.</p>',
        scatter(profiles,'memory','Sampled memory peak (GiB)'),scatter(profiles,'time','Annual elapsed time (seconds)')]
    horizon_rows=[]
    for short in profiles:
        for long in profiles:
            if (short['population_requested']!=long['population_requested'] or short['end_year']>=long['end_year']
                    or any(short[k]!=long[k] for k in ('heap_limit_bytes','container_limit_bytes','cpu_allowance_millis'))):continue
            for rep in short['repetitions']:
                partner=next((r for r in long['repetitions'] if r['seed']==rep['seed']),None)
                milestone=next((r for r in partner['intervals'] if r['end_year']==short['end_year']),None) if partner else None
                if milestone is None:continue
                stop=short['whole_process']['sampled_peaks'];through=milestone['process_cumulative_peaks']
                horizon_rows.append([f"{short['population_requested']:,}",rep['seed'],short['end_year'],long['end_year'],
                    number(stop['heap_used_bytes']/GIB,3),number(stop['working_set_bytes']/GIB,3),
                    number(through['heap_used_bytes']/GIB,3),number(through['working_set_bytes']/GIB,3)])
    if horizon_rows:
        body+=['<h2>Ending earlier versus passing through the same year</h2>',
            '<p>The earlier-ending run includes its finalisation and shutdown sample. The longer run is measured only through the matching year. '
            'All use the same prepared input package within a population and the same seed. These compare observed demand, not minimum required memory.</p>',
            table(['Initial people','Seed','Earlier ending year','Longer ending year','Earlier run: peak heap (GiB)',
                'Earlier run: peak working RAM (GiB)','Longer run through earlier year: heap (GiB)',
                'Longer run through earlier year: working RAM (GiB)'],horizon_rows)]
    for p in profiles:
        name=f"{p['population_requested']:,} initial people; ending in {p['end_year']}"
        body+=['<section><h2>'+escape(name)+'</h2>',
            '<p>CPU allowance: '+number(p['cpu_allowance_millis']/1000)+' vCPUs; maximum heap: '+number(p['heap_limit_bytes']/GIB)+
            ' GiB; container: '+number(p['container_limit_bytes']/GIB)+' GiB. Memory observations are sampled every two seconds and at boundaries. '
            'Working RAM subtracts inactive file cache; it is not a guaranteed amount the kernel can immediately reclaim.</p>','<h3>Memory and time by phase</h3>']
        phase_rows=[]
        for label,row in p['phases'].items():
            u=row['utilisation']
            phase_rows.append([label.replace('_',' ').capitalize(),number(row['elapsed_ms']/1000),
                number(row['sampled_peaks']['heap_used_bytes']/GIB,3),number(row['sampled_peaks']['working_set_bytes']/GIB,3),
                number(row['sampled_peaks']['nonheap_used_bytes']/GIB,3),
                number(u['average_vcpus']),number(u['gc_counter_wall_percent'])])
        body.append(table(['Phase','Elapsed (s)','Peak heap (GiB)','Peak working RAM (GiB)','Peak Java non-heap (GiB)',
            'Mean vCPUs used','GC counter / wall (%)'],phase_rows))
        body.append('<p>Initialisation ends at the first year-start message. Between-repetition work includes cleanup and the next population build. '
            'Finalisation runs from the final repetition’s finish marker to the Java shutdown-hook sample. Phase endpoints overlap; short gaps and other shutdown hooks can lie outside phase measurements.</p>')
        ws=p['workspace'];burst=ws['largest_observed_growth']
        body+=['<h3>Working storage and observed growth</h3>',table(['Samples','Peak workspace (GiB)','Peak CSV files (GiB)',
            'Largest observed growth (GiB)','Over sampling interval (s)','Longest sampling gap (s)'],[[ws['samples'],
            number(ws['sampled_peak']['work_and_request_bytes']/GIB,3),number(ws['sampled_peak']['csv_bytes']/GIB,3),
            number(burst['growth_bytes']/GIB,3) if burst else 'Unavailable',number(burst['elapsed_ms']/1000) if burst else 'Unavailable',
            number(ws['maximum_sample_gap_ms']/1000) if ws['maximum_sample_gap_ms'] is not None else 'Unavailable']]),
            '<p>'+escape(ws['limitation'])+'</p>']
        for rep in p['repetitions']:
            annual=[]
            for row in rep['annual']:
                u=row['utilisation']
                annual.append([row['year'],f"{row['max_observed_person_count']:,}",f"{row['exported_person_rows']:,}",
                    number(row['elapsed_ms']/1000),number(row['sampled_peaks']['heap_used_bytes']/GIB,3),
                    number(row['sampled_peaks']['working_set_bytes']/GIB,3),number(u['average_vcpus']),
                    number(u['cpu_allowance_percent']),number(u['gc_counter_wall_percent']),number(u['throttled_period_percent']),
                    number(row['annual_csv_bytes']/GIB,3),number(row['cumulative_csv_bytes']/GIB,3)])
            body+=['<h3>Repetition '+str(rep['repetition'])+'; seed '+escape(rep['seed'])+'</h3><div class="scroll">',
                table(['Year','Observed persons, max','Exported person rows','Elapsed (s)','Peak heap (GiB)','Peak working RAM (GiB)',
                    'Mean vCPUs used','CPU allowance used (%)','GC counter / wall (%)','Quota periods throttled (%)',
                    'This year’s CSV (GiB)','CSV through this year (GiB)'],annual),'</div>']
        body+=['</section>']
    body+=['<h2>Interpretation</h2><ul><li>GC counter / wall sums Java collector counters. It is not a percentage of application time paused.</li>',
        '<li>CPU allowance used divides measured container CPU time by elapsed time and the configured quota. Quota-period throttling '
        'does not measure CPU competition with other customers on a shared VM.</li>',
        '<li>CSV sizes come from the post-run, checksum-verified streaming audit. They exclude database, input and temporary files; '
        'workspace peaks include those while they exist. No raw CSV is parsed by these additional measurements during simulation.</li>',
        '<li>Source inputs, model, seed and controls are frozen. Different ending horizons have genuine separate runs. '
        'One seed does not establish requirements for every permitted policy, population path or repetition count.</li></ul></body></html>']
    (output/'comparison.html').write_text('\n'.join(body)+'\n');os.chmod(output/'comparison.html',0o600)
