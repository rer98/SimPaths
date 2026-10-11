#!/usr/bin/env python3
# (C) Copyright 2026, by Ross Richardson
# Index an explicitly verified saved publication and measure selected chart reads.
# No model executions, raw CSV copies, database or network access are required.
# @author ross richardson
import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend',type=Path,required=True)
    parser.add_argument('--publication',type=Path,required=True)
    parser.add_argument('--sha256',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():parser.error('Choose a new output directory')
    sys.path.insert(0,str(args.frontend.resolve(strict=True)))
    from jasmine_web.batch.aggregate_io import invoke
    from jasmine_web.batch.aggregate_limits import AggregateLimits
    from jasmine_web.batch.aggregate_sections import compact,section_key,VIEW_ROWS,VIEW_LIMIT
    source=args.publication.resolve(strict=True)
    if source.stat().st_size>256*1024**2:raise ValueError('Publication exceeds the trusted proof bound')
    raw=source.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=args.sha256:raise ValueError('Saved publication checksum differs')
    public=json.loads(raw);del raw
    args.output.mkdir(mode=0o700,parents=True);views=args.output/'views';views.mkdir(mode=0o700)
    (args.output/'COPYRIGHT.md').write_text(
        '<!-- (C) Copyright 2026, by Ross Richardson\n'
        'Generated chart-section indexing and delivery evidence.\n'
        '@author ross richardson\n-->\n\n'
        'This notice attributes the generated index, catalogue, chart-response and '
        'test-report artifacts; it does not relicense source data or upstream code.\n')
    limits=AggregateLimits(96*1024**2,256*1024**2,200000,400000)
    def run(argv,*,pass_fds):
        with (args.output/'private-helper.log').open('ab') as log:
            subprocess.run(argv,pass_fds=pass_fds,stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                timeout=120,check=True,env={'PATH':os.defpath,'LANG':'C.UTF-8'})
    directory=os.open(views,os.O_RDONLY|os.O_DIRECTORY)
    try:
        started=time.monotonic()
        with source.open('rb') as stream:
            index=json.loads(invoke(dict(operation='index',format=public['format'],memory_mib=3072,
                publication_limits=limits.describe(),sha256=args.sha256),(stream.fileno(),directory),run))
        indexing=time.monotonic()-started
        request=dict(memory_mib=512,publication_limits=limits.describe(),sha256=args.sha256,
                     index_sha256=index['index_sha256'])
        with (views/'index.json').open('rb') as stream:
            cat_bytes=invoke(dict(request,operation='catalogue'),(stream.fileno(),directory),run)
            cat=json.loads(cat_bytes)
            (args.output/'catalogue.json').write_bytes(cat_bytes)
            groups=defaultdict(list)
            for series in public['data']['series']:
                for row in series['rows']:
                    kind='levels' if row['metric_type'] in ('mean','share') else row['metric_type']
                    groups[(series['configuration'],row['variable'],row['stratifier'],kind)].append(row)
            # Byte-for-byte row parity for every section, including paired
            # estimates, suppression/nulls, original order and all 52 years.
            for identity,rows in groups.items():
                actual=(views/(section_key(*identity)+'.json')).read_bytes()
                if actual!=compact(rows):raise AssertionError('Indexed rows differ from their original values')
            samples=[]
            choices=[('Highest Level of Education','Overall','levels'),
                ('Mental Component Summary (MCS)','Overall','levels'),
                ('Mental Component Summary (MCS)','Gender','levels'),
                ('Hourly earnings','Overall','wage_bin'),('Age','Gender','pyramid_bin')]
            for variable,stratifier,kind in choices:
                selection=dict(variable=variable,stratifier=stratifier,kind=kind,
                    configurations=[c['id'] for c in cat['configurations']])
                start=time.monotonic()
                body=invoke(dict(request,operation='view',selection=selection),(stream.fileno(),directory),run)
                elapsed=time.monotonic()-start;value=json.loads(body)
                if 'series' not in value:raise AssertionError('Representative chart view was not available')
                count=sum(len(s['rows']) for s in value['series'])
                if count>VIEW_ROWS or len(body)>VIEW_LIMIT:raise AssertionError('Chart bounds differ')
                filename='sample-'+str(len(samples))+'.json';(args.output/filename).write_bytes(body)
                samples.append(dict(variable=variable,stratifier=stratifier,kind=kind,bytes=len(body),rows=count,
                    helper_seconds=round(elapsed,4),file=filename))
        report=dict(passed=True,source_sha256=args.sha256,source_bytes=source.stat().st_size,
            no_models=True,no_raw_copies=True,all_sections_equal=True,sections=len(groups),
            total_rows=sum(len(rows) for rows in groups.values()),catalogue_bytes=len(cat_bytes),
            indexing_seconds=round(indexing,4),index=index,samples=samples)
        (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))
    finally:os.close(directory)


if __name__=='__main__':main()
