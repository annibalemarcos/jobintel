from __future__ import annotations
import argparse, asyncio, csv, hashlib, html as htmlmod, json, os, re, sqlite3, sys, time
from datetime import datetime, timezone
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
import tldextract

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
    def __init__(self, cfg, outdir: Path, ai_enabled=True, job_cache_path=None, ignore_job_cache=False):
        self.cfg=cfg; self.outdir=outdir; self.ai_enabled=ai_enabled
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
        self.browser_semaphore=asyncio.Semaphore(cfg.get('max_browser_concurrency',1))
        self.ai_semaphore=asyncio.Semaphore(cfg.get('max_ai_concurrency',1))
        self.job_cache=JobCache(job_cache_path) if job_cache_path else None
        self.ignore_job_cache=ignore_job_cache
        self.job_cache_locks={}
        self.job_session_cache={}
        self.job_stats={'found':0,'cache_hits':0,'collected':0,'success':0,'failures':0}
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
            if remaining>0:await asyncio.sleep(remaining)
            self.host_last_request[host]=time.monotonic()

    async def request(self,url):
        retries=max(0,int(self.cfg.get('request_retries',0)))
        backoff=max(0,float(self.cfg.get('retry_backoff_seconds',0.5)))
        for attempt in range(retries+1):
            try:
                await self.wait_for_host(url)
                response=await self.client.get(url)
                if response.status_code not in (408,425,429) and response.status_code<500:
                    return response
                if attempt==retries:return response
                retry_after=response.headers.get('retry-after','')
                wait=float(retry_after) if retry_after.replace('.','',1).isdigit() else backoff*(2**attempt)
                wait=min(wait,float(self.cfg.get('max_retry_after_seconds',30)))
            except (httpx.TimeoutException,httpx.NetworkError):
                if attempt==retries:return None
                wait=backoff*(2**attempt)
            await asyncio.sleep(wait)
        return None

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
        links=[]
        for a in soup.find_all('a',href=True):
            href=urldefrag(urljoin(final_url,a['href'].strip()))[0]
            if href.startswith(('http://','https://')) and not urlparse(href).path.lower().endswith(BAD_EXT): links.append(href)
        return Page(final_url,status,title,text,list(dict.fromkeys(links)),self.extract_emails(' '.join(email_source)),raw)

    async def fetch(self, url, check_robots=True)->Optional[Page]:
        try:
            if check_robots and not await self.robots_allowed(url):return None
            r=await self.request(url)
            if r is None:return None
            ctype=r.headers.get('content-type','').lower()
            if r.status_code>=400 or ('text/html' not in ctype and 'application/xhtml' not in ctype): return None
            return self.parse_html(r.text,str(r.url),r.status_code)
        except Exception: return None

    async def browser_fetch(self,url)->Optional[Page]:
        if not self.cfg.get('use_browser_fallback'): return None
        if not await self.robots_allowed(url):return None
        try:
            from playwright.async_api import async_playwright
            async with self.browser_semaphore:
                await self.wait_for_host(url)
                async with async_playwright() as p:
                    b=await p.chromium.launch(headless=True)
                    page=await b.new_page(user_agent=self.user_agent)
                    resp=await page.goto(url, wait_until='domcontentloaded', timeout=self.cfg['request_timeout_seconds']*1000)
                    await page.wait_for_timeout(1800)
                    raw=await page.content(); final=page.url
                    await b.close()
                    return self.parse_html(raw,final,resp.status if resp else 200)
        except Exception: return None

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
        return t[:180]

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

    def html_job_description(self,raw,page_text=''):
        if not raw:
            return page_text.strip() if len(page_text.strip())>=80 else ''
        soup=BeautifulSoup(raw,'html.parser')
        for x in soup(['script','style','noscript','svg','canvas','template','iframe','nav','header','footer','aside','form','button']):
            x.decompose()
        for x in list(soup.find_all(True)):
            if x.parent is None: continue
            role=str(x.get('role') or '').lower()
            marker=' '.join([str(x.get('id') or ''),*map(str,x.get('class') or [])]).lower()
            if role in {'navigation','banner','contentinfo','complementary'} or any(
                token in marker for token in ('cookie','breadcrumb','sidebar','navigation','main-menu','site-menu',
                                               'mobile-menu','site-header','site-footer','social-share',
                                               'share-buttons','related-jobs')
            ):
                x.decompose()
        explicit=[]
        for x in soup.find_all(True):
            marker=' '.join([str(x.get('id') or ''),*map(str,x.get('class') or [])]).lower().replace('_','-')
            if str(x.get('itemprop') or '').lower()=='description' or any(token in marker for token in (
                'job-description','jobdescription','posting-description','position-description','job-content','job-details'
            )):
                text=self.clean_description(str(x))
                if text: explicit.append(text)
        if explicit:return max(explicit,key=len)
        semantic=[self.clean_description(str(x)) for x in soup.find_all(['main','article'])]
        semantic=[x for x in semantic if x]
        if semantic:return max(semantic,key=len)
        body=self.clean_description(str(soup.body or soup))
        return body if len(body)>=80 else ''

    def extract_job_details(self,page):
        postings=[]
        if page.raw_html:
            soup=BeautifulSoup(page.raw_html,'html.parser')
            def collect(value):
                if isinstance(value,dict):
                    kind=value.get('@type')
                    kinds=kind if isinstance(kind,list) else [kind]
                    if any(str(x).lower()=='jobposting' for x in kinds): postings.append(value)
                    for nested in value.values(): collect(nested)
                elif isinstance(value,list):
                    for nested in value: collect(nested)
            for script in soup.find_all('script'):
                if 'ld+json' not in str(script.get('type') or '').lower(): continue
                try: collect(json.loads(script.string or script.get_text() or ''))
                except (json.JSONDecodeError,TypeError,ValueError): continue
        details={}
        if postings:
            posting=max(postings,key=lambda p:(len(str(p.get('description') or '')),sum(bool(p.get(k)) for k in ('title','hiringOrganization','jobLocation','datePosted'))))
            title=posting.get('title')
            if isinstance(title,str) and title.strip(): details['title']=self.clean_job_title(title)
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
        else: description=''
        if not description: description=self.html_job_description(page.raw_html,page.text)
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

    async def enrich_job_records(self,res,pages):
        page_cache={self.job_url_key(p.url):p for p in pages}
        attempted={}
        for job in res.job_records:
            self.job_stats['found']+=1
            url=job.get('url',''); key=self.job_url_key(url)
            if not url:
                self.job_stats['failures']+=1
                continue
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
                        continue
                    self.job_stats['collected']+=1
                    page=page_cache.get(key)
                    if page is None:
                        if key not in attempted:
                            page=await self.fetch(url)
                            if page is None: page=await self.browser_fetch(url)
                            attempted[key]=page
                        else: page=attempted[key]
                        if page is not None:
                            page_cache[key]=page
                            page_cache[self.job_url_key(page.url)]=page
                            if not self.is_challenge(page.title,page.text):
                                pages.append(page)
                                res.evidence_urls=list(dict.fromkeys(res.evidence_urls+[page.url]))
                    if page is None or self.is_challenge(page.title,page.text):
                        if self.job_cache:self.job_cache.store(key,job,'failed')
                        self.job_stats['failures']+=1
                        continue
                    details=self.extract_job_details(page)
                    if details.get('title'): details['title']=self.clean_job_title(details['title'],res.site_name)
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
                    continue
        res.pages_crawled=len({p.url for p in pages})

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
        try:
            r=await self.client.get(api)
            if r.status_code != 200: return []
            data=r.json(); out=[]
            for j in data.get('jobs',[]):
                if not isinstance(j,dict) or j.get('isListed') is False: continue
                url=str(j.get('jobUrl') or '').strip(); title=str(j.get('title') or '').strip()
                if url.startswith(('http://','https://')) and title:
                    out.append((url,title,api))
            return out
        except Exception:
            return []

    def link_score(self,u,anchor=''):
        s=(u+' '+anchor).lower(); score=0
        for k in self.cfg['career_keywords']:
            if k in s: score+=20
        for p in self.cfg['priority_paths']:
            if p in s: score+=8
        if self.is_ats(u): score+=35
        if any(x in s for x in ('blog','news','academy','docs','developers','price','token','swap')): score-=3
        return score

    async def resolve_home(self,domain):
        domain=domain.strip().lower().replace('http://','').replace('https://','').strip('/')
        challenge=None;robots_denied=False
        for u in (f'https://{domain}',f'https://www.{domain}',f'http://{domain}',f'http://www.{domain}'):
            if not await self.robots_allowed(u):robots_denied=True;continue
            p=await self.fetch(u,check_robots=False)
            if p and p.status<400 and len(p.text)>20:
                if self.is_challenge(p.title,p.text): challenge=p; continue
                return p,'OK'
        bp=await self.browser_fetch(f'https://{domain}')
        if bp and not self.is_challenge(bp.title,bp.text): return bp,'OK_BROWSER'
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
                title=p.title.strip()
                jobs.append((p.url,title[:180] if title else ''))
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
        queue=[(home.url,0,1000)]; seen=set(); pages=[]; external_candidates=set()
        while queue and len(pages)<self.cfg['max_pages_per_domain']:
            queue.sort(key=lambda x:x[2],reverse=True); url,depth,_=queue.pop(0)
            if url in seen or depth>self.cfg['max_depth']: continue
            seen.add(url); p=home if url==home.url else await self.fetch(url)
            if not p or self.is_challenge(p.title,p.text): continue
            pages.append(p)
            for l in p.links:
                host=urlparse(l).netloc.lower(); same=self.root_domain(l)==base; score=self.link_score(l)
                if same and l not in seen and depth<self.cfg['max_depth']: queue.append((l,depth+1,score))
                elif self.is_ats(l) or self.career_url_signal(l): external_candidates.add(l)
        # Validate external career/ATS landing pages.
        external_pages=[]
        for u in sorted(external_candidates,key=self.link_score,reverse=True)[:12]:
            p=await self.fetch(u)
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
            p=await self.fetch(u)
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
            records.append({'url':u,'title':self.clean_job_title(t,res.site_name)})
        res.job_records=records
        res.job_urls=[j['url'] for j in records]
        res.job_titles=[j['title'] for j in records]
        # Defensive separation: a URL can never be both a careers landing and an individual job.
        jobset=set(res.job_urls); res.careers_urls=[u for u in res.careers_urls if u not in jobset]
        if self.ai: await self.ai_enrich(res,pages)
        else: res.site_type='Unclassified (AI disabled)'
        await self.enrich_job_records(res,pages)
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
        try:
            async with self.ai_semaphore:
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
                clean=self.clean_job_title(t,res.site_name)
                if u not in by_url:
                    rec={'url':u,'title':clean}; records.append(rec); by_url[u]=rec
                elif not by_url[u].get('title') and clean:
                    by_url[u]['title']=clean
            res.job_records=records
            res.job_urls=[j['url'] for j in records]
            res.job_titles=[j.get('title','') for j in records]
        except Exception as e:
            res.site_type='Unclassified (AI error)'; res.error=(res.error+'; ' if res.error else '')+'AI: '+str(e)[:250]

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

