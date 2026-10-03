import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from applications import ApplicationStore, job_key
from job_description_quality import (QUALITY_RULE_VERSION, attach_job_description_quality,
                                     description_hash, evaluate_job_description_quality)
from profile_fit import (ProfileFitBatch, RUBRIC_VERSION, attach_profile_fit,
                         current_model, preview_analysis)


class JobDescriptionQualityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=ApplicationStore(Path(self.temp.name)/'applications.sqlite3')
        self.profile=self.store.save_profile({'desired_roles':['Operations Manager'],
                                              'professional_summary':'Operations professional'})

    def test_complete_description_has_responsibilities_requirements_and_experience(self):
        job={'description':'''Responsibilities\n- Manage forecasting and staffing plans.\n- Lead weekly quality reviews.\nRequirements\n- 5+ years of experience in workforce management.\n- Proficiency with SQL and Excel.'''}
        result=evaluate_job_description_quality(job)
        self.assertEqual(result['status'],'complete')
        self.assertIn('Responsabilidades ou deveres descritos',result['signals'])
        self.assertIn('Requisitos ou qualificações identificados',result['signals'])

    def test_responsibilities_without_substantial_requirements_are_partial(self):
        result=evaluate_job_description_quality({'description':'In this role, you will coordinate daily customer support operations and lead team training.'})
        self.assertEqual(result['status'],'partial')

    def test_long_institutional_product_copy_is_insufficient(self):
        text=('About us. Our mission is to empower people with our products and our platform. '
              'Our ecosystem transforms the future of finance for customers around the world. ')*30
        result=evaluate_job_description_quality({'description':text})
        self.assertEqual(result['status'],'insufficient')
        self.assertIn('Responsabilidades do cargo',result['missing_signals'])

    def test_short_but_clear_role_description_is_not_insufficient(self):
        result=evaluate_job_description_quality({'description':'Responsibilities: Manage support escalations. Requirements: 3 years of experience.'})
        self.assertIn(result['status'],{'partial','complete'})
        self.assertNotEqual(result['status'],'insufficient')

    def test_absent_salary_location_and_contract_do_not_make_quality_insufficient(self):
        description=('Responsibilities\n- Lead workforce planning and improve support operations.\n'
                     'Requirements\n- 4 years of experience.\n- Skills in Excel and SQL.')
        result=evaluate_job_description_quality({'description':description})
        self.assertEqual(result['status'],'complete')
        self.assertIn('Remuneração (opcional)',result['missing_signals'])
        self.assertIn('Localização (opcional)',result['missing_signals'])
        self.assertIn('Tipo de contrato (opcional)',result['missing_signals'])

    def test_same_input_and_rule_version_are_stable(self):
        job={'description':'Our product supports people. No role responsibilities or requirements are present.'}
        self.assertEqual(evaluate_job_description_quality(job),evaluate_job_description_quality(job))
        self.assertEqual(evaluate_job_description_quality(job)['quality_rule_version'],QUALITY_RULE_VERSION)

    def test_dashboard_uses_current_hash_and_preserves_quality_history(self):
        url='https://jobs.example/aave-strategy'
        old={'url':url,'title':'Strategy & Business Development Associate','description':''}
        current={'url':url,'title':'Strategy & Business Development Associate','description':(
            'Responsibilities: Manage strategic partnerships and lead ecosystem initiatives. '
            'Requirements: 3 years of experience in business development and knowledge of DeFi.')}
        old_quality=evaluate_job_description_quality(old)
        current_quality=evaluate_job_description_quality(current)
        self.assertEqual(old_quality['status'],'insufficient')
        self.assertEqual(current_quality['status'],'complete')
        self.store.save_job_description_quality(job_key(url),old_quality)
        self.store.save_job_description_quality(job_key(url),current_quality)
        data={'records':[{'date':'2026-10-01','jobs':[old]}, {'date':'2026-10-03','jobs':[current]}]}

        attach_job_description_quality(data,self.store)

        self.assertEqual(data['records'][0]['jobs'][0]['description_quality']['status'],'insufficient')
        self.assertEqual(data['records'][1]['jobs'][0]['description_quality']['status'],'complete')
        self.assertEqual(data['records'][1]['jobs'][0]['description_quality']['job_description_hash'],
                         description_hash(current))
        with self.store.connect() as db:
            rows=db.execute('SELECT job_description_hash FROM job_description_quality WHERE job_key=? AND quality_rule_version=?',
                            (job_key(url),QUALITY_RULE_VERSION)).fetchall()
        self.assertEqual({row['job_description_hash'] for row in rows},
                         {description_hash(old),description_hash(current)})

    def test_old_complete_result_does_not_make_current_insufficient_job_eligible(self):
        job={'url':'https://jobs.example/current-insufficient','title':'Operations Manager',
             'description':'About us. Our products empower customers. '*25}
        old={'url':job['url'],'description':('Responsibilities: Lead support operations. '
             'Requirements: 4 years of experience with workforce management.')}
        self.store.save_job_description_quality(job_key(job['url']),evaluate_job_description_quality(old))
        data,batch=self._batch(job)
        preview=batch.preview([job['url']])
        self.assertEqual(preview['insufficient'],1)
        self.assertEqual(preview['new'],0)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM job_description_quality WHERE job_key=?',
                                         (job_key(job['url']),)).fetchone()[0],2)
        with patch('profile_fit.create_openai_client') as client, patch('profile_fit.analyze_with_client') as analyze:
            task=batch.start([job['url']],preview['new'],preview['plan_token'])
            state=self._finish(batch,task)
        client.assert_not_called();analyze.assert_not_called()
        self.assertEqual(state['ignored_insufficient'],1)

    def test_missing_current_hash_is_evaluated_locally_and_persisted(self):
        url='https://jobs.example/changed-description'
        old={'url':url,'description':'Responsibilities: Lead strategy. Requirements: 3 years of experience.'}
        current={'url':url,'title':'Strategy Associate','description':(
            'Responsibilities: Coordinate research and develop market plans. '
            'Requirements: 2 years of experience with analytics.')}
        self.store.save_job_description_quality(job_key(url),evaluate_job_description_quality(old))
        data={'records':[{'date':'2026-10-03','jobs':[current]}]}

        preview=preview_analysis(data,self.store,[url],model='test-model')

        self.assertEqual(preview['complete'],1)
        self.assertEqual(preview['insufficient'],0)
        self.assertEqual(data['records'][0]['jobs'][0]['description_quality']['job_description_hash'],
                         description_hash(current))
        self.assertEqual(self.store.get_job_description_quality(job_key(url),description_hash(current),
                                                                 QUALITY_RULE_VERSION)['status'],'complete')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM job_description_quality WHERE job_key=?',
                                         (job_key(url),)).fetchone()[0],2)

    def test_preview_counts_only_latest_occurrence_hashes(self):
        url_complete='https://jobs.example/complete'
        url_partial='https://jobs.example/partial'
        url_insufficient='https://jobs.example/insufficient'
        complete={'url':url_complete,'description':(
            'Responsibilities: Manage planning and lead execution. Requirements: 3 years of experience in operations.')}
        partial={'url':url_partial,'description':'In this role, you will coordinate customer support operations.'}
        insufficient={'url':url_insufficient,'description':'Our product empowers customers. '*20}
        stale=[{'url':url_complete,'description':''},
               {'url':url_partial,'description':'Responsibilities: Manage teams. Requirements: 3 years of experience.'},
               {'url':url_insufficient,'description':'Responsibilities: Manage teams. Requirements: 3 years of experience.'}]
        for old in stale:self.store.save_job_description_quality(job_key(old['url']),evaluate_job_description_quality(old))
        data={'records':[{'date':'2026-10-03','run_id':'a-quality-old','id':'a-quality-old#0','jobs':stale},
                         {'date':'2026-10-03','run_id':'z-quality-current','id':'z-quality-current#0',
                          'jobs':[complete,partial,insufficient]}]}

        preview=preview_analysis(data,self.store,[url_complete,url_partial,url_insufficient],model='test-model')

        self.assertEqual((preview['complete'],preview['partial'],preview['insufficient']),(1,1,1))
        self.assertEqual([job['description_quality']['status'] for job in data['records'][1]['jobs']],
                         ['complete','partial','insufficient'])

    def _batch(self,job):
        data={'records':[{'date':'2026-10-03','name':'Example','jobs':[job]}]}
        return data,ProfileFitBatch(self.store,lambda:data)

    def _finish(self,batch,task):
        for _ in range(100):
            state=batch.progress(task['id'])
            if not state['running']:return state
            time.sleep(.02)
        self.fail('Profile analysis batch did not finish.')

    def test_insufficient_job_without_cached_analysis_never_calls_ai(self):
        job={'url':'https://jobs.example/role','title':'Senior Operations Manager','description':'About us. Our products empower customers. '*25}
        data,batch=self._batch(job)
        preview=batch.preview([job['url']])
        self.assertEqual(preview['insufficient'],1)
        self.assertEqual(preview['new'],0)
        self.assertEqual(preview['ignored_insufficient'],1)
        with patch('profile_fit.create_openai_client') as client, patch('profile_fit.analyze_with_client') as analyze:
            task=batch.start([job['url']],preview['new'],preview['plan_token'])
            state=self._finish(batch,task)
        client.assert_not_called();analyze.assert_not_called()
        self.assertEqual(state['ignored_insufficient'],1)

    def test_existing_analysis_for_insufficient_job_is_preserved(self):
        job={'url':'https://jobs.example/role','title':'Senior Operations Manager','description':'About us. Our products empower customers. '*25}
        data,batch=self._batch(job)
        key=job_key(job['url']);digest=description_hash(job);model=current_model()
        analysis={'profile_score':82,'overall_fit':'strong','summary':'Análise histórica preservada.',
                  'strengths':['Experiência anterior registrada.'],'gaps':[],'transferable_experience':[],
                  'mandatory_requirements':[],'preferred_requirements':[],'unknowns':[], 'dimensions':{},
                  'career_value':{'rating':'unknown','rationale':'','aligned_goals':[],'cautions':[]},
                  'recommendation_reasoning':'Resultado histórico.','job_key':key,'profile_id':self.profile['profile_id'],
                  'profile_version':self.profile['version'],'profile_version_id':f"{self.profile['profile_id']}:v{self.profile['version']}",
                  'job_description_hash':digest,'rubric_version':RUBRIC_VERSION,'model':model,'analyzed_at':'2026-10-01T00:00:00+00:00'}
        self.store.save_profile_fit(analysis)
        preview=batch.preview([job['url']])
        self.assertEqual(preview['insufficient'],1);self.assertEqual(preview['reusable'],1);self.assertEqual(preview['new'],0)
        with patch('profile_fit.create_openai_client') as client, patch('profile_fit.analyze_with_client') as analyze:
            task=batch.start([job['url']],preview['new'],preview['plan_token'])
            state=self._finish(batch,task)
        client.assert_not_called();analyze.assert_not_called()
        stored=self.store.get_profile_fit(key,self.profile['profile_id'],self.profile['version'],digest,RUBRIC_VERSION,model)
        self.assertEqual(stored['profile_score'],82)
        data=attach_profile_fit(data,self.store,model)
        self.assertEqual(data['records'][0]['jobs'][0]['score']['total'],82)
        self.assertEqual(state['reused'],1)


if __name__=='__main__':unittest.main()
