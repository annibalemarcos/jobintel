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
function reviewQueueDate(row) {
  const published=Date.parse(row.item?.date_posted||'');
  const observed=Date.parse(row.date||'');
  return Number.isFinite(published)?published:Number.isFinite(observed)?observed:0;
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
  if (tab === 'jobs' && !['preMatchStatus','qualityStatus','eligibilityStatus','recommendationStatus','userDecisionStatus','reviewQueueStatus'].some(k=>f[k])) {
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
export function query(records, f, tab) {
  // Resolve the visible occurrence for each job before filtering by quality.
  // Otherwise an older hash with (for example) "insufficient" can survive
  // the filter even when the latest occurrence is complete.
  const stateFilter=tab==='jobs'&&(f.preMatchStatus||f.qualityStatus||f.eligibilityStatus||f.recommendationStatus||f.userDecisionStatus||f.reviewQueueStatus);
  const matchingFilters=stateFilter?{...f,preMatchStatus:'',qualityStatus:'',eligibilityStatus:'',recommendationStatus:'',userDecisionStatus:'',reviewQueueStatus:''}:f;
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
  }
  if(f.sort==='review_queue')rows=[...rows].sort((a,b)=>{
    const order={review_first:0,review_later:1,insufficient_data:2};
    const rank=row=>order[reviewQueueGroup(row.item)]??3;
    return rank(a)-rank(b)||reviewQueueDate(b)-reviewQueueDate(a)
      ||norm(a.item?.title).localeCompare(norm(b.item?.title))
      ||String(a.item?.url||'').localeCompare(String(b.item?.url||''));
  });
  else rows = [...rows].sort((a, b) => {
    if(['score_desc','score_asc','application_updated','applied_newest','followup','priority','pre_match'].includes(f.sort)) {
      const value=r=>{
        const jobs=r.item?.url?[r.item]:r.jobs;
        if(f.sort.startsWith('score'))return jobs.reduce((n,j)=>Math.max(n,j.score?.total ?? -1),-1);
        if(f.sort==='priority')return jobs.reduce((n,j)=>Math.max(n,{low:1,normal:2,high:3}[j.application?.priority] || 0),0);
        if(f.sort==='pre_match')return jobs.reduce((n,j)=>Math.min(n,{strong_candidate:0,possible_candidate:1,weak_candidate:2}[j.pre_match?.classification] ?? 3),3);
        const field=f.sort==='application_updated'?'updated_at':f.sort==='applied_newest'?'applied_at':'follow_up_at';
        const vals=jobs.map(j=>j.application?.[field]).filter(Boolean).sort();
        return f.sort==='followup'?vals[0] || '9999':vals.at(-1) || '';
      };
      const x=value(a),y=value(b);
      if(f.sort==='score_asc')return (x===-1?101:x)-(y===-1?101:y);
      if(f.sort==='score_desc'||f.sort==='priority')return y-x;
      if(f.sort==='pre_match')return x-y || b.date.localeCompare(a.date);
      return f.sort==='followup'?String(x).localeCompare(String(y)):String(y).localeCompare(String(x));
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
