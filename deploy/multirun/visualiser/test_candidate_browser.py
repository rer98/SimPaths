"""(C) Copyright 2026, by Ross Richardson
Open the pinned Visualiser in Chromium with fictional prepared results and the hosted guidance pages.
@author ross richardson
"""
import argparse
import json
from pathlib import Path
import re
from urllib.parse import urlsplit, parse_qs

ORIGIN='https://visualiser.test'
KEY='a'*64
PUBLICATION='b'*64
VARIABLE='Mental Component Summary (MCS)'
CONFIGS=[dict(id='base',role='Baseline',name='Reference',key='baseline'),
         dict(id='a',role='Scenario',name='Policy A',key='scenario_1')]
CATALOGUE=dict(format='simpaths.visualiser.catalogue.v1',publication=PUBLICATION,
    configurations=CONFIGS,seeds=['606','607','608'],comparison_available=True,notice='Fixture',
    limits=dict(response_bytes=8*1024**2,response_rows=20000),variables=[dict(name=VARIABLE,module='Health',
        years=[2019,2020],views=[dict(stratifier='Overall',kind='levels',bytes=4000,rows=4)])])


def row(identifier,year):
    baseline=identifier=='base'
    value=(40 if baseline else 45) + year-2019
    return dict(year=year,scenario='baseline' if baseline else 'scenario',module='Health',variable=VARIABLE,
        variable_value='Continuous Mean',stratifier='Overall',stratifier_value='Overall',metric_type='mean',
        n_runs=3,total_sample=300,min_sample=100,mean_sample=100,mean_value=value,sd_value=1,lower_ci=value-1,
        upper_ci=value+1,paired_mean_delta=None if baseline else 5,paired_lower_ci=None if baseline else 4,
        paired_upper_ci=None if baseline else 6,paired_n_runs=0 if baseline else 3)


def check(build):
    from playwright.sync_api import sync_playwright, expect
    issues=[]
    revision=None
    deleted=0
    def serve(route):
        url=urlsplit(route.request.url)
        if url.netloc=='visualiser.test':
            if url.path=='/visualiser/'+KEY:
                return route.fulfill(path=build/'index.html',content_type='text/html')
            if url.path.startswith('/visualiser-assets/'):
                name=url.path.removeprefix('/visualiser-assets/')
                if name=='interpreting-results.html':
                    name='Interpreting-results.html'
                if re.fullmatch(r'[A-Za-z0-9_.-]+',name) and (build/name).is_file():
                    return route.fulfill(path=build/name)
            if url.path=='/static/batch_theme.js':
                return route.fulfill(body='',content_type='application/javascript')
            if url.path=='/static/simpaths-favicon.svg':
                return route.fulfill(body='<svg xmlns="http://www.w3.org/2000/svg"/>',content_type='image/svg+xml')
            if url.path=='/api/visualiser/'+KEY:
                return route.fulfill(json={'recalculation':dict(available=deleted==0,deleted=deleted,
                    configurations=['base','a'],state='available')})
            if url.path=='/api/session':return route.fulfill(json={'signed_in':True,'csrf':'fixture'})
            if url.path=='/api/visualiser/'+KEY+'/recalculate':
                assert route.request.method=='POST' and route.request.headers['x-csrf-token']=='fixture'
                return route.fulfill(json={'state':'pending'})
            if url.path=='/api/visualiser/'+KEY+'/catalogue':
                return route.fulfill(json=(dict(CATALOGUE,calculator_revision=revision) if revision else CATALOGUE))
            if url.path=='/api/visualiser/'+KEY+'/view':
                p=parse_qs(url.query)
                selected=p['configuration']
                return route.fulfill(json=dict(format='simpaths.visualiser.view.v1',publication=PUBLICATION,
                    selection=dict(variable=p['variable'][0],stratifier=p['stratifier'][0],kind=p['kind'][0],
                                   configurations=selected),series=[
                        dict(configuration=i,rows=[row(i,y) for y in (2019,2020)]) for i in selected]))
        issues.append('Unexpected request '+url.path)
        route.abort()

    with sync_playwright() as playwright:
        browser=playwright.chromium.launch()
        try:
            page=browser.new_page(viewport=dict(width=1440,height=900))
            page.route('**/*',serve)
            page.on('pageerror',lambda exc:issues.append(str(exc)))
            page.goto(ORIGIN+'/visualiser/'+KEY)
            expect(page.get_by_role('tab',name='Visualiser',exact=True)).to_have_attribute('aria-selected','true')
            page.get_by_role('button',name=re.compile(r'^Health\s*▼$')).click()
            page.get_by_role('button',name=VARIABLE,exact=True).click()
            expect(page.locator('svg path[fill="none"][stroke-dasharray]:not([stroke-dasharray="none"])').first).to_be_visible()
            page.get_by_role('button',name='Δ Baseline → Scenario',exact=True).click()
            expect(page.get_by_text('95% uncertainty intervals use paired run differences',exact=False).first).to_be_visible()
            page.get_by_role('tab',name='Interpreting Results').click()
            page.locator('.sp-page[data-page="interpreting-results.html"] a[href="citation.html"]').first.click()
            expect(page.locator('.sp-page[data-page="citation.html"] .container')).to_be_visible()
            page.get_by_role('tab',name='Funders & Partners').click()
            expect(page.locator('.sp-page[data-page="funders.html"] img[src$="UKRILogo.png"]')).to_be_visible()
            expect(page.get_by_text('These charts use an earlier calculation method.')).to_have_count(0)
            manifest=json.loads((build/'build.json').read_text())
            for edition in manifest['historical_editions']:
                revision=edition['revision']
                deleted=0
                page.goto('about:blank')
                page.goto(ORIGIN+'/visualiser/'+KEY)
                page.get_by_role('button',name=re.compile(r'^Health\s*▼$')).click()
                page.get_by_role('button',name=VARIABLE,exact=True).click()
                expect(page.locator('svg path[fill="none"][stroke-dasharray]:not([stroke-dasharray="none"])').first).to_be_visible()
                page.get_by_role('button',name='Δ Baseline → Scenario',exact=True).click()
                expect(page.get_by_text('95% uncertainty intervals use paired run differences',exact=False).first).to_be_visible()
                page.get_by_role('button',name='Recalculate with the latest method').click()
                expect(page.get_by_text('Recalculation requested.',exact=False)).to_be_visible()
                for deleted,words in [(1,'deleted for some selected configurations'),(2,'these summaries can no longer be recalculated')]:
                    page.reload()
                    expect(page.locator('.vm-calculation-notice')).to_contain_text(words)
                    expect(page.get_by_role('button',name='Recalculate with the latest method')).to_have_count(0)
            if issues:
                raise AssertionError(issues)
        finally:
            browser.close()
    count = len(manifest['historical_editions'])
    print(f'Chromium opened the current charts, paired comparison and guidance tabs. '
          f'Historical chart readers tested: {count}.',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build",type=Path)
    check(parser.parse_args().build)
