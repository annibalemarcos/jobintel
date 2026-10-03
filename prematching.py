"""Deterministic local triage of collected jobs against the active candidate profile."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone

from applications import job_key

RULE_VERSION = 'prematch-v1'
STOP_WORDS = {'a','an','and','the','de','da','do','das','dos','for','in','of','or','to','e','em'}
GENERIC_ROLE_WORDS = {'engineer','engineering','developer','development','manager','management',
                      'analyst','specialist','associate','director','officer','lead','senior','junior',
                      'staff','principal','head','of','the','and','de','da','do','e'}
CUSTOMER_ANCHORS = {'customer','client','cx','support','success','service'}
CUSTOMER_FUNCTIONS = {'operations','operation','ops','experience','enablement','care','relations'}


def normalize(value):
    text=unicodedata.normalize('NFD',str(value or '')).encode('ascii','ignore').decode('ascii').lower()
    return re.sub(r'[^a-z0-9]+',' ',text).strip()


def tokens(value):
    return {word for word in normalize(value).split() if word not in STOP_WORDS}


def has_phrase(text, phrase):
    source=normalize(text); wanted=normalize(phrase)
    return bool(wanted and re.search(rf'(?<![a-z0-9]){re.escape(wanted)}(?![a-z0-9])',source))


def customer_operations(text):
    words=tokens(text)
    return bool(words & CUSTOMER_ANCHORS) and bool(words & CUSTOMER_FUNCTIONS)


def clear_role_match(role, title):
    role_words=tokens(role)-GENERIC_ROLE_WORDS
    title_words=tokens(title)
    if has_phrase(title,role): return 'direct'
    if role_words and role_words.issubset(title_words): return 'related'
    if customer_operations(role) and customer_operations(title): return 'related'
    return ''


def classify_pre_match(profile, job):
    title=str(job.get('title') or '')
    description=str(job.get('description') or '')
    body=f'{title}\n{description}'
    positives=[]; negatives=[]; unknown=[]; score=25; hard_conflict=False

    excluded=next((role for role in profile.get('excluded_roles',[]) if clear_role_match(role,title)),None)
    if excluded:
        negatives.append(f'Cargo indesejado corresponde ao título: {excluded}')
        score-=48

    desired=profile.get('desired_roles',[])
    role_match=next(((role,clear_role_match(role,title)) for role in desired if clear_role_match(role,title)),None)
    if role_match:
        role,kind=role_match
        positives.append(f'Cargo relacionado ao desejado ({kind}): {role}')
        score+=40 if kind=='direct' else 32
    elif desired:
        if profile.get('related_roles_open',True):
            negatives.append('Título sem proximidade funcional clara com os cargos desejados')
            score-=10
        else:
            unknown.append('Proximidade de cargo não considerada: opção desativada')
    else:
        unknown.append('Cargos desejados não informados')

    found_interests=[item for item in profile.get('interests',[]) if has_phrase(body,item)]
    if found_interests:
        positives.append('Área de interesse encontrada: '+', '.join(found_interests[:3])); score+=min(15,7*len(found_interests))
    elif profile.get('interests'):
        unknown.append('Áreas de interesse não identificadas na vaga')
    else: unknown.append('Áreas de interesse não informadas')

    seniority=str(profile.get('seniority') or '').strip()
    if seniority:
        seniority_terms={'intern':['intern','internship'], 'estagio':['intern','internship','junior'],
                         'junior':['junior','jr'], 'mid':['mid level','mid level','intermediate'],
                         'pleno':['mid level','intermediate','pleno'], 'senior':['senior','sr','staff','principal'],
                         'senioridade':['senior','sr','staff','principal'], 'lead':['lead','principal','head'],
                         'principal':['principal','staff','lead'], 'executive':['chief','vp','executive']}
        expected=seniority_terms.get(normalize(seniority),[normalize(seniority)])
        actual=next((term for term in ('internship','intern','junior','mid level','intermediate','pleno','senior','staff','principal','lead','head','executive','chief','vp') if has_phrase(title,term)),None)
        if actual and any(has_phrase(actual,term) or has_phrase(term,actual) for term in expected):
            positives.append(f'Senioridade compatível: {seniority}'); score+=6
        elif actual:
            negatives.append(f'Senioridade anunciada difere da desejada: {actual}'); score-=5
        else: unknown.append('Senioridade da vaga não identificada')
    else: unknown.append('Senioridade desejada não informada')

    work_mode=next((mode for mode,patterns in {
        'remote':('remote','fully remote','work from anywhere','distributed'),
        'hybrid':('hybrid','partly remote'), 'onsite':('on site','onsite','in office','office based')
    }.items() if any(has_phrase(body,pattern) for pattern in patterns)),None)
    if work_mode:
        preference=(profile.get('work_modes') or {}).get(work_mode)
        if work_mode in profile.get('work_mode_restrictions',[]):
            negatives.append(f'Restrição obrigatória incompatível: {work_mode}')
            score-=65; hard_conflict=True
        elif preference=='preferred':
            positives.append(f'Modelo de trabalho preferido: {work_mode}'); score+=8
        elif preference=='acceptable':
            positives.append(f'Modelo de trabalho aceitável: {work_mode}'); score+=3
        elif preference=='unwanted':
            negatives.append(f'Modelo de trabalho não desejado: {work_mode}'); score-=14
    else: unknown.append('Modelo de trabalho não informado ou pouco claro')

    location=str(job.get('location') or '')
    regions=profile.get('regions') or []
    current_location=str(profile.get('current_location') or '')
    if location:
        matched=[region for region in [*regions,current_location] if region and (has_phrase(location,region) or has_phrase(region,location))]
        if matched:
            positives.append('Localização coincide com a preferência: '+matched[0]); score+=6
        else: unknown.append('Compatibilidade geográfica não confirmada')
    else: unknown.append('Localização da vaga não informada')

    contract_text=normalize(str(job.get('employment_type') or job.get('contract_type') or ''))
    if contract_text:
        accepted=[kind for kind in profile.get('contract_types',[]) if has_phrase(contract_text,kind) or has_phrase(kind,contract_text)]
        if accepted:
            positives.append('Tipo de contratação aceitável: '+accepted[0]); score+=5
        else: unknown.append('Tipo de contratação anunciado não consta entre os tipos selecionados; sem exclusão')
    else: unknown.append('Tipo de contratação não informado')

    salary=job.get('salary_min')
    desired_min=profile.get('salary_min')
    salary_period=str(job.get('salary_period') or '')
    profile_period=str(profile.get('salary_period') or '')
    if salary not in (None,'') and desired_min not in (None,'') and (not salary_period or salary_period==profile_period):
        try:
            if float(salary)>=float(desired_min): positives.append('Remuneração anunciada atende ao mínimo informado'); score+=6
            else: negatives.append('Remuneração anunciada abaixo do mínimo informado'); score-=12
        except (TypeError,ValueError): unknown.append('Remuneração não pôde ser comparada')
    else: unknown.append('Remuneração ausente ou não comparável')

    sectors=[*profile.get('employer_types',[]),*profile.get('domain_knowledge',[])]
    matched_sectors=[item for item in sectors if has_phrase(body,item)]
    if matched_sectors:
        positives.append('Setor/domínio relacionado: '+', '.join(matched_sectors[:3])); score+=min(12,5*len(matched_sectors))
    elif sectors: unknown.append('Setor/domínio não identificado na vaga')

    skills=[*profile.get('skills',[]),*profile.get('technologies',[])]
    matched_skills=[item for item in skills if has_phrase(body,item)]
    if matched_skills:
        positives.append('Competências/tecnologias citadas: '+', '.join(matched_skills[:5])); score+=min(18,4*len(matched_skills))
    elif skills: unknown.append('Competências do perfil não identificadas na vaga')

    score=max(0,min(100,score))
    classification='weak_candidate' if hard_conflict else 'strong_candidate' if score>=70 else 'possible_candidate' if score>=40 else 'weak_candidate'
    return {'classification':classification,'pre_score':score,'positive_signals':positives,
            'negative_signals':negatives,'unknowns':list(dict.fromkeys(unknown))}


def description_hash(job):
    existing=job.get('description_hash')
    if isinstance(existing,str) and existing: return existing
    description=str(job.get('description') or '')
    return hashlib.sha256(description.encode('utf-8')).hexdigest() if description else ''


def add_prematch_to_data(data, store):
    active=store.get_profile()
    if not active['profile_id']: return data
    for row in data.get('records',[]):
        for job in row.get('jobs',[]):
            try: key=job_key(job['url'])
            except (KeyError,ValueError): continue
            digest=description_hash(job)
            result=store.get_pre_match(key,active['profile_id'],active['version'],digest,RULE_VERSION)
            if result: job['pre_match']=result
    return data


def run_pre_match(data, store, urls):
    active=store.get_profile()
    if not active['profile_id']: raise ValueError('Salve um Perfil antes de executar o pré-match.')
    try:
        wanted={job_key(url) for url in urls if isinstance(url,str)}
    except ValueError: raise ValueError('Uma ou mais URLs de vaga são inválidas.') from None
    latest={}
    for row in data.get('records',[]):
        for job in row.get('jobs',[]):
            try: key=job_key(job['url'])
            except (KeyError,ValueError): continue
            if key not in wanted: continue
            existing=latest.get(key)
            if not existing or row.get('date','')>existing[0].get('date',''):
                latest[key]=(row,job)
    counts={'strong_candidate':0,'possible_candidate':0,'weak_candidate':0}; reused=0
    for key,(row,job) in latest.items():
        digest=description_hash(job)
        result=store.get_pre_match(key,active['profile_id'],active['version'],digest,RULE_VERSION)
        if result: reused+=1
        else:
            result=classify_pre_match(active['profile'],job)
            result.update(profile_id=active['profile_id'],profile_version=active['version'],
                          profile_version_id=f"{active['profile_id']}:v{active['version']}",
                          description_hash=digest,rule_version=RULE_VERSION,
                          created_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))
            store.save_pre_match(key,result)
        counts[result['classification']]+=1
    total=sum(counts.values())
    return {'total':total,'strong':counts['strong_candidate'],'possible':counts['possible_candidate'],
            'weak':counts['weak_candidate'],'reused':reused,
            'message':f"Pré-match: {total} | Fortes: {counts['strong_candidate']} | Possíveis: {counts['possible_candidate']} | Fracos: {counts['weak_candidate']} | Reutilizados: {reused}"}
