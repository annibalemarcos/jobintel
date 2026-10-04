"""Deterministic, local profile triage for crawler_v2."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path


PROFILE_FILTER_VERSION = 'profile_filter_v1'
PROFILE_FINGERPRINT_FIELDS = (
    'desired_roles', 'excluded_roles', 'related_roles_open', 'professional_summary',
    'work_history', 'skills', 'technologies', 'domain_knowledge', 'interests', 'work_modes',
    'work_mode_restrictions', 'regions', 'current_location', 'relocation', 'languages', 'analysis_notes',
)
QUALIFIERS = {
    'senior', 'sr', 'lead', 'head', 'manager', 'director', 'specialist', 'associate',
    'principal', 'staff', 'expert', 'junior', 'jr', 'intern', 'internship', 'entry',
    'level', 'tier', 'i', 'ii', 'iii', 'iv', 'v', 'the', 'of', 'and', 'a', 'an',
}
HARD_MISMATCH_PATTERNS = tuple((re.compile(pattern), reason) for pattern, reason in (
    (r'\bsolutions engineer\b', 'engineering family outside desired roles'),
    (r'\b(?:software|backend|back end|frontend|front end|full stack|mobile|smart contract|protocol|infrastructure|devops|site reliability|sre|security|application security|cloud security|data) engineer\b', 'engineering family outside desired roles'),
    (r'\bengineering manager\b|\bengineer\b', 'engineering family outside desired roles'),
    (r'\bdata scientist\b|\bmachine learning (?:scientist|engineer)\b|\bresearch scientist\b|\bquantitative researcher\b|\bquant researcher\b|\bquant\b', 'technical research family outside desired roles'),
    (r'\btrader\b|\btrading operations\b', 'trading family outside desired roles'),
    (r'\baccount executive\b|\bsales (?:representative|executive|manager|lead)\b|\benterprise partnerships\b|\bpartnerships lead\b', 'sales/partnerships family outside desired roles'),
    (r'\blegal counsel\b|\battorney\b|\blawyer\b|\baccountant\b|\bcontroller\b|\bactuary\b|\b(?:medical|clinical) (?:doctor|specialist|practitioner)\b|\bgraphic designer\b', 'specialized professional family outside desired roles'),
    (r'\b(?:security|it|revenue) operations (?:engineer|analyst|manager)\b', 'functional family outside desired roles'),
))


def normalize_text(value) -> str:
    text = unicodedata.normalize('NFKD', str(value or ''))
    text = ''.join(char for char in text if not unicodedata.combining(char)).lower()
    text = text.replace('&', ' and ')
    text = re.sub(r'\bp\s*2\s*p\b', 'p2p', text)
    text = re.sub(r'\bops\b', 'operations', text)
    text = re.sub(r'\bcx\b', 'customer experience', text)
    text = re.sub(r'\bmoderators?\b|\bmoderation\b', 'moderation', text)
    text = re.sub(r'\bdisputes\b', 'dispute', text)
    text = re.sub(r'\boperation\b', 'operations', text)
    text = re.sub(r'\bengineers\b', 'engineer', text)
    text = re.sub(r'\bscientists\b', 'scientist', text)
    return ' '.join(re.findall(r'[a-z0-9]+', text))


def _tokens(value, remove_qualifiers=True) -> tuple[str, ...]:
    tokens = normalize_text(value).split()
    if remove_qualifiers:
        tokens = [token for token in tokens if token not in QUALIFIERS]
    return tuple(tokens)


def _values(value) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return []


def _family(value) -> str | None:
    text = normalize_text(value)
    words = set(text.split())
    if {'social', 'media'} <= words:
        return 'social_media'
    if 'p2p' in words and 'dispute' in words:
        return 'p2p_disputes'
    if 'community' in words and ('moderation' in words or 'operations' in words):
        return 'community_operations'
    if ('latam' in words or 'regional' in words or 'region' in words) and 'operations' in words:
        return 'regional_operations'
    if 'market' in words and ('expansion' in words or 'entry' in words or 'growth' in words):
        return 'market_expansion'
    if 'customer' in words and 'operations' in words:
        return 'customer_operations'
    if 'customer' in words and 'experience' in words and 'operations' in words:
        return 'customer_operations'
    if 'customer' in words and 'success' in words and 'operations' in words:
        return 'customer_operations'
    if 'product' in words and 'operations' in words:
        return 'product_operations'
    if 'support' in words and ('head' in words or 'lead' in words or 'leader' in words or 'leadership' in words):
        return 'support_leadership'
    if 'support' in words and 'customer' in words and 'operations' not in words:
        return 'customer_support'
    if 'operations' in words and 'manager' in set(normalize_text(value).split()) and len(words) <= 2:
        return 'operations_manager'
    if 'implementation' in words:
        return 'implementation'
    if 'risk' in words and 'operations' in words:
        return 'risk_operations'
    if ('knowledge' in words and ('management' in words or 'manager' in words or 'operations' in words)):
        return 'knowledge_operations'
    if 'trust' in words and 'safety' in words:
        return 'trust_safety'
    if 'platform' in words and 'integrity' in words:
        return 'platform_integrity'
    if 'ai' in words and 'operations' in words:
        return 'ai_operations'
    if 'solutions' in words and ('consulting' in words or 'consultant' in words):
        return 'solutions_consulting'
    return None


def _canonical_profile(document: dict) -> dict:
    profile = document['profile']
    return {key: profile.get(key) for key in PROFILE_FINGERPRINT_FIELDS}


@dataclass(frozen=True)
class LoadedProfile:
    profile: dict
    profile_version: object
    fingerprint: str
    profile_file: str


def load_profile(path) -> LoadedProfile:
    file_path = Path(path)
    if not file_path.is_file():
        raise ValueError(f'Arquivo de perfil não encontrado: {file_path}')
    try:
        document = json.loads(file_path.read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f'Não foi possível ler o JSON do perfil: {exc}') from None
    if not isinstance(document, dict) or not isinstance(document.get('profile'), dict):
        raise ValueError('JSON de perfil inválido: objeto "profile" ausente.')
    profile = document['profile']
    desired = profile.get('desired_roles', [])
    if not isinstance(desired, list) or any(not isinstance(item, str) for item in desired):
        raise ValueError('JSON de perfil inválido: "profile.desired_roles" deve ser uma lista de textos.')
    for key in ('excluded_roles', 'interests', 'work_history', 'skills', 'domain_knowledge', 'regions', 'languages'):
        value = profile.get(key)
        if value is not None and not isinstance(value, (list, str)):
            raise ValueError(f'JSON de perfil inválido: "profile.{key}" deve ser texto ou lista.')
    canonical = json.dumps(_canonical_profile(document), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    fingerprint = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    return LoadedProfile(profile, document.get('profile_version'), fingerprint, str(file_path.resolve()))


@dataclass(frozen=True)
class MatchResult:
    classification: str
    reason: str
    signals: tuple[str, ...] = ()


class ProfileMatcher:
    """Precompiled profile representation; classification performs no I/O."""

    def __init__(self, profile: dict):
        desired = _values(profile.get('desired_roles'))
        excluded = _values(profile.get('excluded_roles'))
        self.desired_signatures = {_tokens(role) for role in desired if _tokens(role)}
        self.desired_families = {_family(role) for role in desired} - {None}
        self.excluded_roles = [(_tokens(role), _tokens(role, False)) for role in excluded]
        self.related_roles_open = profile.get('related_roles_open') is not False
        context = []
        for field in ('interests', 'domain_knowledge', 'work_history', 'professional_summary', 'analysis_notes'):
            context.extend(_values(profile.get(field)))
        self.context_families = {_family(value) for value in context} - {None}
        # Only explicit desired-role phrases can act as description-level functional
        # evidence. Skills, interests and domain knowledge remain secondary signals.
        self.desired_role_phrases = tuple(dict.fromkeys(
            normalize_text(value) for value in desired if len(_tokens(value)) >= 2
        ))
        self.regions = tuple(normalize_text(value) for value in _values(profile.get('regions')))
        self.current_location = normalize_text(profile.get('current_location', ''))
        self.relocation = profile.get('relocation')
        self.work_modes = profile.get('work_modes') if isinstance(profile.get('work_modes'), dict) else {}
        self.work_mode_restrictions = profile.get('work_mode_restrictions', [])
        self.skill_phrases = tuple(dict.fromkeys(normalize_text(value) for field in ('skills', 'technologies')
                                                for value in _values(profile.get(field)) if len(_tokens(value)) >= 1))
        self.language_phrases = tuple(dict.fromkeys(
            normalize_text(value.split('·', 1)[0].split('-', 1)[0])
            for value in _values(profile.get('languages'))
        ))

    def _explicit_desired_match(self, title):
        signature = _tokens(title)
        family = _family(title)
        if signature in self.desired_signatures:
            return True
        # Exact role identity can be qualified with levels or seniority, but cannot lose its profession.
        return bool(family and family in self.desired_families)

    def _excluded_match(self, title):
        title_tokens = set(_tokens(title, False))
        for base_tokens, full_tokens in self.excluded_roles:
            # Internship and professional psychology exclusions are explicit occupations/categories.
            if 'internship' in full_tokens and {'intern', 'internship'} & title_tokens:
                return 'explicit excluded role'
            if not base_tokens:
                continue
            if {'psychology'} <= set(base_tokens) and ({'psychology', 'psychologist'} & title_tokens):
                return 'explicit excluded role'
            if set(base_tokens) == {'psychologist'} and 'psychologist' in title_tokens:
                return 'explicit excluded role'
            title_core = set(_tokens(title))
            base_core = set(_tokens(' '.join(base_tokens)))
            if not base_core <= title_core:
                continue
            qualifiers = set(full_tokens) - set(base_tokens)
            if 'junior' in qualifiers or 'jr' in qualifiers:
                if not ({'junior', 'jr', 'entry'} & title_tokens):
                    continue
            if 'tier' in qualifiers and not {'tier', '1'} <= title_tokens:
                continue
            if 'entry' in qualifiers and not {'entry', 'level'} <= title_tokens and 'entry' not in title_tokens:
                continue
            return 'explicit excluded role'
        return ''

    @staticmethod
    def _hard_mismatch(title):
        text = normalize_text(title)
        for pattern, reason in HARD_MISMATCH_PATTERNS:
            if pattern.search(text):
                if (pattern.pattern == r'\bengineering manager\b|\bengineer\b' and
                        re.search(r'\b(?:customer|technical) support\b', text)):
                    return ''
                return reason
        return ''

    def classify_title(self, title: str) -> MatchResult:
        title = str(title or '').strip()
        if not title:
            return MatchResult('POSSIBLE', 'job title unavailable', ('title unknown',))
        if self._explicit_desired_match(title):
            family = _family(title)
            reason = f'desired role family: {family.replace("_", " ")}' if family else 'desired role match'
            return MatchResult('LIKELY_RELEVANT', reason, ('desired role',))
        excluded = self._excluded_match(title)
        if excluded:
            return MatchResult('CLEARLY_IRRELEVANT', excluded, ('excluded role',))
        mismatch = self._hard_mismatch(title)
        if mismatch:
            return MatchResult('CLEARLY_IRRELEVANT', mismatch, ('hard professional mismatch',))
        family = _family(title)
        if family and family in self.context_families:
            if self.related_roles_open:
                return MatchResult('POSSIBLE', f'adjacent role family: {family.replace("_", " ")}', ('related profile interest',))
            return MatchResult('POSSIBLE', 'adjacent role; related roles are closed', ('adjacent title',))
        return MatchResult('POSSIBLE', 'title is ambiguous; retain for review', ('uncertain title fit',))

    def _explicit_location_conflict(self, job):
        description = normalize_text(job.get('description'))
        location = normalize_text(job.get('location'))
        combined = f'{location} {description}'
        remote = bool(re.search(r'\b(remote|work from anywhere|distributed|fully remote)\b', combined))
        mode = normalize_text(job.get('work_mode') or '')
        if not mode:
            mode = 'remote' if remote else ('onsite' if re.search(r'\b(onsite|on site|in office|in-office)\b', combined) else '')
        restrictions = self.work_mode_restrictions
        restrictions = restrictions if isinstance(restrictions, list) else []
        for restriction in restrictions:
            if isinstance(restriction, str):
                rule = normalize_text(restriction)
                forbidden_mode = next((kind for kind in ('remote', 'hybrid', 'onsite', 'in person') if kind in rule), '')
                forbidden = bool(forbidden_mode)
            elif isinstance(restriction, dict):
                rule = normalize_text(restriction.get('mode') or restriction.get('work_mode') or restriction.get('type') or '')
                forbidden_mode = next((kind for kind in ('remote', 'hybrid', 'onsite', 'in person') if kind in rule), '')
                forbidden = restriction.get('allowed') is False or restriction.get('accepted') is False
            else:
                continue
            if forbidden and forbidden_mode and mode and forbidden_mode in mode:
                return f'explicit {forbidden_mode} work-mode conflict'
        if remote:
            return ''
        if self.relocation is False and re.search(r'\b(relocation required|must relocate|relocation is required)\b', description):
            return 'explicit relocation requirement conflicts with profile'
        onsite = bool(re.search(r'\b(onsite only|on site only|in office|in-office|must be onsite|must be based in|must reside in|residence in)\b', description))
        if onsite and location and self.current_location:
            accepted = any(region and region in location for region in self.regions)
            same_as_current = any(part and part in location for part in self.current_location.split() if len(part) > 3)
            if not accepted and not same_as_current and self.relocation is False:
                return 'explicit location conflict'
        return ''

    def classify_job(self, job: dict) -> MatchResult:
        title = str(job.get('title') or '')
        title_result = self.classify_title(title)
        body = normalize_text(' '.join(str(job.get(key) or '') for key in ('title', 'description', 'location')))
        signals = list(title_result.signals)
        mode = normalize_text(job.get('work_mode') or '')
        if not mode:
            if re.search(r'\b(remote|work from anywhere|distributed|fully remote)\b', body):mode='remote'
            elif re.search(r'\b(hybrid)\b', body):mode='hybrid'
            elif re.search(r'\b(onsite|on site|in office|in-office)\b', body):mode='onsite'
        preference = normalize_text(self.work_modes.get(mode, '')) if mode else ''
        if preference in ('preferred', 'acceptable'):
            signals.append(f'{mode} work mode is {preference}')
        elif preference in ('unwanted', 'not desired'):
            signals.append(f'{mode} work mode differs from preference')
        location = normalize_text(job.get('location'))
        if location:
            if any(region and region in location for region in self.regions):signals.append('profile region match')
        else:
            signals.append('location unknown')
        language_hits=[phrase for phrase in self.language_phrases if phrase and phrase in body]
        if language_hits:signals.append('language evidence: '+', '.join(language_hits))
        skill_hits=[phrase for phrase in self.skill_phrases if phrase and phrase in body]
        if skill_hits:signals.append('secondary skill evidence: '+', '.join(skill_hits[:3]))
        location_conflict = self._explicit_location_conflict(job)
        if location_conflict:
            return MatchResult('NOT_RELEVANT', location_conflict, ('explicit mandatory conflict',))
        if title_result.classification == 'LIKELY_RELEVANT':
            return MatchResult('RELEVANT', title_result.reason, tuple(dict.fromkeys(signals)))
        if title_result.classification == 'CLEARLY_IRRELEVANT':
            return MatchResult('NOT_RELEVANT', title_result.reason, title_result.signals)
        family = _family(title)
        if family and family in self.context_families and self.related_roles_open:
            signals.append('related professional context')
            return MatchResult('RELEVANT', f'profile context supports {family.replace("_", " ")}', tuple(dict.fromkeys(signals)))
        description=normalize_text(job.get('description'))
        specialized_security_title=re.search(r'\b(?:protocol|smart contract) security researcher\b',normalize_text(title))
        technical_research_signals=sum(bool(re.search(pattern,description)) for pattern in (
            r'\bsecurity research\b', r'\battacker driven analysis\b', r'\bcomplex codebases\b',
            r'\bexploitability\b', r'\baudits?\b', r'\bsmart contract security\b', r'\bprotocol security\b',
        ))
        if specialized_security_title and technical_research_signals >= 3:
            return MatchResult('NOT_RELEVANT', 'protocol/smart contract security research family outside desired roles',
                               ('specialized security research identity',))
        for phrase in self.desired_role_phrases:
            if phrase and phrase in body:
                signals.append('description/profile phrase')
                return MatchResult('RELEVANT', f'profile-aligned evidence: {phrase}', tuple(dict.fromkeys(signals)))
        if not str(job.get('description') or '').strip():
            return MatchResult('REVIEW', 'description unavailable; insufficient local evidence', tuple(dict.fromkeys(signals+['description unknown'])))
        return MatchResult('REVIEW', title_result.reason, tuple(dict.fromkeys(signals or ['insufficient positive or negative evidence'])))


def validate_resume_profile(original_info: dict, fingerprint: str):
    original = original_info.get('profile_fingerprint') if isinstance(original_info, dict) else None
    if not original or original != fingerprint:
        raise ValueError('O perfil utilizado nesta execução é diferente do perfil original. Inicie uma nova execução em vez de usar --resume.')
