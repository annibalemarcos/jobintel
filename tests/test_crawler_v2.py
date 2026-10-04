import asyncio
import csv
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from crawler_v2 import (App, Page, Result, validate_config, write_jobs_csv,
                        write_jobs_profiled_csv, write_jobs_rejected_csv,
                        write_browser_fallbacks_csv)
from profile_matcher import (PROFILE_FILTER_VERSION, ProfileMatcher, load_profile,
                             validate_resume_profile)


def sample_profile(**changes):
    profile = {
        'desired_roles': ['Customer Operations', 'Product Operations', 'P2P Dispute Resolution',
                          'Community Moderator', 'Social Media Manager', 'LATAM Operations',
                          'Regional Operations', 'Operations Manager', 'Head of Support'],
        'excluded_roles': ['Junior Customer Support', 'Tier 1 Customer Support', 'Internship',
                           'Psychologist', 'Psychology'],
        'related_roles_open': True,
        'professional_summary': 'Customer support and operations leader for remote teams.',
        'work_history': ['Head of Support - fictional company', 'P2P Dispute Moderator'],
        'skills': ['Customer Support', 'Python'],
        'domain_knowledge': ['Trust & Safety', 'Web3 Operations', 'P2P Dispute Resolution'],
        'interests': ['Implementation', 'Risk Operations', 'Knowledge Management'],
        'work_modes': {'remote': 'preferred', 'hybrid': 'acceptable', 'onsite': 'unwanted'},
        'regions': ['Brazil', 'LATAM', 'Worldwide Remote'],
        'relocation': False,
        'languages': ['Portuguese', 'English'],
        'analysis_notes': 'Technical projects do not represent professional engineering experience.',
    }
    profile.update(changes)
    return profile


def save_profile(path, profile=None, **document_changes):
    document = {'format': 'test-profile', 'profile_version': 3, 'profile': profile or sample_profile()}
    document.update(document_changes)
    path.write_text(json.dumps(document, ensure_ascii=False), encoding='utf-8')
    return path


def app_config(**changes):
    return {
        'request_timeout_seconds': 2, 'max_concurrency': 4,
        'max_browser_concurrency': 1, 'max_ai_concurrency': 1,
        'max_job_concurrency': 2, 'delay_between_requests_seconds': 0,
        'use_browser_fallback': False, **changes,
    }


