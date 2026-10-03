"""Explicitly requested, cached application-material generation."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

RULE_VERSION = 'application_materials_v1'
LANGUAGES = {'auto', 'en', 'pt'}

SYSTEM_INSTRUCTIONS = """Create a truthful, specific application package with a cover letter, cold-email subject, and cold-email body. Return only the required structured fields. Use only facts explicitly supported by the supplied profile and job. Never invent experience, responsibilities, achievements, metrics, tools, technologies, credentials, education, projects, languages, location, authorization, relocation, or prior company relationships. Do not claim missing job requirements. Do not turn transferable experience into direct experience. Do not claim professional engineering experience unless the profile's professional history supports it. Distinguish practical familiarity in skills/tools from formal professional experience; practical familiarity may only be described as hands-on familiarity through projects when that is supported. Resume metadata is not resume content: do not claim to have read or analyzed the resume. Profile Fit is interpretive context, not a source of new facts; every factual claim must be independently supported by the supplied profile/job. Do not expose internal scores, recommendations, or Profile Fit terminology. Do not assume recipient names. Never invent the candidate's name, contact details, or signature; use only supplied identity details, or omit a personal name from the sign-off. Use a professional, natural, concise tone, no empty corporate clichés or flattery. Cover letter should be about 300-450 words and refer accurately to the company and role, connecting 2-4 relevant supported facts. Cold email should be about 80-150 words, distinct from the letter, with a short clear subject, neutral greeting when no recipient name exists, a simple call to action, and a signature supported by profile data. Mention salary only when useful and supported. Follow the requested language."""


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def description_hash(job):
    return hashlib.sha256(str(job.get('description') or '').encode('utf-8')).hexdigest()


def resolve_language(job, language='auto'):
    if language not in LANGUAGES:
        raise ValueError('Idioma inválido.')
    if language != 'auto':
        return language
    text=' '.join(str(job.get(key) or '') for key in ('title','description')).lower()
    tokens=re.findall(r"[a-záàâãéêíóôõúç]+",text)
    pt={'para','com','uma','por','que','você','responsabilidades','requisitos','experiência','empresa','trabalho','sobre','nossa','seu'}
    en={'the','and','for','with','you','your','responsibilities','requirements','experience','company','role','about','our','this'}
    pt_count=sum(word in pt for word in tokens);en_count=sum(word in en for word in tokens)
    return 'pt' if pt_count>en_count else 'en'


def _valid_current_fit(job, active, digest):
    fit=job.get('profile_analysis')
    if not isinstance(fit,dict):return None
    if fit.get('profile_id')!=active['profile_id']:return None
    if fit.get('profile_version')!=active['version']:return None
    if fit.get('profile_version_id')!=f"{active['profile_id']}:v{active['version']}":return None
    if fit.get('job_description_hash')!=digest:return None
    if fit.get('rubric_version')!='profile_fit_v1':return None
    return fit


def build_material_plan(job, active, model, language='auto'):
    if not isinstance(job,dict) or not job.get('url'):raise ValueError('Vaga atual inválida.')
    if not active.get('profile_id') or not isinstance(active.get('profile'),dict):
        raise ValueError('Salve um Perfil antes de gerar materiais.')
    profile=active['profile'];digest=description_hash(job)
    chosen=resolve_language(job,language)
    profile_fields=('desired_roles','interests','seniority','employer_types','excluded_roles','related_roles_open',
        'current_location','work_modes','work_mode_restrictions','regions','relocation','currency','salary_min',
        'salary_desired','salary_period','salary_flexibility','contract_types','professional_summary',
        'experience_years','work_history','skills','technologies','domain_knowledge','languages','education',
        'certifications','analysis_notes')
    safe_profile={key:profile.get(key) for key in profile_fields}
    resume=profile.get('resume')
    if isinstance(resume,dict):
        safe_profile['resume_metadata']={key:resume.get(key) for key in ('original_name','type','uploaded_at') if resume.get(key)}
    job_fields=('title','company','location','date_posted','description','employment_type','contract_type',
        'salary_min','salary_max','salary_period','currency')
    safe_job={key:job.get(key) for key in job_fields if job.get(key) not in (None,'',[])}
    fit=_valid_current_fit(job,active,digest)
    fit_context=fit if fit is not None else None
    fit_reference={key:fit.get(key) for key in ('rubric_version','model','analyzed_at','profile_id','profile_version')
        if fit is not None and fit.get(key) is not None}
    context={'profile':safe_profile,'job':safe_job,'profile_fit':fit_context,
             'resume_notice':'Only metadata is available; file contents have not been read.' if safe_profile.get('resume_metadata') else None,
             'language':chosen}
    fit_digest=hashlib.sha256(_canonical(fit_context).encode('utf-8')).hexdigest() if fit_context is not None else ''
    fingerprint_payload={'job_key':job.get('key') or job['url'],'description_hash':digest,
        'profile_id':active['profile_id'],'profile_version':active['version'],'context':context,
        'profile_fit_hash':fit_digest,'rule_version':RULE_VERSION,'model':model,'language':chosen}
    fingerprint=hashlib.sha256(_canonical(fingerprint_payload).encode('utf-8')).hexdigest()
    return {'job_key':job.get('key') or job['url'],'job_url':job['url'],'job_title':str(job.get('title') or ''),
        'company':str(job.get('company') or ''),'job_description_hash':digest,'profile_id':active['profile_id'],
        'profile_version':active['version'],'profile_version_id':f"{active['profile_id']}:v{active['version']}",
        'profile_fit':fit_context,'profile_fit_available':fit_context is not None,'profile_fit_hash':fit_digest,
        'profile_fit_reference':_canonical(fit_reference) if fit_context is not None else '',
        'rule_version':RULE_VERSION,'model':model,'language':chosen,'selected_language':language,
        'quality':(job.get('description_quality') or {}).get('status','unknown'),'context':context,
        'input_fingerprint':fingerprint}


def preview_materials(job, active, store, model, language='auto', regenerate=False):
    plan=build_material_plan(job,active,model,language)
    exact=store.get_application_material(plan['job_key'],plan['input_fingerprint'])
    history=store.list_application_materials(plan['job_key'])
    cached=exact if exact and not regenerate else None
    if cached:
        new_calls=0;blocked=False
    else:
        new_calls=1;blocked=plan['quality']=='insufficient'
    plan.update({'cache_available':bool(exact),'cached_generation_id':exact['id'] if exact else None,
        'cached_material':cached,'history':history,'regenerate':bool(regenerate),'new_calls':0 if blocked else new_calls,
        'blocked':blocked,'warning':'A descrição é parcial; os materiais podem ser menos específicos.'
            if plan['quality']=='partial' else ('Descrição insuficiente para gerar materiais específicos com segurança.' if blocked else ''),
        'confirmation_token':plan['input_fingerprint']})
    return plan


def response_schema():
    fields={'cover_letter':{'type':'string'},'cold_email_subject':{'type':'string'},'cold_email_body':{'type':'string'}}
    return {'type':'object','additionalProperties':False,'properties':fields,'required':list(fields)}


def validate_materials(value):
    if not isinstance(value,dict):raise ValueError('A resposta dos materiais não é um objeto estruturado.')
    expected=('cover_letter','cold_email_subject','cold_email_body')
    if any(not isinstance(value.get(key),str) or not value[key].strip() for key in expected):
        raise ValueError('A resposta não contém os três materiais preenchidos.')
    result={key:value[key].strip() for key in expected}
    if not 250<=len(result['cover_letter'])<=12000:raise ValueError('Cover Letter fora do limite de tamanho permitido.')
    if not 5<=len(result['cold_email_subject'])<=180:raise ValueError('Assunto do e-mail fora do limite de tamanho permitido.')
    if not 80<=len(result['cold_email_body'])<=5000:raise ValueError('Corpo do e-mail fora do limite de tamanho permitido.')
    if len(result['cover_letter'])<=len(result['cold_email_body']):raise ValueError('A Cover Letter deve ser claramente mais longa que o Cold Email.')
    placeholder=re.compile(r'\[(?:company|hiring manager|insert name|first name|job title)[^\]]*\]|\b(?:TODO|XXX|TBD)\b',re.I)
    if any(placeholder.search(text) for text in result.values()):raise ValueError('A resposta contém um placeholder que precisa ser removido.')
    return result


def generate_with_client(client, plan):
    response=client.responses.create(model=plan['model'],input=[
        {'role':'system','content':SYSTEM_INSTRUCTIONS},
        {'role':'user','content':_canonical(plan['context'])}],
        text={'format':{'type':'json_schema','name':'application_materials_v1','strict':True,'schema':response_schema()}},
        max_output_tokens=3000)
    text=getattr(response,'output_text','')
    if not isinstance(text,str) or not text.strip():raise ValueError('A API não retornou materiais estruturados.')
    try:value=json.loads(text)
    except json.JSONDecodeError:raise ValueError('A API retornou JSON inválido.') from None
    return validate_materials(value)


def create_openai_client():
    from profile_fit import create_openai_client as create
    return create()


def generate_and_persist(plan, store, client_factory=None):
    if plan['quality']=='insufficient':raise ValueError('Descrição insuficiente para gerar materiais específicos com segurança.')
    now=datetime.now(timezone.utc).isoformat(timespec='seconds')
    base={key:plan[key] for key in ('job_key','job_url','job_title','company','job_description_hash','profile_id',
        'profile_version','profile_fit_hash','profile_fit_reference','rule_version','model','language','input_fingerprint')}
    try:
        client=(client_factory or create_openai_client)()
        package=generate_with_client(client,plan)
        return store.save_application_material({**base,**package,'status':'success','error':'','created_at':now})
    except Exception as error:
        store.save_application_material({**base,'status':'failed','error':str(error)[:1000],'created_at':now})
        raise
