from __future__ import annotations
import argparse, asyncio, csv, hashlib, html as htmlmod, json, os, re, sqlite3, sys, time
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse, urldefrag, parse_qsl, urlencode
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
import tldextract
from profile_matcher import PROFILE_FILTER_VERSION, ProfileMatcher, load_profile, validate_resume_profile

console = Console()
VERSION = '2.1.4'
EMAIL_RE = re.compile(r"(?i)(?<![\w.+-])([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,63})(?![\w.-])")
OBFUSCATED_RE = re.compile(r"(?i)\b([a-z0-9._%+-]+)\s*(?:\[at\]|\(at\)|\sat\s)\s*([a-z0-9.-]+)\s*(?:\[dot\]|\(dot\)|\sdot\s)\s*([a-z]{2,24})\b")
BAD_EXT = ('.jpg','.jpeg','.png','.gif','.svg','.webp','.pdf','.zip','.rar','.7z','.mp4','.mp3','.css','.js','.woff','.woff2','.ico','.xml')
ATS_HOSTS = ('greenhouse.io','lever.co','ashbyhq.com','workable.com','smartrecruiters.com','breezy.hr','applytojob.com','recruitee.com','teamtailor.com','workdayjobs.com','myworkdayjobs.com')
PUBLIC_EMAIL_HOSTS = ('gmail.com','outlook.com','hotmail.com','proton.me','protonmail.com','yahoo.com','yandex.ru','icloud.com')
CHALLENGE_MARKERS = ('just a moment','checking your browser','verify you are human','attention required','cf-chl-','cloudflare ray id','captcha')
TLD_EXTRACT = tldextract.TLDExtract(suffix_list_urls=())

JOB_CACHE_FIELDS = ('title','company','location','date_posted','description')

@dataclass
class Page:
    url: str
    status: int
    title: str = ''
    text: str = ''
    links: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    raw_html: str = ''
    link_anchors: dict[str, str] = field(default_factory=dict)

@dataclass
class Result:
    input_domain: str
    alive: bool = False
    canonical_url: str = ''
    site_name: str = ''
    site_type: str = ''
    description: str = ''
    emails: list[str] = field(default_factory=list)
    careers_urls: list[str] = field(default_factory=list)
    job_urls: list[str] = field(default_factory=list)
    job_titles: list[str] = field(default_factory=list)
    job_records: list[dict] = field(default_factory=list)
    pages_crawled: int = 0
    evidence_urls: list[str] = field(default_factory=list)
    access_status: str = ''
    error: str = ''
    profile_rejected_jobs: list[dict] = field(default_factory=list)
    profile_stats: dict = field(default_factory=dict)

class JobCache:
    def __init__(self,path):
        self.path=Path(path)
        self.connection=sqlite3.connect(self.path)
        self.connection.row_factory=sqlite3.Row
        self.connection.execute('''CREATE TABLE IF NOT EXISTS jobs (
            normalized_url TEXT PRIMARY KEY,
            job_url TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            company TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '',
            date_posted TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            description_hash TEXT NOT NULL DEFAULT '',
            collected_at TEXT NOT NULL,
            status TEXT NOT NULL
        )''')
        self.connection.commit()

    def get_success(self,normalized_url):
        row=self.connection.execute(
            "SELECT * FROM jobs WHERE normalized_url=? AND status='success' AND TRIM(description)<>''",
            (normalized_url,)).fetchone()
        return dict(row) if row else None

    def store(self,normalized_url,job,status):
        description=str(job.get('description') or '')
        status='success' if status=='success' and description.strip() else 'failed'
        description_hash=hashlib.sha256(description.encode('utf-8')).hexdigest() if status=='success' else ''
        values=(normalized_url,str(job.get('url') or ''),str(job.get('title') or ''),str(job.get('company') or ''),
                str(job.get('location') or ''),str(job.get('date_posted') or ''),description,description_hash,
                datetime.now(timezone.utc).isoformat(timespec='seconds'),status)
        self.connection.execute('''INSERT INTO jobs
            (normalized_url,job_url,title,company,location,date_posted,description,description_hash,collected_at,status)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(normalized_url) DO UPDATE SET
                job_url=excluded.job_url,title=excluded.title,company=excluded.company,location=excluded.location,
                date_posted=excluded.date_posted,description=excluded.description,
                description_hash=excluded.description_hash,collected_at=excluded.collected_at,status=excluded.status
            WHERE excluded.status='success' OR jobs.status<>'success' OR TRIM(jobs.description)='' ''',values)
        self.connection.commit()

    def close(self): self.connection.close()

