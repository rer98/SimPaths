#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Private live approval of Visualiser disk ceilings; never starts/stops service
processes, deletes files, contacts Docker or sends email.
@author ross richardson
"""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    location=parser.add_mutually_exclusive_group(required=True)
    location.add_argument('--config',type=Path,help='Existing private VM TOML configuration')
    location.add_argument('--state',type=Path,help='Existing private MultiRun state')
    parser.add_argument('--frontend',type=Path,help='JAS-mine-web checkout for laptop use')
    parser.add_argument('--maximum-mib',type=int,help='Raise approved total storage, up to 131072 MiB')
    parser.add_argument('--summary-maximum-mib',type=int,help='Raise the summary ceiling inside that total, up to 65536 MiB')
    args=parser.parse_args(argv)
    from deploy._workflow import frontend_path
    sys.path.insert(0,str(args.frontend or frontend_path()))
    if args.config:
        from deploy.multirun.vm_config import load_config
        state=load_config(args.config).state
    else:state=args.state
    from deploy.multirun.releases import existing_directory
    existing_directory(state)
    root=Path(state)/'execution/visualiser-cache'
    from jasmine_web.batch.visualiser_storage import approve,budget
    for value in (args.maximum_mib,args.summary_maximum_mib):
        if value is not None and value<1:parser.error('Storage ceilings must be positive MiB')
    if args.summary_maximum_mib is not None and args.summary_maximum_mib>65536:
        parser.error('Summary storage cannot exceed 65536 MiB')
    if args.maximum_mib is not None or args.summary_maximum_mib is not None:
        result=approve(root,
            maximum_bytes=args.maximum_mib*1024**2 if args.maximum_mib is not None else None,
            summary_maximum_bytes=args.summary_maximum_mib*1024**2 if args.summary_maximum_mib is not None else None)
    else:result=budget(root)
    print(json.dumps(result,indent=2,sort_keys=True))
    return 0


if __name__=='__main__':raise SystemExit(main())
