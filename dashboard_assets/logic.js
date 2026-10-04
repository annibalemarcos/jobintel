// Pure query functions shared by the UI and regression tests.
export const norm = value => String(value ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
export const applicationLabels={new:'Não aplicada',saved:'Quero aplicar',applied:'Apliquei',responded:'Recebi resposta',interview:'Entrevista',offer:'Proposta',hired:'Contratado',rejected:'Rejeitado',withdrawn:'Desisti',closed:'Vaga encerrada'};
export function urlKey(value) {
  try { const u = new URL(value); u.hash = ''; u.hostname = u.hostname.toLowerCase(); u.pathname = u.pathname.replace(/\/$/, '') || '/'; u.searchParams.sort(); return u.href; }
  catch { return norm(value).trim(); }
}
export function domainKey(value) { return norm(value).replace(/^https?:\/\//, '').replace(/^www\./, '').replace(/\/$/, ''); }
export function expand(records, tab) {
  if (['overview', 'companies', 'runs'].includes(tab)) return records;
  return records.flatMap(r => {
    const values = ['jobs','applications'].includes(tab) ? r.jobs : tab === 'emails' ? r.emails : r.careers;
    if (tab === 'applications') return values.filter(j=>j.application?.tracked).map((value,i)=>({...r,item:value,item_id:`${r.id}:${tab}:${i}`}));
    return values.map((value, i) => ({ ...r, item: value, item_id: `${r.id}:${tab}:${i}` }));
  });
}
export function reached(a,stage){
  const groups={applied:['applied','responded','interview','offer','hired','rejected'],responded:['responded','interview','offer','hired','rejected'],interview:['interview','offer','hired'],offer:['offer','hired']};
  const states=groups[stage] || [stage];
  return !!a[stage==='responded'?'response_at':stage+'_at'] || states.includes(a.status) || (a.history || []).some(e=>states.includes(e.changes?.status?.to));
}
export function applicationMatches(job, f) {
  const a = job.application || {status:'new',priority:'normal'}, s = job.score || {};
  if(f.applicationStatus && a.status!==f.applicationStatus || f.priority && a.priority!==f.priority) return false;
  if(f.rejectionStage && a.rejection_stage!==f.rejectionStage) return false;
  if(f.milestone && (f.milestone==='rejected'?a.status!=='rejected':!reached(a,f.milestone)))return false;
  if(f.hasResponse && reached(a,'responded') !== (f.hasResponse==='yes')) return false;
  if(f.scoreStatus && (s.total !== null && s.total !== undefined) !== (f.scoreStatus==='scored')) return false;
  if(f.minScore && (s.total==null || s.total<Number(f.minScore)) || f.maxScore && (s.total==null || s.total>Number(f.maxScore))) return false;
  if(f.appliedFrom && (!a.applied_at || a.applied_at<f.appliedFrom) || f.appliedTo && (!a.applied_at || a.applied_at>f.appliedTo)) return false;
  if(f.followUp && (!a.follow_up_at || a.follow_up_at>f.followUp || ['rejected','hired','withdrawn','closed'].includes(a.status))) return false;
  return true;
}
function preMatchMatches(job, status) {
  if (!status) return true;
  const classification=job.pre_match?.classification || 'unscored';
  return classification===status;
}
export function reviewQueueGroup(job) {
  if(job?.application_decision?.recommendation!=='review')return null;
  if(job.description_quality?.status==='insufficient')return 'insufficient_data';
  const risks=job.application_decision?.risks||{};
  if(['location','work_model','work_authorization','hard_requirements'].some(key=>risks[key]?.status==='conflict'))
    return 'review_later';
  if(job.description_quality?.status==='complete'&&job.ai_analysis_eligibility?.status==='eligible'
      &&['possible_candidate','strong_candidate'].includes(job.pre_match?.classification)&&!job.profile_analysis)
    return 'review_first';
  return 'review_later';
}
function desiredRoleFamily(value) {
  let text=norm(value).replace(/\([^)]*\)/g,' ').replace(/\bops\b/g,'operations');
  text=text.replace(/\bcustomer support operations\b/g,'support operations')
    .replace(/\bcommunity moderation\b/g,'community moderator')
    .replace(/\bp2p dispute resolution\b/g,'p2p dispute')
    .replace(/\bp2p dispute (?:resolution )?(?:moderator|specialist|moderation)\b/g,'p2p dispute')
    .replace(/\bp2p moderator\b/g,'p2p dispute');
  const qualifiers=new Set(['senior','sr','lead','head','manager','director','associate','specialist','staff','principal']);
  let tokens=text.match(/[a-z0-9]+/g)||[];
  tokens=tokens.filter(token=>!qualifiers.has(token));
  if(tokens.length>1) tokens=tokens.filter(token=>!['global','regional','latam','emea','apac'].includes(token));
  return tokens.join(' ');
}
export function desiredRoleMatches(jobTitle, desiredRoles=[]) {
  const title=desiredRoleFamily(jobTitle);
  if(!title||!Array.isArray(desiredRoles))return false;
  return desiredRoles.some(role=>{
    const desired=desiredRoleFamily(role);
    return !!desired&&title===desired;
  });
}
const RECRUITING_MAILBOXES=new Set(['jobs','careers','career','recruiting','recruitment','talent','talentacquisition','hr','humanresources','people','peopleops']);
const GENERAL_MAILBOXES=new Set(['contact','hello','info']);
export function validCompanyEmails(emails=[]) {
  return (Array.isArray(emails)?emails:[]).filter(value=>typeof value==='string'&&/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim()));
}
export function bestContactEmail(emails=[]) {
  return validCompanyEmails(emails).sort((a,b)=>{
    const rank=value=>{
      const local=value.trim().split('@')[0].toLowerCase().split('+')[0];
      return RECRUITING_MAILBOXES.has(local)?0:GENERAL_MAILBOXES.has(local)?1:2;
    };
    return rank(a)-rank(b)||a.localeCompare(b);
  })[0]||'';
}
export function coldEmailCategory(record) {
  const hasEmail=validCompanyEmails(record?.emails).length>0;
  if((Array.isArray(record?.careers)&&record.careers.length>0)||(Array.isArray(record?.jobs)&&record.jobs.length>0))return 'careers';
  if(!hasEmail)return 'no_email';
  const verified=record?.alive===true&&['OK','OK_BROWSER'].includes(record?.status)
    &&Number(record?.pages)>0&&!String(record?.error||'').trim();
  return verified?'opportunity':'unknown';
}
export function coldEmailFilterMatches(record,filter) {
  if(!filter)return true;
  const hasEmail=validCompanyEmails(record?.emails).length>0;
  const hasCareers=(Array.isArray(record?.careers)&&record.careers.length>0)
    ||(Array.isArray(record?.jobs)&&record.jobs.length>0);
  if(filter==='no_email')return !hasEmail;
  if(filter==='careers')return hasCareers;
  if(filter==='opportunity')return hasEmail&&coldEmailCategory(record)==='opportunity';
  return true;
}
function reviewQueueDate(row) {
  const published=Date.parse(row.item?.date_posted||'');
  const observed=Date.parse(row.date||'');
  return Number.isFinite(published)?published:Number.isFinite(observed)?observed:0;
}
function timestamp(value) {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  const text=String(value).trim();
  if (!text) return null;
  const local=text.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?)?$/);
  if (local) {
    const [,day,month,year,hour='0',minute='0',second='0',fraction='0']=local;
    return Date.UTC(Number(year),Number(month)-1,Number(day),Number(hour),Number(minute),Number(second),Number((fraction+'000').slice(0,3)));
  }
  const parsed=Date.parse(text);
  return Number.isFinite(parsed)?parsed:null;
}
function compareNullable(a,b,direction) {
  if (a===null) return b===null?0:1;
  if (b===null) return -1;
  return direction*(a-b);
}
function scoreValue(row) {
  const items=row.item?.url?[row.item]:row.jobs;
  const scores=items.map(job=>job.profile_analysis?.profile_score ?? job.score?.total)
    .filter(value=>value!==null&&value!==undefined&&String(value).trim()!=='').map(Number).filter(Number.isFinite);
  return scores.length?Math.max(...scores):null;
}
function jobDateValue(row,field,latest=false) {
  const items=row.item?.url?[row.item]:(row.jobs||[]);
  const values=items.map(item=>timestamp(item.application?.[field])).filter(value=>value!==null);
  return values.length?(latest?Math.max(...values):Math.min(...values)):null;
}
function applicationText(job) {
  const a=job.application || {},s=job.score || {};
  return [a.status,applicationLabels[a.status || 'new'],a.notes,a.contact,a.channel,a.rejection_reason,a.resume_version,s.explanation,...(s.strengths || []),...(s.gaps || [])].join(' ');
}
export function searchText(r) {
  return norm([r.domain, r.name, r.type, r.description, r.canonical, r.status, r.run_id, r.date, r.model, r.version, r.input_file,
    r.error, ...r.emails, ...r.careers, ...r.evidence, ...r.jobs.flatMap(j => [j.url, j.title, applicationText(j)]),
    r.item?.title, r.item?.url, typeof r.item === 'string' ? r.item : ''].join(' '));
}
export function matches(r, f, tab) {
  const jobTab=['jobs','applications'].includes(tab);
  if(jobTab ? !applicationMatches(r.item,f) : !r.jobs.some(j=>applicationMatches(j,f)) && ['applicationStatus','priority','rejectionStage','hasResponse','scoreStatus','minScore','maxScore','appliedFrom','appliedTo','followUp','milestone'].some(k=>f[k]))return false;
  if (tab === 'jobs' && !['preMatchStatus','qualityStatus','eligibilityStatus','recommendationStatus','userDecisionStatus','reviewQueueStatus','desiredRoleStatus'].some(k=>f[k])) {
    if (!preMatchMatches(r.item, f.preMatchStatus)) return false;
    if (f.qualityStatus && (r.item.description_quality?.status || 'unscored') !== f.qualityStatus) return false;
    if (f.eligibilityStatus && (r.item.ai_analysis_eligibility?.status || 'unscored') !== f.eligibilityStatus) return false;
    if (f.recommendationStatus && (r.item.application_decision?.recommendation || 'unscored') !== f.recommendationStatus) return false;
    if (f.userDecisionStatus && (r.item.user_decision?.user_decision || 'not_reviewed') !== f.userDecisionStatus) return false;
  }
  if (f.runs.length && !f.runs.includes(r.run_id)) return false;
  if (f.status && r.status !== f.status || f.type && r.type !== f.type) return false;
  for (const key of ['model', 'version', 'input_file']) if (f[key] && r[key] !== f[key]) return false;
  if (f.domain && !norm(`${r.domain} ${r.name}`).includes(norm(f.domain))) return false;
  const day = r.date.slice(0, 10);
  if ((f.from || f.to) && !day) return false;
  if (f.from && day < f.from || f.to && day > f.to) return false;
  if (f.alive && r.alive !== (f.alive === 'yes')) return false;
  if (f.ai && (f.ai === 'unknown' ? r.ai !== null : r.ai !== (f.ai === 'yes'))) return false;
  for (const [key, value] of [['hasEmails', r.emails.length > 0], ['hasJobs', r.jobs.length > 0], ['hasCareers', r.careers.length > 0], ['hasError', !!r.error]]) {
    if (f[key] && value !== (f[key] === 'yes')) return false;
  }
  for (const [key, value] of [['minPages', r.pages], ['minJobs', r.jobs.length], ['minEmails', r.emails.length]]) {
    if (f[key] !== '' && value < Number(f[key])) return false;
  }
  for (const [key, value] of [['maxPages', r.pages], ['maxJobs', r.jobs.length], ['maxEmails', r.emails.length]]) {
    if (f[key] !== '' && value > Number(f[key])) return false;
  }
  const emailText = tab === 'emails' ? r.item : r.emails.join(' ');
  const jobText = jobTab ? r.item.title : r.jobs.map(j => j.title).join(' ');
  const urls = jobTab ? r.item.url : tab === 'careers' ? r.item : [r.canonical, ...r.evidence, ...r.careers, ...r.jobs.map(j => j.url)].join(' ');
  if (f.email && !norm(emailText).includes(norm(f.email))) return false;
  if (f.job && !norm(jobText).includes(norm(f.job))) return false;
  if (f.url && !norm(urls).includes(norm(f.url))) return false;
  // In detail tables, search the current item plus company/run context, not sibling items.
  const text = ['jobs', 'applications', 'emails', 'careers'].includes(tab)
    ? norm([r.domain, r.name, r.type, r.description, r.status, r.run_id, r.date, r.error, r.model, r.version, r.input_file,
      r.item?.title, r.item?.url, jobTab ? applicationText(r.item) : '', typeof r.item === 'string' ? r.item : ''].join(' '))
    : searchText(r);
  return norm(f.search).trim().split(/\s+/).every(word => text.includes(word));
}
export function identity(r, tab) {
  if (tab === 'emails') return norm(r.item).trim();
  if (['jobs','applications'].includes(tab)) return r.item.key || urlKey(r.item.url);
  if (tab === 'careers') return urlKey(r.item);
  return domainKey(r.domain);
}
export function query(records, f, tab, desiredRoles=[]) {
  // Resolve the visible occurrence for each job before filtering by quality.
  // Otherwise an older hash with (for example) "insufficient" can survive
  // the filter even when the latest occurrence is complete.
  const stateFilter=tab==='jobs'&&(f.preMatchStatus||f.qualityStatus||f.eligibilityStatus||f.recommendationStatus||f.userDecisionStatus||f.reviewQueueStatus||f.desiredRoleStatus);
  const matchingFilters=stateFilter?{...f,preMatchStatus:'',qualityStatus:'',eligibilityStatus:'',recommendationStatus:'',userDecisionStatus:'',reviewQueueStatus:'',desiredRoleStatus:''}:f;
  const filtered = expand(records, tab).filter(r => matches(r, matchingFilters, tab));
  let rows = filtered;
  if (f.dedup && tab !== 'runs') {
    const groups = new Map();
    for (const r of filtered) {
      const key = identity(r, tab), old = groups.get(key);
      const timestamp = `${r.date}|${r.run_id}|${r.id}`;
      const previous = old && `${old.date}|${old.run_id}|${old.id}`;
      if (!old || (f.keep === 'earliest' ? timestamp < previous : timestamp > previous)) groups.set(key, r);
    }
    rows = [...groups.values()];
  }
  if(stateFilter){
    if(f.preMatchStatus)rows=rows.filter(r=>(r.item.pre_match?.classification||'unscored')===f.preMatchStatus);
    if(f.qualityStatus)rows=rows.filter(r=>(r.item.description_quality?.status||'unscored')===f.qualityStatus);
    if(f.eligibilityStatus)rows=rows.filter(r=>(r.item.ai_analysis_eligibility?.status||'unscored')===f.eligibilityStatus);
    if(f.recommendationStatus)rows=rows.filter(r=>(r.item.application_decision?.recommendation||'unscored')===f.recommendationStatus);
    if(f.userDecisionStatus)rows=rows.filter(r=>(r.item.user_decision?.user_decision||'not_reviewed')===f.userDecisionStatus);
    if(f.reviewQueueStatus)rows=rows.filter(r=>reviewQueueGroup(r.item)===f.reviewQueueStatus);
    if(f.desiredRoleStatus)rows=desiredRoles.length?rows.filter(r=>desiredRoleMatches(r.item?.title,desiredRoles)===(f.desiredRoleStatus==='compatible')):[];
  }
  if(tab==='companies'&&f.coldEmailStatus)rows=rows.filter(row=>coldEmailFilterMatches(row,f.coldEmailStatus));
  if(f.sort==='review_queue')rows=[...rows].sort((a,b)=>{
    const order={review_first:0,review_later:1,insufficient_data:2};
    const rank=row=>order[reviewQueueGroup(row.item)]??3;
    return rank(a)-rank(b)||reviewQueueDate(b)-reviewQueueDate(a)
      ||norm(a.item?.title).localeCompare(norm(b.item?.title))
      ||String(a.item?.url||'').localeCompare(String(b.item?.url||''));
  });
  else if(['score_desc','score_asc'].includes(f.sort)) {
    const direction=f.sort==='score_desc'?-1:1;
    rows=[...rows].sort((a,b)=>compareNullable(scoreValue(a),scoreValue(b),direction));
  }
  else if(['applied_newest','applied_oldest','followup','followup_desc','execution_newest','execution_oldest'].includes(f.sort)) {
    const descending=['applied_newest','followup_desc','execution_newest'].includes(f.sort);
    const direction=descending?-1:1;
    const value=row=>f.sort.startsWith('execution_')?timestamp(row.date):jobDateValue(row,f.sort.startsWith('applied_')?'applied_at':'follow_up_at',
      ['applied_newest','followup_desc'].includes(f.sort));
    rows=[...rows].sort((a,b)=>compareNullable(value(a),value(b),direction));
  }
  else rows = [...rows].sort((a, b) => {
    if(['application_updated','priority','pre_match'].includes(f.sort)) {
      const value=r=>{
        const jobs=r.item?.url?[r.item]:r.jobs;
        if(f.sort==='priority')return jobs.reduce((n,j)=>Math.max(n,{low:1,normal:2,high:3}[j.application?.priority] || 0),0);
        if(f.sort==='pre_match')return jobs.reduce((n,j)=>Math.min(n,{strong_candidate:0,possible_candidate:1,weak_candidate:2}[j.pre_match?.classification] ?? 3),3);
        const field='updated_at';
        const vals=jobs.map(j=>j.application?.[field]).filter(Boolean).sort();
        return vals.at(-1) || '';
      };
      const x=value(a),y=value(b);
      if(f.sort==='priority')return y-x;
      if(f.sort==='pre_match')return x-y || b.date.localeCompare(a.date);
      return String(y).localeCompare(String(x));
    }
    if (f.sort === 'oldest') return a.date.localeCompare(b.date) || a.run_id.localeCompare(b.run_id);
    if (f.sort === 'newest') return b.date.localeCompare(a.date) || b.run_id.localeCompare(a.run_id);
    if (f.sort === 'name' || f.sort === 'domain') return norm(a[f.sort]).localeCompare(norm(b[f.sort])) || b.date.localeCompare(a.date);
    const score = r => f.sort === 'jobs' ? r.jobs.length : f.sort === 'emails' ? r.emails.length : r.pages;
    return score(b) - score(a) || b.date.localeCompare(a.date);
  });
  return { rows, raw: filtered.length, hidden: filtered.length - rows.length };
}
export function csvCell(value) {
  let text = String(value ?? '');
  // A collected site title must not become a spreadsheet formula.
  if (/^[\s]*[=+@-]/.test(text)) text = "'" + text;
  return '"' + text.replaceAll('"', '""') + '"';
}
export function makeCsv(headers, rows) {
  return '\ufeff' + [headers, ...rows].map(row => row.map(csvCell).join(',')).join('\r\n');
}