def write_emails_csv(path,results):
    fields=['Domain','Site Name','Email']
    with open(path,'w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
        for r in results:
            for e in r.emails: w.writerow({'Domain':r.input_domain,'Site Name':r.site_name,'Email':e})

def write_run_info(path,args,started_at,results,requested,app,ai_requested,completed=False,resumed=False):
    data={'version':VERSION,'started_at':started_at,'finished_at':datetime.now().isoformat(timespec='seconds'),
          'input_file':str(args.csv),'domains_requested':requested,'domains_processed':len(results),
          'ai_enabled':ai_requested,'ai_available':bool(app.ai),'model':app.model,
          'completed':completed,'resumed':resumed}
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
        'robots_fail_closed':False,'max_domain_concurrency':3,'max_browser_concurrency':1,
        'max_ai_concurrency':1,'use_browser_fallback':True,'ai_enabled':True,
        'user_agent':f'SiteIntelCrawler/{VERSION} (+local research tool)',
        **raw,
    }
    positive=('max_pages_per_domain','max_depth','request_timeout_seconds','max_concurrency','checkpoint_every',
              'max_ats_job_pages','max_domain_concurrency','max_browser_concurrency','max_ai_concurrency')
    for key in positive:
        if not isinstance(cfg.get(key),int) or isinstance(cfg.get(key),bool) or cfg[key]<1:
            raise ValueError(f'Configuração inválida: {key} deve ser um inteiro positivo.')
    for key in ('delay_between_requests_seconds','retry_backoff_seconds','max_retry_after_seconds'):
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

