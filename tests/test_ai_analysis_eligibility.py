import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import ai_analysis_eligibility
from ai_analysis_eligibility import (CACHE_REVISION, RULE_VERSION, evaluate_ai_analysis_eligibility,
                                     get_or_evaluate_ai_analysis_eligibility)
from applications import ApplicationStore, job_key
from job_description_quality import description_hash, evaluate_job_description_quality
from profile_fit import ProfileFitBatch, RUBRIC_VERSION, current_model


class AIAnalysisEligibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=ApplicationStore(Path(self.temp.name)/'applications.sqlite3')
        self.saved=self.store.save_profile({
            'desired_roles':['Customer Operations Manager','Product Operations','Implementation Specialist',
                'Risk Operations','Solutions Consultant','Head of Support'],
            'excluded_roles':['Junior Customer Support','Tier 1 Customer Support','Entry-Level Customer Support',
                'Call Center Agent','Customer Service Agent','Internship','Psychologist','Psychology'],
            'related_roles_open':True,'seniority':'Senior',
            'skills':['Customer Support - Advanced'],'technologies':['Python','FastAPI','APIs']})
        self.profile=self.saved['profile']

    def test_expected_role_families_and_conservative_titles(self):
        cases={
            'Customer Operations Manager':'eligible',
            'Implementation Specialist':'eligible',
            'Software Engineer, Backend':'excluded',
            'Staff Frontend Engineer':'excluded',
            'Principal iOS Engineer':'excluded',
            'Senior React Native Engineer':'excluded',
            'Android Engineer':'excluded',
            'Mobile Software Engineer':'excluded',
            'Senior Data Scientist':'excluded',
            'Senior Analytics Engineer, GFCO Analytics':'excluded',
            'Legal Counsel':'excluded',
            'Solutions Engineer':'review',
            'Technical Program Manager':'review',
            'Product Operations Manager':'eligible',
            'Product Manager':'review',
            'Risk Operations Analyst':'eligible',
            'Junior Customer Support':'excluded',
            'Customer Support Operations Manager':'eligible',
        }
        for title,expected in cases.items():
            with self.subTest(title=title):
                result=evaluate_ai_analysis_eligibility({'title':title},self.profile)
                self.assertEqual(result['status'],expected)
                self.assertEqual(result['rule_version'],RULE_VERSION)

    def test_solution_engineer_with_consultative_description_is_never_hard_excluded(self):
        result=evaluate_ai_analysis_eligibility({'title':'Solutions Engineer',
            'description':'Partner with customers to scope deployments, guide implementation, and support adoption.'},self.profile)
        self.assertNotEqual(result['status'],'excluded')

    def test_implementation_engineer_with_consultative_description_is_not_excluded(self):
        result=evaluate_ai_analysis_eligibility({'title':'Implementation Engineer',
            'description':'Guide customer deployments, configure integrations, train users, and support adoption.'},self.profile)
        self.assertNotEqual(result['status'],'excluded')

    def test_product_designer_requires_explicit_design_experience_evidence(self):
        strong={'title':'Senior Product Designer','description':(
            'Required: 5+ years of product design experience. Portfolio demonstrating end-to-end product design, '
            'clear evidence of UX thinking and visual craft.')}
        self.assertEqual(evaluate_ai_analysis_eligibility(strong,self.profile)['status'],'excluded')
        ambiguous=evaluate_ai_analysis_eligibility({'title':'Product Designer','description':'Join our product team.'},self.profile)
        self.assertNotEqual(ambiguous['status'],'excluded')

    def test_practical_technology_familiarity_does_not_qualify_engineering_roles(self):
        for title in ('Software Engineer, Backend','Principal iOS Engineer','Senior React Native Engineer',
                      'Android Engineer','Mobile Software Engineer'):
            with self.subTest(title=title):
                result=evaluate_ai_analysis_eligibility({'title':title,
                    'description':'Build APIs using JavaScript, Python, FastAPI and mobile technologies.'},self.profile)
                self.assertEqual(result['status'],'excluded')

    def _job(self,title,description,url='https://jobs.example/role'):
        return {'url':url,'title':title,'company':'Example','description':description}

    def _batch(self,job):
        data={'records':[{'id':'run#0','run_id':'run','date':'2026-10-03','name':'Example','jobs':[job]}]}
        return data,ProfileFitBatch(self.store,lambda:data)

    def _finish(self,batch,task):
        for _ in range(150):
            state=batch.progress(task['id'])
            if not state['running']:return state
            time.sleep(.02)
        self.fail('Profile analysis batch did not finish.')

    def _store_quality(self,job):
        value=evaluate_job_description_quality(job)
        self.store.save_job_description_quality(job_key(job['url']),value)
        return value

    def test_insufficient_quality_blocks_even_an_eligible_role(self):
        job=self._job('Customer Operations Manager','About us. Our products empower customers. '*25)
        self._store_quality(job);data,batch=self._batch(job)
        preview=batch.preview([job['url']])
        self.assertEqual(preview['insufficient'],1);self.assertEqual(preview['new'],0)
        self.assertEqual(preview['ignored_insufficient'],1)
        self.assertEqual(preview['eligibility_eligible'],1)
        with patch('profile_fit.create_openai_client') as client, patch('profile_fit.analyze_with_client') as analyze:
            task=batch.start([job['url']],preview['new'],preview['plan_token']);state=self._finish(batch,task)
        client.assert_not_called();analyze.assert_not_called()
        self.assertEqual(state['ignored_insufficient'],1)

    def test_complete_excluded_job_does_not_call_ai(self):
        job=self._job('Software Engineer, Backend',
            'Responsibilities: Build and maintain services. Requirements: 5 years of experience building software.')
        self._store_quality(job);_,batch=self._batch(job);preview=batch.preview([job['url']])
        self.assertEqual(preview['complete'],1);self.assertEqual(preview['eligibility_excluded'],1)
        self.assertEqual(preview['ignored_excluded'],1);self.assertEqual(preview['new'],0)
        with patch('profile_fit.create_openai_client') as client, patch('profile_fit.analyze_with_client') as analyze:
            task=batch.start([job['url']],preview['new'],preview['plan_token']);state=self._finish(batch,task)
        client.assert_not_called();analyze.assert_not_called()
        self.assertEqual(state['ignored_excluded'],1)

    def test_complete_eligible_job_uses_only_mocked_ai_and_cache_rules(self):
        job=self._job('Customer Operations Manager',
            'Responsibilities: Lead support operations and manage customer teams. '
            'Requirements: 5 years of experience in customer operations.')
        self._store_quality(job);_,batch=self._batch(job);preview=batch.preview([job['url']])
        self.assertEqual(preview['eligibility_eligible'],1);self.assertEqual(preview['new'],1)
        with patch('profile_fit.create_openai_client',return_value=object()) as client, \
             patch('profile_fit.analyze_with_client',return_value={'profile_score':77,'overall_fit':'strong',
                   'summary':'Fake deterministic test result.','strengths':[],'gaps':[]}) as analyze:
            task=batch.start([job['url']],preview['new'],preview['plan_token']);state=self._finish(batch,task)
        client.assert_called_once();analyze.assert_called_once()
        self.assertEqual(state['success'],1)
        digest=description_hash(job)
        self.assertIsNotNone(self.store.get_profile_fit(job_key(job['url']),self.saved['profile_id'],
            self.saved['version'],digest,RUBRIC_VERSION,current_model()))

    def test_existing_profile_fit_is_reused_even_when_eligibility_excludes(self):
        job=self._job('Software Engineer, Backend',
            'Responsibilities: Build systems. Requirements: 5 years of experience building software.')
        self._store_quality(job);_,batch=self._batch(job)
        result={'job_key':job_key(job['url']),'profile_id':self.saved['profile_id'],
            'profile_version':self.saved['version'],'job_description_hash':description_hash(job),
            'rubric_version':RUBRIC_VERSION,'model':current_model(),'profile_score':81,
            'overall_fit':'strong','analysis_json':'{}','analyzed_at':'2026-10-02T00:00:00Z'}
        self.store.save_profile_fit(result)
        preview=batch.preview([job['url']])
        self.assertEqual(preview['eligibility_excluded'],1);self.assertEqual(preview['reusable'],1)
        self.assertEqual(preview['new'],0);self.assertEqual(preview['ignored_excluded'],0)
        with patch('profile_fit.create_openai_client') as client:
            task=batch.start([job['url']],preview['new'],preview['plan_token']);state=self._finish(batch,task)
        client.assert_not_called();self.assertEqual(state['reused'],1)
        stored=self.store.get_profile_fit(job_key(job['url']),self.saved['profile_id'],self.saved['version'],
            description_hash(job),RUBRIC_VERSION,current_model())
        self.assertEqual(stored['profile_score'],81)

    def test_eligibility_cache_keys_description_profile_and_rule_versions(self):
        job=self._job('Customer Operations Manager','Responsibilities: Lead support teams.')
        key=job_key(job['url'])
        result,reused=get_or_evaluate_ai_analysis_eligibility(key,job,self.profile,self.store)
        self.assertFalse(reused)
        same,reused=get_or_evaluate_ai_analysis_eligibility(key,job,self.profile,self.store)
        self.assertTrue(reused);self.assertEqual(same['status'],result['status'])

        changed_job={**job,'description':job['description']+' Requirements: 5 years of experience.'}
        _,reused=get_or_evaluate_ai_analysis_eligibility(key,changed_job,self.profile,self.store)
        self.assertFalse(reused)
        self.assertIsNotNone(self.store.get_ai_analysis_eligibility(key,description_hash(job),self.saved['profile_id'],
                                                                    self.saved['version'],RULE_VERSION,CACHE_REVISION))

        changed_profile={**self.profile,'professional_summary':'Resumo alterado'}
        self.saved=self.store.save_profile(changed_profile)
        _,reused=get_or_evaluate_ai_analysis_eligibility(key,changed_job,self.saved['profile'],self.store)
        self.assertFalse(reused)
        self.assertIsNotNone(self.store.get_ai_analysis_eligibility(key,description_hash(changed_job),
            self.saved['profile_id'],self.saved['version'],RULE_VERSION,CACHE_REVISION))
        with patch.object(ai_analysis_eligibility,'CACHE_REVISION','mobile-design-fix-test'):
            _,reused=get_or_evaluate_ai_analysis_eligibility(key,changed_job,self.saved['profile'],self.store)
            self.assertFalse(reused)
            self.assertIsNotNone(self.store.get_ai_analysis_eligibility(key,description_hash(changed_job),
                self.saved['profile_id'],self.saved['version'],RULE_VERSION,'mobile-design-fix-test'))


if __name__=='__main__':unittest.main()
