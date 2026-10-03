import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from applications import ApplicationStore, job_key
from application_materials import (RULE_VERSION, build_material_plan, generate_and_persist,
    generate_with_client, preview_materials, resolve_language, response_schema, validate_materials)
from dashboard import Handler


def valid_package():
    return {'cover_letter':'A' * 500,'cold_email_subject':'Interest in the Operations Manager role',
            'cold_email_body':'B' * 300}


class ApplicationMaterialsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store=ApplicationStore(Path(self.temp.name)/'applications.sqlite3')
        self.profile=self.store.save_profile({'desired_roles':['Customer Operations Manager'],
            'professional_summary':'Operations professional with customer support leadership experience.',
            'work_history':['Customer Operations Lead · Example · Led support operations'],
            'skills':['Support operations'], 'technologies':['Python (practical projects)']})
        self.job={'url':'https://jobs.example/operations-manager','key':job_key('https://jobs.example/operations-manager'),
            'title':'Operations Manager','company':'Example Inc','description':'Operations role. Responsibilities include leading support teams, improving customer experience, and coordinating service workflows. '*20,
            'description_quality':{'status':'complete'}}

    def plan(self,job=None,profile=None,model='test-model',language='auto'):
        return build_material_plan(job or self.job,profile or self.profile,model,language)

    def success(self,plan,package=None):
        return self.store.save_application_material({key:plan[key] for key in ('job_key','job_url','job_title','company',
            'job_description_hash','profile_id','profile_version','profile_fit_hash','profile_fit_reference','rule_version','model','language','input_fingerprint')} | 
            {**(package or valid_package()),'status':'success','created_at':'2026-10-03T10:00:00Z'})

    def test_complete_profile_without_profile_fit_allows_one_call(self):
        preview=preview_materials(self.job,self.profile,self.store,'test-model')
        self.assertEqual(preview['quality'],'complete')
        self.assertFalse(preview['profile_fit_available'])
        self.assertEqual(preview['new_calls'],1)

    def test_profile_fit_valid_for_current_occurrence_is_included(self):
        job={**self.job,'profile_analysis':{'profile_id':self.profile['profile_id'],'profile_version':self.profile['version'],
            'profile_version_id':f"{self.profile['profile_id']}:v{self.profile['version']}",
            'job_description_hash':hashlib.sha256(self.job['description'].encode()).hexdigest(),
            'rubric_version':'profile_fit_v1','strengths':['Support operations']}}
        plan=self.plan(job=job)
        self.assertEqual(plan['context']['profile_fit']['strengths'],['Support operations'])
        self.assertTrue(plan['profile_fit_hash'])

    def test_missing_profile_fit_does_not_prevent_generation(self):
        self.assertEqual(self.plan()['context']['profile_fit'],None)
        self.assertEqual(preview_materials(self.job,self.profile,self.store,'test-model')['new_calls'],1)

    def test_partial_quality_allows_generation_with_warning(self):
        job={**self.job,'description_quality':{'status':'partial'}}
        preview=preview_materials(job,self.profile,self.store,'test-model')
        self.assertEqual(preview['new_calls'],1)
        self.assertIn('parcial',preview['warning'])

    def test_insufficient_quality_blocks_new_generation(self):
        job={**self.job,'description_quality':{'status':'insufficient'}}
        preview=preview_materials(job,self.profile,self.store,'test-model')
        self.assertTrue(preview['blocked']);self.assertEqual(preview['new_calls'],0)

    def test_exact_cache_avoids_new_call_and_failed_rows_are_not_reused(self):
        plan=self.plan();saved=self.success(plan)
        self.assertEqual(preview_materials(self.job,self.profile,self.store,'test-model')['new_calls'],0)
        failed={**plan,'status':'failed','error':'local fake failure','created_at':'2026-10-03T10:01:00Z'}
        self.store.save_application_material(failed)
        self.assertEqual(self.store.get_application_material(plan['job_key'],plan['input_fingerprint'])['id'],saved['id'])

    def test_failed_only_cache_requires_another_attempt(self):
        plan=self.plan();self.store.save_application_material({**plan,'status':'failed','error':'fake','created_at':'now'})
        self.assertEqual(preview_materials(self.job,self.profile,self.store,'test-model')['new_calls'],1)

    def test_description_profile_model_language_and_fit_changes_miss_cache(self):
        base=self.plan();self.success(base)
        changed_job={**self.job,'description':self.job['description']+'new'}
        self.assertNotEqual(base['input_fingerprint'],self.plan(job=changed_job)['input_fingerprint'])
        changed_profile=self.store.save_profile({'desired_roles':['Customer Operations Manager'],'professional_summary':'Updated summary.'})
        self.assertNotEqual(base['input_fingerprint'],self.plan(profile=changed_profile)['input_fingerprint'])
        self.assertNotEqual(base['input_fingerprint'],self.plan(profile=changed_profile,model='other-model')['input_fingerprint'])
        self.assertNotEqual(base['input_fingerprint'],self.plan(profile=changed_profile,language='pt')['input_fingerprint'])
        fit_job={**changed_job,'profile_analysis':{'profile_id':changed_profile['profile_id'],'profile_version':changed_profile['version'],
            'profile_version_id':f"{changed_profile['profile_id']}:v{changed_profile['version']}",
            'job_description_hash':hashlib.sha256(changed_job['description'].encode()).hexdigest(),
            'rubric_version':'profile_fit_v1','summary':'new fit context'}}
        self.assertNotEqual(self.plan(job=fit_job,profile=changed_profile)['profile_fit_hash'],'')

    def test_fit_context_change_changes_fingerprint(self):
        digest=hashlib.sha256(self.job['description'].encode()).hexdigest()
        first={**self.job,'profile_analysis':{'profile_id':self.profile['profile_id'],'profile_version':1,
            'profile_version_id':f"{self.profile['profile_id']}:v{self.profile['version']}",
            'job_description_hash':digest,'rubric_version':'profile_fit_v1','summary':'first'}}
        second={**first,'profile_analysis':{**first['profile_analysis'],'summary':'second'}}
        self.assertNotEqual(self.plan(job=first)['input_fingerprint'],self.plan(job=second)['input_fingerprint'])

    def test_invalid_empty_or_placeholder_output_fails_local_validation(self):
        for value in ({**valid_package(),'cold_email_body':''},{**valid_package(),'cover_letter':'[Company] '+('A'*500)}):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):validate_materials(value)

    def test_structured_output_and_persistence_are_one_generation(self):
        plan=self.plan();client=Mock()
        with patch('application_materials.generate_with_client',return_value=valid_package()) as generate:
            result=generate_and_persist(plan,self.store,client_factory=lambda:client)
        generate.assert_called_once_with(client,plan)
        self.assertEqual(result['status'],'success')
        self.assertEqual(result['cover_letter'],'A'*500)
        self.assertEqual(result['cold_email_subject'],valid_package()['cold_email_subject'])
        self.assertEqual(result['cold_email_body'],'B'*300)
        self.assertEqual(result['rule_version'],RULE_VERSION)

    def test_provider_receives_one_strict_structured_output_request(self):
        client=Mock();client.responses.create.return_value=SimpleNamespace(output_text=json.dumps(valid_package()))
        value=generate_with_client(client,self.plan())
        self.assertEqual(value,valid_package())
        client.responses.create.assert_called_once()
        request=client.responses.create.call_args.kwargs
        self.assertEqual(request['text']['format']['type'],'json_schema')
        self.assertEqual(request['text']['format']['schema'],response_schema())

    def test_regeneration_adds_history_without_overwriting_and_does_not_touch_application(self):
        plan=self.plan();before=self.store.get_user_application_decision(plan['job_key'])
        with patch('application_materials.generate_with_client',side_effect=[valid_package(),{**valid_package(),'cover_letter':'C'*500}]), \
             patch('application_materials.create_openai_client',return_value=object()):
            first=generate_and_persist(plan,self.store)
            second=generate_and_persist(plan,self.store)
        self.assertNotEqual(first['id'],second['id'])
        self.assertEqual(self.store.get_application_material(plan['job_key'],plan['input_fingerprint'])['cover_letter'],'C'*500)
        self.assertEqual(len(self.store.list_application_materials(plan['job_key'])),2)
        self.assertEqual(before,self.store.get_user_application_decision(plan['job_key']))
        applications,_=self.store.snapshot();self.assertNotIn(plan['job_key'],applications)

    def test_generation_failure_is_recorded_without_destroying_previous_success(self):
        plan=self.plan();saved=self.success(plan)
        with patch('application_materials.create_openai_client',side_effect=RuntimeError('fake API failure')):
            with self.assertRaisesRegex(RuntimeError,'fake API failure'):generate_and_persist(plan,self.store)
        self.assertEqual(self.store.get_application_material(plan['job_key'],plan['input_fingerprint'])['id'],saved['id'])
        self.assertEqual(self.store.list_application_materials(plan['job_key'])[0]['status'],'failed')

    def test_auto_language_is_deterministic_and_only_english_or_portuguese(self):
        self.assertEqual(resolve_language({'title':'Customer Operations Manager','description':'The role and responsibilities include working with the team.'}),'en')
        self.assertEqual(resolve_language({'title':'Gerente de Operações','description':'A vaga é para você, com responsabilidades e experiência.'}),'pt')

    def test_preview_and_unconfirmed_generate_route_do_not_call_provider(self):
        for path,body,expected in (
            ('/api/application-materials/preview',{'url':self.job['url'],'language':'auto'},200),
            ('/api/application-materials/generate',{'url':self.job['url'],'language':'auto'},409)):
            handler=Handler.__new__(Handler);handler.path=path
            raw=json.dumps(body).encode();handler.headers={'Host':'127.0.0.1:8765','Origin':'http://127.0.0.1:8765',
                'X-Site-Intel':'dashboard','Content-Length':str(len(raw)),'Content-Type':'application/json'}
            handler.rfile=io.BytesIO(raw);handler.wfile=io.BytesIO();handler.server=SimpleNamespace(store=self.store)
            handler.send_response=Mock();handler.send_header=Mock();handler.end_headers=Mock()
            with patch.object(Handler,'_material_job',return_value=self.job),patch('dashboard.current_model',return_value='test-model'), \
                 patch('application_materials.create_openai_client') as create_client:
                handler.do_POST()
            create_client.assert_not_called();self.assertEqual(handler.send_response.call_args.args[0],expected)


if __name__=='__main__':unittest.main()