def persist(out,args,started_at,domains,result_map,app,ai_requested,completed=False,resumed=False):
    results=[result_map[d] for d in domains if d in result_map]
    save_checkpoint(out/'checkpoint.json',results)
    write_csv(out/'results_all.csv',results)
    write_csv(out/'results_valid.csv',results,alive_only=True)
    write_jobs_csv(out/'jobs.csv',results)
    write_emails_csv(out/'emails.csv',results)
    write_run_info(out/'run_info.json',args,started_at,results,len(domains),app,ai_requested,completed,resumed)

async def main():
    ap=argparse.ArgumentParser(description=f'Crypto/Web3 domain intelligence crawler v{VERSION}')
    ap.add_argument('csv',nargs='?',default='domains.csv')
    ap.add_argument('--config',default='config.json');ap.add_argument('--out',default='output')
    ap.add_argument('--no-ai',action='store_true');ap.add_argument('--model')
    ap.add_argument('--fresh',action='store_true',help='Inicia nova execução e ignora a leitura do cache de vagas, sem apagá-lo.')
    ap.add_argument('--resume',nargs='?',const='latest',metavar='RUN_DIR',help='Retoma uma pasta específica ou a execução mais recente em --out.')
    ap.add_argument('--limit',type=int,default=0);ap.add_argument('--offset',type=int,default=0,help='Ignora os primeiros N domínios antes de aplicar --limit.')
    ap.add_argument('--concurrency',type=int,help='Sobrescreve max_domain_concurrency.')
    ap.add_argument('--no-robots',action='store_true',help='Ignora robots.txt explicitamente (não recomendado).')
    ap.add_argument('--test-api',action='store_true')
    args=ap.parse_args();load_dotenv();started=datetime.now()
    if args.resume and args.fresh:ap.error('--resume e --fresh não podem ser usados juntos.')
    if args.test_api and args.no_ai:ap.error('--test-api e --no-ai não podem ser usados juntos.')
    if args.limit<0 or args.offset<0:ap.error('--limit e --offset devem ser não negativos.')
    try:
        cfg=validate_config(json.loads(Path(args.config).read_text(encoding='utf-8')))
    except (OSError,ValueError,json.JSONDecodeError) as e:
        ap.error(f'Não foi possível carregar a configuração: {e}')
    if args.model:cfg['ai_model']=args.model
    if args.concurrency is not None:
        if args.concurrency<1:ap.error('--concurrency deve ser um inteiro positivo.')
        cfg['max_domain_concurrency']=args.concurrency
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
    app=App(cfg,out,ai_requested,job_cache_path=None if args.test_api else outroot/'jobs_cache.sqlite3',
            ignore_job_cache=args.fresh)
    try:
        ai_ok=await app.preflight_ai() if app.ai else False
        if args.test_api:
            if not ai_ok:console.print('[red]Teste da API falhou: confira OPENAI_API_KEY, OPENAI_MODEL e billing.[/]')
            return 0 if ai_ok else 1
        cp=out/'checkpoint.json'
        try:loaded=load_checkpoint(cp,strict=True) if args.resume else {}
        except ValueError as e:ap.error(str(e))
        resume_info={}
        if args.resume and (out/'run_info.json').exists():
            try:
                value=json.loads((out/'run_info.json').read_text(encoding='utf-8'))
                resume_info=value if isinstance(value,dict) else {}
            except (OSError,ValueError,TypeError):resume_info={}
        outside=[d for d in loaded if d not in domains]
        if outside:ap.error('A seleção atual excluiria domínios já gravados no checkpoint. Retome com o mesmo arquivo, offset e limite.')
        expected=resume_info.get('domains_requested')
        if 'completed' in resume_info and isinstance(expected,int) and expected!=len(domains):
            ap.error(f'A execução original possui {expected} domínios, mas a seleção atual possui {len(domains)}.')
        done={d:loaded[d] for d in domains if d in loaded}
        started_at=started.isoformat(timespec='seconds')
        if resume_info:started_at=str(resume_info.get('started_at') or started_at)
        console.print(f'[bold cyan]JobIntel Crawler V{VERSION}[/] — {len(domains)} domínios | modelo: {app.model} | retomada: {len(done)} | concorrência: {cfg["max_domain_concurrency"]}')
        console.print(f'[dim]Saída: {out}[/]')
        semaphore=asyncio.Semaphore(cfg['max_domain_concurrency'])
        async def crawl_one(domain):
            async with semaphore:
                try:return domain,await app.crawl_domain(domain)
                except Exception as e:
                    console.print(f'[red]Erro inesperado em {domain}: {str(e)[:300]}[/]')
                    return domain,Result(domain,access_status='ERROR',error='Unexpected crawler error: '+str(e)[:500])
        pending=[d for d in domains if d not in done];since_checkpoint=0
        with Progress(SpinnerColumn(),TextColumn('[progress.description]{task.description}'),BarColumn(),TaskProgressColumn(),console=console) as prog:
            task=prog.add_task('Crawling',total=len(domains),completed=len(done))
            tasks=[asyncio.create_task(crawl_one(d)) for d in pending]
            for future in asyncio.as_completed(tasks):
                d,r=await future;done[d]=r;since_checkpoint+=1
                prog.update(task,description=f'[cyan]{d}[/]');prog.advance(task)
                if since_checkpoint>=cfg['checkpoint_every']:
                    persist(out,args,started_at,domains,done,app,ai_requested,resumed=bool(args.resume));since_checkpoint=0
        persist(out,args,started_at,domains,done,app,ai_requested,completed=True,resumed=bool(args.resume))
        console.print(f'[bold green]{app.job_stats_summary()}[/]')
        return 0
    finally:await app.close()

if __name__=='__main__':sys.exit(asyncio.run(main()))