class App:
    def __init__(self, cfg, outdir: Path, ai_enabled=True, job_cache_path=None, ignore_job_cache=False, profile_matcher=None):
        self.cfg=cfg; self.outdir=outdir; self.ai_enabled=ai_enabled
        self.profile_matcher=profile_matcher
        self.user_agent=cfg.get('user_agent') or f'SiteIntelCrawler/{VERSION} (+local research tool)'
        self.robots_user_agent=self.user_agent.split('/',1)[0].split(' ',1)[0]
        self.client=httpx.AsyncClient(follow_redirects=True, timeout=cfg['request_timeout_seconds'], headers={
            'User-Agent':self.user_agent
        }, limits=httpx.Limits(max_connections=cfg['max_concurrency'], max_keepalive_connections=cfg['max_concurrency']))
        self.model=os.getenv('OPENAI_MODEL') or cfg.get('ai_model') or 'gpt-5.6-luna'
        self.robots_cache={}
        self.robots_lock=asyncio.Lock()
        self.host_locks={}
        self.host_last_request={}
        self.host_delays={}
        self.host_retry_counts={}
        self.browser_semaphore=asyncio.Semaphore(cfg.get('max_browser_concurrency',1))
        self.job_semaphore=asyncio.Semaphore(cfg.get('max_job_concurrency',4))
        self.ai_semaphore=asyncio.Semaphore(cfg.get('max_ai_concurrency',1))
        self.job_cache=JobCache(job_cache_path) if job_cache_path else None
        self.ignore_job_cache=ignore_job_cache
        self.job_cache_locks={}
        self.job_session_cache={}
        self.discovery_query_keys=set()
        self.http_outcomes={}
        self.browser_fallback_records=[]
        self.job_stats={'found':0,'cache_hits':0,'collected':0,'success':0,'failures':0}
        self.performance={'http_requests':0,'http_seconds':0.0,'rate_wait_seconds':0.0,
                          'backoff_seconds':0.0,'timeouts':0,'retries':0,
                          'browser_fallbacks':0,'browser_seconds':0.0,
                          'general_pages_fetched':0,'career_pages_fetched':0,
                          'job_detail_pages_deferred':0,'low_value_pages_skipped':0,
                          'browser_fallbacks_skipped':0,'query_variants_deduped':0,
                          'retry_budget_exhausted':0,'domains_early_stopped':0,
                          'ai_calls':0,'ai_seconds':0.0,'domain_seconds':[]}
        self.ai=None
        if ai_enabled and os.getenv('OPENAI_API_KEY'):
            from openai import OpenAI
            self.ai=OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
        elif ai_enabled:
            console.print('[yellow]OPENAI_API_KEY ausente: crawling funciona, IA desativada.[/]')

    async def close(self):
        await self.client.aclose()
        if self.job_cache:self.job_cache.close()

    async def preflight_ai(self):
        if not self.ai: return False
        started=time.monotonic();self.performance['ai_calls']+=1
        try:
            r=await asyncio.to_thread(self.ai.responses.create, model=self.model, input='Reply with exactly: OK', max_output_tokens=16)
            ok='OK' in r.output_text.upper()
            if ok: console.print(f'[green]✓ API OpenAI ativa[/] — modelo: {self.model}')
            else:
                console.print('[bold red]✗ A API OpenAI respondeu, mas não confirmou o teste.[/]')
                self.ai=None
            return ok
        except Exception as e:
            console.print('[bold red]✗ API OpenAI indisponível.[/] O crawler continuará sem IA.')
            console.print(f'[red]{str(e)[:500]}[/]')
            self.ai=None
            return False
        finally:self.performance['ai_seconds']+=time.monotonic()-started

    def root_domain(self, u):
        x=TLD_EXTRACT(u)
        return '.'.join(p for p in [x.domain,x.suffix] if p)

    async def wait_for_host(self,url):
        host=urlparse(url).netloc.lower()
        delay=max(0,float(self.cfg.get('delay_between_requests_seconds',0)),self.host_delays.get(host,0))
        if not delay:return
        lock=self.host_locks.setdefault(host,asyncio.Lock())
        async with lock:
            remaining=delay-(time.monotonic()-self.host_last_request.get(host,0))
            if remaining>0:
                started=time.monotonic()
                await asyncio.sleep(remaining)
                self.performance['rate_wait_seconds']+=time.monotonic()-started
            self.host_last_request[host]=time.monotonic()

    async def request(self,url):
        retries=max(0,int(self.cfg.get('request_retries',0)))
        backoff=max(0,float(self.cfg.get('retry_backoff_seconds',0.5)))
        for attempt in range(retries+1):
            started=time.monotonic()
            try:
                await self.wait_for_host(url)
                self.performance['http_requests']+=1
                request_started=time.monotonic()
                response=await self.client.get(url)
                self.performance['http_seconds']+=time.monotonic()-request_started
                retryable=response.status_code in (408,425,429) or response.status_code>=500
                if not retryable:
                    return response
                if attempt==retries:return response
                if self._retry_budget_exhausted(url):
                    self.performance['retry_budget_exhausted']+=1
                    return response
                self.performance['retries']+=1
                self._record_retry(url)
                retry_after=response.headers.get('retry-after','')
                try:wait=float(retry_after)
                except (TypeError,ValueError):
                    try:wait=max(0,(parsedate_to_datetime(retry_after)-datetime.now(timezone.utc)).total_seconds())
                    except Exception:wait=backoff*(2**attempt)
                wait=min(wait,float(self.cfg.get('max_retry_after_seconds',30)))
            except httpx.TimeoutException:
                self.performance['http_seconds']+=time.monotonic()-request_started
                self.performance['timeouts']+=1
                if attempt==retries:return None
                if self._retry_budget_exhausted(url):
                    self.performance['retry_budget_exhausted']+=1
                    return None
                self.performance['retries']+=1
                self._record_retry(url)
                wait=backoff*(2**attempt)
            except httpx.NetworkError:
                self.performance['http_seconds']+=time.monotonic()-request_started
                if attempt==retries:return None
                if self._retry_budget_exhausted(url):
                    self.performance['retry_budget_exhausted']+=1
                    return None
                self.performance['retries']+=1
                self._record_retry(url)
                wait=backoff*(2**attempt)
            self.performance['backoff_seconds']+=wait
            await asyncio.sleep(wait)
        return None

    def _retry_budget_exhausted(self,url):
        limit=int(self.cfg.get('max_retries_per_host',8))
        return self.host_retry_counts.get(urlparse(url).netloc.lower(),0)>=limit

    def _record_retry(self,url):
        host=urlparse(url).netloc.lower()
        self.host_retry_counts[host]=self.host_retry_counts.get(host,0)+1

    async def robots_allowed(self,url):
        if not self.cfg.get('respect_robots_txt',True):return True
        parsed=urlparse(url); origin=f'{parsed.scheme}://{parsed.netloc.lower()}'
        if origin not in self.robots_cache:
            async with self.robots_lock:
                if origin not in self.robots_cache:
                    self.robots_cache[origin]=asyncio.create_task(self.load_robots(origin))
        rule=await self.robots_cache[origin]
        return rule if isinstance(rule,bool) else rule.can_fetch(self.robots_user_agent,url)

    async def load_robots(self,origin):
        robots_url=origin+'/robots.txt';response=await self.request(robots_url)
        if response is None or response.status_code>=500:
            return not self.cfg.get('robots_fail_closed',False)
        if response.status_code in (401,403):return False
        if response.status_code>=400:return True
        parser=RobotFileParser();parser.set_url(robots_url);parser.parse(response.text.splitlines())
        delay=parser.crawl_delay(self.robots_user_agent) or parser.crawl_delay('*')
        if delay is not None:self.host_delays[urlparse(origin).netloc.lower()]=max(0,float(delay))
        return parser

    def is_challenge(self, title, text):
        blob=(title+' '+text[:3000]).lower()
        return any(x in blob for x in CHALLENGE_MARKERS)

    def parse_html(self, raw, final_url, status):
        soup=BeautifulSoup(raw,'html.parser')
        title=(soup.title.get_text(' ',strip=True) if soup.title else '')[:300]
        # E-mails: somente mailto e texto visível. Bundles JavaScript e metadados ocultos
        # não são fontes confiáveis de contato.
        email_source=[]
        for a in soup.find_all('a',href=True):
            href=a['href'].strip()
            if href.lower().startswith('mailto:'):
                email_source.append(href[7:].split('?',1)[0])
        for x in soup(['script','style','noscript','svg','template']): x.decompose()
        text=re.sub(r'\s+',' ',' '.join(soup.stripped_strings))[:180000]
        email_source.append(text)
        links=[]; anchors={}
        for a in soup.find_all('a',href=True):
            href=urldefrag(urljoin(final_url,a['href'].strip()))[0]
            if href.startswith(('http://','https://')) and not urlparse(href).path.lower().endswith(BAD_EXT):
                links.append(href)
                anchor=re.sub(r'\s+',' ',a.get_text(' ',strip=True)).strip()
                if anchor and href not in anchors:anchors[href]=anchor[:180]
        return Page(final_url,status,title,text,list(dict.fromkeys(links)),self.extract_emails(' '.join(email_source)),raw,anchors)

    async def fetch(self, url, check_robots=True)->Optional[Page]:
        try:
            if check_robots and not await self.robots_allowed(url):
                self.http_outcomes[url]={'status':'','reason':'robots_disallowed'}
                return None
            r=await self.request(url)
            if r is None:
                self.http_outcomes[url]={'status':'','reason':'request_failed_or_exhausted_retries'}
                return None
            ctype=r.headers.get('content-type','').lower()
            if r.status_code>=400:
                self.http_outcomes[url]={'status':r.status_code,'reason':f'http_status_{r.status_code}'}
                return None
            if 'text/html' not in ctype and 'application/xhtml' not in ctype:
                self.http_outcomes[url]={'status':r.status_code,'reason':'non_html_content_type'}
                return None
            page=self.parse_html(r.text,str(r.url),r.status_code)
            self.http_outcomes[url]={'status':r.status_code,'reason':''}
            return page
        except Exception as exc:
            self.http_outcomes[url]={'status':'','reason':f'http_exception_{type(exc).__name__}'}
            return None

    async def browser_fetch(self,url,purpose='UNKNOWN',http_outcome=None,access_status='')->Optional[Page]:
        if not self.cfg.get('use_browser_fallback'): return None
        if not await self.robots_allowed(url):return None
        started_at=''
        started_monotonic=None
        http_outcome=http_outcome or self.http_outcomes.get(url,{})
        browser_result='failed';error='';html_bytes=0;page_result=None;browser_final_url=''
        try:
            from playwright.async_api import async_playwright
            async with self.browser_semaphore:
                started_monotonic=time.monotonic();self.performance['browser_fallbacks']+=1
                started_at=datetime.now(timezone.utc).isoformat(timespec='milliseconds')
                domain=self.root_domain(url) or urlparse(url).netloc.lower()
                display_url=self._safe_diagnostic_url(url)
                console.print(f'[cyan][BROWSER] START domain={domain} purpose={self._browser_purpose(purpose)} url={display_url}[/]')
                await self.wait_for_host(url)
                async with async_playwright() as p:
                    b=await p.chromium.launch(headless=True)
                    try:
                        page=await b.new_page(user_agent=self.user_agent)
                        resp=await page.goto(url, wait_until='domcontentloaded', timeout=self.cfg.get('browser_timeout_seconds',25)*1000)
                        await self._wait_for_browser_domcontentloaded(page)
                        settle=float(self.cfg.get('browser_settle_seconds',0.5))
                        if settle:await page.wait_for_timeout(int(settle*1000))
                        raw=None
                        for attempt in range(3):
                            try:
                                raw=await page.content()
                                break
                            except Exception as content_error:
                                if not self._is_content_navigation_race(content_error) or attempt>=2:
                                    raise
                                console.print(f'[yellow][BROWSER] CONTENT_RETRY attempt={attempt+1} reason=navigation_in_progress[/]')
                                await self._wait_for_browser_domcontentloaded(page)
                        if raw is None:raise RuntimeError('Browser content unavailable')
                        final=page.url;browser_final_url=self._safe_diagnostic_url(final)
                        html_bytes=len(raw.encode('utf-8'))
                        page_result=self.parse_html(raw,final,resp.status if resp else 200)
                        browser_result=('usable_html' if page_result.text.strip() and (not resp or resp.status<400)
                                        and not self.is_challenge(page_result.title,page_result.text) else 'unusable_html')
                        return page_result
                    finally:
                        await b.close()
        except Exception as exc:
            error=self._summarize_browser_error(exc)
            return None
        finally:
            if started_monotonic is not None:
                duration=time.monotonic()-started_monotonic
                self.performance['browser_seconds']+=duration
                domain=self.root_domain(url) or urlparse(url).netloc.lower()
                record={
                    'domain':domain,'url':self._safe_diagnostic_url(url),'purpose':self._browser_purpose(purpose),
                    'browser_final_url':browser_final_url,
                    'started_at':started_at,'duration_seconds':f'{duration:.3f}',
                    'http_status_before':http_outcome.get('status',''),
                    'http_failure_reason':http_outcome.get('reason',''),
                    'browser_result':browser_result,'html_bytes':html_bytes,'error':error,
                    'access_status':access_status,
                }
                self.browser_fallback_records.append(record)
                if browser_result=='usable_html':
                    console.print(f'[green][BROWSER] END domain={domain} purpose={record["purpose"]} duration={duration:.1f}s result={browser_result}[/]')
                else:
                    reason=error or browser_result
                    console.print(f'[red][BROWSER] FAIL domain={domain} purpose={record["purpose"]} duration={duration:.1f}s reason={reason}[/]')
                write_browser_fallbacks_csv(self.outdir/'browser_fallbacks.csv',self.browser_fallback_records)

    async def _wait_for_browser_domcontentloaded(self,page):
        timeout_ms=min(int(self.cfg.get('browser_timeout_seconds',25)*1000),2000)
        try:
            await page.wait_for_load_state('domcontentloaded',timeout=timeout_ms)
            return True
        except Exception as exc:
            # A bounded wait may expire on pages with a continuing navigation; still
            # let the narrowly-scoped page.content retry decide whether content is ready.
            if 'timeout' in type(exc).__name__.lower() or 'timeout' in str(exc).lower():
                return False
            raise

    @staticmethod
    def _is_content_navigation_race(exc):
        message=str(exc).lower()
        return 'navigating' in message and ('changing the content' in message or 'page.content' in message)

    @staticmethod
    def _browser_purpose(purpose):
        return {'homepage':'HOME_RESOLUTION','career':'CAREERS','ats':'ATS','job_detail':'JOB_DETAIL'}.get(
            str(purpose).lower(),str(purpose).upper())

    @staticmethod
    def _summarize_browser_error(exc):
        message=str(exc)
        message=re.sub(r'https?://\S+','[url]',message)
        message=re.sub(r'(?i)\b(cookie|authorization|token|secret|password|api[_ -]?key)\b\s*[:=]\s*[^\s,;]+',r'\1=[redacted]',message)
        message=re.sub(r'(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b','[email redacted]',message)
        message=re.sub(r'\s+',' ',message).strip()[:160]
        return f'{type(exc).__name__}: {message}' if message else type(exc).__name__

    @staticmethod
    def _safe_diagnostic_url(url):
        parsed=urlparse(url)
        safe_params=[]
        for key,value in parse_qsl(parsed.query,keep_blank_values=True):
            if (re.search(r'(?i)(token|secret|key|auth|password|session|cookie|email|user|phone|name|code|signature|jwt|credential)',key)
                    or re.search(r'(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b',value)):
                value='[redacted]'
            safe_params.append((key,value))
        return parsed._replace(query=urlencode(safe_params)).geturl()

    def valid_email(self,e):
        e=htmlmod.unescape(e).lower().strip('.,;:<>[](){}\"\'')
        if len(e)>254 or '..' in e or e.count('@')!=1: return False
        local,host=e.rsplit('@',1)
        if not local or len(local)>64 or local.startswith(('u003e','u003c','\\u003e','\\u003c')): return False
        if any(x in e for x in ('example.com','example.org','example.net','sentry.io','calendar.google.com')): return False
        if host.endswith(('.js','.css','.json','.map','.png','.jpg','.svg')): return False
        if re.search(r'@\d+(?:\.\d+){1,}',e): return False
        ext=tldextract.extract(host)
        if not ext.domain or not ext.suffix: return False
        # Reject version/package artifacts such as lodash@4.17.23-xxx.js
        if re.match(r'^\d+(?:\.\d+){1,}',host): return False
        return True

    def extract_emails(self,s):
        s=htmlmod.unescape(s).replace('\\u003e',' ').replace('\\u003c',' ')
        # JSON/JS control escapes immediately before an address (e.g. \nsupport@...)
        # must be separators, never part of the mailbox local-part.
        s=re.sub(r'\\[nrtbf]', ' ', s)
        vals={m.group(1).lower().strip('.,;:<>[](){}\"\'') for m in EMAIL_RE.finditer(s)}
        for m in OBFUSCATED_RE.finditer(s): vals.add(f'{m.group(1)}@{m.group(2)}.{m.group(3)}'.lower())
        return sorted(e for e in vals if self.valid_email(e))

    def filter_emails(self,emails,site_root):
        bad=set(x.lower() for x in self.cfg['email_localpart_blacklist']); out=[]
        for e in sorted(set(x.lower() for x in emails if self.valid_email(x))):
            local,host=e.rsplit('@',1); tokens=set(re.split(r'[._+\-]',local))
            if local in bad or tokens & bad: continue
            # Keep same-brand/root-domain mail plus common public mailboxes; other domains are likely page noise.
            if self.root_domain(host)!=site_root and host not in PUBLIC_EMAIL_HOSTS: continue
            out.append(e)
        pref=self.cfg['email_localpart_preferred']
        return sorted(out,key=lambda e:(0 if any(k in e.split('@')[0] for k in pref) else 1,e))

    def is_ats(self,u): return any(h in urlparse(u).netloc.lower() for h in ATS_HOSTS)

    def career_url_signal(self,u):
        """Strong signal for a careers landing page. Avoid generic words such as 'positions'."""
        if self.is_ats(u):
            # ATS board roots are careers pages; individual ATS job URLs are handled separately.
            path=urlparse(u).path.strip('/')
            if 'ashbyhq.com' in urlparse(u).netloc.lower(): return len(path.split('/')) <= 1
            return True
        path=urlparse(u).path.lower().replace('_','-').rstrip('/')
        if any(x in path for x in ('/help/','/docs/','/blog/','/news/','/article','/academy/')): return False
        segs=[x for x in path.split('/') if x]
        strong={'career','careers','jobs','hiring','vacancies','open-roles','open-positions','join-us','join-our-team','work-with-us','employment'}
        return any(seg in strong for seg in segs)

    def is_job_detail_url(self,u):
        path=urlparse(u).path.rstrip('/').lower()
        if any(x in path for x in ('privacy','terms','about','contact','help','faq','blog','news')): return False
        if 'jobs.ashbyhq.com' in urlparse(u).netloc.lower():
            return len([x for x in path.split('/') if x]) >= 2
        # Own-site job details can live below several common careers containers.
        # The container itself (/careers, /jobs, /open-positions...) is a landing page, not a vacancy.
        segs=[x for x in path.split('/') if x]
        containers={'career','careers','job','jobs','open-roles','open-positions','vacancies'}
        for i,seg in enumerate(segs):
            if seg in containers and i < len(segs)-1:
                return True
        return False

    def clean_job_title(self,title,site_name=''):
        t=re.sub(r'\s+',' ',(title or '')).strip()
        if not t: return ''
        if site_name:
            esc=re.escape(site_name.strip())
            t=re.sub(rf'\s*[|–—-]\s*{esc}\s*$', '', t, flags=re.I).strip()
        return t

    def _normalize_discovered_job_title(self,value,site_name=''):
        title=self.clean_job_title(value,site_name)
        if not title:return ''
        narrative_start=re.search(
            r"\s+(?=(?:as\s+(?:our|the|a|an)\b|this is\b|you(?:'|’)ll\b|you will\b|"
            r"we(?:'|’)re\b|we are\b|in this role\b|responsibilities include\b|"
            r"about (?:the|this) role\b|what you(?:'|’)ll do\b))",title,re.I)
        if narrative_start:title=title[:narrative_start.start()].strip(' -|–—:')
        return title

    def _usable_job_title(self,value,site_name=''):
        title=self._normalize_discovered_job_title(value,site_name)
        if not title:return ''
        normalized=' '.join(re.findall(r'[a-z0-9]+',title.casefold()))
        if normalized in {'404','404 not found','404 page not found','page not found','not found','home','homepage','careers','jobs'}:
            return ''
        if re.search(r'\b(?:as our first|you(?:\'|’)ll|you will|we are|we\'re|in this role|responsibilities include|about the role|what you(?:\'|’)ll do)\b',title,re.I):
            return ''
        return title

    @staticmethod
    def _job_posting_jsonld(raw):
        postings=[]
        if not raw:return postings
        soup=BeautifulSoup(raw,'html.parser')
        def collect(value):
            if isinstance(value,dict):
                kind=value.get('@type')
                kinds=kind if isinstance(kind,list) else [kind]
                if any(str(x).lower()=='jobposting' for x in kinds):postings.append(value)
                for nested in value.values():collect(nested)
            elif isinstance(value,list):
                for nested in value:collect(nested)
        for script in soup.find_all('script'):
            if 'ld+json' not in str(script.get('type') or '').lower():continue
            try:collect(json.loads(script.string or script.get_text() or ''))
            except (json.JSONDecodeError,TypeError,ValueError):continue
        return postings

    def extract_job_title(self,page,site_name='',anchor_title=''):
        postings=self._job_posting_jsonld(page.raw_html)
        for posting in postings:
            title=self._usable_job_title(posting.get('title'),site_name)
            if title:return title
        if page.raw_html:
            soup=BeautifulSoup(page.raw_html,'html.parser')
            for heading in soup.find_all('h1'):
                title=self._usable_job_title(heading.get_text(' ',strip=True),site_name)
                if title and (not anchor_title or self._titles_agree(title,anchor_title)):return title
            for prop in ('og:title','twitter:title'):
                meta=soup.find('meta',attrs={'property':prop}) or soup.find('meta',attrs={'name':prop})
                if meta:
                    title=self._usable_job_title(meta.get('content',''),site_name)
                    if title and (not anchor_title or self._titles_agree(title,anchor_title)):return title
            meta=soup.find('meta',attrs={'name':re.compile(r'^title$',re.I)})
            if meta:
                title=self._usable_job_title(meta.get('content',''),site_name)
                if title and (not anchor_title or self._titles_agree(title,anchor_title)):return title
            title=self._usable_job_title(anchor_title,site_name)
            if title:return title
            if soup.title:
                title=self._usable_job_title(soup.title.get_text(' ',strip=True),site_name)
                if title:return title
        return ''

    @staticmethod
    def _titles_agree(first,second):
        left=set(re.findall(r'[a-z0-9]+',str(first or '').casefold()))
        right=set(re.findall(r'[a-z0-9]+',str(second or '').casefold()))
        return bool(left and right and len(left&right)/len(right)>=0.6)

    def clean_description(self,value):
        if not isinstance(value,str) or not value.strip(): return ''
        soup=BeautifulSoup(htmlmod.unescape(value),'html.parser')
        for x in soup(['script','style','noscript','svg','canvas','template','iframe']): x.decompose()
        lines=[]
        for line in soup.get_text('\n',strip=True).splitlines():
            line=re.sub(r'\s+',' ',line).strip()
            if line: lines.append(line)
        return '\n'.join(lines)

    def job_location_text(self,value):
        locations=value if isinstance(value,list) else [value]
        rendered=[]
        for location in locations:
            if isinstance(location,str):
                text=re.sub(r'\s+',' ',location).strip()
            elif isinstance(location,dict):
                parts=[]
                name=location.get('name')
                if isinstance(name,str) and name.strip(): parts.append(name.strip())
                address=location.get('address')
                if isinstance(address,str) and address.strip(): parts.append(address.strip())
                elif isinstance(address,dict):
                    for key in ('streetAddress','addressLocality','addressRegion','postalCode','addressCountry'):
                        part=address.get(key)
                        if isinstance(part,dict): part=part.get('name') or part.get('code')
                        if isinstance(part,str) and part.strip(): parts.append(part.strip())
                text=', '.join(dict.fromkeys(parts))
            else: text=''
            if text and text not in rendered: rendered.append(text)
        return ' ; '.join(rendered)

    def html_job_description(self,raw,page_text='',job_title=''):
        if not raw:return ''
        soup=BeautifulSoup(raw,'html.parser')
        document_title=soup.title.get_text(' ',strip=True) if soup.title else ''
        if re.search(r'(?i)\b(?:404|page not found|not found|access denied|error)\b',document_title):return ''
        for x in soup(['script','style','noscript','svg','canvas','template','iframe','nav','header','footer','aside','form','button']):
            x.decompose()
        for x in list(soup.find_all(True)):
            if x.parent is None: continue
            role=str(x.get('role') or '').lower()
            marker=' '.join([str(x.get('id') or ''),*map(str,x.get('class') or [])]).lower()
            if role in {'navigation','banner','contentinfo','complementary'} or any(
                token in marker for token in ('cookie','breadcrumb','sidebar','navigation','main-menu','site-menu',
                                               'mobile-menu','site-header','site-footer','social-share',
                                               'share-buttons','related-jobs','navbar','menu-toggle')
            ):
                x.decompose()
        explicit_by_priority=[]
        for x in soup.find_all(True):
            marker=' '.join([str(x.get('id') or ''),*map(str,x.get('class') or [])]).lower().replace('_','-')
            itemprop=str(x.get('itemprop') or '').lower()=='description'
            group=0 if itemprop or any(token in marker for token in (
                'job-description','jobdescription','posting-description','position-description')) else (
                1 if any(token in marker for token in ('job-content','job-details','job-posting','job-container',
                                                       'job-detail','job-page','position-content','position-container',
                                                       'vacancy-description','vacancy-container')) else -1)
            if group>=0:
                text=self.clean_description(str(x))
                if text:explicit_by_priority.append((group,text,x))
        if explicit_by_priority:
            description_nodes=[node for group,_,node in explicit_by_priority if group==0]
            containers=[(text,node) for group,text,node in explicit_by_priority if group==1]
            heading=soup.find('h1')
            heading_title=self._usable_job_title(heading.get_text(' ',strip=True)) if heading else ''
            heading_matches=bool(job_title and heading_title and self._titles_agree(heading_title,job_title))
            enclosing=[text for text,node in containers if any(
                description_node is node or description_node in node.find_all(True)
                for description_node in description_nodes)]
            if enclosing and heading_matches:return self._dedupe_description(max(enclosing,key=len))
            if description_nodes:
                return self._dedupe_description(max((text for group,text,_ in explicit_by_priority if group==0),key=len))
            if heading_matches and containers:return self._dedupe_description(max((text for text,_ in containers),key=len))
            return ''
        heading=soup.find('h1')
        heading_title=self._usable_job_title(heading.get_text(' ',strip=True)) if heading else ''
        if heading and heading_title and job_title and self._titles_agree(heading_title,job_title):
            body=soup.body
            has_specific_container=False
            for ancestor in heading.parents:
                if getattr(ancestor,'name',None) in ('main','article'):
                    has_specific_container=True
                    text=self.clean_description(str(ancestor))
                    if len(text)>=80:
                        return self._dedupe_description(text)
            # A matching, non-generic H1 is semantic evidence on simple job pages
            # that omit main/article wrappers. Do not fall back to body without it.
            if body and not has_specific_container:
                text=self.clean_description(str(body))
                if len(text)>=80:return self._dedupe_description(text)
        return ''

    @staticmethod
    def _dedupe_description(value):
        lines=[re.sub(r'\s+',' ',line).strip() for line in str(value or '').splitlines()]
        lines=[line for line in lines if line]
        output=[];seen_lines=set();seen_blocks=set();index=0
        while index<len(lines):
            repeated_size=0
            for size in range(min(8,(len(lines)-index)//2),1,-1):
                block=tuple(line.casefold() for line in lines[index:index+size])
                if block in seen_blocks:
                    repeated_size=size;break
            if repeated_size:
                index+=repeated_size;continue
            line=lines[index];key=line.casefold()
            if len(line)>=18 and key in seen_lines:
                index+=1;continue
            output.append(line)
            if len(line)>=18:seen_lines.add(key)
            for size in range(2,min(8,len(output))+1):
                seen_blocks.add(tuple(item.casefold() for item in output[-size:]))
            index+=1
        return '\n'.join(output)

    def extract_job_details(self,page):
        postings=self._job_posting_jsonld(page.raw_html)
        details={}
        if postings:
            posting=max(postings,key=lambda p:(len(str(p.get('description') or '')),sum(bool(p.get(k)) for k in ('title','hiringOrganization','jobLocation','datePosted'))))
            title=self._usable_job_title(posting.get('title'))
            if title:details['title']=title
            organization=posting.get('hiringOrganization')
            if isinstance(organization,list):
                organization=next((x for x in organization if isinstance(x,(dict,str))),None)
            if isinstance(organization,dict): organization=organization.get('name')
            if isinstance(organization,str) and organization.strip(): details['company']=re.sub(r'\s+',' ',organization).strip()
            location=self.job_location_text(posting.get('jobLocation'))
            if not location:
                location_type=posting.get('jobLocationType')
                if isinstance(location_type,str): location=location_type.strip()
            if location: details['location']=location
            date_posted=posting.get('datePosted')
            if isinstance(date_posted,str) and date_posted.strip(): details['date_posted']=date_posted.strip()
            description=self.clean_description(posting.get('description'))
        else:description=''
        if not details.get('title'):
            title=self.extract_job_title(page)
            if title:details['title']=title
        if not description:description=self.html_job_description(page.raw_html,page.text,details.get('title',''))
        if description: details['description']=description
        return details

    def job_url_key(self,url):
        parsed=urlparse(urldefrag(url)[0])
        return f'{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip("/") or "/"}' + (f'?{parsed.query}' if parsed.query else '')

    def apply_cached_job(self,job,cached):
        for field in JOB_CACHE_FIELDS:
            value=cached.get(field)
            if value: job[field]=value

    def job_stats_summary(self):
        stats=self.job_stats
        return (f'Vagas: {stats["found"]} | Cache: {stats["cache_hits"]} | '
                f'Coletadas: {stats["collected"]} | Sucesso: {stats["success"]} | Falhas: {stats["failures"]}')

    def _sync_job_lists(self,res):
        res.job_urls=[job.get('url','') for job in res.job_records]
        res.job_titles=[job.get('title','') for job in res.job_records]

    def apply_title_gate(self,res,pages):
        if not self.profile_matcher:
            return
        stats=res.profile_stats
        for key in ('jobs_discovered','title_likely','title_possible','title_rejected','job_pages_avoided','jobs_enriched',
                    'relevant_final','review_final','rejected_after_description'):
            stats.setdefault(key,0)
        page_keys={self.job_url_key(page.url) for page in pages}
        rejected_keys={self.job_url_key(record.get('url','')) for record in res.profile_rejected_jobs
                       if record.get('stage')=='TITLE_GATE' and record.get('url')}
        seen=set();kept=[]
        for index,job in enumerate(res.job_records):
            url=job.get('url','');key=self.job_url_key(url) if url else f'__missing_url_{index}'
            if key in rejected_keys or key in seen:
                continue
            seen.add(key)
            if (job.get('title_gate') in ('LIKELY_RELEVANT','POSSIBLE') and
                    job.get('_title_gate_title',job.get('title',''))==job.get('title','')):
                kept.append(job)
                continue
            previous=job.get('title_gate')
            if previous=='LIKELY_RELEVANT':stats['title_likely']=max(0,stats['title_likely']-1)
            elif previous=='POSSIBLE':stats['title_possible']=max(0,stats['title_possible']-1)
            decision=self.profile_matcher.classify_title(job.get('title',''))
            job['title_gate']=decision.classification
            job['title_gate_reason']=decision.reason
            job['_title_gate_title']=job.get('title','')
            if not job.get('_title_gate_counted'):
                stats['jobs_discovered']+=1
                job['_title_gate_counted']=True
            if decision.classification=='CLEARLY_IRRELEVANT':
                rejection={**job,'stage':'TITLE_GATE','classification':'CLEARLY_IRRELEVANT','reason':decision.reason}
                res.profile_rejected_jobs.append(rejection)
                rejected_keys.add(key)
                cache_hit=bool(self.job_session_cache.get(key))
                if not cache_hit and self.job_cache and not self.ignore_job_cache:
                    try:cache_hit=bool(self.job_cache.get_success(key))
                    except Exception:cache_hit=False
                if url and key not in page_keys and not cache_hit:
                    stats['job_pages_avoided']+=1
                stats['title_rejected']+=1
            elif decision.classification=='LIKELY_RELEVANT':
                stats['title_likely']+=1;kept.append(job)
            else:
                stats['title_possible']+=1;kept.append(job)
        res.job_records=kept
        self._sync_job_lists(res)

    def apply_job_relevance(self,res):
        if not self.profile_matcher:
            return
        kept=[]
        for job in res.job_records:
            if job.get('profile_relevance') in ('RELEVANT','REVIEW'):
                kept.append(job)
                continue
            decision=self.profile_matcher.classify_job(job)
            job['profile_relevance']=decision.classification
            job['profile_relevance_reason']=decision.reason
            if decision.classification=='NOT_RELEVANT':
                res.profile_rejected_jobs.append({**job,'stage':'JOB_RELEVANCE',
                    'classification':'NOT_RELEVANT','reason':decision.reason})
                res.profile_stats['rejected_after_description']+=1
            else:
                kept.append(job)
                key='relevant_final' if decision.classification=='RELEVANT' else 'review_final'
                res.profile_stats[key]+=1
        res.job_records=kept
        self._sync_job_lists(res)

    async def process_job_pipeline(self,res,pages):
        self.apply_title_gate(res,pages)
        if self.ai:await self.ai_enrich(res,pages)
        self.apply_title_gate(res,pages)
        success_before=self.job_stats['success']
        await self.enrich_job_records(res,pages)
        res.profile_stats['jobs_enriched']=res.profile_stats.get('jobs_enriched',0)+self.job_stats['success']-success_before
        self.apply_job_relevance(res)

    @staticmethod
    def profile_filter_summary(results):
        totals={key:0 for key in ('jobs_discovered','title_likely','title_possible','title_rejected',
                                  'job_pages_avoided','jobs_enriched','relevant_final','review_final','rejected_after_description')}
        for result in results:
            for key in totals:totals[key]+=int((result.profile_stats or {}).get(key,0))
        return ('PROFILE FILTER\n'
                f'Vagas descobertas: {totals["jobs_discovered"]} | '
                f'Title gate relevantes: {totals["title_likely"]} | Title gate possíveis: {totals["title_possible"]}\n'
                f'Title gate rejeitadas: {totals["title_rejected"]} | Job pages evitadas: {totals["job_pages_avoided"]}\n'
                f'Vagas enriquecidas: {totals["jobs_enriched"]} | '
                f'Relevantes finais: {totals["relevant_final"]} | Para revisão: {totals["review_final"]} | '
                f'Rejeitadas após descrição: {totals["rejected_after_description"]}')

    async def enrich_job_records(self,res,pages):
        page_cache={self.job_url_key(p.url):p for p in pages}
        async def enrich_one(job):
            self.job_stats['found']+=1
            url=job.get('url',''); key=self.job_url_key(url)
            if not url:
                self.job_stats['failures']+=1
                return
            lock=self.job_cache_locks.setdefault(key,asyncio.Lock())
            async with lock:
                try:
                    cached=self.job_session_cache.get(key)
                    if cached is None and self.job_cache and not self.ignore_job_cache:
                        cached=self.job_cache.get_success(key)
                    if cached is not None:
                        self.apply_cached_job(job,cached)
                        self.job_session_cache[key]=cached
                        self.job_stats['cache_hits']+=1
                        self.job_stats['success']+=1
                        return
                    self.job_stats['collected']+=1
                    page=page_cache.get(key)
                    if page is None:
                        async with self.job_semaphore:
                            page=await self.fetch(url)
                        if page is None: page=await self.fallback_for_purpose(url,'job_detail',access_status=res.access_status)
                        if page is not None:
                            page_cache[key]=page
                            page_cache[self.job_url_key(page.url)]=page
                            if not self.is_challenge(page.title,page.text):
                                pages.append(page)
                                res.evidence_urls=list(dict.fromkeys(res.evidence_urls+[page.url]))
                    if page is None or self.is_challenge(page.title,page.text):
                        if self.job_cache:self.job_cache.store(key,job,'failed')
                        self.job_stats['failures']+=1
                        return
                    details=self.extract_job_details(page)
                    anchor_title=self._usable_job_title(job.get('title'),res.site_name)
                    extracted_title=self.extract_job_title(page,res.site_name,anchor_title)
                    if extracted_title:
                        details['title']=extracted_title
                    elif anchor_title:
                        details.pop('title',None)
                        job['title']=anchor_title
                    else:
                        details.pop('title',None)
                        if job.get('title'):job['title']=''
                    if not details.get('description') and page.raw_html:
                        description=self.html_job_description(page.raw_html,page.text,anchor_title or extracted_title)
                        if description:details['description']=description
                    job.update(details)
                    success=bool(str(job.get('description') or '').strip())
                    if success:self.job_session_cache[key]=dict(job)
                    if self.job_cache:self.job_cache.store(key,job,'success' if success else 'failed')
                    self.job_stats['success' if success else 'failures']+=1
                except Exception:
                    if self.job_cache:
                        try:self.job_cache.store(key,job,'failed')
                        except Exception:pass
                    self.job_stats['failures']+=1
                    return
        await asyncio.gather(*(enrich_one(job) for job in res.job_records))
        res.pages_crawled=len({p.url for p in pages})

    def performance_summary(self,elapsed,domains_processed):
        p=self.performance;minutes=max(elapsed/60,1e-9)
        avg_domain=(sum(p['domain_seconds'])/len(p['domain_seconds'])) if p['domain_seconds'] else 0
        return (f'PERFORMANCE\nTempo total: {time.strftime("%H:%M:%S",time.gmtime(elapsed))} | '
                f'Domínios: {domains_processed} | Domínios/minuto: {domains_processed/minutes:.1f}\n'
                f'HTTP requests: {p["http_requests"]} | Requests/minuto: {p["http_requests"]/minutes:.1f} | '
                f'Tempo HTTP: {p["http_seconds"]:.1f}s | Rate limit: {p["rate_wait_seconds"]:.1f}s | '
                f'Backoff: {p["backoff_seconds"]:.1f}s\n'
                f'Timeouts: {p["timeouts"]} | Retries: {p["retries"]} | '
                f'Browser fallbacks: {p["browser_fallbacks"]} ({p["browser_seconds"]:.1f}s) | '
                f'IA calls: {p["ai_calls"]} ({p["ai_seconds"]:.1f}s)\n'
                f'DISCOVERY\nGeneral pages: {p["general_pages_fetched"]} | Career pages: {p["career_pages_fetched"]} | '
                f'Low-value skipped: {p["low_value_pages_skipped"]} | Query variants deduped: {p["query_variants_deduped"]} | '
                f'Job details deferred: {p["job_detail_pages_deferred"]} | Domains early-stopped: {p["domains_early_stopped"]}\n'
                f'BROWSER\nFallbacks executed: {p["browser_fallbacks"]} | Fallbacks skipped: {p["browser_fallbacks_skipped"]} | '
                f'Retry budgets exhausted: {p["retry_budget_exhausted"]}\n'
                f'Tempo médio por domínio: {avg_domain:.1f}s | '
                f'Vagas: {self.job_stats["found"]} | Cache hits: {self.job_stats["cache_hits"]} | '
                f'Enriquecidas: {self.job_stats["success"]-self.job_stats["cache_hits"]} | '
                f'Falhas: {self.job_stats["failures"]}')

    def dedupe_titles(self,titles,site_name=''):
        out=[]; seen=set()
        for title in titles:
            t=self.clean_job_title(title,site_name)
            key=re.sub(r'[^a-z0-9]+',' ',t.lower()).strip()
            if t and key not in seen:
                seen.add(key); out.append(t)
        return out

    async def ashby_jobs(self,board_url):
        """Use Ashby's public unauthenticated Job Postings API for a discovered board URL."""
        pu=urlparse(board_url)
        if 'jobs.ashbyhq.com' not in pu.netloc.lower(): return []
        parts=[x for x in pu.path.split('/') if x]
        if not parts: return []
        board=parts[0]
        api=f'https://api.ashbyhq.com/posting-api/job-board/{board}'
        started=time.monotonic();self.performance['http_requests']+=1
        try:
            r=await self.client.get(api)
        except Exception as exc:
            self.performance['http_seconds']+=time.monotonic()-started
            if isinstance(exc,httpx.TimeoutException):self.performance['timeouts']+=1
            return []
        self.performance['http_seconds']+=time.monotonic()-started
        try:
            if r.status_code != 200: return []
            data=r.json(); out=[]
            for j in data.get('jobs',[]):
                if not isinstance(j,dict) or j.get('isListed') is False: continue
                url=str(j.get('jobUrl') or '').strip(); title=str(j.get('title') or '').strip()
                if url.startswith(('http://','https://')) and title:
                    out.append((url,title,api))
            return out
        except Exception:return []

    def link_score(self,u,anchor=''):
        s=(u+' '+anchor).lower(); score=0
        for k in self.cfg['career_keywords']:
            if k in s: score+=20
        for p in self.cfg['priority_paths']:
            if p in s: score+=8
        if self.is_ats(u): score+=1000
        if self.career_url_signal(u):score+=500
        if any(x in s for x in ('about','company','contact','team')):score+=80
        if self.is_job_detail_url(u):score+=250
        if self.is_low_value_url(u):score-=100
        return score

    def is_low_value_url(self,u):
        path=urlparse(u).path.lower().replace('_','-')
        return any(re.search(rf'(^|/){re.escape(term)}(?:/|$|-)',path) for term in (
            'docs','documentation','help','article','articles','academy','blog','news','press',
            'legal','privacy','terms','status','pricing','whitepaper','product','token','swap',
            'analytics','portfolio'))

    def normalize_discovery_url(self,u):
        parsed=urlparse(u)
        # Keep query strings for job/career/contact routes, where they may carry identity or routing.
        if self.is_job_detail_url(u) or self.career_url_signal(u) or any(x in parsed.path.lower() for x in ('contact','about')):
            return u
        if not parsed.query:return u
        normalized=parsed._replace(query='',fragment='').geturl()
        if normalized not in self.discovery_query_keys:
            self.discovery_query_keys.add(normalized)
            self.performance['query_variants_deduped']+=1
        return normalized

    @staticmethod
    def _credible_job_anchor(anchor):
        normalized=re.sub(r'\s+',' ',str(anchor or '')).strip().lower()
        return (5<=len(normalized)<=180 and normalized not in {
            'apply','apply now','learn more','view job','view role','details','read more',
            'careers','jobs','open positions','open roles'})

    def browser_fallback_worthwhile(self,url,purpose):
        if purpose in ('homepage','career','ats','job_detail'):return True
        return self.is_ats(url) or self.career_url_signal(url) or self.is_job_detail_url(url)

    async def fallback_for_purpose(self,url,purpose,http_outcome=None,access_status=''):
        if not self.browser_fallback_worthwhile(url,purpose):
            self.performance['browser_fallbacks_skipped']+=1
            return None
        return await self.browser_fetch(url,purpose,http_outcome,access_status)

    async def resolve_home(self,domain):
        domain=domain.strip().lower().replace('http://','').replace('https://','').strip('/')
        challenge=None;robots_denied=False;last_http_outcome={}
        for u in (f'https://{domain}',f'https://www.{domain}',f'http://{domain}',f'http://www.{domain}'):
            if not await self.robots_allowed(u):robots_denied=True;continue
            p=await self.fetch(u,check_robots=False)
            last_http_outcome=self.http_outcomes.get(u,{})
            if p and p.status<400 and len(p.text)>20:
                if self.is_challenge(p.title,p.text): challenge=p; continue
                return p,'OK'
        bp=await self.fallback_for_purpose(f'https://{domain}','homepage',last_http_outcome)
        if bp and not self.is_challenge(bp.title,bp.text):
            if self.browser_fallback_records:self.browser_fallback_records[-1]['access_status']='OK_BROWSER'
            write_browser_fallbacks_csv(self.outdir/'browser_fallbacks.csv',self.browser_fallback_records)
            return bp,'OK_BROWSER'
        if challenge or bp: return (bp or challenge),'ANTI_BOT'
        if robots_denied:return None,'ROBOTS_DENIED'
        return None,'NO_RESPONSE'

    def deterministic_jobs(self,pages,career_urls):
        careers=set(career_urls); jobs=[]
        for p in pages:
            path=urlparse(p.url).path.rstrip('/').lower()
            # A role page usually sits below /careers or /jobs and is not the landing page itself.
            roleish=self.is_job_detail_url(p.url)
            if roleish:
                anchor=next((candidate.link_anchors.get(p.url,'') for candidate in pages if p.url in candidate.links),'')
                title=self.extract_job_title(p,anchor_title=anchor) or self._usable_job_title(p.title)
                jobs.append((p.url,title))
        return jobs

    async def crawl_domain(self,domain):
        res=Result(domain)
        home,status=await self.resolve_home(domain); res.access_status=status
        if not home:
            res.error='No usable HTTP/HTML response'; return res
        if status=='ANTI_BOT':
            res.canonical_url=home.url; res.site_name=self.guess_name(home,domain); res.error='Anti-bot/challenge page detected'; return res
        res.alive=True; res.canonical_url=home.url
        base=self.root_domain(home.url)
        queue=[(home.url,0,1000,False)]; seen=set(); pages=[]; external_candidates=set();deferred_jobs=[]
        general_count=0;career_count=0;career_identified=False
        general_budget=self.cfg.get('max_general_pages_per_domain',12)
        while queue and len(pages)<self.cfg['max_pages_per_domain']:
            queue.sort(key=lambda x:x[2],reverse=True); url,depth,_,parent_low=queue.pop(0)
            key=self.normalize_discovery_url(url)
            if key in seen or depth>self.cfg['max_depth']: continue
            if self.is_job_detail_url(url):
                anchor=next((p.link_anchors.get(url,'') for p in pages if url in p.links),'')
                if self._credible_job_anchor(anchor):
                    deferred_jobs.append((url,anchor));self.performance['job_detail_pages_deferred']+=1
                    seen.add(key);continue
            high_value=self.career_url_signal(url) or self.is_job_detail_url(url) or any(x in urlparse(url).path.lower() for x in ('about','contact','company','team'))
            low_value=self.is_low_value_url(url) and not high_value
            if low_value and (parent_low or depth>1):
                self.performance['low_value_pages_skipped']+=1;seen.add(key);continue
            if general_count>=general_budget and not high_value:
                self.performance['low_value_pages_skipped']+=1;seen.add(key);continue
            # Once jobs/careers are located, stop broad exploration if nothing high-value remains.
            if career_identified and not high_value and not any(item[2]>=250 for item in queue):
                self.performance['domains_early_stopped']+=1;break
            seen.add(key); p=home if url==home.url else await self.fetch(url)
            if p is None and url!=home.url:
                purpose=('ats' if self.is_ats(url) else 'career' if self.career_url_signal(url)
                         else 'job_detail' if self.is_job_detail_url(url) else 'general')
                p=await self.fallback_for_purpose(url,purpose,access_status=res.access_status)
            if not p or self.is_challenge(p.title,p.text): continue
            pages.append(p)
            if self.career_url_signal(p.url) or self.is_ats(p.url):
                career_count+=1;career_identified=True;self.performance['career_pages_fetched']+=1
            else:
                general_count+=1;self.performance['general_pages_fetched']+=1
            for l in p.links:
                lkey=self.normalize_discovery_url(l)
                if lkey in seen:continue
                host=urlparse(l).netloc.lower(); same=self.root_domain(l)==base; score=self.link_score(l,p.link_anchors.get(l,''))
                if self.is_job_detail_url(l) and self._credible_job_anchor(p.link_anchors.get(l,'')):
                    deferred_jobs.append((l,p.link_anchors[l]));self.performance['job_detail_pages_deferred']+=1;seen.add(lkey);continue
                if same and depth<self.cfg['max_depth']:
                    if self.is_low_value_url(l) and not (self.career_url_signal(l) or any(x in urlparse(l).path.lower() for x in ('about','contact','company','team'))):
                        if depth>=1 or parent_low:
                            self.performance['low_value_pages_skipped']+=1;continue
                    queue.append((l,depth+1,score,self.is_low_value_url(p.url)))
                elif self.is_ats(l) or self.career_url_signal(l): external_candidates.add(l)
        # Validate external career/ATS landing pages.
        external_pages=[]
        for u in sorted(external_candidates,key=self.link_score,reverse=True)[:12]:
            p=await self.fetch(u)
            if p is None:p=await self.fallback_for_purpose(u,'ats' if self.is_ats(u) else 'career',access_status=res.access_status)
            if p and not self.is_challenge(p.title,p.text): external_pages.append(p)
        pages.extend(external_pages)
        # One controlled extra hop inside ATS to capture actual vacancy pages.
        ats_job_candidates=[]
        for p in external_pages:
            if self.is_ats(p.url):
                for l in p.links:
                    if self.is_ats(l) and l!=p.url: ats_job_candidates.append(l)
        for u in list(dict.fromkeys(ats_job_candidates))[:self.cfg.get('max_ats_job_pages',20)]:
            if u in {p.url for p in pages}: continue
            anchor=next((p.link_anchors.get(u,'') for p in external_pages if u in p.links),'')
            if self.is_job_detail_url(u) and self._credible_job_anchor(anchor):
                deferred_jobs.append((u,anchor));self.performance['job_detail_pages_deferred']+=1;continue
            p=await self.fetch(u)
            if p is None:p=await self.fallback_for_purpose(u,'job_detail',access_status=res.access_status)
            if p and not self.is_challenge(p.title,p.text): pages.append(p)

        # Deduplicate pages by final URL.
        uniq={}
        for p in pages: uniq.setdefault(p.url,p)
        pages=list(uniq.values())
        res.pages_crawled=len(pages); res.evidence_urls=[p.url for p in pages]
        res.emails=self.filter_emails([e for p in pages for e in p.emails],base)
        # Careers = validated landing pages only. Individual vacancy URLs stay exclusively in job_urls.
        res.careers_urls=list(dict.fromkeys(p.url for p in pages if self.career_url_signal(p.url) and not self.is_job_detail_url(p.url)))
        det_jobs=self.deterministic_jobs(pages,res.careers_urls)
        job_pairs=list(det_jobs)

        # Enumerate jobs from every discovered Ashby board through Ashby's public posting API.
        ashby_evidence=[]
        for board in [u for u in res.careers_urls if 'jobs.ashbyhq.com' in urlparse(u).netloc.lower()]:
            for ju,jt,api_url in await self.ashby_jobs(board):
                job_pairs.append((ju,jt)); ashby_evidence.append(api_url)
        res.evidence_urls=list(dict.fromkeys(res.evidence_urls+ashby_evidence))

        # Jobs are stored as URL/title records first. Never maintain two independent lists.
        res.site_name=self.guess_name(pages[0],domain)
        records=[]; seen_job_urls=set()
        for u,t in job_pairs:
            if not u or u in seen_job_urls: continue
            seen_job_urls.add(u)
            records.append({'url':u,'title':self._usable_job_title(t,res.site_name)})
        res.job_records=records
        for u,t in deferred_jobs:
            if u not in seen_job_urls:
                seen_job_urls.add(u);res.job_records.append({'url':u,'title':self._usable_job_title(t,res.site_name)})
        res.job_urls=[j['url'] for j in records]
        res.job_titles=[j['title'] for j in records]
        # Defensive separation: a URL can never be both a careers landing and an individual job.
        jobset=set(res.job_urls); res.careers_urls=[u for u in res.careers_urls if u not in jobset]
        if not self.ai: res.site_type='Unclassified (AI disabled)'
        await self.process_job_pipeline(res,pages)
        # Keep compatibility columns derived from the canonical paired records.
        res.job_urls=[j['url'] for j in res.job_records]
        res.job_titles=[j.get('title','') for j in res.job_records]
        res.careers_urls=[u for u in res.careers_urls if u not in set(res.job_urls)]
        return res

    def guess_name(self,p,domain):
        if p.title and not self.is_challenge(p.title,p.text):
            x=re.split(r'\s+[|–—-]\s+',p.title)[0].strip()
            if 1<len(x)<80:return x
        return domain.split('.')[0].title()

    async def ai_enrich(self,res,pages):
        selected=sorted(pages,key=lambda p:self.link_score(p.url),reverse=True)[:14]
        evidence=[{'url':p.url,'title':p.title,'text':p.text[:3000]} for p in selected]
        prompt={'domain':res.input_domain,'canonical_url':res.canonical_url,'site_name_guess':res.site_name,'validated_pages':evidence,
                'instructions':'Classify service and identify actual open role pages ONLY from validated_pages. Never create, repair, infer, or alter a URL. Generic careers/about/help pages are not jobs.'}
        schema='''Return ONLY JSON: {"site_name":"...","site_type":"DEX|CEX|Swap|Wallet|Bridge|DeFi|Lending|Staking|Infrastructure|Analytics|Trading Bot|Payment|Marketplace|Web3 Service|Crypto Service|Other","description":"short factual description","jobs":[{"url":"EXACT URL FROM validated_pages","title":"role title"}]}'''
        ai_started=None
        try:
            async with self.ai_semaphore:
                ai_started=time.monotonic();self.performance['ai_calls']+=1
                response=await asyncio.to_thread(self.ai.responses.create, model=self.model, input=schema+'\nINPUT:\n'+json.dumps(prompt,ensure_ascii=False), max_output_tokens=1000)
            txt=re.sub(r'^```(?:json)?\s*|\s*```$','',response.output_text.strip(),flags=re.I|re.S); d=json.loads(txt)
            res.site_name=(d.get('site_name') or res.site_name)[:120]; res.site_type=(d.get('site_type') or 'Other')[:80]; res.description=(d.get('description') or '')[:500]
            valid={p.url for p in pages}; jobs=[]
            for j in d.get('jobs',[]):
                u=j.get('url') if isinstance(j,dict) else None
                # A careers landing page may list role names, but it is NOT an individual job URL.
                # Reject it rather than pairing a title with a generic careers page.
                if u in valid and self.is_job_detail_url(u):
                    jobs.append((u,str(j.get('title','')).strip()))
            # Merge by URL, preserving the URL/title pair as one record.
            records=list(res.job_records); by_url={j['url']:j for j in records}
            for u,t in jobs:
                clean=self._usable_job_title(t,res.site_name)
                if u not in by_url:
                    rec={'url':u,'title':clean}; records.append(rec); by_url[u]=rec
                elif not by_url[u].get('title') and clean:
                    by_url[u]['title']=clean
            res.job_records=records
            res.job_urls=[j['url'] for j in records]
            res.job_titles=[j.get('title','') for j in records]
        except Exception as e:
            res.site_type='Unclassified (AI error)'; res.error=(res.error+'; ' if res.error else '')+'AI: '+str(e)[:250]
        finally:
            if ai_started is not None:self.performance['ai_seconds']+=time.monotonic()-ai_started

def load_domains(path):
    with open(path,'r',encoding='utf-8-sig',newline='') as f: rows=list(csv.DictReader(f))
    if not rows:return []
    key='Domain' if 'Domain' in rows[0] else list(rows[0])[0]
    return list(dict.fromkeys((r.get(key) or '').strip() for r in rows if (r.get(key) or '').strip()))

def write_csv(path,results,alive_only=False):
    fields=['Domain','Alive','Access Status','Canonical URL','Site Name','Site Type','Description','Emails encontrados','Careers URLs','Job URLs','Job Titles','Pages Crawled','Evidence URLs','Error']
    with open(path,'w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
        for r in results:
            if alive_only and not r.alive: continue
            w.writerow({'Domain':r.input_domain,'Alive':'YES' if r.alive else 'NO','Access Status':r.access_status,'Canonical URL':r.canonical_url,'Site Name':r.site_name,'Site Type':r.site_type,'Description':r.description,'Emails encontrados':' ; '.join(r.emails),'Careers URLs':' ; '.join(r.careers_urls),'Job URLs':' ; '.join(r.job_urls),'Job Titles':' ; '.join(r.job_titles),'Pages Crawled':r.pages_crawled,'Evidence URLs':' ; '.join(r.evidence_urls),'Error':r.error})

def write_jobs_csv(path,results):
    fields=['Domain','Site Name','Job Title','Job URL','Company','Location','Date Posted','Job Description']
    with open(path,'w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
        for r in results:
            records=r.job_records or [{'url':u,'title':(r.job_titles[i] if i < len(r.job_titles) else '')} for i,u in enumerate(r.job_urls)]
            for job in records:
                w.writerow({'Domain':r.input_domain,'Site Name':r.site_name,'Job Title':job.get('title',''),'Job URL':job.get('url',''),
                            'Company':job.get('company',''),'Location':job.get('location',''),'Date Posted':job.get('date_posted',''),
                            'Job Description':job.get('description','')})

def _job_output_row(result,job):
    return {'Domain':result.input_domain,'Site Name':result.site_name,'Job Title':job.get('title',''),
            'Job URL':job.get('url',''),'Company':job.get('company',''),'Location':job.get('location',''),
            'Date Posted':job.get('date_posted',''),'Job Description':job.get('description','')}

def write_jobs_profiled_csv(path,results):
    fields=['Domain','Site Name','Job Title','Job URL','Company','Location','Date Posted','Job Description',
            'Title Gate','Profile Relevance','Profile Relevance Reason']
    with open(path,'w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for result in results:
            for job in result.job_records:
                writer.writerow({**_job_output_row(result,job),'Title Gate':job.get('title_gate',''),
                    'Profile Relevance':job.get('profile_relevance',''),
                    'Profile Relevance Reason':job.get('profile_relevance_reason','')})

def write_jobs_rejected_csv(path,results):
    fields=['Domain','Site Name','Job Title','Job URL','Stage','Classification','Reason']
    with open(path,'w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for result in results:
            for job in result.profile_rejected_jobs:
                writer.writerow({'Domain':result.input_domain,'Site Name':result.site_name,
                    'Job Title':job.get('title',''),'Job URL':job.get('url',''),
                    'Stage':job.get('stage',''),'Classification':job.get('classification',''),
                    'Reason':job.get('reason','')})

def write_emails_csv(path,results):
    fields=['Domain','Site Name','Email']
    with open(path,'w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
        for r in results:
            for e in r.emails: w.writerow({'Domain':r.input_domain,'Site Name':r.site_name,'Email':e})

def write_browser_fallbacks_csv(path,records):
    fields=['domain','url','browser_final_url','purpose','started_at','duration_seconds','http_status_before',
            'http_failure_reason','browser_result','html_bytes','error','access_status']
    with open(path,'w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for record in records:writer.writerow({key:record.get(key,'') for key in fields})

def write_run_info(path,args,started_at,results,requested,app,ai_requested,completed=False,resumed=False,profile_info=None):
    data={'version':VERSION,'started_at':started_at,'finished_at':datetime.now().isoformat(timespec='seconds'),
          'input_file':str(args.csv),'domains_requested':requested,'domains_processed':len(results),
          'ai_enabled':ai_requested,'ai_available':bool(app.ai),'model':app.model,
          'completed':completed,'resumed':resumed}
    if profile_info:
        data.update({'profile_file':profile_info['profile_file'],'profile_version':profile_info['profile_version'],
                     'profile_fingerprint':profile_info['profile_fingerprint'],
                     'profile_filter_version':profile_info['profile_filter_version']})
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')

def load_checkpoint(path,strict=False):
    if not path.exists(): return {}
    try:
        data=json.loads(path.read_text(encoding='utf-8')); return {x['input_domain']:Result(**x) for x in data}
    except Exception:
        if strict:raise ValueError(f'Checkpoint inválido ou incompatível: {path}') from None
        return {}

def save_checkpoint(path,results):
    tmp=path.with_suffix('.tmp'); tmp.write_text(json.dumps([asdict(x) for x in results],ensure_ascii=False,indent=2),encoding='utf-8'); tmp.replace(path)

def validate_config(raw):
    if not isinstance(raw,dict):raise ValueError('A configuração deve ser um objeto JSON.')
    cfg={
        'request_retries':2,'retry_backoff_seconds':0.75,'max_retry_after_seconds':30,'respect_robots_txt':True,
        'robots_fail_closed':False,'max_domain_concurrency':3,'max_job_concurrency':4,'max_browser_concurrency':1,
        'browser_settle_seconds':0.5,'browser_timeout_seconds':25,
        'max_general_pages_per_domain':12,'max_retries_per_host':8,
        'max_ai_concurrency':1,'use_browser_fallback':True,'ai_enabled':True,
        'user_agent':f'SiteIntelCrawler/{VERSION} (+local research tool)',
        **raw,
    }
    positive=('max_pages_per_domain','max_depth','request_timeout_seconds','browser_timeout_seconds','max_concurrency','checkpoint_every',
              'max_general_pages_per_domain','max_retries_per_host',
              'max_ats_job_pages','max_domain_concurrency','max_job_concurrency','max_browser_concurrency','max_ai_concurrency')
    for key in positive:
        if not isinstance(cfg.get(key),int) or isinstance(cfg.get(key),bool) or cfg[key]<1:
            raise ValueError(f'Configuração inválida: {key} deve ser um inteiro positivo.')
    for key in ('delay_between_requests_seconds','retry_backoff_seconds','max_retry_after_seconds','browser_settle_seconds'):
        if not isinstance(cfg.get(key),(int,float)) or isinstance(cfg.get(key),bool) or cfg[key]<0:
            raise ValueError(f'Configuração inválida: {key} deve ser um número não negativo.')
    if not isinstance(cfg.get('request_retries'),int) or isinstance(cfg.get('request_retries'),bool) or cfg['request_retries']<0:
        raise ValueError('Configuração inválida: request_retries deve ser um inteiro não negativo.')
    for key in ('use_browser_fallback','ai_enabled','respect_robots_txt','robots_fail_closed'):
        if not isinstance(cfg.get(key),bool):raise ValueError(f'Configuração inválida: {key} deve ser booleano.')
    for key in ('career_keywords','priority_paths','email_localpart_blacklist','email_localpart_preferred'):
        if not isinstance(cfg.get(key),list) or not all(isinstance(x,str) for x in cfg[key]):
            raise ValueError(f'Configuração inválida: {key} deve ser uma lista de textos.')
    return cfg

def create_run_dir(outroot,started):
    base=outroot/started.strftime('%Y-%m-%d_%H-%M-%S');candidate=base;suffix=2
    while candidate.exists():
        candidate=Path(str(base)+f'-{suffix:02d}');suffix+=1
    candidate.mkdir(parents=True)
    return candidate

def resume_run_dir(outroot,value):
    if value!='latest':
        folder=Path(value).resolve()
    else:
        def resumable(path):
            try:
                info=json.loads((path/'run_info.json').read_text(encoding='utf-8'))
                return info.get('completed') is not True
            except (OSError,ValueError,TypeError,AttributeError):
                return True
        candidates=sorted((p for p in outroot.iterdir() if p.is_dir() and (p/'checkpoint.json').exists() and resumable(p)),
                          key=lambda p:(p.stat().st_mtime,p.name),reverse=True) if outroot.exists() else []
        if not candidates:raise ValueError(f'Nenhuma execução retomável encontrada em {outroot}.')
        folder=candidates[0].resolve()
    if not folder.is_dir() or not (folder/'checkpoint.json').is_file():
        raise ValueError(f'A pasta de retomada não contém checkpoint.json: {folder}')
    return folder

def persist(out,args,started_at,domains,result_map,app,ai_requested,completed=False,resumed=False,profile_info=None):
    results=[result_map[d] for d in domains if d in result_map]
    save_checkpoint(out/'checkpoint.json',results)
    write_csv(out/'results_all.csv',results)
    write_csv(out/'results_valid.csv',results,alive_only=True)
    write_jobs_csv(out/'jobs.csv',results)
    write_jobs_profiled_csv(out/'jobs_profiled.csv',results)
    write_jobs_rejected_csv(out/'jobs_rejected.csv',results)
    write_emails_csv(out/'emails.csv',results)
    write_browser_fallbacks_csv(out/'browser_fallbacks.csv',app.browser_fallback_records)
    write_run_info(out/'run_info.json',args,started_at,results,len(domains),app,ai_requested,completed,resumed,profile_info)

async def main():
    ap=argparse.ArgumentParser(description=f'Crypto/Web3 domain intelligence crawler v{VERSION}')
    ap.add_argument('csv',nargs='?',default='domains.csv')
    ap.add_argument('--config',default='config.json');ap.add_argument('--out',default='output')
    ap.add_argument('--no-ai',action='store_true');ap.add_argument('--model')
    ap.add_argument('--fresh',action='store_true',help='Inicia nova execução e ignora a leitura do cache de vagas, sem apagá-lo.')
    ap.add_argument('--resume',nargs='?',const='latest',metavar='RUN_DIR',help='Retoma uma pasta específica ou a execução mais recente em --out.')
    ap.add_argument('--limit',type=int,default=0);ap.add_argument('--offset',type=int,default=0,help='Ignora os primeiros N domínios antes de aplicar --limit.')
    ap.add_argument('--concurrency',type=int,help='Sobrescreve max_domain_concurrency.')
    ap.add_argument('--job-concurrency',type=int,help='Sobrescreve max_job_concurrency (detalhes das vagas).')
    ap.add_argument('--browser-concurrency',type=int,help='Sobrescreve max_browser_concurrency.')
    ap.add_argument('--no-robots',action='store_true',help='Ignora robots.txt explicitamente (não recomendado).')
    ap.add_argument('--test-api',action='store_true')
    ap.add_argument('--profile',help='Arquivo JSON exportado pelo dashboard com o perfil profissional.')
    ap.add_argument('--profile-test',action='store_true',help='Testa localmente uma pequena bateria de títulos com o perfil.')
    ap.add_argument('--test-title',help='Classifica localmente um título sem crawler, HTTP, Playwright ou IA.')
    args=ap.parse_args();load_dotenv();started=datetime.now()
    if args.resume and args.fresh:ap.error('--resume e --fresh não podem ser usados juntos.')
    if args.test_api and args.no_ai:ap.error('--test-api e --no-ai não podem ser usados juntos.')
    if args.profile_test and args.test_title is not None:ap.error('--profile-test e --test-title não podem ser usados juntos.')
    if args.limit<0 or args.offset<0:ap.error('--limit e --offset devem ser não negativos.')
    if (args.profile_test or args.test_title is not None) and not args.profile:
        ap.error('--profile é obrigatório com --profile-test e --test-title.')
    if not args.test_api and not args.profile:
        ap.error('--profile é obrigatório para crawling normal.')
    loaded_profile=None;matcher=None;profile_info=None
    if args.profile:
        try:
            loaded_profile=load_profile(args.profile)
            matcher=ProfileMatcher(loaded_profile.profile)
            profile_info={'profile_file':loaded_profile.profile_file,'profile_version':loaded_profile.profile_version,
                          'profile_fingerprint':loaded_profile.fingerprint,'profile_filter_version':PROFILE_FILTER_VERSION}
        except ValueError as exc:ap.error(str(exc))
    if args.profile_test or args.test_title is not None:
        if args.profile_test:
            titles=('Customer Operations Manager','Senior Customer Operations Lead','Solutions Engineer',
                    'Enterprise Partnerships Lead (Financial Markets)','Senior Software Engineer',
                    'Backend Engineer','Account Executive','Quantitative Researcher','P2P Dispute Moderator',
                    'Social Media Manager','Risk Operations Analyst','Revenue Operations Analyst')
            console.print('PROFILE TITLE TEST')
            for title in titles:
                result=matcher.classify_title(title)
                console.print(f'{title} | {result.classification} | {result.reason}')
        else:
            result=matcher.classify_title(args.test_title)
            console.print('PROFILE TITLE TEST')
            console.print(f'Title: {args.test_title}')
            console.print(f'Classification: {result.classification}')
            console.print(f'Reason: {result.reason}')
        return 0
    try:
        cfg=validate_config(json.loads(Path(args.config).read_text(encoding='utf-8')))
    except (OSError,ValueError,json.JSONDecodeError) as e:
        ap.error(f'Não foi possível carregar a configuração: {e}')
    if args.model:cfg['ai_model']=args.model
    if args.concurrency is not None:
        if args.concurrency<1:ap.error('--concurrency deve ser um inteiro positivo.')
        cfg['max_domain_concurrency']=args.concurrency
    if args.job_concurrency is not None:
        if args.job_concurrency<1:ap.error('--job-concurrency deve ser um inteiro positivo.')
        cfg['max_job_concurrency']=args.job_concurrency
    if args.browser_concurrency is not None:
        if args.browser_concurrency<1:ap.error('--browser-concurrency deve ser um inteiro positivo.')
        cfg['max_browser_concurrency']=args.browser_concurrency
    if args.no_robots:
        cfg['respect_robots_txt']=False
        console.print('[yellow]Aviso: respeito a robots.txt desativado explicitamente.[/]')
    ai_requested=bool(cfg.get('ai_enabled',True)) and not args.no_ai
    if args.test_api:ai_requested=True
    domains=[]
    if not args.test_api:
        try:domains=load_domains(args.csv)
        except (OSError,csv.Error) as e:ap.error(f'Não foi possível ler o arquivo de domínios: {e}')
        domains=domains[args.offset:] if args.offset else domains
        domains=domains[:args.limit] if args.limit else domains
        if not domains:ap.error('Nenhum domínio foi selecionado para a coleta.')
    outroot=Path(args.out)
    if args.test_api:
        out=outroot
    else:
        try:
            out=resume_run_dir(outroot,args.resume) if args.resume else create_run_dir(outroot,started)
        except (OSError,ValueError) as e:ap.error(str(e))
    loaded={};resume_info={}
    if not args.test_api:
        cp=out/'checkpoint.json'
        try:loaded=load_checkpoint(cp,strict=True) if args.resume else {}
        except ValueError as e:ap.error(str(e))
        if args.resume and (out/'run_info.json').exists():
            try:
                value=json.loads((out/'run_info.json').read_text(encoding='utf-8'))
                resume_info=value if isinstance(value,dict) else {}
            except (OSError,ValueError,TypeError):resume_info={}
        if args.resume:
            try:validate_resume_profile(resume_info,loaded_profile.fingerprint)
            except ValueError as exc:ap.error(str(exc))
        outside=[domain for domain in loaded if domain not in domains]
        if outside:ap.error('A seleção atual excluiria domínios já gravados no checkpoint. Retome com o mesmo arquivo, offset e limite.')
        expected=resume_info.get('domains_requested')
        if 'completed' in resume_info and isinstance(expected,int) and expected!=len(domains):
            ap.error(f'A execução original possui {expected} domínios, mas a seleção atual possui {len(domains)}.')
    app=App(cfg,out,ai_requested,job_cache_path=None if args.test_api else outroot/'jobs_cache.sqlite3',
            ignore_job_cache=args.fresh,profile_matcher=matcher)
    try:
        if args.test_api:
            ai_ok=await app.preflight_ai() if app.ai else False
            if not ai_ok:console.print('[red]Teste da API falhou: confira OPENAI_API_KEY, OPENAI_MODEL e billing.[/]')
            return 0 if ai_ok else 1
        ai_ok=await app.preflight_ai() if app.ai else False
        done={d:loaded[d] for d in domains if d in loaded}
        run_monotonic=time.monotonic()
        started_at=started.isoformat(timespec='seconds')
        if resume_info:started_at=str(resume_info.get('started_at') or started_at)
        console.print(f'[bold cyan]JobIntel Crawler V{VERSION}[/] — {len(domains)} domínios | modelo: {app.model} | retomada: {len(done)} | concorrência: {cfg["max_domain_concurrency"]}')
        console.print(f'[dim]Saída: {out}[/]')
        semaphore=asyncio.Semaphore(cfg['max_domain_concurrency'])
        async def crawl_one(domain):
            async with semaphore:
                domain_started=time.monotonic()
                try:return domain,await app.crawl_domain(domain)
                except Exception as e:
                    console.print(f'[red]Erro inesperado em {domain}: {str(e)[:300]}[/]')
                    return domain,Result(domain,access_status='ERROR',error='Unexpected crawler error: '+str(e)[:500])
                finally:app.performance['domain_seconds'].append(time.monotonic()-domain_started)
        pending=[d for d in domains if d not in done];since_checkpoint=0
        with Progress(SpinnerColumn(),TextColumn('[progress.description]{task.description}'),BarColumn(),TaskProgressColumn(),console=console) as prog:
            task=prog.add_task('Crawling',total=len(domains),completed=len(done))
            tasks=[asyncio.create_task(crawl_one(d)) for d in pending]
            for future in asyncio.as_completed(tasks):
                d,r=await future;done[d]=r;since_checkpoint+=1
                prog.update(task,description=f'[cyan]{d}[/]');prog.advance(task)
                if since_checkpoint>=cfg['checkpoint_every']:
                    persist(out,args,started_at,domains,done,app,ai_requested,resumed=bool(args.resume),profile_info=profile_info);since_checkpoint=0
        persist(out,args,started_at,domains,done,app,ai_requested,completed=True,resumed=bool(args.resume),profile_info=profile_info)
        console.print(f'[bold green]{app.job_stats_summary()}[/]')
        console.print(app.performance_summary(time.monotonic()-run_monotonic,len(pending)))
        console.print(app.profile_filter_summary([done[domain] for domain in domains if domain in done]))
        return 0
    finally:await app.close()

if __name__=='__main__':sys.exit(asyncio.run(main()))
