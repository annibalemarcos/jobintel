import csv
import io
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import urlopen
from unittest.mock import Mock, patch

from applications import ApplicationStore
import dashboard
from dashboard import Handler, collect


class DashboardDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='.dashboard-tests-', dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_jobs_filter_controls_are_declared_once_and_queue_controls_remain(self):
        markup=(Path(dashboard.__file__).resolve().parent/'dashboard_assets'/'index.html').read_text(encoding='utf-8')
        for control_id in ('preMatchStatus','qualityStatus','eligibilityStatus','recommendationStatus',
                           'userDecisionStatus','applicationStatus','priority','scoreStatus','hasResponse',
                           'reviewQueueStatus','desiredRoleStatus','sort','dedup','keep','auto','advanced','advanced-toggle'):
            with self.subTest(control_id=control_id):
                self.assertEqual(markup.count(f'id="{control_id}"'),1)
        self.assertEqual(markup.count('id="review-queue-filter"'),1)

    def test_desired_role_filter_is_declared_for_jobs_and_uses_existing_profile_api(self):
        root=Path(dashboard.__file__).resolve().parent/'dashboard_assets'
        markup=(root/'index.html').read_text(encoding='utf-8')
        source=(root/'app.js').read_text(encoding='utf-8')
        self.assertEqual(markup.count('id="desired-role-filter"'),1)
        self.assertIn('<option value="compatible">Compatível</option>',markup)
        self.assertIn('<option value="incompatible">Não compatível</option>',markup)
        self.assertIn("$('desired-role-filter').hidden=tab!=='jobs'",source)
        self.assertIn("fetch('/api/profile'",source)
        self.assertIn('setDesiredRoles(value.profile?.desired_roles)',source)

    def test_cold_email_filter_is_companies_only_dynamic_and_reuses_contacts_cell(self):
        root=Path(dashboard.__file__).resolve().parent/'dashboard_assets'
        markup=(root/'index.html').read_text(encoding='utf-8')
        source=(root/'app.js').read_text(encoding='utf-8')
        self.assertEqual(markup.count('id="coldEmailStatus"'),1)
        self.assertEqual(markup.count('id="cold-email-filter"'),1)
        for value,label in (('opportunity','Oportunidade de cold email'),('careers','Com página de carreiras/vagas'),('no_email','Sem e-mail')):
            self.assertIn(f'<option value="{value}">{label}</option>',markup)
        self.assertIn("$('cold-email-filter').hidden=tab!=='companies'",source)
        self.assertIn('updateColdEmailCounts(f)',source)
        self.assertIn('bestContactEmail(r.emails)',source)
        self.assertIn("page=1;render();",source)

    def test_jobs_table_sortable_columns_keep_tracking_action_third(self):
        root=Path(dashboard.__file__).resolve().parent
        source=(root/'dashboard_assets'/'app.js').read_text(encoding='utf-8')
        columns="['Vaga / candidatura','Empresa','Acompanhar','Score de perfil','Recomendação','Minha decisão','Status da candidatura','Pré-match','Qualidade da descrição','Elegibilidade IA','Prioridade','Apliquei em','Próximo retorno','Execução']"
        self.assertIn(f"tab === 'jobs' ? {columns}",source)
        self.assertIn('company(r),...(tab===\'jobs\'?[`<button class="detail-button" data-detail="${i}">Acompanhar →</button>`',source)
        self.assertIn("jobTab ? openApplication(rows[Number(b.dataset.detail)])",source)
        self.assertIn('aria-sort="${direction}"',source)
        self.assertIn("$('thead').querySelectorAll('[data-sort-first]')",source)
        self.assertTrue((root/'dashboard_assets'/'table_sort.css').is_file())

    def test_current_public_branding_uses_jobintel(self):
        markup=(Path(dashboard.__file__).resolve().parent/'dashboard_assets'/'index.html').read_text(encoding='utf-8')
        self.assertIn('<title>JobIntel · Dashboard</title>',markup)
        self.assertIn('aria-label="JobIntel início"',markup)
        self.assertIn('>JobIntel<small>JOB SEARCH</small>',markup)
        self.assertIn('JOB SEARCH &amp; APPLICATION INTELLIGENCE',markup)
        self.assertNotIn('CRYPTO & WEB3',markup)

    def make_response_handler(self, write_error=None):
        handler=Handler.__new__(Handler)
        handler.send_response=Mock()
        handler.send_header=Mock()
        handler.end_headers=Mock()
        handler.wfile=SimpleNamespace(write=Mock(side_effect=write_error))
        return handler

    def test_response_silently_stops_for_client_disconnects(self):
        for error in (BrokenPipeError(),ConnectionResetError(),ConnectionAbortedError()):
            with self.subTest(error=type(error).__name__):
                handler=self.make_response_handler(error)
                handler.respond(b'{"ok":true}','application/json')
                handler.send_response.assert_called_once_with(200)
                handler.wfile.write.assert_called_once_with(b'{"ok":true}')

    def test_client_disconnect_during_headers_is_also_suppressed(self):
        for method in ('send_response','send_header','end_headers'):
            with self.subTest(method=method):
                handler=self.make_response_handler()
                getattr(handler,method).side_effect=ConnectionAbortedError()
                handler.respond(b'body','text/plain')
                handler.wfile.write.assert_not_called()

    def test_unrelated_response_error_is_not_silently_swallowed(self):
        handler=self.make_response_handler(ValueError('invalid response'))
        with self.assertRaisesRegex(ValueError,'invalid response'):
            handler.respond(b'body','text/plain')

    def test_get_does_not_attempt_second_response_after_write_disconnect(self):
        handler=self.make_response_handler(BrokenPipeError())
        handler.path='/api/data'
        handler.server=SimpleNamespace(data_root=self.root,
                                       store=SimpleNamespace(get_profile=lambda:{'profile':{}}))
        with patch.object(dashboard,'collect',return_value={}), \
             patch.object(dashboard,'enrich',side_effect=lambda data,store:data), \
             patch.object(dashboard,'add_prematch_to_data',side_effect=lambda data,store:data), \
             patch.object(dashboard,'attach_job_description_quality',side_effect=lambda data,store:data), \
             patch.object(dashboard,'attach_ai_analysis_eligibility',side_effect=lambda data,profile,store:data), \
             patch.object(dashboard,'attach_profile_fit',side_effect=lambda data,store:data):
            handler.do_GET()
        handler.send_response.assert_called_once_with(200)
        handler.wfile.write.assert_called_once()

    def test_post_uses_same_disconnect_protection(self):
        handler=self.make_response_handler(ConnectionResetError())
        handler.path='/api/pre-match'
        handler.headers={'Host':'localhost','Origin':'http://localhost','X-Site-Intel':'dashboard',
                         'Content-Length':'11','Content-Type':'application/json'}
        handler.rfile=io.BytesIO(b'{"urls":[]}')
        handler.server=SimpleNamespace(data_root=self.root,store=object())
        with patch.object(dashboard,'run_pre_match',return_value={'ok':True}):
            handler.do_POST()
        handler.send_response.assert_called_once_with(200)
        handler.wfile.write.assert_called_once()

    def test_normal_response_is_unchanged(self):
        handler=self.make_response_handler()
        handler.respond(b'payload','text/plain; charset=utf-8',201,{'X-Test':'yes'})
        handler.send_response.assert_called_once_with(201)
        self.assertEqual(handler.send_header.call_args_list[0].args,('Content-Type','text/plain; charset=utf-8'))
        self.assertIn(('Content-Length','7'),[call.args for call in handler.send_header.call_args_list])
        self.assertIn(('X-Test','yes'),[call.args for call in handler.send_header.call_args_list])
        handler.end_headers.assert_called_once_with()
        handler.wfile.write.assert_called_once_with(b'payload')

    def test_manual_decision_api_persists_without_marking_application_applied(self):
        handler=self.make_response_handler()
        handler.path='/api/application-decision'
        raw=json.dumps({'url':'https://jobs.example/role','decision':{
            'user_decision':'want_to_apply','reason':'career_value','note':'Explorar transição.','version':0}}).encode()
        handler.headers={'Host':'localhost','Origin':'http://localhost','X-Site-Intel':'dashboard',
                         'Content-Length':str(len(raw)),'Content-Type':'application/json'}
        handler.rfile=io.BytesIO(raw)
        store=ApplicationStore(self.root/'.dashboard_data'/'applications.sqlite3')
        handler.server=SimpleNamespace(store=store)
        handler.do_POST()
        self.assertEqual(handler.send_response.call_args.args,(200,))
        saved=json.loads(handler.wfile.write.call_args.args[0])
        self.assertEqual(saved['user_decision'],'want_to_apply')
        self.assertEqual(saved['reason'],'career_value')
        self.assertEqual(saved['note'],'Explorar transição.')
        self.assertEqual(store.snapshot()[0],{})

    def write_csv(self, folder, filename, fields, rows):
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / filename).open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def test_all_and_valid_are_not_double_counted_and_jobs_preserve_pairs(self):
        folder = self.root / 'output' / '2026-10-03_03-57-02'
        fields = ['Domain', 'Alive', 'Job URLs', 'Job Titles', 'Pages Crawled']
        rows = [{'Domain': 'aave.com', 'Alive': 'YES', 'Job URLs': 'https://aave.com/jobs/1',
                 'Job Titles': 'a ; b', 'Pages Crawled': '45'}]
        for name in ['results_all.csv', 'results_valid.csv']:
            self.write_csv(folder, name, fields, rows)
        self.write_csv(folder, 'jobs.csv', ['Domain', 'Job URL', 'Job Title'],
                       [{'Domain': 'aave.com', 'Job URL': 'https://aave.com/jobs/1', 'Job Title': 'a ; b'}])
        data = collect(self.root)
        self.assertEqual(len(data['records']), 1)
        self.assertEqual(data['records'][0]['jobs'][0]['title'], 'a ; b')
        self.assertEqual(data['runs'][0]['date'], '2026-10-03T03:57:02')
        self.assertIsNone(data['records'][0]['ai'])

    def test_runs_remain_separate_and_broken_metadata_is_reported(self):
        for i in range(2):
            folder = self.root / f'run{i}'
            self.write_csv(folder, 'results_all.csv', ['Domain', 'Alive'], [{'Domain':'test.com', 'Alive':'NO'}])
        (self.root / 'run0' / 'run_info.json').write_text('{broken', encoding='utf-8')
        data = collect(self.root)
        self.assertEqual(len(data['records']), 2)
        self.assertNotEqual(data['records'][0]['id'], data['records'][1]['id'])
        self.assertEqual(len(data['warnings']), 1)

    def test_checkpoint_fallback_and_excluded_folders(self):
        folder = self.root / 'output'
        folder.mkdir()
        row = {'input_domain':'example.com','alive':True,'job_records':[{'url':'https://example.com/jobs/1','title':'Engineer'}], 'emails':['a@example.com','a@example.com']}
        (folder / 'checkpoint.json').write_text(json.dumps([row]), encoding='utf-8')
        self.write_csv(self.root / '.venv', 'results_all.csv', ['Domain'], [{'Domain':'noise.com'}])
        data = collect(self.root)
        self.assertEqual(len(data['runs']), 1)
        self.assertEqual(data['records'][0]['emails'], ['a@example.com'])
        self.assertEqual(data['runs'][0]['jobs'], 1)

    def test_profile_api_returns_json_for_empty_profile_and_api_errors(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        server.data_root=self.root
        server.store=ApplicationStore(self.root/'.dashboard_data'/'applications.sqlite3')
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base=f'http://127.0.0.1:{server.server_port}'
        with urlopen(base+'/api/profile') as response:
            profile=json.loads(response.read())
            self.assertIn('application/json',response.headers['Content-Type'])
        self.assertEqual(profile['version'],0)
        self.assertIsNone(profile['profile_id'])
        for path in ('/api/profile/resume','/api/profile/unknown'):
            try: urlopen(base+path)
            except HTTPError as response:
                with response:
                    self.assertEqual(response.code,404)
                    self.assertIn('application/json',response.headers['Content-Type'])
                    self.assertIn('error',json.loads(response.read()))
            else: self.fail(f'{path} deveria responder 404')


if __name__ == '__main__':
    unittest.main()
