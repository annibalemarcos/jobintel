import asyncio
import hashlib
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx

from crawler import (App, Page, Result, VERSION, create_run_dir, load_checkpoint, persist,
                     resume_run_dir, save_checkpoint, validate_config)


ROOT = Path(__file__).resolve().parents[1]


def config(**changes):
    data = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
    data.update(changes)
    return validate_config(data)


class CrawlerRuleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = App(config(delay_between_requests_seconds=0, retry_backoff_seconds=0), ROOT, False)

    async def asyncTearDown(self):
        await self.app.close()

    async def test_visible_and_mailto_emails_exclude_script_bundles(self):
        page = self.app.parse_html('''<html><head><title>Example</title>
            <script>const owner="bundle-noise@aave.com"</script></head><body>
            <p>Talk to careers@aave.com</p><a href="mailto:jobs@aave.com?subject=Hi">Apply</a>
            </body></html>''', 'https://aave.com', 200)
        self.assertEqual(page.emails, ['careers@aave.com', 'jobs@aave.com'])

    async def test_career_landing_and_job_detail_are_separate(self):
        self.assertTrue(self.app.career_url_signal('https://example.com/careers'))
        self.assertFalse(self.app.is_job_detail_url('https://example.com/careers'))
        self.assertTrue(self.app.is_job_detail_url('https://example.com/careers/platform-engineer'))
        self.assertFalse(self.app.career_url_signal('https://example.com/blog/careers-in-web3'))

    async def test_retry_recovers_after_server_error(self):
        responses = [httpx.Response(503), httpx.Response(200)]
        fake = AsyncMock(side_effect=responses)
        self.app.client.get = fake
        response = await self.app.request('https://example.com/page')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(fake.await_count, 2)

    async def test_robots_rules_are_cached_and_enforced(self):
        self.app.request = AsyncMock(return_value=httpx.Response(
            200, text='User-agent: SiteIntelCrawler\nCrawl-delay: 2\nDisallow: /private\nAllow: /public\n'))
        self.assertFalse(await self.app.robots_allowed('https://example.com/private/report'))
        self.assertTrue(await self.app.robots_allowed('https://example.com/public/jobs'))
        self.assertEqual(self.app.request.await_count, 1)
        self.assertEqual(self.app.host_delays['example.com'], 2)

    async def test_extracts_job_posting_json_ld(self):
        page = self.app.parse_html('''<html><head><script type="application/ld+json">
            {"@context":"https://schema.org","@type":"JobPosting","title":"Backend Engineer",
             "description":"<p>Build reliable systems.</p><ul><li>Own production services</li></ul>",
             "hiringOrganization":{"@type":"Organization","name":"Example Labs"},
             "jobLocation":{"@type":"Place","address":{"addressLocality":"Lisbon","addressCountry":"PT"}},
             "datePosted":"2026-09-20"}
            </script></head><body></body></html>''', 'https://example.com/jobs/backend', 200)
        self.assertEqual(self.app.extract_job_details(page), {
            'title':'Backend Engineer','company':'Example Labs','location':'Lisbon, PT',
            'date_posted':'2026-09-20','description':'Build reliable systems.\nOwn production services'
        })

    async def test_preserves_clean_html_job_description(self):
        page = self.app.parse_html('''<html><body><nav>Navigation noise</nav><main>
            <h1>Protocol Engineer</h1><p>Design and maintain the protocol.</p>
            <ul><li>Ship production code.</li><li>Review changes.</li></ul>
            <script>hiddenNoise()</script></main><footer>Footer noise</footer></body></html>''',
            'https://example.com/jobs/protocol', 200)
        description = self.app.extract_job_details(page)['description']
        self.assertIn('Design and maintain the protocol.', description)
        self.assertIn('Ship production code.', description)
        self.assertNotIn('Navigation noise', description)
        self.assertNotIn('hiddenNoise', description)

    async def test_missing_job_posting_fields_are_not_invented(self):
        page = self.app.parse_html('''<html><head><script type="application/ld+json">
            {"@type":"JobPosting","title":"Data Engineer"}
            </script></head><body></body></html>''', 'https://example.com/jobs/data', 200)
        self.assertEqual(self.app.extract_job_details(page), {'title':'Data Engineer'})

    async def test_failed_job_does_not_stop_other_enrichment(self):
        result=Result('example.com',site_name='Example',job_records=[
            {'url':'https://example.com/jobs/missing','title':'Missing Role'},
            {'url':'https://example.com/jobs/good','title':'Old Title'},
        ])
        good=self.app.parse_html('''<script type="application/ld+json">
            {"@type":"JobPosting","title":"Good Role","description":"<p>Complete job description.</p>"}
            </script>''', 'https://example.com/jobs/good', 200)
        self.app.fetch=AsyncMock(side_effect=[None,good])
        self.app.browser_fetch=AsyncMock(return_value=None)
        await self.app.enrich_job_records(result,[])
        self.assertEqual(result.job_records[0], {'url':'https://example.com/jobs/missing','title':'Missing Role'})
        self.assertEqual(result.job_records[1]['title'], 'Good Role')
        self.assertEqual(result.job_records[1]['description'], 'Complete job description.')
        self.assertEqual(self.app.job_stats_summary(),'Vagas: 2 | Cache: 0 | Coletadas: 2 | Sucesso: 1 | Falhas: 1')


