import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import application_decision
from application_decision import (RULE_VERSION, attach_application_decisions,
                                  decision_fingerprint, evaluate_application_decision)
from applications import ApplicationStore, job_key


def local_signals(**changes):
    value={'description_quality':{'status':'complete','quality_rule_version':'job_description_quality_v1'},
           'ai_analysis_eligibility':{'status':'eligible','rule_version':'ai_analysis_eligibility_v1'},
           'pre_match':{'classification':'strong_candidate','pre_score':82,'rule_version':'prematch-v1'}}
    value.update(changes)
    return value


def fit(overall='strong',score=82,**changes):
    value={'overall_fit':overall,'profile_score':score,'strengths':['Liderou operações de suporte.'],
           'gaps':[],'transferable_experience':[],'mandatory_requirements':[
               {'requirement':'Experiência com operações','status':'met','evidence':'Histórico informado.'}],
           'unknowns':[],'career_value':{'rating':'medium','rationale':'Alinhamento de carreira.'},
           'dimensions':{}}
    value.update(changes)
    return value


class ApplicationDecisionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=ApplicationStore(Path(self.temp.name)/'applications.sqlite3')
        self.profile=self.store.save_profile({'desired_roles':['Customer Operations'],'current_location':'Brazil',
            'relocation':False,'work_modes':{'remote':'preferred','hybrid':'acceptable','onsite':'unwanted'},
            'work_mode_restrictions':[],'contract_types':['Full-time'],'languages':['English · C1']})
        self.job={'url':'https://jobs.example/customer-ops','title':'Customer Operations Manager','company':'Example',
            'location':'Remote - Brazil','description':'Manage customer operations and lead the team.',
            **local_signals()}

    def test_insufficient_description_requires_review_before_other_signals(self):
        job={**self.job,'description_quality':{'status':'insufficient'},
             'ai_analysis_eligibility':{'status':'excluded','reason':'hard mismatch'}}
        self.assertEqual(evaluate_application_decision(job,self.profile['profile'])['recommendation'],'review')

    def test_excluded_eligibility_is_low_priority(self):
        result=evaluate_application_decision({**self.job,'ai_analysis_eligibility':{
            'status':'excluded','reason':'Família de engenharia incompatível.','exclusion_signals':['Software Engineer']}},self.profile['profile'])
        self.assertEqual(result['recommendation'],'low_priority')
        self.assertIn('Software Engineer',result['negative_signals'])

    def test_strong_local_signals_without_profile_fit_can_only_consider_without_ai(self):
        with patch('profile_fit.create_openai_client') as create_client:
            result=evaluate_application_decision(self.job,self.profile['profile'])
        create_client.assert_not_called()
        self.assertEqual(result['recommendation'],'consider')

    def test_insufficient_local_signals_without_profile_fit_require_review(self):
        result=evaluate_application_decision({'title':'Role','url':self.job['url']},self.profile['profile'])
        self.assertEqual(result['recommendation'],'review')

    def test_strong_profile_fit_and_requirements_prioritize(self):
        result=evaluate_application_decision({**self.job,'profile_analysis':fit()},self.profile['profile'])
        self.assertEqual(result['recommendation'],'prioritize')

    def test_moderate_fit_with_transferable_experience_and_career_value_is_consider(self):
        analysis=fit('moderate',56,transferable_experience=['Experiência em suporte transferível.'],
                     career_value={'rating':'high','rationale':'Transição para operações de produto.'})
        result=evaluate_application_decision({**self.job,'profile_analysis':analysis},self.profile['profile'])
        self.assertEqual(result['recommendation'],'consider')

    def test_weak_fit_and_unmet_mandatory_requirement_is_low_priority(self):
        analysis=fit('poor',24,mandatory_requirements=[{'requirement':'Licença obrigatória','status':'not_met','evidence':'Não consta.'}])
        self.assertEqual(evaluate_application_decision({**self.job,'profile_analysis':analysis},self.profile['profile'])['recommendation'],'low_priority')

    def test_remote_usa_and_brazil_with_unknown_policy_is_not_location_conflict(self):
        job={**self.job,'location':'Remote - USA','description':'Work remotely with a US-based team.'}
        risk=evaluate_application_decision(job,self.profile['profile'])['risks']['location']
        self.assertEqual(risk['status'],'unknown')

    def test_explicit_international_exclusion_can_be_location_conflict(self):
        job={**self.job,'location':'Remote - USA','description':'US residents only. International applicants are not accepted.'}
        self.assertEqual(evaluate_application_decision(job,self.profile['profile'])['risks']['location']['status'],'conflict')

    def test_unknown_fit_dimension_remains_unknown_practical_risk(self):
        analysis=fit(dimensions={'location_fit':{'status':'unknown','score':None},
                                 'language_fit':{'status':'unknown','score':None}})
        risks=evaluate_application_decision({**self.job,'location':'','profile_analysis':analysis},self.profile['profile'])['risks']
        self.assertEqual(risks['location']['status'],'unknown')
        self.assertEqual(risks['language']['status'],'unknown')

    def test_manual_decision_is_separate_from_recommendation_and_application_tracking(self):
        before=self.store.get_user_application_decision(job_key(self.job['url']))
        self.assertEqual(before['user_decision'],'not_reviewed')
        saved=self.store.save_user_application_decision(self.job['url'],{
            'user_decision':'want_to_apply','reason':'career_value','note':'Transição estratégica.', 'version':0})
        self.assertEqual(saved['user_decision'],'want_to_apply')
        self.assertEqual(self.store.snapshot()[0],{})
        self.assertEqual(len(saved['history']),1)

    def test_manual_history_survives_recommendation_invalidation(self):
        self.store.save_user_application_decision(self.job['url'],{
            'user_decision':'do_not_apply','reason':'location','note':'Sem relocação.', 'version':0})
        data={'records':[{'jobs':[dict(self.job)]}]}
        with contextlib.redirect_stdout(io.StringIO()):attach_application_decisions(data,self.store)
        changed={**self.job,'description':self.job['description']+' Additional responsibilities.'}
        next_data={'records':[{'jobs':[changed]}]}
        with contextlib.redirect_stdout(io.StringIO()):attach_application_decisions(next_data,self.store)
        manual=next_data['records'][0]['jobs'][0]['user_decision']
        self.assertEqual(manual['user_decision'],'do_not_apply')
        self.assertEqual(manual['reason'],'location')
        self.assertEqual(len(manual['history']),1)

    def test_recommendation_cache_reuses_equal_inputs_and_invalidates_changed_inputs(self):
        def attach(job):
            data={'records':[{'jobs':[dict(job)]}]}
            with contextlib.redirect_stdout(io.StringIO()):attach_application_decisions(data,self.store)
            return data['records'][0]['jobs'][0]['application_decision']
        first=attach(self.job);second=attach(self.job)
        self.assertEqual(first['input_fingerprint'],second['input_fingerprint'])
        self.assertEqual(first['generated_at'],second['generated_at'])
        changed=attach({**self.job,'description':self.job['description']+' New.'})
        self.assertNotEqual(first['input_fingerprint'],changed['input_fingerprint'])
        self.store.save_profile({**self.profile['profile'],'seniority':'Senior'})
        profile_changed=attach({**self.job,'description':self.job['description']+' New.'})
        self.assertNotEqual(changed['profile_version_id'],profile_changed['profile_version_id'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM application_decisions').fetchone()[0],3)

    def test_permissive_local_signals_without_profile_fit_require_review(self):
        for quality,eligibility,prematch in (
            ('complete','eligible','possible_candidate'),
            ('partial','eligible','possible_candidate'),
            ('complete','review','possible_candidate'),
            ('complete','review','strong_candidate'),
            ('complete','eligible','weak_candidate'),
            (None,None,None),
        ):
            with self.subTest(quality=quality,eligibility=eligibility,prematch=prematch):
                signals={}
                if quality:signals['description_quality']={'status':quality}
                if eligibility:signals['ai_analysis_eligibility']={'status':eligibility,'reason':'Revisão conservadora.'}
                if prematch:signals['pre_match']={'classification':prematch}
                result=evaluate_application_decision({'title':'Role',**signals},self.profile['profile'])
                self.assertEqual(result['recommendation'],'review')

    def test_known_real_world_examples_without_profile_fit_are_review(self):
        for title in ('Customer Support Specialist II','Senior Analytics Engineer, GFCO Analytics'):
            with self.subTest(title=title):
                job={'title':title,'description_quality':{'status':'complete'},
                     'ai_analysis_eligibility':{'status':'eligible'},
                     'pre_match':{'classification':'possible_candidate'}}
                result=evaluate_application_decision(job,self.profile['profile'])
                self.assertEqual(result['recommendation'],'review')
                self.assertIn('Profile Fit ainda não está disponível',result['primary_reason'])

    def test_no_profile_fit_combination_can_prioritize(self):
        for quality in ('complete','partial','insufficient',None):
            for eligibility in ('eligible','review','excluded',None):
                for prematch in ('strong_candidate','possible_candidate','weak_candidate',None):
                    job={}
                    if quality:job['description_quality']={'status':quality}
                    if eligibility:job['ai_analysis_eligibility']={'status':eligibility,'reason':'hard mismatch'}
                    if prematch:job['pre_match']={'classification':prematch}
                    result=evaluate_application_decision(job,self.profile['profile'])
                    self.assertNotEqual(result['recommendation'],'prioritize',
                        (quality,eligibility,prematch))

    def test_insufficient_overrides_strong_signals_and_excluded_remains_low_priority(self):
        strong={'classification':'strong_candidate'}
        self.assertEqual(evaluate_application_decision({'description_quality':{'status':'insufficient'},
            'ai_analysis_eligibility':{'status':'eligible'},'pre_match':strong},self.profile['profile'])['recommendation'],'review')
        for prematch in ('strong_candidate','possible_candidate'):
            self.assertEqual(evaluate_application_decision({'description_quality':{'status':'complete'},
                'ai_analysis_eligibility':{'status':'excluded','reason':'Hard mismatch.'},
                'pre_match':{'classification':prematch}},self.profile['profile'])['recommendation'],'low_priority')

    def test_profile_fit_existing_recommendation_states_remain_available(self):
        self.assertEqual(evaluate_application_decision({**self.job,'profile_analysis':fit()},self.profile['profile'])['recommendation'],'prioritize')
        moderate=fit('moderate',56,transferable_experience=['Transferível.'],career_value={'rating':'high','rationale':'Estratégico.'})
        self.assertEqual(evaluate_application_decision({**self.job,'profile_analysis':moderate},self.profile['profile'])['recommendation'],'consider')
        self.assertEqual(evaluate_application_decision({**self.job,'profile_analysis':fit('moderate',56),
            'ai_analysis_eligibility':{'status':'review'}},self.profile['profile'])['recommendation'],'consider')
        weak=fit('poor',24,mandatory_requirements=[{'requirement':'Essencial','status':'not_met'}])
        self.assertEqual(evaluate_application_decision({**self.job,'profile_analysis':weak},self.profile['profile'])['recommendation'],'low_priority')

    def test_rule_version_bump_preserves_v1_v2_and_manual_histories(self):
        url=self.job['url'];key=job_key(url);profile_version='local-user:v1'
        manual=self.store.save_user_application_decision(url,{
            'user_decision':'want_to_apply','reason':'career_value','note':'Manter para avaliar.', 'version':0})
        data={'records':[{'jobs':[dict(self.job)]}]}
        with patch.object(application_decision,'RULE_VERSION','application_decision_v1'):
            old_fingerprint=decision_fingerprint(self.job,profile_version)
            old=evaluate_application_decision(self.job,self.profile['profile'])
            old['recommendation']='prioritize'
            old.update(profile_version_id=profile_version,input_fingerprint=old_fingerprint)
            self.store.save_application_decision(key,old_fingerprint,old)
        with patch.object(application_decision,'RULE_VERSION','application_decision_v2'):
            v2_fingerprint=decision_fingerprint(self.job,profile_version)
            v2=evaluate_application_decision(self.job,self.profile['profile'])
            v2.update(profile_version_id=profile_version,input_fingerprint=v2_fingerprint)
            self.store.save_application_decision(key,v2_fingerprint,v2)
        self.assertEqual(RULE_VERSION,'application_decision_v3')
        with contextlib.redirect_stdout(io.StringIO()):attach_application_decisions(data,self.store)
        current=data['records'][0]['jobs'][0]
        self.assertEqual(current['application_decision']['rule_version'],'application_decision_v3')
        self.assertEqual(current['user_decision']['user_decision'],'want_to_apply')
        self.assertEqual(current['user_decision']['version'],manual['version'])
        self.assertEqual(len(current['user_decision']['history']),1)
        self.assertIsNotNone(self.store.get_application_decision(key,old_fingerprint,'application_decision_v1'))
        self.assertIsNotNone(self.store.get_application_decision(key,v2_fingerprint,'application_decision_v2'))
        with self.store.connect() as db:
            versions={row['rule_version'] for row in db.execute('SELECT rule_version FROM application_decisions WHERE job_key=?',(key,))}
            manual_events=db.execute('SELECT COUNT(*) FROM user_application_decision_events WHERE job_key=?',(key,)).fetchone()[0]
        self.assertEqual(versions,{'application_decision_v1','application_decision_v2','application_decision_v3'})
        self.assertEqual(manual_events,1)

    def _calibration_profile(self,**overrides):
        return {'current_location':'Campinas, São Paulo, Brazil','relocation':False,
            'work_modes':{'remote':'acceptable','hybrid':'acceptable','onsite':'unwanted'},
            'work_mode_restrictions':[],'languages':['Portuguese · Native','English · C1'],
            'experience_years':'12','work_history':['Customer Support Specialist · 12 years'],'certifications':[],
            'education':[],**overrides}

    def _calibration_job(self,title,description,location=''):
        return {'title':title,'company':'Example','location':location,'description':description,
            **local_signals(description_quality={'status':'complete'})}

    def test_manila_mandatory_office_conflicts_and_remains_review(self):
        job=self._calibration_job('Customer Support Specialist II',
            'This full-time position requires five-day in-office presence in Manila.', 'Manila, Philippines')
        result=evaluate_application_decision(job,self._calibration_profile())
        self.assertEqual(result['risks']['location']['status'],'conflict')
        self.assertEqual(result['risks']['work_model']['status'],'conflict')
        self.assertEqual(result['recommendation'],'review')

    def test_remote_usa_without_residency_rule_is_not_location_conflict(self):
        job=self._calibration_job('Customer Operations Manager','Work remotely with a US-based team.','Remote - USA')
        risk=evaluate_application_decision(job,self._calibration_profile())['risks']
        self.assertNotEqual(risk['location']['status'],'conflict')
        self.assertEqual(risk['location']['status'],'unknown')

    def test_explicit_us_residency_conflicts_but_missing_work_authorization_is_unknown(self):
        job=self._calibration_job('Customer Operations Manager',
            'Remote — USA. Applicants must reside in the United States. US work authorization required.','Remote - USA')
        risks=evaluate_application_decision(job,self._calibration_profile())['risks']
        self.assertEqual(risks['location']['status'],'conflict')
        self.assertEqual(risks['work_authorization']['status'],'unknown')
        self.assertIn('não informa autorização',risks['work_authorization']['reason'])

    def test_hybrid_london_required_office_days_conflict_when_relocation_disabled(self):
        job=self._calibration_job('Customer Operations Manager',
            'Hybrid role, three days per week in office in London.','Hybrid — London')
        risks=evaluate_application_decision(job,self._calibration_profile())['risks']
        self.assertEqual(risks['location']['status'],'conflict')
        self.assertEqual(risks['work_model']['status'],'conflict')

    def test_remote_worldwide_does_not_create_location_conflict(self):
        job=self._calibration_job('Customer Operations Manager','Remote worldwide.','Remote worldwide')
        self.assertNotEqual(evaluate_application_decision(job,self._calibration_profile())['risks']['location']['status'],'conflict')

    def test_required_languages_use_profile_evidence_and_unknown_absence(self):
        english=self._calibration_job('Support Specialist','Fluent English required.')
        japanese=self._calibration_job('Support Specialist','Fluent Japanese required.')
        profile=self._calibration_profile()
        self.assertEqual(evaluate_application_decision(english,profile)['risks']['language']['status'],'ok')
        result=evaluate_application_decision(japanese,profile)['risks']['language']
        self.assertEqual(result['status'],'unknown')
        self.assertIn('schema não indica',result['reason'])

    def test_hard_requirements_do_not_turn_unproven_absence_into_conflict(self):
        cases=(('Customer Support Specialist','5+ years customer support experience required.',
                self._calibration_profile(), 'ok'),
               ('Software Engineer','5+ years professional software engineering experience required.',
                self._calibration_profile(technologies=['Python · projetos práticos','FastAPI']), 'unknown'),
               ('Accountant','Python preferred.',self._calibration_profile(),'unknown'),
               ('Accountant','CPA certification required.',self._calibration_profile(),'unknown'))
        for title,description,profile,expected in cases:
            with self.subTest(description=description):
                risk=evaluate_application_decision(self._calibration_job(title,description),profile)['risks']['hard_requirements']
                self.assertEqual(risk['status'],expected)
                self.assertNotEqual(risk['status'],'conflict')


if __name__=='__main__':unittest.main()
