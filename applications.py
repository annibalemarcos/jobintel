"""Durable application tracking, independent of crawler output files."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
import zipfile
from contextlib import contextmanager
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

STATUSES = {'new': 'Não aplicada', 'saved': 'Quero aplicar', 'applied': 'Apliquei',
            'responded': 'Recebi resposta', 'interview': 'Entrevista', 'offer': 'Proposta',
            'hired': 'Contratado', 'rejected': 'Rejeitado', 'withdrawn': 'Desisti', 'closed': 'Vaga encerrada'}
DATES = ('applied_at', 'response_at', 'interview_at', 'offer_at', 'hired_at', 'rejected_at', 'follow_up_at')
TEXT = {'notes': 20000, 'contact': 500, 'channel': 200, 'rejection_reason': 2000, 'resume_version': 500}
PROFILE_ID = 'local-user'
PROFILE_LISTS = ('desired_roles','interests','employer_types','excluded_roles','regions','contract_types',
                 'work_mode_restrictions','work_history','skills','technologies','domain_knowledge','languages','education','certifications')
PROFILE_TEXT_LIMITS = {'current_location':300,'seniority':120,'currency':8,'salary_period':20,
                       'salary_flexibility':30,'professional_summary':10000,'analysis_notes':20000}


class ConflictError(ValueError):
    pass


def job_key(url):
    if not isinstance(url, str) or len(url) > 4000:
        raise ValueError('URL inválida.')
    p = urlsplit(url.strip())
    if p.scheme.lower() not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ValueError('Informe uma URL http ou https sem credenciais.')
    host = p.hostname.lower()
    if ':' in host:
        host = '[' + host + ']'
    if p.port and not (p.scheme.lower() == 'https' and p.port == 443 or p.scheme.lower() == 'http' and p.port == 80):
        host += ':' + str(p.port)
    path = p.path[:-1] if p.path.endswith('/') else p.path
    return urlunsplit((p.scheme.lower(), host, path or '/', urlencode(sorted(parse_qsl(p.query, keep_blank_values=True), key=lambda x: x[0])), ''))


def unscored():
    return {'status': 'not_analyzed', 'total': None, 'skills': None, 'experience': None,
            'seniority': None, 'location': None, 'language': None, 'explanation': '',
            'strengths': [], 'gaps': [], 'profile_id': None, 'profile_version': None,
            'model': None, 'rubric_version': None, 'analyzed_at': None, 'evidence': []}


def blank_application():
    return {'tracked': False, 'status': 'new', 'priority': 'normal', 'rejection_stage': 'unknown', 'version': 0,
            **{k: '' for k in DATES}, **{k: '' for k in TEXT}, 'created_at': '', 'updated_at': '', 'history': []}


def blank_profile():
    return {'desired_roles': [], 'interests': [], 'seniority': '', 'employer_types': [], 'excluded_roles': [],
            'related_roles_open': True, 'current_location': '',
            'work_modes': {'remote':'acceptable','hybrid':'acceptable','onsite':'acceptable'},
            'work_mode_restrictions': [],
            'regions': [], 'relocation': False, 'currency':'', 'salary_min':'', 'salary_desired':'',
            'salary_period':'annual', 'salary_flexibility':'reference', 'contract_types':[],
            'professional_summary':'','experience_years':'','work_history':[],'skills':[],'technologies':[],
            'domain_knowledge':[],'languages':[],'education':[],'certifications':[],'analysis_notes':'',
            'resume':None}


def validate_profile(profile):
    if not isinstance(profile,dict): raise ValueError('Perfil inválido.')
    value={**blank_profile(),**profile}
    for key in PROFILE_LISTS:
        items=value.get(key)
        if not isinstance(items,list) or len(items)>200 or any(not isinstance(item,str) or len(item)>1000 for item in items):
            raise ValueError(f'Campo inválido: {key}.')
        value[key]=list(dict.fromkeys(item.strip() for item in items if item.strip()))
    if any(item not in {'remote','hybrid','onsite'} for item in value['work_mode_restrictions']):
        raise ValueError('Restrição de modelo de trabalho inválida.')
    for key,limit in PROFILE_TEXT_LIMITS.items():
        item=value.get(key)
        if not isinstance(item,str) or len(item)>limit: raise ValueError(f'Campo inválido: {key}.')
        value[key]=item.strip()
    for key in ('related_roles_open','relocation'):
        if not isinstance(value.get(key),bool): raise ValueError(f'Campo inválido: {key}.')
    if value['salary_period'] not in {'annual','monthly','hourly'}: raise ValueError('Período salarial inválido.')
    if value['salary_flexibility'] not in {'rigid','flexible','reference'}: raise ValueError('Flexibilidade salarial inválida.')
    modes=value.get('work_modes')
    if not isinstance(modes,dict): raise ValueError('Preferências de trabalho inválidas.')
    value['work_modes']={key:modes.get(key,'acceptable') for key in ('remote','hybrid','onsite')}
    if any(mode not in {'preferred','acceptable','unwanted'} for mode in value['work_modes'].values()):
        raise ValueError('Nível de preferência de trabalho inválido.')
    for key in ('salary_min','salary_desired','experience_years'):
        item=value.get(key,'')
        if item in (None,''): value[key]=''; continue
        try: number=float(item)
        except (TypeError,ValueError): raise ValueError(f'Campo numérico inválido: {key}.') from None
        if number<0 or number>1_000_000_000: raise ValueError(f'Campo numérico inválido: {key}.')
        value[key]=str(item)
    if value.get('resume') is not None and not isinstance(value['resume'],dict): raise ValueError('Currículo inválido.')
    return value


class ApplicationStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS applications (
                    job_key TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT NOT NULL,
                    company TEXT NOT NULL, domain TEXT NOT NULL, source TEXT NOT NULL,
                    payload TEXT NOT NULL, version INTEGER NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS application_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_key TEXT NOT NULL,
                    timestamp TEXT NOT NULL, changes TEXT NOT NULL,
                    FOREIGN KEY(job_key) REFERENCES applications(job_key) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS candidate_profiles (
                    id TEXT NOT NULL, version INTEGER NOT NULL, summary TEXT NOT NULL DEFAULT '',
                    skills_json TEXT NOT NULL DEFAULT '[]', experience_json TEXT NOT NULL DEFAULT '[]',
                    preferences_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL,
                    PRIMARY KEY(id, version)
                );
                CREATE TABLE IF NOT EXISTS job_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_key TEXT NOT NULL,
                    profile_id TEXT NOT NULL, profile_version INTEGER NOT NULL,
                    total REAL CHECK(total BETWEEN 0 AND 100),
                    skills REAL CHECK(skills BETWEEN 0 AND 100),
                    experience REAL CHECK(experience BETWEEN 0 AND 100),
                    seniority REAL CHECK(seniority BETWEEN 0 AND 100),
                    location REAL CHECK(location BETWEEN 0 AND 100),
                    language REAL CHECK(language BETWEEN 0 AND 100),
                    status TEXT NOT NULL DEFAULT 'analyzed', explanation TEXT NOT NULL DEFAULT '',
                    strengths_json TEXT NOT NULL DEFAULT '[]', gaps_json TEXT NOT NULL DEFAULT '[]',
                    evidence_json TEXT NOT NULL DEFAULT '[]', model TEXT, rubric_version TEXT,
                    analyzed_at TEXT NOT NULL,
                    FOREIGN KEY(profile_id, profile_version) REFERENCES candidate_profiles(id, version)
                );
                CREATE TABLE IF NOT EXISTS active_candidate_profile (
                    profile_id TEXT PRIMARY KEY, version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS job_pre_matches (
                    job_key TEXT NOT NULL, profile_id TEXT NOT NULL, profile_version INTEGER NOT NULL,
                    description_hash TEXT NOT NULL DEFAULT '', rule_version TEXT NOT NULL,
                    classification TEXT NOT NULL CHECK(classification IN ('strong_candidate','possible_candidate','weak_candidate')),
                    pre_score INTEGER NOT NULL CHECK(pre_score BETWEEN 0 AND 100),
                    positive_json TEXT NOT NULL, negative_json TEXT NOT NULL, unknown_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(job_key,profile_id,profile_version,description_hash,rule_version),
                    FOREIGN KEY(profile_id,profile_version) REFERENCES candidate_profiles(id,version)
                );
                CREATE TABLE IF NOT EXISTS profile_fit_analyses (
                    job_key TEXT NOT NULL, profile_id TEXT NOT NULL, profile_version INTEGER NOT NULL,
                    job_description_hash TEXT NOT NULL, rubric_version TEXT NOT NULL, model TEXT NOT NULL,
                    profile_score INTEGER NOT NULL CHECK(profile_score BETWEEN 0 AND 100),
                    overall_fit TEXT NOT NULL, analysis_json TEXT NOT NULL, analyzed_at TEXT NOT NULL,
                    PRIMARY KEY(job_key,profile_id,profile_version,job_description_hash,rubric_version,model),
                    FOREIGN KEY(profile_id,profile_version) REFERENCES candidate_profiles(id,version)
                );
                CREATE TABLE IF NOT EXISTS application_material_generations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_key TEXT NOT NULL, job_url TEXT NOT NULL,
                    job_title TEXT NOT NULL DEFAULT '', company TEXT NOT NULL DEFAULT '',
                    job_description_hash TEXT NOT NULL, profile_id TEXT NOT NULL, profile_version INTEGER NOT NULL,
                    profile_fit_hash TEXT NOT NULL DEFAULT '', profile_fit_reference TEXT NOT NULL DEFAULT '',
                    rule_version TEXT NOT NULL, model TEXT NOT NULL,
                    language TEXT NOT NULL, input_fingerprint TEXT NOT NULL, cover_letter TEXT NOT NULL DEFAULT '',
                    cold_email_subject TEXT NOT NULL DEFAULT '', cold_email_body TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL CHECK(status IN ('success','failed')), error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS application_material_cache_idx
                    ON application_material_generations(job_key,input_fingerprint,status,id);
                CREATE TABLE IF NOT EXISTS job_description_quality (
                    job_key TEXT NOT NULL, job_description_hash TEXT NOT NULL, quality_rule_version TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('complete','partial','insufficient')),
                    score INTEGER NOT NULL CHECK(score BETWEEN 0 AND 100), signals_json TEXT NOT NULL,
                    missing_signals_json TEXT NOT NULL, reason TEXT NOT NULL, evaluated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(job_key,job_description_hash,quality_rule_version)
                );
                CREATE TABLE IF NOT EXISTS ai_analysis_eligibility (
                    job_key TEXT NOT NULL, job_description_hash TEXT NOT NULL,
                    profile_id TEXT NOT NULL, profile_version INTEGER NOT NULL,
                    rule_version TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('eligible','review','excluded')),
                    reason TEXT NOT NULL, signals_json TEXT NOT NULL, exclusion_signals_json TEXT NOT NULL,
                    evaluated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(job_key,job_description_hash,profile_id,profile_version,rule_version)
                );
                CREATE INDEX IF NOT EXISTS ai_eligibility_job_idx ON ai_analysis_eligibility(job_key);
                CREATE TABLE IF NOT EXISTS ai_analysis_eligibility_cache (
                    job_key TEXT NOT NULL, job_description_hash TEXT NOT NULL,
                    profile_id TEXT NOT NULL, profile_version INTEGER NOT NULL,
                    rule_version TEXT NOT NULL, cache_revision TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('eligible','review','excluded')),
                    reason TEXT NOT NULL, signals_json TEXT NOT NULL, exclusion_signals_json TEXT NOT NULL,
                    evaluated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(job_key,job_description_hash,profile_id,profile_version,rule_version,cache_revision)
                );
                CREATE INDEX IF NOT EXISTS ai_eligibility_cache_job_idx ON ai_analysis_eligibility_cache(job_key);
                CREATE INDEX IF NOT EXISTS job_quality_key_idx ON job_description_quality(job_key);
                CREATE INDEX IF NOT EXISTS profile_fit_job_idx ON profile_fit_analyses(job_key,analyzed_at);
                CREATE INDEX IF NOT EXISTS score_job_idx ON job_scores(job_key, analyzed_at);
                CREATE TABLE IF NOT EXISTS application_decisions (
                    job_key TEXT NOT NULL, input_fingerprint TEXT NOT NULL, rule_version TEXT NOT NULL,
                    profile_version_id TEXT, job_description_hash TEXT NOT NULL DEFAULT '',
                    recommendation_json TEXT NOT NULL, generated_at TEXT NOT NULL,
                    PRIMARY KEY(job_key,input_fingerprint,rule_version)
                );
                CREATE INDEX IF NOT EXISTS application_decision_job_idx ON application_decisions(job_key,generated_at);
                CREATE TABLE IF NOT EXISTS user_application_decisions (
                    job_key TEXT PRIMARY KEY, user_decision TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS user_application_decision_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_key TEXT NOT NULL,
                    timestamp TEXT NOT NULL, changes_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS user_decision_job_idx ON user_application_decision_events(job_key,id);
                PRAGMA user_version = 1;
            ''')
            material_columns={row['name'] for row in db.execute('PRAGMA table_info(application_material_generations)')}
            if 'profile_fit_reference' not in material_columns:
                db.execute("ALTER TABLE application_material_generations ADD COLUMN profile_fit_reference TEXT NOT NULL DEFAULT ''")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys = ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def get_profile(self):
        with self.connect() as db:
            active=db.execute('SELECT profile_id,version FROM active_candidate_profile LIMIT 1').fetchone()
            if not active: return {'profile_id':None,'version':0,'updated_at':'','profile':blank_profile()}
            row=db.execute('SELECT * FROM candidate_profiles WHERE id=? AND version=?',
                           (active['profile_id'],active['version'])).fetchone()
            if not row: return {'profile_id':None,'version':0,'updated_at':'','profile':blank_profile()}
            profile={**blank_profile(),**json.loads(row['preferences_json'] or '{}')}
            return {'profile_id':row['id'],'version':row['version'],'updated_at':row['updated_at'],'profile':profile}

    def save_profile(self,profile,resume_upload=None):
        value=validate_profile(profile)
        current=self.get_profile()
        resume_path=None
        if resume_upload is not None:
            if not isinstance(resume_upload,tuple) or len(resume_upload)!=3:
                raise ValueError('Arquivo de currículo inválido.')
            original_name,content_type,content=resume_upload
            if not isinstance(content,bytes) or not content or len(content)>15*1024*1024:
                raise ValueError('O currículo deve ter até 15 MB.')
            name=Path(str(original_name).replace('\\','/')).name
            extension=Path(name).suffix.lower()
            if extension not in {'.pdf','.docx'}: raise ValueError('Envie um arquivo PDF ou DOCX.')
            if extension=='.pdf' and not content.startswith(b'%PDF-'):
                raise ValueError('O arquivo não parece ser um PDF válido.')
            if extension=='.docx':
                try:
                    with zipfile.ZipFile(BytesIO(content)) as archive:
                        if 'word/document.xml' not in archive.namelist(): raise ValueError('O DOCX não contém um documento válido.')
                except zipfile.BadZipFile: raise ValueError('O arquivo não parece ser um DOCX válido.') from None
            safe_name=re.sub(r'[\\/:*?"<>|\x00-\x1f]','_',name).strip(' .')[:180] or ('curriculo'+extension)
            digest=__import__('hashlib').sha256(content).hexdigest()
            previous=(current['profile'].get('resume') or {})
            if previous.get('sha256')!=digest or previous.get('original_name')!=safe_name:
                value['resume']={'original_name':safe_name,'type':'application/pdf' if extension=='.pdf' else
                                 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                                 'uploaded_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
                                 'sha256':digest,'profile_version':None}
                resume_path=(extension,content)
        canonical=lambda item:json.dumps(item,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        if current['profile_id'] and canonical(value)==canonical(current['profile']):
            return current
        now=datetime.now(timezone.utc).isoformat(timespec='seconds')
        profile_id=current['profile_id'] or PROFILE_ID
        with self.connect() as db:
            version=db.execute('SELECT COALESCE(MAX(version),0)+1 FROM candidate_profiles WHERE id=?',(profile_id,)).fetchone()[0]
            stored_file=None
            if resume_path:
                extension,content=resume_path
                folder=self.path.parent/'resumes'/profile_id
                folder.mkdir(parents=True,exist_ok=True)
                filename=f'{uuid.uuid4().hex}{extension}'
                target=folder/filename
                temporary=folder/(filename+'.tmp')
                temporary.write_bytes(content); os.replace(temporary,target)
                stored_file=target
                value['resume']['storage_key']=f'{profile_id}/{filename}'
                value['resume']['profile_version']=version
            db.execute('INSERT INTO candidate_profiles(id,version,summary,skills_json,experience_json,preferences_json,updated_at) VALUES(?,?,?,?,?,?,?)',
                       (profile_id,version,value['professional_summary'],json.dumps(value['skills'],ensure_ascii=False),
                        json.dumps(value['work_history'],ensure_ascii=False),canonical(value),now))
            db.execute('INSERT INTO active_candidate_profile(profile_id,version) VALUES(?,?) ON CONFLICT(profile_id) DO UPDATE SET version=excluded.version',
                       (profile_id,version))
        return {'profile_id':profile_id,'version':version,'updated_at':now,'profile':value}

    def current_resume_path(self):
        profile=self.get_profile()['profile']
        resume=profile.get('resume') or {}
        key=resume.get('storage_key')
        if not isinstance(key,str): return None
        root=(self.path.parent/'resumes').resolve()
        target=(root/key).resolve()
        if root not in target.parents or not target.is_file(): return None
        return target

    def get_pre_match(self,job_key_value,profile_id,profile_version,description_digest,rule_version):
        with self.connect() as db:
            row=db.execute('''SELECT * FROM job_pre_matches WHERE job_key=? AND profile_id=? AND profile_version=?
                AND description_hash=? AND rule_version=?''',
                (job_key_value,profile_id,profile_version,description_digest,rule_version)).fetchone()
        if not row:return None
        return {'classification':row['classification'],'pre_score':row['pre_score'],
                'positive_signals':json.loads(row['positive_json']),'negative_signals':json.loads(row['negative_json']),
                'unknowns':json.loads(row['unknown_json']),'profile_id':row['profile_id'],
                'profile_version':row['profile_version'],'profile_version_id':f"{row['profile_id']}:v{row['profile_version']}",
                'description_hash':row['description_hash'],'rule_version':row['rule_version'],'created_at':row['created_at']}

    def save_pre_match(self,job_key_value,result):
        if result.get('classification') not in {'strong_candidate','possible_candidate','weak_candidate'}:
            raise ValueError('Classificação de pré-match inválida.')
        with self.connect() as db:
            db.execute('''INSERT OR IGNORE INTO job_pre_matches
                (job_key,profile_id,profile_version,description_hash,rule_version,classification,pre_score,
                 positive_json,negative_json,unknown_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                (job_key_value,result['profile_id'],result['profile_version'],result.get('description_hash',''),
                 result['rule_version'],result['classification'],result['pre_score'],
                 json.dumps(result.get('positive_signals',[]),ensure_ascii=False),
                 json.dumps(result.get('negative_signals',[]),ensure_ascii=False),
                 json.dumps(result.get('unknowns',[]),ensure_ascii=False),result['created_at']))

    def get_profile_fit(self,job_key_value,profile_id,profile_version,description_digest,rubric_version,model):
        with self.connect() as db:
            row=db.execute('''SELECT analysis_json FROM profile_fit_analyses WHERE job_key=? AND profile_id=?
                AND profile_version=? AND job_description_hash=? AND rubric_version=? AND model=?''',
                (job_key_value,profile_id,profile_version,description_digest,rubric_version,model)).fetchone()
        return json.loads(row['analysis_json']) if row else None

    def save_profile_fit(self,result):
        required=('job_key','profile_id','profile_version','job_description_hash','rubric_version','model',
                  'profile_score','overall_fit','analyzed_at')
        if any(key not in result for key in required):raise ValueError('Identificadores da análise incompletos.')
        if not isinstance(result['profile_score'],int) or isinstance(result['profile_score'],bool) or not 0<=result['profile_score']<=100:
            raise ValueError('Score de perfil inválido.')
        with self.connect() as db:
            db.execute('''INSERT OR IGNORE INTO profile_fit_analyses
                (job_key,profile_id,profile_version,job_description_hash,rubric_version,model,profile_score,overall_fit,analysis_json,analyzed_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)''',
                (result['job_key'],result['profile_id'],result['profile_version'],result['job_description_hash'],
                 result['rubric_version'],result['model'],result['profile_score'],result['overall_fit'],
                 json.dumps(result,ensure_ascii=False,separators=(',',':')),result['analyzed_at']))

    def get_application_material(self,job_key_value,input_fingerprint):
        with self.connect() as db:
            row=db.execute('''SELECT * FROM application_material_generations
                WHERE job_key=? AND input_fingerprint=? AND status='success' ORDER BY id DESC LIMIT 1''',
                (job_key_value,input_fingerprint)).fetchone()
        return self._application_material_row(row) if row else None

    def list_application_materials(self,job_key_value,limit=20):
        with self.connect() as db:
            rows=db.execute('''SELECT * FROM application_material_generations WHERE job_key=?
                ORDER BY id DESC LIMIT ?''',(job_key_value,max(1,min(int(limit),100)))).fetchall()
        return [self._application_material_row(row) for row in rows]

    @staticmethod
    def _application_material_row(row):
        return {key:row[key] for key in ('id','job_key','job_url','job_title','company','job_description_hash',
            'profile_id','profile_version','profile_fit_hash','profile_fit_reference','rule_version','model','language','input_fingerprint',
            'cover_letter','cold_email_subject','cold_email_body','status','error','created_at')}

    def save_application_material(self,result):
        required=('job_key','job_url','job_description_hash','profile_id','profile_version','rule_version',
                  'model','language','input_fingerprint','status','created_at')
        if any(key not in result for key in required):raise ValueError('Identificadores dos materiais incompletos.')
        if result['status'] not in {'success','failed'}:raise ValueError('Status dos materiais inválido.')
        with self.connect() as db:
            cursor=db.execute('''INSERT INTO application_material_generations
                (job_key,job_url,job_title,company,job_description_hash,profile_id,profile_version,profile_fit_hash,profile_fit_reference,
                 rule_version,model,language,input_fingerprint,cover_letter,cold_email_subject,cold_email_body,status,error,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (result['job_key'],result['job_url'],result.get('job_title',''),result.get('company',''),
                 result['job_description_hash'],result['profile_id'],result['profile_version'],
                 result.get('profile_fit_hash',''),result.get('profile_fit_reference',''),result['rule_version'],result['model'],result['language'],
                 result['input_fingerprint'],result.get('cover_letter',''),result.get('cold_email_subject',''),
                 result.get('cold_email_body',''),result['status'],result.get('error',''),result['created_at']))
            row=db.execute('SELECT * FROM application_material_generations WHERE id=?',(cursor.lastrowid,)).fetchone()
        return self._application_material_row(row)

    def get_job_description_quality(self,job_key_value,description_digest,rule_version):
        with self.connect() as db:
            row=db.execute('''SELECT * FROM job_description_quality WHERE job_key=? AND job_description_hash=?
                AND quality_rule_version=?''',(job_key_value,description_digest,rule_version)).fetchone()
        if not row:return None
        return {'status':row['status'],'score':row['score'],'signals':json.loads(row['signals_json']),
                'missing_signals':json.loads(row['missing_signals_json']),'reason':row['reason'],
                'job_description_hash':row['job_description_hash'],'quality_rule_version':row['quality_rule_version'],
                'evaluated_at':row['evaluated_at']}

    def save_job_description_quality(self,job_key_value,result):
        if result.get('status') not in {'complete','partial','insufficient'}:raise ValueError('Qualidade de descrição inválida.')
        if not isinstance(result.get('score'),int) or isinstance(result.get('score'),bool) or not 0<=result['score']<=100:
            raise ValueError('Score de qualidade inválido.')
        with self.connect() as db:
            db.execute('''INSERT OR IGNORE INTO job_description_quality
                (job_key,job_description_hash,quality_rule_version,status,score,signals_json,missing_signals_json,reason)
                VALUES(?,?,?,?,?,?,?,?)''',
                (job_key_value,result['job_description_hash'],result['quality_rule_version'],result['status'],result['score'],
                 json.dumps(result.get('signals',[]),ensure_ascii=False),
                 json.dumps(result.get('missing_signals',[]),ensure_ascii=False),result.get('reason','')))

    def get_ai_analysis_eligibility(self,job_key_value,description_digest,profile_id,profile_version,rule_version,cache_revision):
        with self.connect() as db:
            row=db.execute('''SELECT * FROM ai_analysis_eligibility_cache WHERE job_key=? AND job_description_hash=?
                AND profile_id=? AND profile_version=? AND rule_version=? AND cache_revision=?''',
                (job_key_value,description_digest,profile_id,profile_version,rule_version,cache_revision)).fetchone()
        if not row:return None
        return {'status':row['status'],'reason':row['reason'],'signals':json.loads(row['signals_json']),
                'exclusion_signals':json.loads(row['exclusion_signals_json']),
                'job_description_hash':row['job_description_hash'],'profile_id':row['profile_id'],
                'profile_version':row['profile_version'],'rule_version':row['rule_version'],
                'cache_revision':row['cache_revision'],'evaluated_at':row['evaluated_at']}

    def save_ai_analysis_eligibility(self,job_key_value,result):
        if result.get('status') not in {'eligible','review','excluded'}:
            raise ValueError('Elegibilidade de análise IA inválida.')
        required=('job_description_hash','profile_id','profile_version','rule_version','cache_revision')
        if any(key not in result for key in required):raise ValueError('Identificadores da elegibilidade incompletos.')
        with self.connect() as db:
            db.execute('''INSERT OR IGNORE INTO ai_analysis_eligibility_cache
                (job_key,job_description_hash,profile_id,profile_version,rule_version,cache_revision,status,reason,
                 signals_json,exclusion_signals_json) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                (job_key_value,result['job_description_hash'],result['profile_id'],result['profile_version'],
                 result['rule_version'],result['cache_revision'],result['status'],result.get('reason',''),
                 json.dumps(result.get('signals',[]),ensure_ascii=False),
                 json.dumps(result.get('exclusion_signals',[]),ensure_ascii=False)))

    def get_application_decision(self,job_key_value,input_fingerprint,rule_version):
        with self.connect() as db:
            row=db.execute('''SELECT recommendation_json FROM application_decisions
                WHERE job_key=? AND input_fingerprint=? AND rule_version=?''',
                (job_key_value,input_fingerprint,rule_version)).fetchone()
        return json.loads(row['recommendation_json']) if row else None

    def save_application_decision(self,job_key_value,input_fingerprint,result):
        if result.get('recommendation') not in {'prioritize','consider','review','low_priority'}:
            raise ValueError('Recomendação de candidatura inválida.')
        if not input_fingerprint or not result.get('rule_version') or not result.get('generated_at'):
            raise ValueError('Identificadores da recomendação incompletos.')
        with self.connect() as db:
            db.execute('''INSERT OR IGNORE INTO application_decisions
                (job_key,input_fingerprint,rule_version,profile_version_id,job_description_hash,recommendation_json,generated_at)
                VALUES(?,?,?,?,?,?,?)''',
                (job_key_value,input_fingerprint,result['rule_version'],result.get('profile_version_id'),
                 result.get('job_description_hash',''),json.dumps(result,ensure_ascii=False,separators=(',',':')),
                 result['generated_at']))

    def get_user_application_decision(self,job_key_value):
        with self.connect() as db:
            row=db.execute('SELECT * FROM user_application_decisions WHERE job_key=?',(job_key_value,)).fetchone()
            events=db.execute('SELECT timestamp,changes_json FROM user_application_decision_events WHERE job_key=? ORDER BY id',
                              (job_key_value,)).fetchall()
        if not row:
            return {'user_decision':'not_reviewed','reason':'','note':'','version':0,'created_at':'','updated_at':'','history':[]}
        return {'user_decision':row['user_decision'],'reason':row['reason'],'note':row['note'],
                'version':row['version'],'created_at':row['created_at'],'updated_at':row['updated_at'],
                'history':[{'at':event['timestamp'],'changes':json.loads(event['changes_json'])} for event in events]}

    def save_user_application_decision(self,url,payload):
        from application_decision import USER_DECISIONS, USER_DECISION_REASONS
        if not isinstance(payload,dict):raise ValueError('Decisão manual inválida.')
        key=job_key(url)
        decision=payload.get('user_decision')
        reason=payload.get('reason','')
        note=payload.get('note','')
        expected=payload.get('version',0)
        if decision not in USER_DECISIONS:raise ValueError('Estado da decisão manual inválido.')
        if not isinstance(reason,str) or (reason and reason not in USER_DECISION_REASONS.get(decision,set())):
            raise ValueError('Motivo incompatível com a decisão manual.')
        if not isinstance(note,str) or len(note)>1000:raise ValueError('A nota pode ter até 1.000 caracteres.')
        if not isinstance(expected,int) or isinstance(expected,bool) or expected<0:raise ValueError('Versão da decisão inválida.')
        reason=reason.strip();note=note.strip();now=datetime.now(timezone.utc).isoformat(timespec='seconds')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM user_application_decisions WHERE job_key=?',(key,)).fetchone()
            version=old['version'] if old else 0
            if expected!=version:raise ConflictError('Esta decisão foi alterada em outra janela. Atualize os dados antes de salvar.')
            previous={'user_decision':old['user_decision'],'reason':old['reason'],'note':old['note']} if old else {
                'user_decision':'not_reviewed','reason':'','note':''}
            current={'user_decision':decision,'reason':reason,'note':note}
            if current!=previous:
                created=old['created_at'] if old else now
                db.execute('''INSERT INTO user_application_decisions(job_key,user_decision,reason,note,version,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?) ON CONFLICT(job_key) DO UPDATE SET user_decision=excluded.user_decision,
                    reason=excluded.reason,note=excluded.note,version=excluded.version,updated_at=excluded.updated_at''',
                    (key,decision,reason,note,version+1,created,now))
                db.execute('INSERT INTO user_application_decision_events(job_key,timestamp,changes_json) VALUES(?,?,?)',
                           (key,now,json.dumps({'from':previous,'to':current},ensure_ascii=False)))
        return self.get_user_application_decision(key)

    def snapshot(self):
        with self.connect() as db:
            events = {}
            for event in db.execute('SELECT * FROM application_events ORDER BY id DESC'):
                events.setdefault(event['job_key'], []).append({'at': event['timestamp'], 'changes': json.loads(event['changes'])})
            applications = {}
            for row in db.execute('SELECT * FROM applications'):
                applications[row['job_key']] = {'url': row['url'], 'title': row['title'],
                    'company': row['company'], 'domain': row['domain'], 'source': row['source'],
                    **blank_application(), **json.loads(row['payload']), 'tracked': True,
                    'version': row['version'], 'created_at': row['created_at'], 'updated_at': row['updated_at'],
                    'history': events.get(row['job_key'], [])}
            scores = {}
            for row in db.execute('SELECT * FROM job_scores ORDER BY analyzed_at, id'):
                scores[row['job_key']] = {**unscored(), **{k: row[k] for k in ('status','total','skills','experience','seniority','location','language','explanation','profile_id','profile_version','model','rubric_version','analyzed_at')},
                    'strengths': json.loads(row['strengths_json']), 'gaps': json.loads(row['gaps_json']), 'evidence': json.loads(row['evidence_json'])}
        return applications, scores

    def save(self, body):
        if not isinstance(body, dict):
            raise ValueError('Dados inválidos.')
        key = job_key(body.get('url'))
        payload = body.get('application')
        if not isinstance(payload, dict):
            raise ValueError('Informe os dados da candidatura.')
        if payload.get('status') not in STATUSES:
            raise ValueError('Status inválido.')
        if payload.get('priority', 'normal') not in ('low', 'normal', 'high'):
            raise ValueError('Prioridade inválida.')
        clean = {'status': payload['status'], 'priority': payload.get('priority', 'normal')}
        if payload.get('rejection_stage', 'unknown') not in ('unknown', 'before_interview', 'after_interview', 'after_offer'):
            raise ValueError('Etapa da rejeição inválida.')
        clean['rejection_stage'] = payload.get('rejection_stage', 'unknown')
        for field in DATES:
            value = payload.get(field, '')
            if not isinstance(value, str):
                raise ValueError('Data inválida.')
            if value:
                try:
                    if date.fromisoformat(value).isoformat() != value:
                        raise ValueError()
                except ValueError:
                    raise ValueError(f'Data inválida: {field}.') from None
            clean[field] = value
        for field, limit in TEXT.items():
            value = payload.get(field, '')
            if not isinstance(value, str) or len(value) > limit:
                raise ValueError(f'Conteúdo inválido ou muito longo: {field}.')
            clean[field] = value.strip()
        snapshot = {}
        for field, limit in [('title',500), ('company',500), ('domain',500)]:
            value = body.get(field, '')
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError(f'Informe {field} válido.')
            snapshot[field] = value.strip()
        expected = body.get('version', 0)
        if not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
            raise ValueError('Versão inválida.')
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM applications WHERE job_key=?', (key,)).fetchone()
            version = old['version'] if old else 0
            if expected != version:
                raise ConflictError('Esta candidatura foi alterada em outra janela. Atualize os dados antes de salvar.')
            previous = json.loads(old['payload']) if old else blank_application()
            changes = {k: {'from': previous.get(k, ''), 'to': v} for k, v in clean.items() if previous.get(k) != v}
            for field in snapshot:
                if old and old[field] != snapshot[field]:
                    changes[field] = {'from': old[field], 'to': snapshot[field]}
            created = old['created_at'] if old else now
            source = old['source'] if old else ('manual' if body.get('manual') else 'crawler')
            if changes or not old:
                db.execute('''INSERT INTO applications VALUES(?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(job_key) DO UPDATE SET url=excluded.url,title=excluded.title,
                    company=excluded.company,domain=excluded.domain,payload=excluded.payload,
                    version=excluded.version,updated_at=excluded.updated_at''',
                    (key, body['url'].strip(), snapshot['title'], snapshot['company'], snapshot['domain'], source,
                     json.dumps(clean, ensure_ascii=False), version+1, created, now))
                db.execute('INSERT INTO application_events(job_key,timestamp,changes) VALUES(?,?,?)',
                           (key, now, json.dumps(changes or {'created': {'from': '', 'to': 'Cadastro criado'}}, ensure_ascii=False)))
        return key

    def delete(self, url, version):
        key = job_key(url)
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ValueError('Versão inválida.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT version FROM applications WHERE job_key=?', (key,)).fetchone()
            if not old:
                raise ValueError('Acompanhamento não encontrado.')
            if old['version'] != version:
                raise ConflictError('A candidatura foi alterada em outra janela. Atualize antes de excluir.')
            db.execute('DELETE FROM applications WHERE job_key=?', (key,))


def enrich(data, store):
    apps, scores = store.snapshot()
    observed = set()
    for row in data['records']:
        for job in row['jobs']:
            try:
                key = job_key(job['url'])
            except ValueError:
                key = job['url']
            observed.add(key)
            job.update(key=key, application=apps.get(key, blank_application()), score=scores.get(key, unscored()))
    extras = {}
    for key, app in apps.items():
        if key in observed:
            continue
        domain = app['domain']
        row = extras.setdefault(domain, {'id': 'tracking#'+domain, 'run_id': 'tracking', 'date': '',
            'domain': domain, 'name': app['company'], 'alive': False, 'status': 'NOT_CHECKED',
            'canonical': '', 'type': 'Cadastro pessoal', 'description': '', 'emails': [], 'careers': [],
            'pages': 0, 'evidence': [], 'error': '', 'jobs': [], 'ai': None, 'ai_available': None,
            'model': '', 'version': '', 'input_file': ''})
        row['date'] = max(row['date'], app['created_at'])
        row['jobs'].append({'key': key, 'url': app['url'], 'title': app['title'], 'application': app, 'score': scores.get(key, unscored())})
    data['records'].extend(extras.values())
    if extras:
        data['runs'].append({'id': 'tracking', 'date': '', 'finished': '', 'ai': None, 'model': '', 'version': '',
            'ai_available': None,
            'completed': None, 'resumed': False,
            'input': 'Cadastros pessoais / vagas fora das coletas atuais', 'count': len(extras), 'requested': None,
            'alive': 0, 'jobs': sum(len(r['jobs']) for r in extras.values()), 'emails': 0, 'errors': 0, 'pages': 0})
    data['application_statuses'] = STATUSES
    data['tracking_schema_version'] = 1
    return data