class CrawlerPersistenceTests(unittest.TestCase):
    def test_version_and_config_validation(self):
        self.assertEqual(VERSION, '2.1.4')
        self.assertEqual(config()['max_domain_concurrency'], 3)
        with self.assertRaises(ValueError):
            config(max_domain_concurrency=0)
        with self.assertRaises(ValueError):
            config(respect_robots_txt='yes')

    def test_checkpoint_roundtrip_and_explicit_resume(self):
        with tempfile.TemporaryDirectory(prefix='.crawler-tests-', dir=ROOT) as temp:
            folder = Path(temp) / 'run'
            folder.mkdir()
            checkpoint = folder / 'checkpoint.json'
            save_checkpoint(checkpoint, [Result('example.com', alive=True)])
            self.assertTrue(load_checkpoint(checkpoint, strict=True)['example.com'].alive)
            self.assertEqual(resume_run_dir(Path(temp), str(folder)), folder.resolve())

    def test_latest_resume_ignores_completed_runs(self):
        with tempfile.TemporaryDirectory(prefix='.crawler-tests-', dir=ROOT) as temp:
            root = Path(temp)
            incomplete = root / 'incomplete'
            complete = root / 'complete'
            for folder, done in ((incomplete, False), (complete, True)):
                folder.mkdir()
                save_checkpoint(folder / 'checkpoint.json', [Result(folder.name + '.org')])
                (folder / 'run_info.json').write_text(json.dumps({'completed': done}), encoding='utf-8')
            self.assertEqual(resume_run_dir(root, 'latest'), incomplete.resolve())

    def test_invalid_checkpoint_is_not_overwritten_on_resume(self):
        with tempfile.TemporaryDirectory(prefix='.crawler-tests-', dir=ROOT) as temp:
            checkpoint = Path(temp) / 'checkpoint.json'
            checkpoint.write_text('{broken', encoding='utf-8')
            with self.assertRaises(ValueError):
                load_checkpoint(checkpoint, strict=True)

    def test_run_directories_do_not_collide_and_metadata_marks_completion(self):
        with tempfile.TemporaryDirectory(prefix='.crawler-tests-', dir=ROOT) as temp:
            root = Path(temp)
            started = datetime(2026, 10, 3, 12, 30, 0)
            first = create_run_dir(root, started)
            second = create_run_dir(root, started)
            self.assertNotEqual(first, second)
            app = SimpleNamespace(ai=None, model='test-model')
            args = SimpleNamespace(csv='domains.csv')
            result = Result('example.org', alive=True, access_status='OK')
            persist(first, args, started.isoformat(), ['example.org'], {'example.org': result},
                    app, ai_requested=False, completed=True, resumed=False)
            info = json.loads((first / 'run_info.json').read_text(encoding='utf-8'))
            self.assertTrue(info['completed'])
            self.assertFalse(info['resumed'])
            self.assertEqual(info['domains_processed'], 1)


class JobCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='.job-cache-tests-',dir=ROOT)
        self.cache_path=Path(self.temp.name)/'jobs_cache.sqlite3'
        self.app=App(config(delay_between_requests_seconds=0),ROOT,False,job_cache_path=self.cache_path)

    async def asyncTearDown(self):
        await self.app.close()
        self.temp.cleanup()

    def job_page(self,description):
        return self.app.parse_html(f'''<script type="application/ld+json">{{
            "@type":"JobPosting","title":"Cached Engineer","company":"Example",
            "description":{json.dumps(description)}
        }}</script>''','https://example.com/jobs/cached',200)

    async def test_new_job_is_collected_and_stored(self):
        result=Result('example.com',job_records=[{'url':'https://example.com/jobs/cached','title':'Engineer'}])
        self.app.fetch=AsyncMock(return_value=self.job_page('A complete new description.'))
        await self.app.enrich_job_records(result,[])
        cached=self.app.job_cache.get_success(self.app.job_url_key(result.job_records[0]['url']))
        self.assertEqual(cached['description'],'A complete new description.')
        self.assertEqual(cached['status'],'success')
        self.assertTrue(cached['collected_at'])

    async def test_successful_cached_job_skips_request(self):
        url='https://EXAMPLE.com/jobs/cached/'
        self.app.job_cache.store(self.app.job_url_key(url),{'url':url,'title':'Stored','description':'Stored description.'},'success')
        result=Result('example.com',job_records=[{'url':'https://example.com/jobs/cached','title':'Discovered'}])
        self.app.fetch=AsyncMock()
        await self.app.enrich_job_records(result,[])
        self.app.fetch.assert_not_awaited()
        self.assertEqual(result.job_records[0]['description'],'Stored description.')
        self.assertEqual(self.app.job_stats_summary(),'Vagas: 1 | Cache: 1 | Coletadas: 0 | Sucesso: 1 | Falhas: 0')

    async def test_description_hash_is_sha256(self):
        url='https://example.com/jobs/cached'
        description='Hash this exact description.'
        self.app.job_cache.store(self.app.job_url_key(url),{'url':url,'description':description},'success')
        cached=self.app.job_cache.get_success(self.app.job_url_key(url))
        self.assertEqual(cached['description_hash'],hashlib.sha256(description.encode('utf-8')).hexdigest())

    async def test_failed_cached_job_is_retried(self):
        url='https://example.com/jobs/cached'
        self.app.job_cache.store(self.app.job_url_key(url),{'url':url,'title':'Old'},'failed')
        result=Result('example.com',job_records=[{'url':url,'title':'Engineer'}])
        self.app.fetch=AsyncMock(return_value=self.job_page('Recovered description.'))
        await self.app.enrich_job_records(result,[])
        self.app.fetch.assert_awaited_once_with(url)
        self.assertEqual(result.job_records[0]['description'],'Recovered description.')

    async def test_fresh_ignores_cache_without_deleting_it(self):
        url='https://example.com/jobs/cached'
        key=self.app.job_url_key(url)
        self.app.job_cache.store(key,{'url':url,'description':'Old cached description.'},'success')
        self.app.ignore_job_cache=True
        result=Result('example.com',job_records=[{'url':url,'title':'Engineer'}])
        self.app.fetch=AsyncMock(return_value=self.job_page('Freshly collected description.'))
        await self.app.enrich_job_records(result,[])
        self.app.fetch.assert_awaited_once_with(url)
        self.assertEqual(self.app.job_cache.get_success(key)['description'],'Freshly collected description.')
        self.assertEqual(self.app.job_stats_summary(),'Vagas: 1 | Cache: 0 | Coletadas: 1 | Sucesso: 1 | Falhas: 0')


if __name__ == '__main__':
    unittest.main()