class ProfileMatcherTests(unittest.TestCase):
    def test_profile_json_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'profile.json'
            with self.assertRaisesRegex(ValueError, 'não encontrado'):
                load_profile(path)
            path.write_text('{not json', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'JSON'):
                load_profile(path)
            path.write_text(json.dumps({'version': 1}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'profile'):
                load_profile(path)
            save_profile(path, {'desired_roles': 'Customer Operations'})
            with self.assertRaisesRegex(ValueError, 'desired_roles'):
                load_profile(path)

    def test_fingerprint_is_stable_and_tracks_classification_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / 'first.json'
            second = Path(tmp) / 'second.json'
            profile = sample_profile()
            save_profile(first, profile)
            save_profile(second, dict(reversed(list(profile.items()))))
            original = load_profile(first).fingerprint
            self.assertEqual(original, load_profile(second).fingerprint)
            changed = sample_profile(**{'desired_roles': ['Risk Operations Manager']})
            save_profile(second, changed)
            self.assertNotEqual(original, load_profile(second).fingerprint)
            self.assertEqual(PROFILE_FILTER_VERSION, 'profile_filter_v1')

    def test_title_gate_required_examples_and_functional_variations(self):
        matcher = ProfileMatcher(sample_profile())
        likely = (
            'Customer Operations Manager', 'Senior Customer Operations Lead', 'Customer Ops Manager',
            'Head of Customer Support', 'Customer Support Operations Manager', 'LATAM Operations Manager',
            'Regional Operations Lead', 'Product Operations Manager', 'Product Ops Lead',
            'Community Operations Manager', 'Community Moderator', 'Community Moderation Specialist',
            'P2P Dispute Moderator', 'P2P Dispute Resolution Specialist', 'Social Media Manager',
        )
        for title in likely:
            with self.subTest(title=title):
                self.assertEqual(matcher.classify_title(title).classification, 'LIKELY_RELEVANT')

        irrelevant = (
            'Solutions Engineer', 'Enterprise Partnerships Lead (Financial Markets)',
            'Senior Software Engineer', 'Software Engineer, Backend', 'Backend Engineer',
            'Frontend Engineer', 'DevOps Engineer', 'Data Engineer', 'Machine Learning Engineer',
            'Senior Data Scientist', 'Account Executive', 'Quantitative Researcher',
            'Trader', 'Legal Counsel', 'Graphic Designer', 'Junior Customer Support',
            'Tier 1 Customer Support', 'Psychologist', 'Psychology', 'Internship',
            'Security Operations Engineer', 'IT Operations Engineer', 'Trading Operations Quant',
            'Revenue Operations Analyst',
        )
        for title in irrelevant:
            with self.subTest(title=title):
                self.assertEqual(matcher.classify_title(title).classification, 'CLEARLY_IRRELEVANT')

        for title in ('Customer Support', 'Customer Support Engineer', 'Risk Operations Analyst', 'Technical Program Manager',
                      'Product Manager', 'Operations Manager, Engineering Support', 'Unknown Role'):
            with self.subTest(title=title):
                self.assertNotEqual(matcher.classify_title(title).classification, 'CLEARLY_IRRELEVANT')

    def test_desired_roles_override_generic_negative_and_related_flag_is_respected(self):
        explicit = ProfileMatcher(sample_profile(desired_roles=['Solutions Engineer']))
        self.assertEqual(explicit.classify_title('Senior Solutions Engineer').classification, 'LIKELY_RELEVANT')
        open_matcher = ProfileMatcher(sample_profile())
        closed_matcher = ProfileMatcher(sample_profile(related_roles_open=False))
        self.assertEqual(open_matcher.classify_title('Implementation Specialist').classification, 'POSSIBLE')
        self.assertEqual(closed_matcher.classify_title('Implementation Specialist').classification, 'POSSIBLE')
        self.assertIn('closed', closed_matcher.classify_title('Implementation Specialist').reason)
        self.assertEqual(open_matcher.classify_title('P2P Dispute Moderator').classification, 'LIKELY_RELEVANT')
        closed_job = closed_matcher.classify_job({'title': 'Risk Operations Analyst', 'description': 'Risk Operations role'})
        self.assertEqual(closed_job.classification, 'REVIEW')

    def test_skills_do_not_override_a_clear_engineering_mismatch(self):
        technical = ProfileMatcher(sample_profile(skills=['Python', 'FastAPI', 'APIs']))
        self.assertEqual(technical.classify_title('Software Engineer').classification, 'CLEARLY_IRRELEVANT')

    def test_stage_two_secondary_description_evidence_cannot_promote_other_families(self):
        matcher = ProfileMatcher(sample_profile())
        description = ('Smart contract security, protocol security, attacker-driven analysis, complex codebases, '
                       'exploitability, audits, security research, knowledge management.')
        self.assertEqual(matcher.classify_title('Protocol Security Researcher').classification, 'POSSIBLE')
        outcome = matcher.classify_job({'title': 'Protocol Security Researcher', 'description': description})
        self.assertEqual(outcome.classification, 'NOT_RELEVANT')

        secondary_only = ('Communication, project management, cross-functional collaboration, process improvement, '
                          'leadership, troubleshooting, documentation, AI, Web3, crypto, knowledge management.')
        ambiguous = matcher.classify_job({'title': 'Unknown Role', 'description': secondary_only})
        self.assertEqual(ambiguous.classification, 'REVIEW')

    def test_stage_two_functional_description_keeps_aligned_roles_relevant(self):
        matcher = ProfileMatcher(sample_profile())
        jobs = {
            'Customer Operations Manager': 'Leads the Customer Operations function and support workflows.',
            'Product Operations Manager': 'Owns Product Operations, product workflows and cross-functional launches.',
            'Head of Customer Support': 'Leads the customer support organization and support leadership.',
            'P2P Dispute Moderator': 'Reviews and resolves P2P dispute cases and dispute resolution workflows.',
        }
        for title, description in jobs.items():
            with self.subTest(title=title):
                outcome = matcher.classify_job({'title': title, 'description': description})
                self.assertEqual(outcome.classification, 'RELEVANT')

    def test_description_unknown_is_review_and_does_not_infer_salary_mismatch(self):
        matcher = ProfileMatcher(sample_profile())
        outcome = matcher.classify_job({'title': 'Unknown Role', 'salary_min': 999999})
        self.assertEqual(outcome.classification, 'REVIEW')
        self.assertIn('description unavailable', outcome.reason)

    def test_only_explicit_work_mode_or_location_restrictions_are_hard_conflicts(self):
        restricted = ProfileMatcher(sample_profile(work_mode_restrictions=[{'mode': 'onsite', 'allowed': False}]))
        onsite = restricted.classify_job({'title': 'Customer Operations Manager', 'description': 'Onsite only',
                                          'location': 'New York, United States', 'work_mode': 'onsite'})
        self.assertEqual(onsite.classification, 'NOT_RELEVANT')
        open_remote = ProfileMatcher(sample_profile())
        remote_us = open_remote.classify_job({'title': 'Customer Operations Manager',
                                              'description': 'Remote role, based in the United States',
                                              'location': 'United States', 'work_mode': 'remote'})
        self.assertEqual(remote_us.classification, 'RELEVANT')
        self.assertFalse(any('location conflict' in signal for signal in remote_us.signals))

    def test_resume_requires_same_profile_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = save_profile(Path(tmp) / 'profile.json')
            fingerprint = load_profile(path).fingerprint
            validate_resume_profile({'profile_fingerprint': fingerprint}, fingerprint)
            with self.assertRaisesRegex(ValueError, 'diferente'):
                validate_resume_profile({'profile_fingerprint': 'different'}, fingerprint)
            with self.assertRaisesRegex(ValueError, 'diferente'):
                validate_resume_profile({}, fingerprint)


class CrawlerV2PerformanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_job_enrichment_is_bounded_and_performance_summary_uses_real_counters(self):
        cfg = {
            'request_timeout_seconds': 2, 'max_concurrency': 4,
            'max_browser_concurrency': 1, 'max_ai_concurrency': 1,
            'max_job_concurrency': 2, 'delay_between_requests_seconds': 0,
            'use_browser_fallback': False,
        }
        with tempfile.TemporaryDirectory() as tmp:
            app = App(cfg, Path(tmp), False)
            active = peak = calls = 0

            async def fetch(url):
                nonlocal active, peak, calls
                active += 1
                calls += 1
                peak = max(peak, active)
                await asyncio.sleep(0.01)
                active -= 1
                return Page(url, 200, title='Role', text='Role description')

            app.fetch = fetch
            app.extract_job_details = lambda page: {'description': page.text}
            app.browser_fetch = AsyncMock(return_value=None)
            result = Result('example.com', site_name='Example', job_records=[
                {'url': f'https://example.com/jobs/{index}', 'title': f'Role {index}'}
                for index in range(4)
            ])
            try:
                await app.enrich_job_records(result, [])
                self.assertEqual(calls, 4)
                self.assertGreater(peak, 1)
                self.assertLessEqual(peak, 2)
                self.assertEqual(app.job_stats['success'], 4)

                app.performance.update(http_requests=12, retries=2, timeouts=1)
                summary = app.performance_summary(60, 4)
                self.assertIn('Domínios/minuto: 4.0', summary)
                self.assertIn('HTTP requests: 12', summary)
                self.assertIn('Timeouts: 1 | Retries: 2', summary)
            finally:
                await app.close()


class ProfileFilterPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = App(app_config(), Path(self.tmp.name), False,
                       profile_matcher=ProfileMatcher(sample_profile()))

    async def asyncTearDown(self):
        await self.app.close()
        self.tmp.cleanup()

    async def test_clearly_irrelevant_job_never_fetches_or_uses_browser(self):
        self.app.fetch = AsyncMock()
        self.app.browser_fetch = AsyncMock()
        result = Result('example.test', site_name='Fictional', job_records=[{
            'title': 'Solutions Engineer', 'url': 'https://example.test/jobs/123'}])
        await self.app.process_job_pipeline(result, [])
        self.assertEqual(result.profile_rejected_jobs[0]['classification'], 'CLEARLY_IRRELEVANT')
        self.app.fetch.assert_not_awaited()
        self.app.browser_fetch.assert_not_awaited()
        self.assertEqual(result.profile_stats['job_pages_avoided'], 1)

    async def test_desired_role_passes_gate_and_is_enriched_concurrently(self):
        active = peak = 0
        async def fetch(url):
            nonlocal active, peak
            active += 1;peak=max(peak,active)
            await asyncio.sleep(0.01)
            active -= 1
            return Page(url, 200, title='Customer Operations Manager', text='Customer operations')
        self.app.fetch = fetch
        self.app.extract_job_details = lambda page: {'description': 'Customer operations responsibilities and support leadership.'}
        self.app.browser_fetch = AsyncMock(return_value=None)
        jobs = [
            {'title': 'Customer Operations Manager', 'url': f'https://example.test/jobs/{i}'}
            for i in range(4)
        ]
        result = Result('example.test', site_name='Fictional', job_records=jobs)
        await self.app.process_job_pipeline(result, [])
        self.assertEqual(len(result.job_records), 4)
        self.assertEqual({job['profile_relevance'] for job in result.job_records}, {'RELEVANT'})
        self.assertGreater(peak, 1)
        self.assertLessEqual(peak, 2)
        self.assertEqual(result.profile_stats['jobs_enriched'], 4)

    async def test_ai_added_job_gets_gate_before_individual_fetch(self):
        self.app.ai = object()
        async def add_irrelevant(res, pages):
            res.job_records.append({'title': 'Solutions Engineer', 'url': 'https://example.test/jobs/ai'})
        self.app.ai_enrich = add_irrelevant
        self.app.fetch = AsyncMock(return_value=Page(
            'https://example.test/jobs/good', 200, title='Customer Operations Manager', text='details'))
        self.app.extract_job_details = lambda page: {'description': 'Customer operations responsibilities.'}
        self.app.browser_fetch = AsyncMock(return_value=None)
        result = Result('example.test', site_name='Fictional', job_records=[{
            'title': 'Customer Operations Manager', 'url': 'https://example.test/jobs/good'}])
        await self.app.process_job_pipeline(result, [])
        self.app.fetch.assert_awaited_once_with('https://example.test/jobs/good')
        self.assertEqual([job['title'] for job in result.job_records], ['Customer Operations Manager'])
        self.assertEqual(result.profile_rejected_jobs[0]['title'], 'Solutions Engineer')

    async def test_failed_job_does_not_prevent_other_jobs_and_browser_limit_is_separate(self):
        self.assertIsNot(self.app.job_semaphore, self.app.browser_semaphore)
        self.assertEqual(self.app.browser_semaphore._value, 1)
        self.assertEqual(self.app.job_semaphore._value, 2)
        async def fetch(url):
            if url.endswith('/bad'):
                raise RuntimeError('mocked page failure')
            return Page(url, 200, title='Customer Operations Manager', text='details')
        self.app.fetch = fetch
        self.app.extract_job_details = lambda page: {'description': 'Customer operations responsibilities.'}
        self.app.browser_fetch = AsyncMock(return_value=None)
        result = Result('example.test', site_name='Fictional', job_records=[
            {'title': 'Customer Operations Manager', 'url': 'https://example.test/jobs/bad'},
            {'title': 'Customer Operations Manager', 'url': 'https://example.test/jobs/good'},
        ])
        await self.app.process_job_pipeline(result, [])
        self.assertEqual(len(result.job_records), 2)
        self.assertEqual(self.app.job_stats['failures'], 1)
        self.assertEqual(self.app.job_stats['success'], 1)

    async def test_cache_hit_and_duplicate_url_do_not_fetch_twice(self):
        await self.app.close()
        cache_path = Path(self.tmp.name) / 'jobs.sqlite3'
        self.app = App(app_config(), Path(self.tmp.name), False, job_cache_path=cache_path,
                       profile_matcher=ProfileMatcher(sample_profile()))
        self.app.fetch = AsyncMock()
        self.app.browser_fetch = AsyncMock()
        url = 'https://example.test/jobs/cached'
        job = {'url': url, 'title': 'Customer Operations Manager', 'description': 'cached description'}
        self.app.job_cache.store(self.app.job_url_key(url), job, 'success')
        result = Result('example.test', site_name='Fictional', job_records=[dict(job), dict(job)])
        await self.app.process_job_pipeline(result, [])
        self.app.fetch.assert_not_awaited()
        self.app.browser_fetch.assert_not_awaited()
        self.assertEqual(self.app.job_stats['cache_hits'], 1)
        self.assertEqual(len(result.job_records), 1)

    def test_profile_csv_outputs_keep_audit_fields(self):
        result = Result('example.test', site_name='Fictional', job_records=[{
            'title': 'Customer Operations Manager', 'url': 'https://example.test/jobs/1',
            'title_gate': 'LIKELY_RELEVANT', 'profile_relevance': 'RELEVANT',
            'profile_relevance_reason': 'desired role match'}], profile_rejected_jobs=[{
                'title': 'Solutions Engineer', 'url': 'https://example.test/jobs/2',
                'stage': 'TITLE_GATE', 'classification': 'CLEARLY_IRRELEVANT',
                'reason': 'engineering family outside desired roles'}])
        profiled = Path(self.tmp.name) / 'jobs_profiled.csv'
        rejected = Path(self.tmp.name) / 'jobs_rejected.csv'
        write_jobs_profiled_csv(profiled, [result]);write_jobs_rejected_csv(rejected, [result])
        self.assertIn('Title Gate', profiled.read_text(encoding='utf-8-sig'))
        self.assertIn('engineering family outside desired roles', rejected.read_text(encoding='utf-8-sig'))


class DiscoveryOptimizationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config = validate_config({
            'max_pages_per_domain': 45, 'max_depth': 3, 'request_timeout_seconds': 2,
            'max_concurrency': 4, 'checkpoint_every': 1, 'max_ats_job_pages': 5,
            'career_keywords': ['career', 'careers', 'jobs', 'hiring'],
            'priority_paths': ['/careers', '/jobs', '/about', '/contact'],
            'email_localpart_blacklist': [], 'email_localpart_preferred': [],
            'delay_between_requests_seconds': 0, 'use_browser_fallback': False,
        })
        self.app = App(config, Path(self.tmp.name), False)

    async def asyncTearDown(self):
        await self.app.close()
        self.tmp.cleanup()

    async def test_careers_outranks_docs_and_blog_and_early_stops_general_discovery(self):
        home = Page('https://sample.test/', 200, title='Sample', text='Company homepage', links=[
            'https://sample.test/docs', 'https://sample.test/blog', 'https://sample.test/careers'])
        requested = []
        async def fetch(url):
            requested.append(url)
            return Page(url, 200, title='Careers' if '/careers' in url else 'Content', text='Useful company content')
        self.app.resolve_home = AsyncMock(return_value=(home, 'OK'))
        self.app.fetch = fetch
        self.app.process_job_pipeline = AsyncMock()
        self.app.ashby_jobs = AsyncMock(return_value=[])
        result = await self.app.crawl_domain('sample.test')
        self.assertEqual(requested[0], 'https://sample.test/careers')
        self.assertNotIn('https://sample.test/docs', requested)
        self.assertNotIn('https://sample.test/blog', requested)
        self.assertEqual(self.app.performance['career_pages_fetched'], 1)
        self.assertEqual(self.app.performance['domains_early_stopped'], 1)

    async def test_help_tree_is_bounded_and_low_value_pages_are_not_deeply_fetched(self):
        home = Page('https://sample.test/', 200, title='Sample', text='Company', links=['https://sample.test/help'])
        help_page = Page('https://sample.test/help', 200, title='Help', text='Help home',
                         links=[f'https://sample.test/help/article-{n}' for n in range(40)])
        self.app.resolve_home = AsyncMock(return_value=(home, 'OK'))
        self.app.fetch = AsyncMock(return_value=help_page)
        self.app.process_job_pipeline = AsyncMock()
        self.app.ashby_jobs = AsyncMock(return_value=[])
        await self.app.crawl_domain('sample.test')
        self.assertEqual(self.app.fetch.await_count, 1)
        self.assertEqual(self.app.performance['low_value_pages_skipped'], 40)

    async def test_failed_secondary_low_value_page_does_not_start_browser(self):
        home=Page('https://sample.test/',200,title='Sample',text='Company',links=['https://sample.test/blog/post'])
        self.app.resolve_home=AsyncMock(return_value=(home,'OK'))
        self.app.fetch=AsyncMock(return_value=None)
        self.app.browser_fetch=AsyncMock()
        self.app.process_job_pipeline=AsyncMock()
        self.app.ashby_jobs=AsyncMock(return_value=[])
        await self.app.crawl_domain('sample.test')
        self.app.browser_fetch.assert_not_awaited()
        self.assertEqual(self.app.performance['browser_fallbacks_skipped'],1)

    def test_general_query_variants_are_deduped_but_job_queries_are_preserved(self):
        first = self.app.normalize_discovery_url('https://sample.test/?to=ethereum')
        second = self.app.normalize_discovery_url('https://sample.test/?to=arbitrum')
        self.assertEqual(first, second)
        self.assertEqual(self.app.performance['query_variants_deduped'], 1)
        job_url = 'https://sample.test/jobs/role?gh_jid=123&source=board'
        self.assertEqual(self.app.normalize_discovery_url(job_url), job_url)

    def test_browser_fallback_is_limited_to_high_value_purposes(self):
        self.assertTrue(self.app.browser_fallback_worthwhile('https://sample.test/', 'homepage'))
        self.assertTrue(self.app.browser_fallback_worthwhile('https://sample.test/careers', 'career'))
        self.assertTrue(self.app.browser_fallback_worthwhile('https://sample.test/jobs/role', 'job_detail'))
        self.assertFalse(self.app.browser_fallback_worthwhile('https://sample.test/blog/post', 'general'))

    async def test_job_detail_can_use_browser_during_post_title_gate_enrichment(self):
        self.app.fetch = AsyncMock(return_value=None)
        page = Page('https://sample.test/jobs/role', 200, title='Customer Operations Manager', text='role details')
        self.app.browser_fetch = AsyncMock(return_value=page)
        self.app.extract_job_details = lambda _: {'description': 'Customer Operations responsibilities'}
        result = Result('sample.test', job_records=[{'url': page.url, 'title': 'Customer Operations Manager'}])
        await self.app.enrich_job_records(result, [])
        self.app.browser_fetch.assert_awaited_once_with(page.url,'job_detail',None,'')

    async def test_discovery_defers_identifiable_job_link_without_fetching_detail(self):
        job_url='https://sample.test/careers/customer-operations-manager'
        home=Page('https://sample.test/',200,title='Sample',text='Company',links=['https://sample.test/careers'],
                  link_anchors={'https://sample.test/careers':'Careers'})
        career=Page('https://sample.test/careers',200,title='Careers',text='Open roles',links=[job_url],
                    link_anchors={job_url:'Customer Operations Manager'})
        calls=[]
        async def fetch(url):
            calls.append(url)
            return career
        self.app.resolve_home=AsyncMock(return_value=(home,'OK'))
        self.app.fetch=fetch
        self.app.process_job_pipeline=AsyncMock()
        self.app.ashby_jobs=AsyncMock(return_value=[])
        result=await self.app.crawl_domain('sample.test')
        self.assertNotIn(job_url,calls)
        self.assertIn({'url':job_url,'title':'Customer Operations Manager'},result.job_records)
        self.assertEqual(self.app.performance['job_detail_pages_deferred'],1)

    async def test_retryable_status_retries_and_404_403_do_not(self):
        class Response:
            def __init__(self,status,headers=None):self.status_code=status;self.headers=headers or {}
        self.app.cfg['request_retries']=2
        self.app.client.get=AsyncMock(side_effect=[Response(503),Response(200)])
        with unittest.mock.patch('crawler_v2.asyncio.sleep',new_callable=AsyncMock):
            response=await self.app.request('https://retry.test/page')
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.app.performance['retries'],1)
        for status in (404,403):
            self.app.client.get=AsyncMock(return_value=Response(status))
            response=await self.app.request(f'https://fixed{status}.test/page')
            self.assertEqual(response.status_code,status)
            self.app.client.get.assert_awaited_once()

    async def test_retry_after_is_respected_and_retry_budget_is_bounded(self):
        class Response:
            def __init__(self,status,headers=None):self.status_code=status;self.headers=headers or {}
        self.app.cfg['request_retries']=3
        self.app.cfg['max_retries_per_host']=1
        self.app.client.get=AsyncMock(side_effect=[Response(429,{'retry-after':'2'}),Response(503),Response(503)])
        with unittest.mock.patch('crawler_v2.asyncio.sleep',new_callable=AsyncMock) as sleep:
            await self.app.request('https://retry-budget.test/page')
        self.assertGreaterEqual(self.app.performance['backoff_seconds'],2)
        self.assertEqual(self.app.performance['retries'],1)
        self.assertEqual(self.app.performance['retry_budget_exhausted'],1)
        self.assertEqual(self.app.client.get.await_count,2)

    async def test_ashby_api_remains_structured_and_local_mockable(self):
        class Response:
            status_code=200
            def json(self):return {'jobs':[{'isListed':True,'jobUrl':'https://jobs.ashbyhq.com/board/role','title':'Operations Manager'}]}
        self.app.client.get=AsyncMock(return_value=Response())
        jobs=await self.app.ashby_jobs('https://jobs.ashbyhq.com/board')
        self.assertEqual(jobs[0][:2],('https://jobs.ashbyhq.com/board/role','Operations Manager'))
        self.app.client.get.assert_awaited_once()

    def test_browser_timeout_and_discovery_budgets_are_configurable(self):
        self.assertEqual(self.app.cfg['browser_timeout_seconds'],25)
        self.assertEqual(self.app.cfg['max_general_pages_per_domain'],12)
        self.assertEqual(self.app.cfg['max_retries_per_host'],8)

    async def _with_mock_browser(self, callback, *, html='<html><body>Rendered vacancy page</body></html>',
                                 error=None, content_errors=None, final_url='https://jobs.sample.test/rendered'):
        owner=self
        pending_content_errors=list(content_errors or [])
        class BrowserPage:
            url=final_url
            async def goto(self,*args,**kwargs):
                if error:raise error
                return types.SimpleNamespace(status=200)
            async def wait_for_load_state(self,state,timeout):
                owner.browser_state_waits+=1
                owner.browser_state_timeouts.append(timeout)
            async def wait_for_timeout(self,*args):pass
            async def content(self):
                owner.browser_content_calls+=1
                if pending_content_errors:raise pending_content_errors.pop(0)
                return html
        class Browser:
            async def new_page(self,**kwargs):return BrowserPage()
            async def close(self):pass
        class Chromium:
            async def launch(self,**kwargs):
                if error:raise error
                return Browser()
        class PlaywrightContext:
            async def __aenter__(self):return types.SimpleNamespace(chromium=Chromium())
            async def __aexit__(self,*args):pass
        playwright=types.ModuleType('playwright')
        async_api=types.ModuleType('playwright.async_api')
        async_api.async_playwright=PlaywrightContext
        playwright.async_api=async_api
        self.app.robots_allowed=AsyncMock(return_value=True)
        self.app.wait_for_host=AsyncMock()
        self.app.cfg['use_browser_fallback']=True
        self.browser_state_waits=0
        self.browser_state_timeouts=[]
        self.browser_content_calls=0
        with patch.dict(sys.modules,{'playwright':playwright,'playwright.async_api':async_api}):
            return await callback()

    async def test_browser_fallback_success_records_domain_url_purpose_duration_and_safe_csv(self):
        url='https://jobs.sample.test/roles/1?api_key=topsecret&campaign=public'
        page=await self._with_mock_browser(lambda:self.app.browser_fetch(
            url,'ats',{'status':503,'reason':'http_status_503'},'OK'))
        self.assertIsNotNone(page)
        record=self.app.browser_fallback_records[0]
        self.assertEqual(record['domain'],self.app.root_domain(url))
        self.assertIn('api_key=%5Bredacted%5D',record['url'])
        self.assertEqual(record['purpose'],'ATS')
        self.assertGreaterEqual(float(record['duration_seconds']),0)
        self.assertEqual(record['http_status_before'],503)
        self.assertEqual(record['browser_result'],'usable_html')
        self.assertEqual(record['html_bytes'],len('<html><body>Rendered vacancy page</body></html>'.encode()))
        self.assertEqual(record['access_status'],'OK')
        csv_text=(Path(self.tmp.name)/'browser_fallbacks.csv').read_text(encoding='utf-8-sig')
        self.assertNotIn('Rendered vacancy page',csv_text)
        self.assertNotIn('topsecret',csv_text)
        with (Path(self.tmp.name)/'browser_fallbacks.csv').open(encoding='utf-8-sig',newline='') as stream:
            rows=list(csv.DictReader(stream))
        self.assertEqual(len(rows),1)

    async def test_browser_fallback_failure_is_summarized_and_multiple_rows_preserve_metrics(self):
        url='https://jobs.sample.test/roles/1'
        await self._with_mock_browser(lambda:self.app.browser_fetch(url,'job_detail'),error=RuntimeError(
            'browser failed token=super-secret'))
        await self._with_mock_browser(lambda:self.app.browser_fetch(url,'career'))
        self.assertEqual(len(self.app.browser_fallback_records),2)
        failed,success=self.app.browser_fallback_records
        self.assertEqual(failed['purpose'],'JOB_DETAIL')
        self.assertEqual(failed['browser_result'],'failed')
        self.assertIn('RuntimeError',failed['error'])
        self.assertNotIn('super-secret',failed['error'])
        self.assertEqual(success['purpose'],'CAREERS')
        self.assertEqual(self.app.performance['browser_fallbacks'],2)
        self.assertGreaterEqual(self.app.performance['browser_seconds'],0)
        self.assertIn('Browser fallbacks: 2',self.app.performance_summary(60,1))
        with (Path(self.tmp.name)/'browser_fallbacks.csv').open(encoding='utf-8-sig',newline='') as stream:
            rows=list(csv.DictReader(stream))
        self.assertEqual(len(rows),2)

    def test_browser_fallback_csv_has_header_when_no_fallback_occurred(self):
        path=Path(self.tmp.name)/'browser_fallbacks.csv'
        write_browser_fallbacks_csv(path,[])
        with path.open(encoding='utf-8-sig',newline='') as stream:
            self.assertEqual(next(csv.reader(stream)),[
                'domain','url','browser_final_url','purpose','started_at','duration_seconds','http_status_before',
                'http_failure_reason','browser_result','html_bytes','error','access_status'])

    async def test_page_content_navigation_race_waits_and_retries_without_new_fallback(self):
        race=RuntimeError('Page.content: Unable to retrieve content because the page is navigating and changing the content.')
        page=await self._with_mock_browser(lambda:self.app.browser_fetch('https://app.sample.test','homepage'),
                                           content_errors=[race])
        self.assertIsNotNone(page)
        self.assertEqual(self.browser_content_calls,2)
        self.assertGreaterEqual(self.browser_state_waits,2)
        self.assertEqual(self.app.performance['browser_fallbacks'],1)
        self.assertEqual(len(self.app.browser_fallback_records),1)
        self.assertEqual(self.app.browser_fallback_records[0]['browser_result'],'usable_html')

    async def test_persistent_content_navigation_race_stops_after_two_retries(self):
        race=RuntimeError('Page.content: page is navigating and changing the content')
        result=await self._with_mock_browser(lambda:self.app.browser_fetch('https://app.sample.test','homepage'),
                                             content_errors=[race,race,race])
        self.assertIsNone(result)
        self.assertEqual(self.browser_content_calls,3)
        self.assertEqual(self.browser_state_waits,3)
        self.assertEqual(self.app.performance['browser_fallbacks'],1)
        self.assertEqual(len(self.app.browser_fallback_records),1)
        self.assertEqual(self.app.browser_fallback_records[0]['browser_result'],'failed')
        self.assertIn('navigating',self.app.browser_fallback_records[0]['error'])

    async def test_non_navigation_content_error_is_not_retried(self):
        result=await self._with_mock_browser(lambda:self.app.browser_fetch('https://app.sample.test','homepage'),
                                             content_errors=[RuntimeError('content process crashed')])
        self.assertIsNone(result)
        self.assertEqual(self.browser_content_calls,1)
        self.assertEqual(self.app.performance['browser_fallbacks'],1)

    async def test_redirected_browser_page_uses_final_url_without_host_restriction(self):
        page=await self._with_mock_browser(
            lambda:self.app.browser_fetch('https://app.rhino.fi','homepage'),
            final_url='https://rhino.fi/company/careers')
        self.assertIsNotNone(page)
        self.assertEqual(page.url,'https://rhino.fi/company/careers')
        self.assertEqual(self.app.browser_fallback_records[0]['browser_final_url'],page.url)
        self.assertEqual(self.app.browser_fallback_records[0]['browser_result'],'usable_html')

    async def test_navigation_wait_is_bounded_without_long_fixed_sleep(self):
        await self._with_mock_browser(lambda:self.app.browser_fetch('https://app.sample.test','homepage'))
        self.assertTrue(self.browser_state_timeouts)
        self.assertLessEqual(max(self.browser_state_timeouts),2000)


class JobPageExtractionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.app=App(app_config(),Path(self.tmp.name),False)

    async def asyncTearDown(self):
        await self.app.close()
        self.tmp.cleanup()

    def page(self,html,title=''):
        return Page('https://jobs.example.test/open-positions/role',200,title=title,raw_html=html)

    def test_h1_beats_contaminated_document_title_and_intro(self):
        html='''<html><head><title>Senior Operations Manager As our first Senior Operations Manager, you'll build the operational backbone...</title></head>
        <body><nav>Careers Products About Contact</nav><main><article><h1>Senior Operations Manager</h1>
        <p>As our first Senior Operations Manager, you'll build the operational backbone for the team.</p>
        <section class="job-description"><h2>Responsibilities</h2><p>Build repeatable processes and operational reporting for regional teams.</p></section></article></main>
        <footer>Legal Resources Privacy</footer></body></html>'''
        page=self.page(html,'Senior Operations Manager As our first...')
        details=self.app.extract_job_details(page)
        self.assertEqual(details['title'],'Senior Operations Manager')
        self.assertNotIn('As our first',details['title'])

    async def test_discovery_title_prefixes_are_cleaned_before_title_gate(self):
        self.app.cfg.update({
            'max_pages_per_domain': 45, 'max_depth': 3,
            'career_keywords': ['careers', 'jobs'], 'priority_paths': ['/careers', '/jobs'],
            'email_localpart_blacklist': [], 'email_localpart_preferred': [],
        })
        home=Page('https://example.test/',200,title='Example',text='Company',links=['https://example.test/careers'],
                  link_anchors={'https://example.test/careers':'Careers'})
        role_urls=[
            'https://example.test/careers/senior-operations-manager',
            'https://example.test/careers/partnerships-manager',
        ]
        titles=[
            "Senior Operations Manager As our first Senior Operations Manager, you'll build the operational backbone...",
            'Partnerships Manager This is an early-stage, high-ownership role...',
        ]
        careers=Page('https://example.test/careers',200,title='Careers',text='Open roles',links=role_urls,
                     link_anchors=dict(zip(role_urls,titles)))
        async def fetch(url):return careers
        self.app.resolve_home=AsyncMock(return_value=(home,'OK'))
        self.app.fetch=fetch
        self.app.profile_matcher=ProfileMatcher(sample_profile())
        self.app.enrich_job_records=AsyncMock()
        result=await self.app.crawl_domain('example.test')
        self.assertEqual([job['title'] for job in result.job_records],[
            'Senior Operations Manager','Partnerships Manager'])
        self.assertEqual([job['_title_gate_title'] for job in result.job_records],[
            'Senior Operations Manager','Partnerships Manager'])

    def test_legitimate_long_job_title_is_not_arbitrarily_truncated(self):
        title=('Senior Manager, Global Customer Operations and Strategic Partnerships for Emerging Markets '
               'with Cross-Functional Program Ownership')
        self.assertEqual(self.app._usable_job_title(title),title)

    async def test_clean_anchor_title_is_preserved_when_page_title_is_contaminated(self):
        html='''<html><head><title>Partnerships Manager As our first partnerships hire, you'll build a global program...</title></head>
        <body><p>Responsibilities include building partner operations and managing strategic relationships across regions.</p>
        <p>Requirements include experience leading operational programs and clear written communication skills.</p></body></html>'''
        page=self.page(html,'Partnerships Manager As our first partnerships hire, you will build...')
        self.app.fetch=AsyncMock(return_value=page)
        result=Result('example.test',site_name='Example',job_records=[{
            'url':page.url,'title':'Partnerships Manager'}])
        await self.app.enrich_job_records(result,[])
        self.assertEqual(result.job_records[0]['title'],'Partnerships Manager')
        self.assertNotIn('As our first',result.job_records[0]['title'])

    def test_jobposting_jsonld_supplies_title_and_complete_description(self):
        html='''<html><script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting",
        "title":"Partnerships Manager","description":"<p>Responsibilities: lead partner operations.</p><p>Requirements: five years of relevant experience.</p>"}</script>
        <body><h1>Careers</h1></body></html>'''
        details=self.app.extract_job_details(self.page(html))
        self.assertEqual(details['title'],'Partnerships Manager')
        self.assertIn('Responsibilities: lead partner operations.',details['description'])
        self.assertIn('Requirements: five years of relevant experience.',details['description'])

    def test_job_container_excludes_nav_and_footer_and_keeps_role_sections(self):
        html='''<html><body><nav>Home Products Careers</nav><main><article><h1>Operations Manager</h1>
        <div class="job-description"><h2>Responsibilities</h2><p>Own operational processes and cross-functional delivery for the organization.</p>
        <h2>Requirements</h2><p>Experience improving processes and leading teams across multiple business functions.</p></div>
        </article></main><footer>Legal Resources Privacy</footer></body></html>'''
        description=self.app.extract_job_details(self.page(html))['description']
        self.assertIn('Responsibilities',description)
        self.assertIn('Requirements',description)
        self.assertNotIn('Home Products Careers',description)
        self.assertNotIn('Legal Resources Privacy',description)

    def test_site_marketing_only_is_not_used_as_job_description(self):
        html='''<html><head><title>404 Page Not Found</title></head><body>
        <nav>Home Products Careers</nav><main><section>Built for complex global payments and onchain finance.
        Use cases Remittances Products Activation Stack About Meet the team Careers Resources Legal.</section></main>
        <footer>Legal Resources Privacy</footer></body></html>'''
        details=self.app.extract_job_details(self.page(html,'404 Page Not Found'))
        self.assertEqual(details.get('description',''),'')

    def test_unmarked_content_without_job_heading_returns_empty_description(self):
        html='''<html><body><header>Home About Contact Careers</header><main>
        <section>Global payments for modern businesses. Use cases include remittances and banking as a service.
        Products include activation stack, smart deposit addresses, bridge and convert, and automated actions.
        Meet the team and explore resources, documentation, and legal terms for customers.</section></main>
        <footer>Privacy Terms Resources</footer></body></html>'''
        self.assertEqual(self.app.html_job_description(html,'long visible global marketing text '*10),'')

    def test_duplicate_description_blocks_are_removed_conservatively(self):
        repeated='Lead weekly operational reviews and document measurable actions for the team.'
        html=f'''<html><body><main><article><h1>Operations Manager</h1><div class="job-description">
        <p>{repeated}</p><p>{repeated}</p><p>Requirements: experience leading operational programs across teams.</p>
        </div></article></main></body></html>'''
        description=self.app.extract_job_details(self.page(html))['description']
        self.assertEqual(description.count(repeated),1)
        self.assertIn('Requirements:',description)

    def test_main_job_container_keeps_description_and_sibling_requirements_and_benefits(self):
        html='''<html><body><main><article><h1>Operations Manager</h1><div class="job-content">
        <section class="job-description"><p>Responsibilities: lead daily operations and improve processes across the company.</p></section>
        <section class="requirements"><p>Requirements: manage cross-functional programs and operational teams.</p></section>
        <section class="benefits"><p>Benefits: health coverage and an annual professional development budget.</p></section>
        </div></article></main></body></html>'''
        description=self.app.extract_job_details(self.page(html))['description']
        self.assertIn('Responsibilities:',description)
        self.assertIn('Requirements:',description)
        self.assertIn('Benefits:',description)

    def test_simple_html_page_still_uses_description_fallback(self):
        html='''<html><body><h1>Customer Operations Manager</h1><p>Lead customer operations and support workflows.</p>
        <p>Coordinate daily escalations, improve processes, and maintain service quality across the organization.</p>
        <p>Requirements include experience managing support operations and cross-functional teams.</p></body></html>'''
        details=self.app.extract_job_details(self.page(html))
        self.assertEqual(details['title'],'Customer Operations Manager')
        self.assertIn('Lead customer operations',details['description'])

    def test_discovery_job_title_is_clean_before_title_gate(self):
        html='''<html><head><title>Senior Operations Manager As our first Senior Operations Manager, you'll build operations...</title></head>
        <body><main><article><h1>Senior Operations Manager</h1><p>Operational scope and team responsibilities.</p></article></main></body></html>'''
        page=self.page(html,'Senior Operations Manager As our first Senior Operations Manager, you will build...')
        pairs=self.app.deterministic_jobs([page],[])
        self.assertEqual(pairs[0][1],'Senior Operations Manager')
        result=Result('example.test',job_records=[{'url':page.url,'title':pairs[0][1]}])
        self.app.profile_matcher=ProfileMatcher(sample_profile())
        self.app.apply_title_gate(result,[page])
        self.assertEqual(result.job_records[0]['_title_gate_title'],'Senior Operations Manager')


if __name__ == '__main__':
    unittest.main()
