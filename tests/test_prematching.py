import tempfile
import unittest
from pathlib import Path

from applications import ApplicationStore
from prematching import classify_pre_match, run_pre_match


class PreMatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ApplicationStore(Path(self.temp.name) / 'applications.sqlite3')
        self.profile = {'desired_roles':['Customer Operations'], 'related_roles_open':True,
                        'interests':['web3'], 'work_modes':{'remote':'preferred','hybrid':'acceptable','onsite':'unwanted'},
                        'work_mode_restrictions':[], 'regions':['Brazil'], 'contract_types':['Full-time'],
                        'skills':['SQL'], 'technologies':[], 'employer_types':[], 'domain_knowledge':[],
                        'excluded_roles':[], 'seniority':'', 'salary_min':'', 'salary_period':'annual'}
        self.store.save_profile(self.profile)
        self.url='https://jobs.example/1'
        self.job={'url':self.url,'title':'Customer Experience Associate','description':'Web3 support operations. SQL. Remote, full-time in Brazil.'}
        self.data={'records':[{'date':'2026-10-03','jobs':[self.job]}]}

    def test_related_job_is_a_candidate(self):
        result=classify_pre_match(self.profile,self.job)
        self.assertIn(result['classification'],{'strong_candidate','possible_candidate'})
        self.assertTrue(any('Cargo relacionado' in signal for signal in result['positive_signals']))

    def test_unrelated_job_is_weak(self):
        result=classify_pre_match({'desired_roles':['Solidity Engineer'],'related_roles_open':False},
                                  {'title':'Executive Chef','description':''})
        self.assertEqual(result['classification'],'weak_candidate')

    def test_related_role_does_not_require_identical_title(self):
        result=classify_pre_match(self.profile,{'title':'CX Operations Lead','description':''})
        self.assertTrue(any('Cargo relacionado' in signal for signal in result['positive_signals']))

    def test_work_preference_and_hard_restriction(self):
        preferred=classify_pre_match(self.profile,{'title':'Role','description':'Remote'})
        self.assertTrue(any('preferido' in signal for signal in preferred['positive_signals']))
        restricted={**self.profile,'work_mode_restrictions':['remote']}
        result=classify_pre_match(restricted,{'title':'Role','description':'Remote'})
        self.assertEqual(result['classification'],'weak_candidate')
        self.assertTrue(any('Restrição obrigatória' in signal for signal in result['negative_signals']))

    def test_missing_information_is_unknown(self):
        result=classify_pre_match(self.profile,{'title':'Customer Operations','description':''})
        self.assertTrue(any('Remuneração ausente' in item for item in result['unknowns']))
        self.assertTrue(any('Localização da vaga não informada' in item for item in result['unknowns']))
        self.assertFalse(any('abaixo do mínimo' in item for item in result['negative_signals']))

    def test_excluded_role_is_a_strong_negative(self):
        profile={**self.profile,'excluded_roles':['Sales Manager']}
        result=classify_pre_match(profile,{'title':'Regional Sales Manager','description':''})
        self.assertTrue(any('Cargo indesejado' in item for item in result['negative_signals']))
        self.assertEqual(result['classification'],'weak_candidate')

    def test_persists_and_reuses_same_profile_job_and_rules(self):
        first=run_pre_match(self.data,self.store,[self.url])
        second=run_pre_match(self.data,self.store,[self.url])
        self.assertEqual(first['reused'],0)
        self.assertEqual(second['reused'],1)

    def test_new_profile_version_gets_new_result(self):
        run_pre_match(self.data,self.store,[self.url])
        changed={**self.profile,'interests':['web3','payments']}
        self.store.save_profile(changed)
        result=run_pre_match(self.data,self.store,[self.url])
        self.assertEqual(result['reused'],0)


if __name__ == '__main__':
    unittest.main()
