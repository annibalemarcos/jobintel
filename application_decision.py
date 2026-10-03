"""Local, deterministic recommendation and practical-risk assessment."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone

RULE_VERSION = 'application_decision_v3'
RECOMMENDATIONS = ('prioritize', 'consider', 'review', 'low_priority')
USER_DECISIONS = ('not_reviewed', 'want_to_analyze', 'want_to_apply', 'do_not_apply', 'archived')
USER_DECISION_REASONS = {
    'do_not_apply': {'low_fit','location','compensation','company','seniority','technology','not_interested','other'},
    'want_to_apply': {'strong_fit','career_value','interesting_company','strategic_transition','other'},
}


def _norm(value):
    text=unicodedata.normalize('NFD',str(value or '')).encode('ascii','ignore').decode('ascii').lower()
    return re.sub(r'[^a-z0-9]+',' ',text).strip()


def _has(text, phrases):
    source=_norm(text)
    return any(re.search(rf'(?<![a-z0-9]){re.escape(_norm(p))}(?![a-z0-9])',source)
               for p in phrases if _norm(p))


_PLACE_ALIASES={
    'brazil':('brazil','brasil'),'united states':('united states','usa','u s a','u s','us'),
    'philippines':('philippines','pilipinas'),'united kingdom':('united kingdom','uk','england'),
    'canada':('canada',),'singapore':('singapore',),'japan':('japan',),'germany':('germany',),
    'france':('france',),'australia':('australia',),'india':('india',),'portugal':('portugal',),
}
_CITY_COUNTRY={'manila':'philippines','campinas':'brazil','sao paulo':'brazil','london':'united kingdom',
    'new york':'united states','san francisco':'united states','toronto':'canada','singapore':'singapore',
    'tokyo':'japan','berlin':'germany','paris':'france','sydney':'australia','mumbai':'india','lisbon':'portugal'}


def _area(value):
    normalized=_norm(value)
    city=next((name for name in sorted(_CITY_COUNTRY,key=len,reverse=True) if _has(normalized,(name,))),None)
    country=next((name for name,aliases in _PLACE_ALIASES.items()
                  if any(_has(normalized,(alias,)) for alias in aliases)),None)
    return city,country or (_CITY_COUNTRY.get(city) if city else None)


def _location_hint(description):
    text=str(description or '')
    patterns=(r'(?i)\b(?:based|located|reside|presence)\s+in\s+([A-Z][A-Za-zÀ-ÿ .-]{1,45})',
              r'(?i)\b(?:office|work)\s+(?:in|from)\s+([A-Z][A-Za-zÀ-ÿ .-]{1,45})')
    for pattern in patterns:
        match=re.search(pattern,text)
        if match:
            value=re.split(r'[,.;:\n]|\b(?:and|with|where|while|for|full.time)\b',match.group(1),maxsplit=1,flags=re.I)[0].strip()
            if value:return value
    normalized=_norm(text)
    for city in sorted(_CITY_COUNTRY,key=len,reverse=True):
        if _has(normalized,(city,)):return city
    return ''


def _work_mode(job):
    location=str(job.get('location') or '')
    description=str(job.get('description') or '')
    structured=' '.join((location,str(job.get('employment_type') or ''),str(job.get('contract_type') or '')))
    source=' '.join((structured,description))
    if _has(structured,('remote','fully remote','work from anywhere','worldwide','remoto')) or _has(description,
        ('fully remote','remote position','remote role','work from anywhere','work remotely','remote work','trabalho remoto')):
        mode='remote'
    elif _has(source,('hybrid','hibrido','híbrido')):
        mode='hybrid'
    elif _has(source,('on site','onsite','in office','in-office','office based','office-based',
                     'presencial','no escritório','trabalho presencial','must work from our office')):
        mode='onsite'
    else:mode=None
    normalized=_norm(source)
    office_days=bool(re.search(r'\b(?:five|5)[ -]days?(?: per week| a week)? in (?:the )?office\b',normalized)
                     or re.search(r'\b(?:three|3)[ -]days?(?: per week| a week)? in (?:the )?office\b',normalized)
                     or re.search(r'\b\d+ days? (?:per week|a week) in (?:the )?office\b',normalized))
    physical_required=(mode=='onsite' or office_days or _has(source,(
        'must work from our office','required to work from the office','office presence required',
        'presença obrigatória no escritório','presencial obrigatório','must be based in','must be located in',
        'required to be located in','must reside in','applicants must reside in','candidates must reside in',
        'deve residir em','necessário residir em','precisa estar localizado em')))
    return mode,physical_required,office_days


def _required_country(text):
    for pattern in (r'\b(?:must|applicants must|candidates must|required to) reside in ([a-z ]+)',
                    r'\b(?:must|applicants must|candidates must|required to) be based in ([a-z ]+)',
                    r'\b(?:must|applicants must|candidates must|required to) be located in ([a-z ]+)'):
        match=re.search(pattern,_norm(text))
        if match:
            fragment=match.group(1).split(' ',3)
            value=' '.join(fragment[:3])
            return _area(value)[1]
    if _has(text,('US residents only','residents of the United States only','only candidates located in the United States')):
        return 'united states'
    return None


def _different_countries(first,second):
    first_city,first_country=_area(first);second_city,second_country=_area(second)
    return bool(first_country and second_country and first_country!=second_country)


def _language_risk(text,profile,fit):
    language_names=('English','Portuguese','Japanese','Spanish','French','German','Mandarin','Chinese',
                    'Korean','Arabic','Italian','Russian','Hindi')
    required=[]
    for name in language_names:
        for sentence in re.split(r'[.!?;\n]+',str(text or '')):
            if _has(sentence,(name,)) and _has(sentence,('required','must','mandatory','fluent','fluency required',
                'obrigatório','obrigatoria','obrigatória','deve ser fluente','fluência obrigatória')):
                if not _has(sentence,('preferred','preferable','nice to have','bonus','desirable','desejável','preferencial')):
                    required.append((name,sentence));break
    if not required:
        language=(fit.get('dimensions') or {}).get('language_fit') or {};status=language.get('status')
        if status=='met':return {'status':'ok','reason':'A análise existente registra requisitos linguísticos atendidos.'}
        if status=='partially_met':return {'status':'attention','reason':'A análise existente registra atendimento parcial aos requisitos linguísticos.'}
        if status=='not_met' and any(isinstance(item,dict) and item.get('status')=='not_met'
            and _has(item.get('requirement',''),('language','english','idioma','inglês'))
            for item in fit.get('mandatory_requirements',[])):
            return {'status':'conflict','reason':'A análise existente identifica requisito linguístico obrigatório não atendido.'}
        return {'status':'unknown','reason':'Não há informação suficiente para confirmar requisitos de idioma.'}
    entries=[str(item) for item in (profile.get('languages') or [])]
    statuses=[]
    for name,sentence in required:
        matched=next((entry for entry in entries if _has(entry,(name,))),None)
        if not matched:
            # The profile schema has no flag saying its language list is exhaustive.
            statuses.append('unknown');continue
        entry=_norm(matched);required_level='fluent' if _has(sentence,('fluent','fluency')) else ''
        lower_level=bool(re.search(r'\b(?:a1|a2|b1|b2|basic|beginner|elementary|intermediate|conversational|basico|intermediario)\b',entry))
        advanced=bool(re.search(r'\b(?:c1|c2|native|fluent|advanced|proficient|near native|nativo|fluente|avancado)\b',entry))
        if required_level and lower_level and not advanced:statuses.append('conflict')
        else:statuses.append('ok')
    if 'conflict' in statuses:return {'status':'conflict','reason':'O perfil informa nível abaixo de um idioma obrigatório explicitamente exigido pela vaga.'}
    if 'unknown' in statuses:return {'status':'unknown','reason':'A vaga exige idioma explicitamente, mas o perfil não declara essa língua; o schema não indica que a lista esteja completa.'}
    return {'status':'ok','reason':'Os idiomas obrigatórios explicitamente identificados constam no perfil em nível compatível.'}


def _hard_requirements_risk(text,profile):
    requirements=[]
    terms=('CPA','CFA','PMP','professional license','licensed professional','mandatory certification',
           'certification','bachelor degree','bachelor’s degree','bachelors degree','degree required',
           'licença profissional','certificação obrigatória','graduação obrigatória')
    fields=' '.join(str(item) for name in ('certifications','education','skills') for item in (profile.get(name) or []))
    for sentence in re.split(r'[.!?;\n]+',str(text or '')):
        if _has(sentence,('preferred','nice to have','bonus','desirable','desejável','preferencial')):continue
        if not _has(sentence,('required','must','mandatory','minimum requirement','applicants must','candidates must',
                               'obrigatório','obrigatória','deve possuir','requisito mínimo')):continue
        term=next((item for item in terms if _has(sentence,(item,))),None)
        if term:
            requirements.append((term,'verified' if _has(fields,(term,)) else 'unknown'))
        year=re.search(r'\b(\d+)\+? years?\b',_norm(sentence))
        if year and _has(sentence,('experience','experiência')):
            amount=int(year.group(1));years=_number(profile.get('experience_years'))
            history=' '.join(str(item) for item in (profile.get('work_history') or []))
            domain=next((cue for cue in ('customer support','customer operations','software engineering',
                'data engineering','analytics engineering','accounting','legal practice') if _has(sentence,(cue,))),None)
            if years is not None and years>=amount and domain and _has(history,(domain,)):
                requirements.append((f'{amount}+ anos em {domain}','verified'))
            else:
                requirements.append((f'{amount}+ anos de experiência especializada','unknown'))
    if not requirements:return {'status':'unknown','reason':'Nenhum requisito profissional obrigatório adicional foi identificado com clareza.'}
    missing=[term for term,status in requirements if status=='unknown']
    if missing:return {'status':'unknown','reason':'Requisito(s) obrigatório(s) explícito(s) sem confirmação suficiente no perfil: '+', '.join(missing)+'. A ausência no perfil não é tratada como falta.'}
    return {'status':'ok','reason':'Os requisitos profissionais obrigatórios identificados têm evidência correspondente no perfil.'}


def _risks(job, profile, fit):
    description=str(job.get('description') or '')
    text=' '.join(str(job.get(k) or '') for k in ('title','description','location','employment_type','contract_type'))
    location=str(job.get('location') or _location_hint(description))
    current=str(profile.get('current_location') or '')
    risk={}
    mode,physical_required,office_days=_work_mode(job)
    physical_constraint=physical_required and bool(location) and not _has(location,('remote','worldwide','global','work from anywhere'))
    different_country=bool(current and location and _different_countries(current,location))
    relocation_required=_has(text,('relocation required','must relocate','must be willing to relocate','required to relocate',
        'relocation is required','mudança necessária','relocação obrigatória'))
    physical_conflict=bool(physical_constraint and profile.get('relocation') is False and different_country)
    if physical_conflict:
        reason=f'A vaga exige presença física em {location}; o perfil está em {current} e informa que não aceita relocação.'
        risk['work_model']={'status':'conflict','reason':reason}
    elif mode is None:
        risk['work_model']={'status':'unknown','reason':'A vaga não informa claramente o modelo de trabalho.'}
    elif mode in (profile.get('work_mode_restrictions') or []):
        risk['work_model']={'status':'conflict','reason':f'O perfil registra restrição obrigatória ao modelo {mode}.'}
    elif profile.get('work_modes',{}).get(mode)=='unwanted':
        risk['work_model']={'status':'attention','reason':f'O modelo {mode} não é desejado no perfil, mas isso é uma preferência.'}
    else:
        detail='com presença física obrigatória' if physical_required else 'sem presença física obrigatória identificada'
        risk['work_model']={'status':'ok','reason':f'A vaga indica modelo {mode} ({detail}) e não há restrição incompatível registrada.'}

    required_country=_required_country(text)
    if not required_country and _has(text,('not open to international applicants','international applicants are not accepted',
        'no international candidates','candidates must be located in')):
        required_country=_area(location)[1]
    remote=mode=='remote' or _has(location,('remote','worldwide','global','work from anywhere'))
    if required_country and current and _different_countries(current,required_country):
        risk['location']={'status':'conflict','reason':'A vaga exige residência/localização nos EUA e o perfil informa localização atual no Brasil.'
            if required_country=='united states' else f'A vaga exige residência em {required_country}; o perfil informa localização em outra região.'}
    elif relocation_required and profile.get('relocation') is False:
        risk['location']={'status':'conflict','reason':'A vaga exige relocação e o perfil informa que não aceita relocação.'}
    elif remote:
        risk['location']={'status':'unknown','reason':'A vaga é remota; localização remota, por si só, não confirma elegibilidade para contratação internacional.'}
    elif location and current and not different_country and _norm(location)==_norm(current):
        risk['location']={'status':'ok','reason':'A localização da vaga corresponde à localização atual do perfil.'}
    elif physical_constraint and profile.get('relocation') is False and different_country:
        risk['location']={'status':'conflict','reason':f'A vaga exige presença em {location}, incompatível com a localização atual {current} sem relocação.'}
    elif physical_constraint and different_country and profile.get('relocation') is True:
        risk['location']={'status':'attention','reason':f'A presença em {location} exigiria mudança; o perfil informa disponibilidade para relocação, sem confirmar decisão para esta vaga.'}
    elif location and _area(location)[1] and _area(current)[1] and _area(location)[1]==_area(current)[1]:
        risk['location']={'status':'attention','reason':'A vaga está no mesmo país informado pelo perfil, mas a compatibilidade de deslocamento/localização não é conhecida.'}
    elif not location:
        risk['location']={'status':'unknown','reason':'A vaga não informa localidade suficiente para comparar com o perfil.'}
    else:
        risk['location']={'status':'unknown','reason':'A vaga informa uma localização, mas sem presença ou requisito de residência explícito não há evidência suficiente de incompatibilidade.'}

    authorization_required=_has(text,('work authorization required','authorized to work in','must be authorized to work',
        'right to work in','eligible to work in','no visa sponsorship','sponsorship unavailable',
        'não oferece patrocínio de visto','autorização de trabalho obrigatória','autorização para trabalhar'))
    authorization=profile.get('work_authorization')
    if authorization_required and not authorization:
        risk['work_authorization']={'status':'unknown','reason':'A vaga exige autorização de trabalho/patrocínio específico, mas o perfil não informa autorização de trabalho.'}
    elif authorization_required and authorization:
        auth_text=' '.join(authorization) if isinstance(authorization,list) else str(authorization)
        target=required_country or ('united states' if _has(text,('US work authorization','United States work authorization')) else '')
        if target and _has(auth_text,('not authorized','not eligible','no right to work','não autorizado','sem autorização')):
            risk['work_authorization']={'status':'conflict','reason':'O perfil registra explicitamente ausência de autorização exigida pela vaga.'}
        elif target and _has(auth_text,('authorized','eligible to work','right to work','autorizado')) and _has(auth_text,(target,)):
            risk['work_authorization']={'status':'ok','reason':'O perfil declara autorização compatível com a exigência identificada.'}
        else:risk['work_authorization']={'status':'unknown','reason':'A vaga exige autorização, mas o dado do perfil não permite compará-la com segurança.'}
    else:
        risk['work_authorization']={'status':'unknown','reason':'A vaga não informa requisito explícito de autorização de trabalho.'}

    salary_min=_number(profile.get('salary_min')); job_min=_number(job.get('salary_min')); job_max=_number(job.get('salary_max'))
    same_basis=(profile.get('currency') and job.get('currency') and _norm(profile['currency'])==_norm(job['currency'])
                and profile.get('salary_period') and job.get('salary_period')
                and profile['salary_period']==job['salary_period'])
    if not (job_min or job_max):
        risk['compensation']={'status':'unknown','reason':'A vaga não informa remuneração.'}
    elif same_basis and salary_min and job_max and job_max<salary_min:
        risk['compensation']={'status':'attention','reason':'O limite superior informado fica abaixo do mínimo indicado no perfil, sem conversão cambial.'}
    elif same_basis:
        risk['compensation']={'status':'ok','reason':'A faixa informada não fica abaixo do mínimo do perfil na mesma moeda e período.'}
    else:
        risk['compensation']={'status':'unknown','reason':'Há informação salarial, mas moeda, período ou mínimo do perfil não permitem comparação segura.'}

    contract=' '.join(str(job.get(k) or '') for k in ('employment_type','contract_type'))
    if not contract:
        contract={'status':'unknown','reason':'A vaga não informa o tipo de contratação.'}
    elif not profile.get('contract_types'):
        contract={'status':'unknown','reason':'O perfil não informa tipos de contratação preferidos.'}
    elif any(_has(contract,(term,)) for term in profile['contract_types']):
        contract={'status':'ok','reason':'O tipo de contratação coincide com uma opção aceitável do perfil.'}
    else:
        contract={'status':'attention','reason':'O tipo informado não coincide com as opções indicadas; opções ausentes não são tratadas como proibição.'}
    risk['contract']=contract

    risk['language']=_language_risk(text,profile,fit)
    risk['hard_requirements']=_hard_requirements_risk(text,profile)
    return risk


def _number(value):
    try:return float(value)
    except (TypeError,ValueError):return None


def _fit_value(fit):
    return isinstance(fit,dict) and isinstance(fit.get('profile_score'),int) and fit.get('overall_fit') in {'excellent','strong','moderate','weak','poor'}


def evaluate_application_decision(job, profile=None):
    """Return an explainable recommendation and separate practical risks."""
    job=job if isinstance(job,dict) else {};profile=profile if isinstance(profile,dict) else {}
    quality=job.get('description_quality') or {};eligibility=job.get('ai_analysis_eligibility') or {}
    prematch=job.get('pre_match') or {};fit=job.get('profile_analysis') or {}
    risks=_risks(job,profile,fit if _fit_value(fit) else {})
    positive=[];negative=[];unknown=[]
    if quality.get('status')=='insufficient':
        recommendation='review';reason='A descrição é insuficiente para uma decisão confiável.'
        unknown.append('Descrição da vaga sem informações suficientes.')
    elif eligibility.get('status')=='excluded':
        recommendation='low_priority';reason=eligibility.get('reason') or 'A elegibilidade local identificou um hard mismatch profissional.'
        negative.extend(eligibility.get('exclusion_signals') or [reason])
    elif _fit_value(fit):
        score=fit['profile_score'];overall=fit['overall_fit'];career=(fit.get('career_value') or {}).get('rating','unknown')
        mandatory=fit.get('mandatory_requirements') or []
        unmet=[item for item in mandatory if isinstance(item,dict) and item.get('status')=='not_met']
        positive.extend(fit.get('strengths') or [])
        positive.extend(fit.get('transferable_experience') or [])
        negative.extend(fit.get('gaps') or [])
        negative.extend(f"Requisito obrigatório não atendido: {item.get('requirement','')}" for item in unmet)
        unknown.extend(fit.get('unknowns') or [])
        dimension_labels={'role_fit':'Cargo','experience_fit':'Experiência','skills_fit':'Competências',
            'seniority_fit':'Senioridade','domain_fit':'Domínio','work_model_fit':'Modelo de trabalho',
            'location_fit':'Localização','compensation_fit':'Remuneração','contract_fit':'Contrato','language_fit':'Idioma'}
        for key,dimension in (fit.get('dimensions') or {}).items():
            if not isinstance(dimension,dict):continue
            label=dimension_labels.get(key,key)
            status=dimension.get('status')
            detail=f"Dimensão {label}: {status or 'unknown'}"
            if dimension.get('score') is not None:detail+=f" ({dimension['score']}/100)"
            if status=='met':positive.append(detail)
            elif status=='partially_met':negative.append(detail)
            elif status=='not_met':negative.append(detail)
            else:unknown.append(f"Dimensão {label}: informação desconhecida.")
        if overall in {'excellent','strong'} and score>=70 and not unmet and quality.get('status')=='complete' and eligibility.get('status')=='eligible':
            recommendation='prioritize';reason='Compatibilidade profissional forte, requisitos obrigatórios sem lacunas identificadas e descrição completa.'
        elif unmet or overall=='poor' or score<30 or (overall=='weak' and score<50 and not fit.get('transferable_experience') and career in {'low','unknown'}):
            recommendation='low_priority';reason='A análise aponta baixa compatibilidade atual ou lacuna em requisito obrigatório essencial.'
        elif overall=='moderate' or fit.get('transferable_experience') or career in {'high','medium'}:
            recommendation='consider';reason='A oportunidade é plausível; experiência transferível ou valor de carreira pode compensar lacunas atuais.'
        else:
            recommendation='review';reason='Os sinais da análise são mistos ou não bastam para uma recomendação mais conclusiva.'
        if not quality.get('status'):unknown.append('Qualidade da descrição não avaliada.')
        if not eligibility.get('status'):unknown.append('Elegibilidade IA não avaliada.')
        elif eligibility.get('status')=='review':unknown.append('Elegibilidade profissional pede revisão antes de priorização alta.')
    else:
        if (quality.get('status') in {'complete','partial'} and eligibility.get('status')=='eligible'
                and prematch.get('classification')=='strong_candidate'):
            recommendation='consider';reason='O pré-match forte justifica investigar a oportunidade; ainda falta Profile Fit para priorizá-la.'
            positive.append('Pré-match forte: evidência preliminar favorável, não confirmação de compatibilidade profunda.')
        elif prematch.get('classification')=='possible_candidate':
            recommendation='review';reason='Há sinais preliminares de proximidade, mas pré-match possível e elegibilidade são sinais de triagem, não bastam para recomendar Considerar; Profile Fit ainda não está disponível.'
            positive.append('Pré-match possível indica uma oportunidade para avaliação adicional.')
            unknown.append('Profile Fit ainda não está disponível; sinais locais não confirmam compatibilidade profunda.')
        elif eligibility.get('status')=='review' and prematch.get('classification')=='strong_candidate':
            recommendation='review';reason='O pré-match é forte, mas a elegibilidade profissional pede revisão; os sinais são conflitantes.'
            positive.append('Pré-match forte é um sinal preliminar favorável.')
            unknown.append('Elegibilidade profissional pede revisão antes de uma recomendação mais positiva.')
        else:
            recommendation='review';reason='Os sinais locais disponíveis são permissivos, ausentes ou insuficientes para uma recomendação mais positiva ou negativa.'
        if prematch.get('classification')=='weak_candidate':
            unknown.append('Pré-match fraco é um sinal de triagem; sem Profile Fit, não comprova incompatibilidade suficiente para baixa prioridade.')
        if quality.get('status'):positive.append(f"Qualidade da descrição: {quality['status']}.")
        else:unknown.append('Qualidade da descrição não avaliada.')
        if eligibility.get('status'):positive.append(f"Elegibilidade IA: {eligibility['status']}.")
        else:unknown.append('Elegibilidade IA não avaliada.')
        if prematch.get('classification'):positive.append(f"Pré-match: {prematch['classification']}.")
        else:unknown.append('Pré-match não avaliado.')
        unknown.append('Profile Fit ainda não disponível; nenhuma análise foi solicitada automaticamente.')
    if eligibility.get('status') not in {'eligible','review','excluded'} and quality.get('status')!='insufficient':
        unknown.append('Elegibilidade IA não avaliada.')
    if quality.get('status') not in {'complete','partial','insufficient'}:
        unknown.append('Qualidade da descrição não avaliada.')
    if risks['location']['status']=='conflict':negative.append(risks['location']['reason'])
    elif risks['location']['status']=='unknown':unknown.append(risks['location']['reason'])
    blocking_conflicts=[key for key in ('location','work_model','work_authorization','hard_requirements')
        if (risks.get(key) or {}).get('status')=='conflict']
    if blocking_conflicts and recommendation in {'prioritize','consider'}:
        recommendation='review'
        reasons='; '.join(risks[key]['reason'] for key in blocking_conflicts)
        reason='Conflito prático explícito requer revisão humana antes de recomendar a vaga: '+reasons
        negative.extend(risks[key]['reason'] for key in blocking_conflicts)
    return {'recommendation':recommendation,'primary_reason':reason,
            'positive_signals':list(dict.fromkeys(x for x in positive if x)),
            'negative_signals':list(dict.fromkeys(x for x in negative if x)),
            'unknown_signals':list(dict.fromkeys(x for x in unknown if x)),
            'risks':risks,'career_value':fit.get('career_value') if _fit_value(fit) else None,
            'generated_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'rule_version':RULE_VERSION}


def decision_fingerprint(job, profile_version_id=None):
    """Hash exactly the material inputs consumed by the deterministic rules."""
    job=job if isinstance(job,dict) else {}
    payload={'title':job.get('title'),'company':job.get('company'),'location':job.get('location'),
        'date_posted':job.get('date_posted'),'description_hash':job.get('description_hash'),
        'description':job.get('description'),'employment_type':job.get('employment_type'),
        'contract_type':job.get('contract_type'),'salary_min':job.get('salary_min'),
        'salary_max':job.get('salary_max'),'salary_period':job.get('salary_period'),'currency':job.get('currency'),
        'profile_version_id':profile_version_id,
        'quality':job.get('description_quality'),'eligibility':job.get('ai_analysis_eligibility'),
        'pre_match':job.get('pre_match'),'profile_fit':job.get('profile_analysis'),
        'rule_version':RULE_VERSION}
    encoded=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def attach_application_decisions(data, store):
    """Attach cached/local recommendations and independent manual decisions."""
    from applications import job_key
    from job_description_quality import description_hash

    active=store.get_profile()
    profile_id=active.get('profile_id')
    version=active.get('version',0)
    profile_version_id=f"{profile_id}:v{version}" if profile_id else None
    counts={key:0 for key in RECOMMENDATIONS};reused=0;calculated=0;total=0
    for record in data.get('records',[]):
        for job in record.get('jobs',[]):
            total+=1
            try:key=job_key(job.get('url'))
            except (ValueError,AttributeError,TypeError):
                job['application_decision']=evaluate_application_decision(job,active.get('profile'))
                job['user_decision']={'user_decision':'not_reviewed','reason':'','note':'','version':0,'history':[]}
                counts[job['application_decision']['recommendation']]+=1
                calculated+=1
                continue
            digest=description_hash(job)
            fingerprint=decision_fingerprint(job,profile_version_id)
            result=store.get_application_decision(key,fingerprint,RULE_VERSION)
            if result:
                reused+=1
            else:
                result=evaluate_application_decision(job,active.get('profile'))
                result.update(profile_version_id=profile_version_id,job_description_hash=digest,
                              input_fingerprint=fingerprint)
                store.save_application_decision(key,fingerprint,result)
                calculated+=1
            job['application_decision']=result
            job['user_decision']=store.get_user_application_decision(key)
            counts[result['recommendation']]+=1
    if calculated:
        print('Decisão de candidatura: {} | Priorizar: {} | Considerar: {} | Revisar: {} | Baixa prioridade: {} | Reutilizadas: {} | Calculadas: {}'.format(
            total,counts['prioritize'],counts['consider'],counts['review'],counts['low_priority'],reused,calculated),flush=True)
    return data
