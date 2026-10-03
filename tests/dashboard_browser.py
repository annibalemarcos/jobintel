"""End-to-end smoke checks against the running local dashboard."""
import copy
import json
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright, expect

URL = 'http://127.0.0.1:8765'
OUTPUT = Path(__file__).resolve().parents[1] / '.dashboard_qa'


def main():
    OUTPUT.mkdir(exist_ok=True)
    with urlopen(URL + '/api/data') as response:
        original = json.load(response)
    assert len(original['records']) == 5
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width':1440,'height':1100}, device_scale_factor=1)
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
        page.goto(URL)
        expect(page.locator('#tbody tr')).to_have_count(5)
        page.locator('#auto').uncheck()
        page.screenshot(path=str(OUTPUT / 'desktop.png'), full_page=True)
        page.locator('[data-tab=jobs]').click()
        assert page.locator('#tbody tr').count() == 13
        page.locator('#search').fill('engineer')
        assert page.locator('#tbody tr').count() == 7
        with page.expect_download() as download:
            page.locator('#export').click()
        text = Path(download.value.path()).read_text(encoding='utf-8-sig')
        assert text.count('\n') == 7  # header plus seven rows, no final newline
        page.locator('#clear').click()
        page.locator('[data-tab=emails]').click()
        assert page.locator('#tbody tr').count() == 6
        page.locator('#advanced-toggle').click()
        page.locator('#email').fill('support@')
        assert page.locator('#tbody tr').count() == 2
        page.locator('#clear').click()
        page.locator('[data-tab=companies]').click()
        page.locator('#status').select_option('OK_BROWSER')
        assert page.locator('#tbody tr').count() == 1, page.locator('#result-count').inner_text()
        assert 'Aerodrome' in page.locator('#tbody').inner_text()
        page.locator('#clear').click()
        page.locator('#from').fill('2026-10-04')
        assert page.locator('#empty').is_visible()
        page.locator('#clear').click()
        page.locator('#hasJobs').select_option('yes')
        assert page.locator('#tbody tr').count() == 2
        page.locator('#tbody [data-detail]').first.click()
        assert page.locator('#detail').is_visible()
        assert 'Evidências' in page.locator('#detail-content').inner_text()
        page.locator('#close-detail').click()
        page.locator('#clear').click()
        page.locator('[data-tab=runs]').click()
        assert page.locator('#tbody tr').count() == 1, page.locator('#notice').inner_text() + page.locator('#result-count').inner_text() + page.locator('#tbody').inner_text()
        page.locator('#tbody [data-detail]').click()
        page.locator('#use-run').click()
        assert page.locator('#title').inner_text() == 'Empresas'
        assert page.locator('#tbody tr').count() == 5
        page.locator('#clear').click()
        page.locator('[data-tab=overview]').click()
        page.locator('#advanced-toggle').click()
        page.set_viewport_size({'width':390,'height':844})
        page.screenshot(path=str(OUTPUT / 'mobile.png'), full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')

        # Duplicate the real run in memory only; original crawler outputs stay untouched.
        mocked = copy.deepcopy(original)
        older = copy.deepcopy(original['records'])
        for row in older:
            row.update(id='older#'+row['domain'],run_id='older',date='2026-10-01T12:00:00')
        mocked['records'].extend(older)
        old_run = copy.deepcopy(original['runs'][0])
        old_run.update(id='older',date='2026-10-01T12:00:00')
        mocked['runs'].append(old_run)
        context = browser.new_context(viewport={'width':1440,'height':1000})
        context.route('**/api/data', lambda route: route.fulfill(json=mocked))
        second = context.new_page()
        second.on('pageerror', lambda e: errors.append(str(e)))
        second.goto(URL)
        expect(second.locator('#tbody tr')).to_have_count(5)
        assert '5 repetidos ocultos' in second.locator('#result-count').inner_text()
        second.locator('.switch-label').click()
        assert second.locator('#tbody tr').count() == 10
        second.locator('.switch-label').click()
        second.locator('#keep').select_option('earliest')
        assert 'older' in second.locator('#tbody').inner_text()
        second.locator('#run').select_option('older')
        assert second.locator('#tbody tr').count() == 5
        second.locator('[data-tab=jobs]').click()
        assert second.locator('#tbody tr').count() == 13
        second.reload()
        expect(second.locator('#tbody tr')).to_have_count(13)
        assert second.locator('#title').inner_text() == 'Vagas'
        assert second.locator('#run').input_value() == 'older'
        browser.close()
    assert not errors, errors
    print('Browser checks passed: real data, search, filters, export, details, runs, duplicates, URL persistence and mobile layout.')


if __name__ == '__main__':
    main()
