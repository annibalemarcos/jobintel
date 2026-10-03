"""Deterministic local quality checks for already-collected job descriptions."""
from __future__ import annotations

import hashlib
import html
import re
import unicodedata

from applications import job_key

QUALITY_RULE_VERSION='job_description_quality_v1'


def _normalize(value):
    value=unicodedata.normalize('NFD',str(value or '')).encode('ascii','ignore').decode('ascii').lower()
    return re.sub(r'[^a-z0-9]+',' ',value).strip()


def _contains(text, phrases):
    return any(re.search(rf'(?<![a-z0-9]){re.escape(_normalize(phrase))}(?![a-z0-9])',text)
               for phrase in phrases if _normalize(phrase))


RESPONSIBILITY_CUES=(
    'responsibilities','what you will do','what you will be doing','what you will own',"what you'll do",'you will',
    'the role','in this role','day to day','duties','key responsibilities','how you will contribute',
    'responsabilidades','atribuicoes','principais atividades','atividades do cargo','o que voce fara',
    'nesse cargo','nesta funcao','sera responsavel por','responsavel por','voce ira','voce sera responsavel')
REQUIREMENT_CUES=(
    'requirements','qualifications','what we are looking for','what we look for','what you bring',
    'who you are','must have','minimum qualifications','required experience','skills and experience',
    'you have','you will need','candidates must','we are looking for',
    'requisitos','qualificacoes','qualificacao','o que buscamos','o que esperamos','requisitos obrigatorios',
    'experiencia necessaria','experiencia exigida','competencias necessarias','diferenciais')
ACTION_CUES=(
    'manage','lead','develop','build','monitor','analyze','analyse','oversee','coordinate','own','support',
    'implement','ensure','maintain','provide','drive','design','resolve','investigate','deliver','create',
    'gerenciar','liderar','desenvolver','construir','monitorar','analisar','supervisionar','coordenar',
    'apoiar','implementar','garantir','manter','fornecer','conduzir','projetar','resolver','investigar',
    'entregar','criar')
EXPERIENCE_CUES=('years of experience','year of experience','years experience','anos de experiencia',
                 'experiencia profissional','minimum of','at least','minimo de','mais de')
SKILL_CUES=('skills','tools','technology','technologies','proficiency','proficient','knowledge of',
            'expertise in','competencias','habilidades','ferramentas','tecnologias','conhecimento em',
            'dominio de','familiaridade com')
SENIORITY_CUES=('senior','junior','mid level','entry level','lead','manager','director','principal','staff',
                'iii','iv','sr','jr','pleno','sênior','júnior','gerente','diretor','coordenador','especialista')
MARKETING_CUES=('about us','who we are','our mission','our vision','our products','our product','our platform',
                'our company','we are on a mission','trusted by','empower people','empowering people',
                'the future of finance','transform the way','our ecosystem','nossa missao','nossa visao',
                'sobre a empresa','nossos produtos','nossa plataforma','nosso ecossistema')


def description_hash(job):
    return hashlib.sha256(str(job.get('description') or '').encode('utf-8')).hexdigest()


