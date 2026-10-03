"""Conservative, deterministic gate for optional profile-fit AI analysis."""
from __future__ import annotations

import re

from applications import job_key
from job_description_quality import description_hash

RULE_VERSION = 'ai_analysis_eligibility_v1'
# Internal cache generation, independent of the stable public rule identifier.
# The first generation is retained in the legacy table as history; corrected
# semantics use this fresh cache without changing the rule version.
CACHE_REVISION = 'practical-specialty-calibration-1'


def _norm(value):
    import unicodedata
    text=unicodedata.normalize('NFD',str(value or '')).encode('ascii','ignore').decode('ascii').lower()
    return re.sub(r'[^a-z0-9]+',' ',text).strip()


def _has(text, phrase):
    source=_norm(text);wanted=_norm(phrase)
    return bool(wanted and re.search(rf'(?<![a-z0-9]){re.escape(wanted)}(?![a-z0-9])',source))


RELATED_CUES=(
    'customer operations','customer support operations','support operations','customer experience','cx operations',
    'customer success operations','product operations','ai operations','cx automation','implementation',
    'solutions consulting','solutions consultant','knowledge operations','knowledge management','trust and safety',
    'risk operations','platform integrity','latam operations','regional operations','market expansion',
    'community operations','operations management','operations manager','head of support','support leadership',
    'web3 operations','crypto operations','fintech operations','business operations','strategy and operations',
)

# These patterns identify the professional core in the title, not incidental
# technical vocabulary in a description or the candidate's practical tool list.
TECHNICAL_TITLE_PATTERNS=(
    r'\bsoftware (?:engineer|developer)\b', r'\b(?:backend|back ?end|frontend|front ?end|full ?stack|mobile|ios|android|react native) (?:software )?(?:engineer|developer)\b',
    r'\bsmart contract (?:engineer|developer)\b', r'\bprotocol (?:engineer|developer)\b',
    r'\binfrastructure engineer\b', r'\bsite reliability engineer\b', r'\bsre\b', r'\bdevops engineer\b',
    r'\bplatform engineer\b', r'\bcloud engineer\b', r'\bnetwork engineer\b',
    r'\b(?:application|cloud|security) security engineer\b', r'\bsecurity engineer\b',
    r'\bdata scientist\b', r'\bmachine learning engineer\b', r'\bml engineer\b', r'\bdata engineer\b',
    r'\bresearch scientist\b', r'\banalytics engineer\b',
)
SPECIALIZED_TITLE_PATTERNS=(
    r'\blegal counsel\b',r'\battorney\b',r'\blawyer\b',r'\baccountant\b',r'\bcontroller\b',
    r'\bactuary\b',r'\bphysician\b',r'\bmedical doctor\b',r'\bclinician\b',r'\bnurse\b',
)
TECH_SECURITY_CUES=('penetration testing','penetration tester','threat modeling','vulnerability research',
                    'exploit development','offensive security','security audit','smart contract audit',
                    'incident response','malware analysis')
DESIGN_TITLE_PATTERNS=(r'\bproduct designer\b',r'\bux designer\b',r'\buser experience designer\b',
                       r'\bproduct design\b',r'\bux design\b')
DESIGN_EXPERIENCE_CUES=('product design experience','ux design experience','user experience design experience',
                        'portfolio demonstrating','portfolio showing','ux thinking','user research',
                        'interaction design','design systems','visual design craft','visual craft')
SOFT_DISTANCE_CUES=('business strategy','strategy','business operations','technical program manager',
                    'program manager','product manager','solutions engineer','sales engineer')
EARLY_CAREER_CUES=('intern','internship','entry level','entry level','junior','jr')


def _role_profile_text(profile):
    """Only role history/targets count as formal technical-profession evidence."""
    parts=list(profile.get('desired_roles') or [])
    for item in profile.get('work_history') or []:
        parts.append(str(item).split('·',1)[0])
    return '\n'.join(parts)


def _profile_has_technical_family(profile, patterns):
    role_text=_norm(_role_profile_text(profile))
    return any(re.search(pattern,role_text) for pattern in patterns)


