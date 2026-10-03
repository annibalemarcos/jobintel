"""CRUD browser regression using an isolated DB; never edits personal tracking."""
import copy
import json
import tempfile
import threading
import sys
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.request import urlopen, Request
from urllib.error import HTTPError

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from applications import ApplicationStore
from dashboard import Handler, ROOT
from playwright.sync_api import sync_playwright, expect


def main():
    output=ROOT / '.dashboard_qa'
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.tracking-browser-',dir=ROOT) as folder:
        dbpath=Path(folder) / 'test.sqlite3'
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        server.data_root=ROOT
        server.store=ApplicationStore(dbpath)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        url=f'http://127.0.0.1:{server.server_port}'
        errors=[]
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True)
                page=browser.new_page(viewport={'width':1440,'height':1100})
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.on('console',lambda msg:errors.append(msg.text) if msg.type=='error' else None)
                page.goto(url)
                print('Browser QA: dashboard carregado.',flush=True)
                expect(page.locator('#tbody tr')).to_have_count(5)
                page.locator('#auto').uncheck()
                expect(page.locator('#application-overview')).to_be_visible()
                assert page.locator('.pipeline-card[data-stage=applied] strong').inner_text()=='0'
                page.locator('[data-tab=jobs]').click()
                expect(page.locator('#tbody tr')).to_have_count(13)
                page.locator('#search').fill('staff frontend')
                expect(page.locator('#tbody tr')).to_have_count(1)
                page.locator('#tbody [data-detail]').click()
                page.locator('#app-status').select_option('applied')
                page.locator('#app-applied_at').fill('2026-10-03')
                page.locator('#app-notes').fill('Meu currículo v1. <script>not executed</script>')
                page.locator('#app-priority').select_option('high')
                page.locator('#app-follow_up_at').fill('2026-10-10')
                page.locator('#save-application').click()
                expect(page.locator('#application-dialog')).not_to_be_visible()
                expect(page.locator('#tbody')).to_contain_text('Apliquei')
                page.reload()
                expect(page.locator('#tbody')).to_contain_text('Apliquei')
                page.locator('#tbody [data-detail]').click()
                expect(page.locator('#app-notes')).to_have_value('Meu currículo v1. <script>not executed</script>')
                page.locator('#app-status').select_option('interview')
                page.locator('#app-interview_at').fill('2026-10-08')
                page.locator('#save-application').click()
                expect(page.locator('#application-dialog')).not_to_be_visible()
                expect(page.locator('#tbody')).to_contain_text('Entrevista')
                page.locator('#tbody [data-detail]').click()
                expect(page.locator('#application-content')).to_contain_text('Apliquei → Entrevista')
                page.locator('#app-status').select_option('rejected')
                page.locator('#app-rejection_stage').select_option('after_interview')
                page.locator('#app-rejection_reason').fill('Processo encerrado após entrevista.')
                page.locator('#save-application').click()
                expect(page.locator('#application-dialog')).not_to_be_visible()
                expect(page.locator('#tbody')).to_contain_text('Rejeitado')
                page.locator('#clear').click()
                page.locator('[data-tab=overview]').click()
                expect(page.locator('.pipeline-card[data-stage=applied] strong')).to_have_text('1')
                expect(page.locator('.pipeline-card[data-stage=interview] strong')).to_have_text('1')
                expect(page.locator('.pipeline-card[data-stage=rejected] strong')).to_have_text('1')
                page.screenshot(path=str(output / 'tracking-overview.png'),full_page=True)
                print('Browser QA: acompanhamento e histórico validados.',flush=True)
                page.locator('.pipeline-card[data-stage=interview]').click()
                expect(page.locator('#title')).to_have_text('Candidaturas')
                expect(page.locator('#tbody tr')).to_have_count(1)
                expect(page.locator('#tbody')).to_contain_text('Rejeitado')
                page.locator('#clear').click()
                page.locator('#new-application').click()
                page.locator('#app-url').fill('https://example.org/jobs/manual-test')
                page.locator('#app-title').fill('Engenheiro de teste manual')
                page.locator('#app-company').fill('Empresa Teste')
                page.locator('#app-domain').fill('example.org')
                page.locator('#app-status').select_option('saved')
                page.locator('#save-application').click()
                expect(page.locator('#application-dialog')).not_to_be_visible()
                expect(page.locator('#tbody tr')).to_have_count(2)
                page.locator('#scoreStatus').select_option('scored')
                expect(page.locator('#tbody tr')).to_have_count(0)
                page.locator('#clear').click()
                page.locator('#search').fill('teste manual')
                expect(page.locator('#tbody tr')).to_have_count(1)
                with page.expect_download() as download:
                    page.locator('#export').click()
                csv=Path(download.value.path()).read_text(encoding='utf-8-sig')
                assert 'Status candidatura' in csv and 'Score geral' in csv and 'Quero aplicar' in csv
                page.locator('#tbody [data-detail]').click()
                page.locator('#app-notes').fill('Cadastro editado')
                page.locator('#save-application').click()
                expect(page.locator('#application-dialog')).not_to_be_visible()
                page.locator('#tbody [data-detail]').click()
                expect(page.locator('#app-notes')).to_have_value('Cadastro editado')
                page.screenshot(path=str(output / 'tracking-form.png'),full_page=True)
                page.locator('#delete-application').click()
                expect(page.locator('#delete-confirmation')).to_be_visible()
                page.locator('#confirm-delete').click()
                expect(page.locator('#application-dialog')).not_to_be_visible()
                expect(page.locator('#tbody tr')).to_have_count(0)
                page.locator('#clear').click()
                page.locator('#tbody [data-detail]').click()
                page.locator('#delete-application').click()
                page.locator('#confirm-delete').click()
                expect(page.locator('#application-dialog')).not_to_be_visible()
                expect(page.locator('#tbody tr')).to_have_count(0)
                page.locator('[data-tab=jobs]').click()
                expect(page.locator('#tbody tr')).to_have_count(13)
                # The original jobs remain after deleting their personal tracking.
                assert server.store.snapshot()[0]=={}
                print('Browser QA: CRUD, filtros, CSV e exclusão validados.',flush=True)
                page.locator('[data-tab=overview]').click()
                page.set_viewport_size({'width':390,'height':844})
                page.screenshot(path=str(output / 'tracking-mobile.png'),full_page=True)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.locator('#new-application').click()
                page.screenshot(path=str(output / 'tracking-mobile-form.png'),full_page=True)
                print('Browser QA: capturas móveis concluídas.',flush=True)
                page.locator('#close-application').click()
                browser.close()
                print('Browser QA: layout móvel validado.',flush=True)
            request=Request(url+'/api/applications',data=b'{}',method='POST',headers={'Content-Type':'application/json'})
            try:urlopen(request)
            except HTTPError as error:assert error.code==403
            else:raise AssertionError('Cross-origin/untrusted request must be rejected')
            assert not errors,errors
            assert ApplicationStore(dbpath).snapshot()[0]=={}
            print('Tracking browser checks passed: create, edit, milestones, history, persistence, manual jobs, filters, CSV, deletion, mobile and write-origin validation.')
        finally:
            print('Browser QA: encerrando servidor temporário.',flush=True)
            server.shutdown();server.server_close();thread.join()


if __name__=='__main__':main()
