"""(C) Copyright 2026, by Ross Richardson
Check the hosted Visualiser's return link in Chromium at desktop and phone widths.
@author ross richardson
"""
import argparse
from pathlib import Path
import re
from urllib.parse import urlsplit


def luminance(rgb):
    channels = [int(value) / 255 for value in re.fullmatch(r'rgb\((\d+), (\d+), (\d+)\)', rgb).groups()]
    linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
    return sum(c * weight for c, weight in zip(linear, (.2126, .7152, .0722)))


def check(build):
    from playwright.sync_api import expect, sync_playwright

    origin = 'http://visualiser.test'
    experiment = '00000000-0000-4000-8000-000000000001'
    path = '/visualiser/experiment/' + experiment

    def serve(route):
        url = urlsplit(route.request.url)
        if url.netloc != 'visualiser.test':
            route.abort()
        elif url.path == path:
            route.fulfill(path=build / 'index.html')
        elif url.path.startswith('/visualiser-assets/'):
            route.fulfill(path=build / Path(url.path).name)
        elif url.path == '/api/visualiser-experiments/' + experiment:
            route.fulfill(json=dict(format='simpaths.visualiser.experiment.v1', experiment=experiment, url=path,
                ready=0, total=1, pending=True, retryable=False, publication=None, configurations=[dict(id=experiment, configuration='first',
                name='First', state='succeeded', charts='queued', comparison='not_applicable')]),
                headers={'X-Visualiser-Cache-Scope': 'a' * 64})
        elif url.path == '/':
            route.fulfill(body='SimPaths Online', content_type='text/html')
        else:
            route.fulfill(status=204)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for width in (1440, 768, 390, 320):
                page = browser.new_page(viewport=dict(width=width, height=900))
                page.route('**/*', serve)
                page.goto(origin + path)
                expect(page.locator('.vm-source-message')).to_contain_text('Charts will appear here')
                link = page.locator('a.vm-return')
                expect(link).to_have_accessible_name('Return to SimPaths Online')
                expect(link).to_have_attribute('href', '/')
                expect(link).to_be_in_viewport()
                logo = link.locator('..').get_by_alt_text('SimPaths logo')
                expect(logo).to_be_in_viewport()
                button, brand = link.bounding_box(), logo.bounding_box()
                assert button['height'] >= 44 and 0 <= button['x'] <= width - button['width']
                if width > 760:
                    assert button['x'] + button['width'] <= brand['x']
                else:
                    assert brand['y'] + brand['height'] <= button['y']
                for hover in (False, True):
                    if hover:
                        link.hover()
                    colors = link.evaluate('(a) => [getComputedStyle(a).color, getComputedStyle(a).backgroundColor]')
                    dark, light = sorted(map(luminance, colors))
                    assert (light + .05) / (dark + .05) >= 4.5
                page.keyboard.press('Tab')
                link.focus()
                assert link.evaluate("a => a.matches(':focus-visible') && getComputedStyle(a).outlineStyle === 'solid'")
                page.keyboard.press('Enter')
                expect(page).to_have_url(origin + '/')
                page.close()
                print(f'Passed header contrast, layout and keyboard return at {width}px.', flush=True)
        finally:
            browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('build', type=Path)
    check(parser.parse_args().build)
