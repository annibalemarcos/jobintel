import json
import tempfile
import unittest
from pathlib import Path

from applications import ApplicationStore, ConflictError, blank_application, enrich, job_key


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='.tracking-tests-', dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.store = ApplicationStore(Path(self.temp.name) / 'applications.sqlite3')

    def body(self, status='applied', version=0):
        return {'url':'https://aave.com/careers/engineer','title':'Engineer','company':'Aave','domain':'aave.com',
                'version':version,'application':{**blank_application(),'status':status,'applied_at':'2026-10-03','notes':'Meu currículo'}}

    def test_persistence_history_conflicts_and_delete(self):
        self.store.save(self.body())
        restarted = ApplicationStore(self.store.path)
        apps, scores = restarted.snapshot()
        key=job_key(self.body()['url'])
        self.assertEqual(apps[key]['status'],'applied')
        self.assertEqual(apps[key]['version'],1)
        self.assertEqual(len(apps[key]['history']),1)
        restarted.save(self.body('interview',1))
        with self.assertRaises(ConflictError):
            restarted.save(self.body('rejected',1))
        self.assertEqual(restarted.snapshot()[0][key]['status'],'interview')
        self.assertEqual(len(restarted.snapshot()[0][key]['history']),2)
        with self.assertRaises(ConflictError):
            restarted.delete(self.body()['url'],1)
        restarted.delete(self.body()['url'],2)
        self.assertEqual(restarted.snapshot()[0],{})

    def test_profile_first_version_persists_and_validates(self):
        profile=self.store.save_profile({'desired_roles':['Protocol Engineer','Platform Engineer'],'related_roles_open':True})
        self.assertEqual(profile['version'],1)
        restarted=ApplicationStore(self.store.path)
        loaded=restarted.get_profile()
        self.assertEqual(loaded['version'],1)
        self.assertEqual(loaded['profile']['desired_roles'],['Protocol Engineer','Platform Engineer'])
        with self.assertRaises(ValueError): self.store.save_profile({'salary_min':'not a number'})

    def test_profile_changes_create_immutable_versions_and_noop_does_not(self):
        first=self.store.save_profile({'desired_roles':['Engineer']})
        same=self.store.save_profile(first['profile'])
        self.assertEqual(same['version'],1)
        second_profile={**first['profile'],'seniority':'Senior'}
        second=self.store.save_profile(second_profile)
        self.assertEqual(second['version'],2)
        with self.store.connect() as db:
            rows=db.execute('SELECT version,preferences_json FROM candidate_profiles ORDER BY version').fetchall()
            active=db.execute('SELECT version FROM active_candidate_profile').fetchone()['version']
        self.assertEqual([row['version'] for row in rows],[1,2])
        self.assertEqual(json.loads(rows[0]['preferences_json'])['seniority'],'')
        self.assertEqual(active,2)

    def test_resume_upload_and_replacement_are_versioned_and_preserved(self):
        pdf=b'%PDF-1.7 test resume'
        first=self.store.save_profile({},('resume.pdf','application/pdf',pdf))
        first_path=self.store.current_resume_path()
        self.assertTrue(first_path.is_file())
        self.assertEqual(first['profile']['resume']['original_name'],'resume.pdf')
        second=self.store.save_profile(first['profile'],('resume.pdf','application/pdf',b'%PDF-1.7 replacement'))
        second_path=self.store.current_resume_path()
        self.assertEqual(second['version'],2)
        self.assertNotEqual(first_path,second_path)
        self.assertTrue(first_path.is_file())
        self.assertEqual(second['profile']['resume']['profile_version'],2)
        with self.assertRaises(ValueError): self.store.save_profile(second['profile'],('resume.exe','application/octet-stream',b'no'))

    def test_validation_preserves_previous_record(self):
        self.store.save(self.body())
        for change in [{'status':'fake'},{'applied_at':'2026-99-99'},{'priority':'invalid'},{'notes':'x'*20001}]:
            body=self.body(version=1)
            body['application'].update(change)
            with self.assertRaises(ValueError):
                self.store.save(body)
        self.assertEqual(next(iter(self.store.snapshot()[0].values()))['version'],1)
        for url in ['javascript:alert(1)','https://user:pass@example.com']:
            with self.assertRaises(ValueError):job_key(url)

    def test_shared_across_runs_and_unscored_is_null(self):
        self.store.save(self.body())
        template={'domain':'aave.com','jobs':[{'url':self.body()['url'],'title':'Engineer'}]}
        rows=[json.loads(json.dumps(template)) for _ in range(2)]
        rows[1]['jobs'][0]['url']+='/'
        data=enrich({'records':rows,'runs':[]},self.store)
        self.assertEqual(len(data['records']),2)
        for row in rows:
            self.assertEqual(row['jobs'][0]['application']['status'],'applied')
            self.assertIsNone(row['jobs'][0]['score']['total'])
            self.assertEqual(row['jobs'][0]['score']['status'],'not_analyzed')

    def test_manual_record_and_prepared_score_schema(self):
        body=self.body('saved');body['manual']=True
        self.store.save(body)
        result=enrich({'records':[],'runs':[]},self.store)
        self.assertEqual(result['records'][0]['run_id'],'tracking')
        key=job_key(body['url'])
        with self.store.connect() as db:
            db.execute("INSERT INTO candidate_profiles(id,version,updated_at) VALUES('profile',1,'2026-10-03')")
            db.execute("INSERT INTO job_scores(job_key,profile_id,profile_version,total,skills,analyzed_at,explanation) VALUES(?,?,1,82,90,'2026-10-03','Fixture de teste')",(key,'profile'))
        self.assertEqual(enrich({'records':[],'runs':[]},self.store)['records'][0]['jobs'][0]['score']['total'],82)
        self.store.delete(body['url'],1)
        self.assertEqual(self.store.snapshot()[1][key]['total'],82,'Deleting tracking must not delete future scores')

    def test_key_normalizes_host_port_fragment_slash_and_query(self):
        self.assertEqual(job_key('https://AAVE.COM:443/jobs/1/?b=2&a=1#apply'),job_key('https://aave.com/jobs/1?a=1&b=2'))


if __name__ == '__main__':unittest.main()
