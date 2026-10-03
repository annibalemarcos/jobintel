import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import profile_fit
from applications import ApplicationStore
from profile_fit import (DIMENSIONS, ProfileFitBatch, analyze_with_client, current_model,
                         description_hash, normalize_location_fit, preview_analysis, validate_analysis)


def valid_result(score=80):
    return {
        'profile_score':score,'overall_fit':'strong','summary':'Experiência operacional relacionada.',
        'strengths':['Liderança de suporte comprovada.'],'gaps':['Experiência profissional com Python não consta.'],
        'transferable_experience':['Experiência de suporte transferível para operações de produto.'],
        'mandatory_requirements':[{'requirement':'Liderar equipe remota','status':'met','evidence':'Histórico profissional informa liderança remota.'}],
        'preferred_requirements':[{'requirement':'Python','status':'unknown','evidence':'A vaga não define nível nem uso profissional.'}],
        'unknowns':['Faixa salarial não informada.'],
        'dimensions':{key:{'status':'unknown','score':None,'evidence':[],'gaps':[],'uncertainties':['Informação não disponível.']} for key in DIMENSIONS},
        'career_value':{'rating':'medium','rationale':'Alinhamento com interesse registrado.','aligned_goals':['Web3'],'cautions':[]},
        'recommendation_reasoning':'A experiência transferível é apoiada pelo histórico informado.'}


class FakeResponse:
    def __init__(self, value):self.output_text=json.dumps(value,ensure_ascii=False)


class FakeClient:
    class Responses:
        def __init__(self,owner):self.owner=owner
        def create(self,**kwargs):
            self.owner.calls.append(kwargs)
            if self.owner.error:raise RuntimeError('API indisponível no fake')
            return FakeResponse(self.owner.value)
    def __init__(self,value=None,error=False):
        self.calls=[];self.value=value or valid_result();self.error=error;self.responses=self.Responses(self)


class ProfileFitTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=ApplicationStore(Path(self.temp.name)/'applications.sqlite3')
        self.profile=self.store.save_profile({'professional_summary':'Liderança de operações e suporte.',
            'work_history':['Customer Operations Lead · liderou equipe remota'],
            'interests':['Web3'], 'desired_roles':['Operations Lead']})
        self.job={'url':'https://jobs.example/role','title':'Product Operations Lead','company':'Example',
                  'description':'Lead remote support operations for a Web3 product.'}
        self.data={'records':[{'id':'fixture','date':'2026-10-03','name':'Example','jobs':[self.job]}]}

    def test_structured_response_and_transferable_experience(self):
        client=FakeClient()
        result=analyze_with_client(client,'test-model',self.profile['profile'],self.job)
        self.assertEqual(result['profile_score'],80)
        self.assertIn('transferível',result['transferable_experience'][0])
        request=client.calls[0]
        self.assertEqual(request['text']['format']['type'],'json_schema')
        self.assertTrue(request['text']['format']['strict'])
        payload=json.loads(request['input'][1]['content'])
        self.assertNotIn('resume',payload['profile'])

    def test_unknown_dimension_does_not_require_a_zero_score(self):
        result=validate_analysis(valid_result(82))
        self.assertIsNone(result['dimensions']['compensation_fit']['score'])
        self.assertEqual(result['profile_score'],82)

    def test_unknown_dimension_numeric_score_normalizes_to_null(self):
        result=valid_result();result['dimensions']['location_fit']['status']='unknown'
        result['dimensions']['location_fit']['score']=60
        normalized=validate_analysis(result)
        self.assertIsNone(normalized['dimensions']['location_fit']['score'])
        self.assertNotEqual(normalized['dimensions']['location_fit']['score'],0)

    def test_unknown_dimension_without_score_is_valid_and_null(self):
        result=valid_result();result['dimensions']['location_fit'].pop('score')
        self.assertIsNone(validate_analysis(result)['dimensions']['location_fit']['score'])

    def test_known_dimension_score_is_preserved(self):
        result=valid_result();result['dimensions']['location_fit'].update(status='met',score=73)
        self.assertEqual(validate_analysis(result)['dimensions']['location_fit']['score'],73)

    def test_remote_usa_without_international_policy_is_unknown_not_a_hard_fail(self):
        value=valid_result(43);loc=value['dimensions']['location_fit']
        loc.update(status='not_met',score=0)
        profile={'current_location':'São Paulo, Brazil','relocation':False,'work_modes':{'remote':'preferred'}}
        job={'location':'Remote - USA','title':'Technical Program Manager','description':'Work remotely from the United States.'}
        normalize_location_fit(value,profile,job)
        self.assertEqual(loc['status'],'unknown');self.assertIsNone(loc['score'])
        self.assertEqual(value['profile_score'],43)
        self.assertTrue(loc['uncertainties'])

    def test_remote_role_explicitly_open_to_brazil_preserves_known_fit(self):
        value=valid_result();loc=value['dimensions']['location_fit']
        loc.update(status='met',score=88)
        normalize_location_fit(value,{'current_location':'Brazil'},
                               {'location':'Remote - USA','description':'Applicants in Brazil are eligible.'})
        self.assertEqual((loc['status'],loc['score']),('met',88))

    def test_explicit_work_authorization_conflict_remains_not_met(self):
        value=valid_result();loc=value['dimensions']['location_fit']
        loc.update(status='not_met',score=0)
        profile={'current_location':'Brazil','analysis_notes':'I do not have US work authorization.'}
        job={'location':'Remote - USA','description':'Must be authorized to work in the United States.'}
        normalize_location_fit(value,profile,job)
        self.assertEqual((loc['status'],loc['score']),('not_met',0))

    def test_relocation_false_only_conflicts_with_explicit_relocation_requirement(self):
        profile={'current_location':'Brazil','relocation':False}
        ordinary=valid_result();ordinary['dimensions']['location_fit'].update(status='not_met',score=0)
        normalize_location_fit(ordinary,profile,{'location':'Remote - USA','description':'Remote team.'})
        self.assertEqual(ordinary['dimensions']['location_fit']['status'],'unknown')
        required=valid_result();required['dimensions']['location_fit'].update(status='not_met',score=0)
        normalize_location_fit(required,profile,{'location':'New York, USA','description':'Relocation required for this role.'})
        self.assertEqual((required['dimensions']['location_fit']['status'],
                          required['dimensions']['location_fit']['score']),('not_met',0))

    def test_location_normalization_does_not_change_other_dimensions_or_prompt_version(self):
        value=valid_result(47);value['dimensions']['role_fit'].update(status='met',score=79)
        value['dimensions']['location_fit'].update(status='not_met',score=0)
        normalize_location_fit(value,{'current_location':'Brazil'}, {'location':'Remote - USA'})
        self.assertEqual(value['profile_score'],47)
        self.assertEqual(value['dimensions']['role_fit']['score'],79)
        self.assertEqual(profile_fit.RUBRIC_VERSION,'profile_fit_v1')
        self.assertIn('nunca not_met',profile_fit.SYSTEM_INSTRUCTIONS)

    def test_mandatory_requirement_not_met_is_preserved(self):
        result=valid_result();result['mandatory_requirements'][0]['status']='not_met'
        self.assertEqual(validate_analysis(result)['mandatory_requirements'][0]['status'],'not_met')

    def test_analysis_cache_keys_profile_hash_rubric_and_model(self):
        preview=preview_analysis(self.data,self.store,[self.job['url']],'model-a')
        result=valid_result();result.update(job_key='https://jobs.example/role',profile_id=self.profile['profile_id'],
            profile_version=self.profile['version'],job_description_hash=profile_fit.description_hash(self.job),
            rubric_version=profile_fit.RUBRIC_VERSION,model='model-a',analyzed_at='2026-10-03T00:00:00+00:00')
        self.store.save_profile_fit(result)
        self.assertEqual(preview_analysis(self.data,self.store,[self.job['url']],'model-a')['reusable'],1)
        self.assertEqual(preview_analysis(self.data,self.store,[self.job['url']],'model-b')['new'],1)
        changed_job={**self.job,'description':self.job['description']+' New requirement.'}
        self.assertEqual(preview_analysis({'records':[{'jobs':[changed_job]}]},self.store,[self.job['url']],'model-a')['new'],1)
        with patch.object(profile_fit,'RUBRIC_VERSION','profile_fit_v2'):
            self.assertEqual(preview_analysis(self.data,self.store,[self.job['url']],'model-a')['new'],1)
        self.store.save_profile({'professional_summary':'Liderança de operações e suporte. Alterado.',
            'work_history':['Customer Operations Lead · liderou equipe remota'],
            'interests':['Web3'], 'desired_roles':['Operations Lead']})
        self.assertEqual(preview_analysis(self.data,self.store,[self.job['url']],'model-a')['new'],1)

    def test_existing_analysis_is_reused_without_creating_client(self):
        result=valid_result();result.update(job_key='https://jobs.example/role',profile_id=self.profile['profile_id'],
            profile_version=self.profile['version'],job_description_hash=profile_fit.description_hash(self.job),
            rubric_version=profile_fit.RUBRIC_VERSION,model=profile_fit.current_model(),analyzed_at='2026-10-03T00:00:00+00:00')
        self.store.save_profile_fit(result)
        manager=ProfileFitBatch(self.store,lambda:self.data)
        preview=manager.preview([self.job['url']])
        with patch('profile_fit.create_openai_client') as create_client:
            started=manager.start([self.job['url']],preview['new'],preview['plan_token'])
            for _ in range(100):
                state=manager.progress(started['id'])
                if not state['running']:break
                time.sleep(.02)
        create_client.assert_not_called()
        self.assertEqual(state['reused'],1)
        self.assertEqual(state['success'],0)

    def test_api_failure_does_not_overwrite_existing_analysis(self):
        old=valid_result(72);old.update(job_key='https://jobs.example/role',profile_id=self.profile['profile_id'],
            profile_version=self.profile['version'],job_description_hash=profile_fit.description_hash(self.job),
            rubric_version=profile_fit.RUBRIC_VERSION,model='model-a',analyzed_at='2026-10-03T00:00:00+00:00')
        self.store.save_profile_fit(old)
        with self.assertRaisesRegex(RuntimeError,'API indisponível'):
            analyze_with_client(FakeClient(error=True),'model-a',self.profile['profile'],self.job)
        invalid=valid_result();invalid['profile_score']=101
        with self.assertRaisesRegex(ValueError,'Score de perfil inválido'):
            analyze_with_client(FakeClient(value=invalid),'model-a',self.profile['profile'],self.job)
        stored=self.store.get_profile_fit(old['job_key'],old['profile_id'],old['profile_version'],
            old['job_description_hash'],old['rubric_version'],old['model'])
        self.assertEqual(stored['profile_score'],72)

    def test_invalid_attempt_is_not_cached_and_next_preview_reuses_only_successful_job(self):
        description=('Responsibilities: Lead customer experience intelligence, knowledge systems, and team workflows. '
                     'Requirements: 5 years of experience coordinating cross-functional operations.')
        first={**self.job,'url':'https://jobs.example/tpm-cx-intelligence','title':'Technical Program Manager, CX Intelligence & AI',
               'description':description}
        failed={**self.job,'url':'https://jobs.example/tpm-knowledge-systems','title':'Technical Program Manager, Knowledge Systems',
                'description':description}
        data={'records':[{'id':'fixture','date':'2026-10-03','name':'Example','jobs':[first,failed]}]}
        successful=valid_result(80)
        successful.update(job_key='https://jobs.example/tpm-cx-intelligence',profile_id=self.profile['profile_id'],
            profile_version=self.profile['version'],job_description_hash=description_hash(first),
            rubric_version=profile_fit.RUBRIC_VERSION,model=current_model(),analyzed_at='2026-10-03T00:00:00Z')
        self.store.save_profile_fit(successful)
        manager=ProfileFitBatch(self.store,lambda:data)
        preview=manager.preview([first['url'],failed['url']])
        self.assertEqual((preview['reusable'],preview['new']),(1,1))

        with patch('profile_fit.create_openai_client',return_value=object()) as client, \
             patch('profile_fit.analyze_with_client',side_effect=ValueError(
                 'Dimensão desconhecida não pode ter score: location_fit.')) as analyze:
            task=manager.start([first['url'],failed['url']],preview['new'],preview['plan_token'])
            for _ in range(150):
                state=manager.progress(task['id'])
                if not state['running']:break
                time.sleep(.02)
        client.assert_called_once();analyze.assert_called_once()
        self.assertEqual(state['failures'],1);self.assertEqual(state['reused'],1)
        digest=description_hash(failed)
        self.assertIsNone(self.store.get_profile_fit('https://jobs.example/tpm-knowledge-systems',
            self.profile['profile_id'],self.profile['version'],digest,profile_fit.RUBRIC_VERSION,current_model()))
        persisted=self.store.get_profile_fit('https://jobs.example/tpm-cx-intelligence',
            self.profile['profile_id'],self.profile['version'],description_hash(first),profile_fit.RUBRIC_VERSION,current_model())
        self.assertEqual(persisted['profile_score'],80)

        next_preview=manager.preview([first['url'],failed['url']])
        self.assertEqual(next_preview['reusable'],1)
        self.assertEqual(next_preview['new'],1)

    def test_batch_continues_after_one_job_fails(self):
        second={**self.job,'url':'https://jobs.example/role-2','title':'Support Operations'}
        data={'records':[{'date':'2026-10-03','name':'Example','jobs':[self.job,second]}]}
        manager=ProfileFitBatch(self.store,lambda:data)
        fake=FakeClient()
        with patch('profile_fit.create_openai_client',return_value=fake), \
             patch('profile_fit.analyze_with_client',side_effect=[RuntimeError('fake failure'),valid_result()]) as analyze:
            preview=manager.preview([self.job['url'],second['url']])
            started=manager.start([self.job['url'],second['url']],preview['new'],preview['plan_token'])
            for _ in range(100):
                state=manager.progress(started['id'])
                if not state['running']:break
                time.sleep(.02)
        self.assertEqual(state['done'],2)
        self.assertEqual(state['failures'],1)
        self.assertEqual(state['success'],1)
        self.assertEqual(analyze.call_count,2)


if __name__=='__main__':unittest.main()