def evaluate_job_description_quality(job):
    """Classify only the information quality of a job, never its profile fit."""
    if not isinstance(job,dict):job={}
    raw=html.unescape(str(job.get('description') or ''))
    text=re.sub(r'<[^>]*>',' ',raw)
    normalized=_normalize(text)
    words=normalized.split()
    action_count=sum(1 for cue in ACTION_CUES if _contains(normalized,(cue,)))
    list_lines=sum(bool(re.match(r'^\s*(?:[-*•▪◦]|\d+[.)])\s+',line)) for line in text.splitlines())
    marketing_count=sum(_contains(normalized,(cue,)) for cue in MARKETING_CUES)
    responsibilities=_contains(normalized,RESPONSIBILITY_CUES) or (action_count>=2 and (list_lines>=2 or not marketing_count))
    requirements=_contains(normalized,REQUIREMENT_CUES)
    experience=_contains(normalized,EXPERIENCE_CUES)
    skills=_contains(normalized,SKILL_CUES)
    seniority=_contains(normalized,SENIORITY_CUES)
    salary=bool(job.get('salary_min') or job.get('salary_max') or _contains(normalized,('salary','compensation','pay range','remuneration','salario','remuneracao','faixa salarial')))
    location=bool(job.get('location') or _contains(normalized,('location','based in','localizacao','localidade','located in')))
    work_model=bool(_contains(normalized,('remote','hybrid','on site','onsite','work from anywhere','remoto','hibrido','presencial')))
    contract=bool(job.get('employment_type') or job.get('contract_type') or _contains(normalized,('full time','part time','contractor','contract','freelance','clt','pj','tempo integral','meio periodo')))
    signals=[]
    if responsibilities:signals.append('Responsabilidades ou deveres descritos')
    if requirements:signals.append('Requisitos ou qualificações identificados')
    if experience:signals.append('Expectativa de experiência identificada')
    if skills:signals.append('Competências ou ferramentas mencionadas')
    if seniority:signals.append('Sinal de senioridade presente')
    if action_count:signals.append(f'Verbos de ação encontrados ({action_count})')
    if list_lines:signals.append(f'Atividades em lista ({list_lines})')
    if salary:signals.append('Informação de remuneração disponível')
    if location:signals.append('Informação de localização disponível')
    if work_model:signals.append('Modelo de trabalho mencionado')
    if contract:signals.append('Tipo de contrato mencionado')
    if marketing_count:signals.append('Conteúdo institucional/marketing presente')

    missing=[]
    if not responsibilities:missing.append('Responsabilidades do cargo')
    if not requirements:missing.append('Requisitos ou qualificações')
    if not experience:missing.append('Experiência exigida')
    if not skills:missing.append('Competências ou ferramentas')
    if not seniority:missing.append('Senioridade')
    if not salary:missing.append('Remuneração (opcional)')
    if not location:missing.append('Localização (opcional)')
    if not work_model:missing.append('Modelo de trabalho (opcional)')
    if not contract:missing.append('Tipo de contrato (opcional)')

    score=min(100,round(min(len(words),800)/800*18)+
              (25 if responsibilities else 0)+(25 if requirements else 0)+
              (8 if experience else 0)+(7 if skills else 0)+(5 if seniority else 0)+
              min(8,action_count*2)+min(7,list_lines*2)+
              (3 if salary else 0)+(3 if location else 0)+(2 if work_model else 0)+(2 if contract else 0))
    # Long company/product copy alone never establishes that job-specific information exists.
    if not responsibilities and not requirements:
        status='insufficient'
        score=min(score,25)
        reason='A descrição não apresenta responsabilidades nem requisitos identificáveis da vaga.'
    elif responsibilities and requirements and (len(words)>=50 or experience or skills or list_lines>=2):
        status='complete'
        reason='A descrição apresenta responsabilidades e requisitos com contexto profissional suficiente.'
    else:
        status='partial'
        reason='Há conteúdo relacionado ao cargo, mas faltam detalhes para uma análise profissional completa.'
    return {'status':status,'score':max(0,min(100,int(score))),'signals':signals,
            'missing_signals':missing,'reason':reason,
            'job_description_hash':description_hash(job),'quality_rule_version':QUALITY_RULE_VERSION}


def attach_job_description_quality(data,store):
    for record in data.get('records',[]):
        for job in record.get('jobs',[]):
            try:key=job_key(job.get('url'))
            except (TypeError,ValueError):continue
            result,_=get_or_evaluate_job_description_quality(key,job,store)
            job['description_quality']=result
    return data


def get_or_evaluate_job_description_quality(key,job,store):
    digest=description_hash(job)
    result=store.get_job_description_quality(key,digest,QUALITY_RULE_VERSION)
    if (result is not None and result.get('job_description_hash')==digest
            and result.get('quality_rule_version')==QUALITY_RULE_VERSION):
        return result,True
    value=evaluate_job_description_quality(job)
    store.save_job_description_quality(key,value)
    # A quality result is valid only for the exact description/rule pair. Do not
    # trust a store implementation that returns a same-job result for another hash.
    persisted=store.get_job_description_quality(key,digest,QUALITY_RULE_VERSION)
    if (persisted is not None and persisted.get('job_description_hash')==digest
            and persisted.get('quality_rule_version')==QUALITY_RULE_VERSION):
        return persisted,False
    return value,False
