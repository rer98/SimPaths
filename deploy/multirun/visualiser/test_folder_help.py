#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson
Verify the compiled folder tooltip's real Chromium geometry at responsive widths.
Uses an empty offline Visualiser; no application sessions, data or HTTP server.
@author ross richardson
"""
import argparse
import hashlib
import json
from pathlib import Path


def execute(build,output):
    from playwright.sync_api import sync_playwright,expect
    output.mkdir(parents=True,mode=0o700,exist_ok=False)
    report=dict(passed=False,offline=True,checks=[],browser_errors=[],source_files={
        name:hashlib.sha256((build/name).read_bytes()).hexdigest()
        for name in ('visualiser.css','visualiser.js','build.json')})
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch()
            try:
                context=browser.new_context(viewport=dict(width=1920,height=1000))
                context.route('**/*',lambda route:route.abort())
                page=context.new_page()
                page.on('pageerror',lambda error:report['browser_errors'].append(str(error)))
                page.set_content('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                    '<meta name="viewport" content="width=device-width,initial-scale=1">'
                    '</head><body><div id="root"></div></body></html>')
                page.add_style_tag(path=str(build/'visualiser.css'))
                page.add_script_tag(path=str(build/'visualiser.js'))
                help=page.get_by_role('button',name='View folder structure',exact=True)
                expect(help).to_be_visible()
                for width in (1920,1280,768,360,320):
                    page.set_viewport_size(dict(width=width,height=1000))
                    help.hover()
                    tooltip=page.get_by_role('tooltip')
                    expect(tooltip).to_be_visible()
                    page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                    boxes=help.evaluate('''button=>{
                        const rect=e=>{const b=e.getBoundingClientRect();return {left:b.left,right:b.right,top:b.top,bottom:b.bottom};};
                        return {button:rect(button),anchor:rect(button.parentElement),
                            tooltip:rect(document.querySelector('.vm-folder-tree')),width:innerWidth};
                    }''')
                    a,t=boxes['anchor'],boxes['tooltip']
                    button=boxes['button']
                    assert abs((t['left']+t['right']-button['left']-button['right'])/2)<1.5,boxes
                    assert 6<=t['top']-a['bottom']<=10,boxes
                    assert t['top']>=button['bottom'],boxes
                    assert t['left']>=0 and t['right']<=width,boxes
                    assert 'Your Data Folder/' in tooltip.inner_text()
                    page.screenshot(path=str(output/('folder-help-'+str(width)+'.png')),full_page=True)
                    # Keyboard focus opens the same anchored help and Escape closes it.
                    page.mouse.move(0,0)
                    help.focus();expect(tooltip).to_be_visible()
                    help.press('Escape');expect(tooltip).to_have_count(0)
                    help.evaluate('button=>button.blur()')
                    report['checks'].append(dict(width=width,passed=True,geometry=boxes))
                    print('PASS: folder help is directly below its link and within the '+str(width)+'px viewport',flush=True)
                assert not report['browser_errors'],report['browser_errors']
                report['passed']=True
            finally:browser.close()
    except BaseException as error:
        report['error_type']=type(error).__name__;report['error']=str(error)
        raise
    finally:
        (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        (output/'COPYRIGHT.md').write_text('<!-- (C) Copyright 2026, by Ross Richardson\n'
            'Generated offline folder-tooltip geometry and screenshot evidence.\n@author ross richardson\n-->\n')
    print('PASSED: '+str(output/'report.json'),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();execute(args.build,args.output)


if __name__=='__main__':main()