def evaluate_ai_analysis_eligibility(job, profile, prematch=None):
    """Return a conservative local gate decision; never computes profile fit."""
    job=job if isinstance(job,dict) else {}
    profile=profile if isinstance(profile,dict) else {}
    title=str(job.get('title') or '')
    description=str(job.get('description') or '')
    title_norm=_norm(title)
    signals=[];exclusions=[]

    excluded_role=next((role for role in (profile.get('excluded_roles') or []) if _has(title,role)),None)
    if excluded_role:
        exclusions.append(f'Título corresponde ao cargo explicitamente excluído: {excluded_role}.')

    technical_family=next((pattern for pattern in TECHNICAL_TITLE_PATTERNS
                           if re.search(pattern,_norm(title))),None)
    if technical_family and not _profile_has_technical_family(profile,TECHNICAL_TITLE_PATTERNS):
        # Solutions-oriented engineering titles are not assumed to be engineering-core.
        if _has(title,'solutions engineer') or _has(title,'sales engineer'):
            signals.append('Título técnico-consultivo; sem evidência suficiente para hard mismatch.')
            technical_family=None
        else:
            exclusions.append('Núcleo do título pertence a uma família de engenharia ou ciência técnica.')

    specialized=next((pattern for pattern in SPECIALIZED_TITLE_PATTERNS if re.search(pattern,_norm(title))),None)
    if specialized and not _profile_has_technical_family(profile,SPECIALIZED_TITLE_PATTERNS):
        exclusions.append('Núcleo do título é uma profissão especializada sem trajetória profissional correspondente no perfil.')

    if _has(title,'security researcher') and any(_has(description,cue) for cue in TECH_SECURITY_CUES):
        if not _profile_has_technical_family(profile,TECHNICAL_TITLE_PATTERNS):
            exclusions.append('Pesquisa de segurança exige atuação técnica ofensiva/defensiva explícita.')

    design_title=next((pattern for pattern in DESIGN_TITLE_PATTERNS if re.search(pattern,_norm(title))),None)
    design_evidence=[cue for cue in DESIGN_EXPERIENCE_CUES if _has(description,cue)]
    if design_title and design_evidence:
        exclusions.append('Núcleo profissional de Product/UX Design exige experiência especializada em design.')

    early=next((cue for cue in EARLY_CAREER_CUES if _has(title,cue)),None)
    seniority=_norm(profile.get('seniority'))
    senior_profile=seniority in {'senior','lead','principal','executive'} or any(
        _has(' '.join(profile.get('desired_roles') or []),cue) for cue in ('senior','lead','head','director'))
    title_has_related=any(_has(title,cue) for cue in RELATED_CUES)
    if early and senior_profile and title_has_related:
        exclusions.append(f'Cargo explicitamente {early} representa regressão clara frente à senioridade desejada.')

    if excluded_role:
        signals.append(f'Cargo listado em excluded_roles: {excluded_role}.')
    profile_roles=profile.get('desired_roles') or []
    direct=next((role for role in profile_roles if _has(title,role)),None)
    related=next((cue for cue in RELATED_CUES if _has(title,cue)),None)
    # Relevant function signals from the description are accepted only when they
    # occur in a clearly role-specific title or heading context.
    body_related=next((cue for cue in RELATED_CUES if _has(description,cue)),None)
    if direct:
        signals.append(f'Título relacionado diretamente a cargo desejado: {direct}.')
    elif related:
        signals.append(f'Família funcional adjacente identificada no título: {related}.')
    elif body_related and not exclusions:
        signals.append(f'Área profissional adjacente identificada na descrição: {body_related}.')
    elif any(_has(title,cue) for cue in ('solutions engineer','technical program manager','product manager')):
        signals.append('Título técnico/produto não comprova engenharia como núcleo da função.')
    elif any(_has(title,cue) for cue in SOFT_DISTANCE_CUES):
        signals.append('Escopo de negócio, estratégia ou produto merece revisão, sem hard mismatch.')
    elif prematch and prematch.get('positive_signals'):
        signals.append('Pré-match contém sinal positivo; usado apenas como contexto permissivo.')

    if exclusions:
        status='excluded';reason=exclusions[0]
    elif direct or related or body_related:
        status='eligible';reason='Não foi identificado hard mismatch; há sinais de função compatível ou adjacente.'
    else:
        status='review';reason='Não há hard mismatch claro, mas a proximidade profissional não é suficientemente clara.'
    return {'status':status,'reason':reason,'signals':signals,'exclusion_signals':exclusions,
            'rule_version':RULE_VERSION}


def get_or_evaluate_ai_analysis_eligibility(key, job, profile, store, prematch=None):
    active=store.get_profile()
    digest=description_hash(job)
    result=store.get_ai_analysis_eligibility(key,digest,active['profile_id'],active['version'],RULE_VERSION,CACHE_REVISION)
    if result is not None and result.get('job_description_hash')==digest and result.get('rule_version')==RULE_VERSION:
        return result,True
    value=evaluate_ai_analysis_eligibility(job,profile,prematch)
    value.update({'job_description_hash':digest,'profile_id':active['profile_id'],
                  'profile_version':active['version'],'cache_revision':CACHE_REVISION})
    store.save_ai_analysis_eligibility(key,value)
    persisted=store.get_ai_analysis_eligibility(key,digest,active['profile_id'],active['version'],RULE_VERSION,CACHE_REVISION)
    if persisted and persisted.get('job_description_hash')==digest and persisted.get('rule_version')==RULE_VERSION:
        return persisted,False
    return value,False


def attach_ai_analysis_eligibility(data, active_profile, store):
    if not active_profile or not active_profile.get('profile_id'):
        return data
    profile=active_profile.get('profile') or {}
    for record in data.get('records',[]):
        for job in record.get('jobs',[]):
            try:key=job_key(job.get('url'))
            except (TypeError,ValueError):continue
            result,_=get_or_evaluate_ai_analysis_eligibility(key,job,profile,store,job.get('pre_match'))
            job['ai_analysis_eligibility']=result
    return data
