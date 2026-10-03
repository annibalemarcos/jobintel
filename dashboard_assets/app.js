import {query, norm, identity, makeCsv, reviewQueueGroup} from './logic.js';
import {initTracking, renderApplicationOverview, openApplication, applicationBadge, scoreBadge, statuses} from './tracking.js';
import {exportProfileJson, importProfileJson} from './profile_io.js';
import {analysisText, safeFilename} from './profile_analysis_export.js';
import {materialsFilename, materialsText} from './application_materials_io.js';

const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const count = value => Number(value).toLocaleString('pt-BR');
const labels = {overview:'Visão geral', companies:'Empresas', jobs:'Vagas', applications:'Candidaturas', emails:'Contatos', careers:'Carreiras', runs:'Execuções', profile:'Perfil'};
const tableTitles = {overview:'Empresas coletadas', companies:'Empresas coletadas', jobs:'Vagas e candidaturas encontradas', applications:'Meu acompanhamento de vagas', emails:'E-mails encontrados', careers:'Páginas de carreira', runs:'Histórico de execuções'};
const fields = ['search','status','type','sort','domain','from','to','alive','ai','hasEmails','hasJobs','hasCareers','hasError','email','job','url','minPages','maxPages','minJobs','maxJobs','minEmails','maxEmails','model','version','input_file','keep','applicationStatus','priority','scoreStatus','preMatchStatus','qualityStatus','eligibilityStatus','recommendationStatus','userDecisionStatus','reviewQueueStatus','hasResponse','minScore','maxScore','appliedFrom','appliedTo','followUp','rejectionStage','milestone'];
const filterLabels = {search:'Busca',status:'Status',type:'Categoria',domain:'Empresa',from:'De',to:'Até',alive:'Acesso',ai:'IA',hasEmails:'E-mails',hasJobs:'Vagas',hasCareers:'Carreiras',hasError:'Erros',email:'E-mail contém',job:'Cargo contém',url:'URL contém',minPages:'Mín. páginas',maxPages:'Máx. páginas',minJobs:'Mín. vagas',maxJobs:'Máx. vagas',minEmails:'Mín. e-mails',maxEmails:'Máx. e-mails',model:'Modelo',version:'Versão',input_file:'Arquivo de entrada'};
Object.assign(filterLabels,{applicationStatus:'Candidatura',priority:'Prioridade',scoreStatus:'Score',preMatchStatus:'Pré-match',qualityStatus:'Qualidade da descrição',eligibilityStatus:'Elegibilidade IA',recommendationStatus:'Recomendação',userDecisionStatus:'Minha decisão',reviewQueueStatus:'Fila de revisão',hasResponse:'Resposta',minScore:'Score mín.',maxScore:'Score máx.',appliedFrom:'Apliquei de',appliedTo:'Apliquei até',followUp:'Retorno até',rejectionStage:'Rejeição',milestone:'Etapa alcançada'});
let data = {records:[],runs:[],warnings:[]}, tab = 'overview', page = 1, current = [], queryResult = null, busy = false, firstLoad = true;
let loadedSuccessfully = false;
let profileState = null;
let visibleProfileAnalysis = null;
let materialsState = null;

function filters() {
  const f = Object.fromEntries(fields.map(key => [key, $(key).value]));
  f.runs = [...$('run').selectedOptions].map(o => o.value).filter(Boolean);
  f.dedup = $('dedup').checked;
  return f;
}
function persist() {
  const f = filters();
  const state = {tab, ...f, pageSize:$('page-size').value, auto:$('auto').checked};
  const encoded = new URLSearchParams({view:JSON.stringify(state)}).toString();
  history.replaceState(null, '', `${location.pathname}#${encoded}`);
}
function restore() {
  try {
    const state = JSON.parse(new URLSearchParams(location.hash.slice(1)).get('view') || '{}');
    if (labels[state.tab]) tab = state.tab;
    for (const key of fields) if (typeof state[key] === 'string') $(key).value = state[key];
    if (typeof state.dedup === 'boolean') $('dedup').checked = state.dedup;
    if (typeof state.auto === 'boolean') $('auto').checked = state.auto;
    if (['25','50','100'].includes(state.pageSize)) $('page-size').value = state.pageSize;
    return state;
  } catch { return {}; }
}
const restored = restore();
function dateLabel(date, time = false) {
  if (!date) return 'Data não informada';
  const d = new Date(date);
  return Number.isNaN(d.getTime()) ? date : d.toLocaleDateString('pt-BR') + (time ? ` · ${d.toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'})}` : '');
}
function link(url, label = url) {
  try { const u = new URL(url); if (!['https:','http:'].includes(u.protocol)) return esc(label); }
  catch { return esc(label || '—'); }
  return `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)} ↗</a>`;
}
function statusBadge(r) {
  const names = {OK:'Acessível',OK_BROWSER:'Via navegador',ANTI_BOT:'Anti-bot',ROBOTS_DENIED:'Bloqueado por robots.txt',NO_RESPONSE:'Sem resposta',ERROR:'Erro interno',UNKNOWN:'Não informado',NOT_CHECKED:'Não verificado'};
  const css = r.alive ? 'good' : ['ANTI_BOT','ROBOTS_DENIED'].includes(r.status) ? 'warn' : r.status === 'NOT_CHECKED' ? '' : 'bad';
  return `<span class="badge ${css}" title="${esc(r.status)}">${esc(names[r.status] || r.status)}</span>`;
}
function runState(r) {
  if (r.completed === true) return '<span class="badge good">Concluída</span>';
  if (r.completed === false) return '<span class="badge warn">Em andamento / interrompida</span>';
  return '<span class="badge">Não informado</span>';
}
function aiLabel(ai) { return ai === null ? 'Não informada' : ai ? 'Habilitada¹' : 'Desabilitada'; }
function company(r) {
  return `<div class="company"><span class="avatar">${esc(r.domain.slice(0,2).toUpperCase())}</span><div><span class="company-name" title="${esc(r.name)}">${esc(r.name)}</span><span class="subtext">${esc(r.domain)}</span></div></div>`;
}
function updateOptions() {
  const selectedRuns = firstLoad ? restored.runs || [] : filters().runs;
  $('run').innerHTML = '<option value="">Todas as execuções</option>' + data.runs.map(r => `<option value="${esc(r.id)}">${esc(dateLabel(r.date,true))} · ${esc(r.id)} (${r.count})</option>`).join('');
  for (const o of $('run').options) o.selected = selectedRuns.includes(o.value);
  if (!$('run').selectedOptions.length) $('run').options[0].selected = true;
  const optionTitles = {status:'Todos os status',type:'Todas as categorias',model:'Todos os modelos',version:'Todas as versões',input_file:'Todos os arquivos'};
  for (const key of Object.keys(optionTitles)) {
    const value = firstLoad && restored[key] ? restored[key] : $(key).value;
    const options = [...new Set(data.records.map(r => r[key]).filter(Boolean))].sort();
    $(key).innerHTML = `<option value="">${optionTitles[key]}</option>` + options.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
    if (options.includes(value)) $(key).value = value;
  }
}
async function load(manual = false) {
  if (busy) return;
  busy = true; $('refresh').disabled = true; $('refresh').textContent = 'Atualizando…';
  try {
    const response = await fetch('/api/data', {cache:'no-store'});
    if (!response.ok) throw new Error(`Resposta ${response.status}`);
    data = await response.json();
    updateOptions(); firstLoad = false; loadedSuccessfully = true;
    $('updated').textContent = `Atualizado às ${new Date(data.updated_at).toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'})}`;
    const messages = [...data.warnings];
    if (!data.runs.length) messages.push('Nenhuma execução encontrada. Rode o crawler e clique em Atualizar; as pastas de saída serão reconhecidas automaticamente.');
    $('notice').hidden = !messages.length; $('notice').textContent = messages.join(' ');
    render();
    if(tab==='profile'&&!profileState)loadProfile();
    if (manual) toast('Resultados atualizados.');
  } catch (error) {
    $('notice').hidden = false;
    $('notice').textContent = `Não foi possível atualizar os dados. Verifique se o dashboard continua aberto no terminal. ${loadedSuccessfully ? 'Os últimos dados carregados foram mantidos.' : ''} (${error.message})`;
    if (!loadedSuccessfully) { $('result-count').textContent = 'Falha no carregamento'; $('tbody').innerHTML = ''; }
  } finally { busy = false; $('refresh').disabled = false; $('refresh').textContent = '↻ Atualizar'; }
}
function activeFilters(f) {
  return Object.keys(filterLabels).filter(k => f[k] !== '').map(k => ({key:k,label:`${filterLabels[k]}: ${f[k]}`}))
    .concat(f.runs.map(id => ({key:'run',value:id,label:`Execução: ${id}`})));
}
function render() {
  const f = filters();
  document.querySelectorAll('nav button').forEach(b => { b.classList.toggle('active', b.dataset.tab === tab); b.setAttribute('aria-current',b.dataset.tab === tab ? 'page' : 'false'); });
  $('title').textContent = tab==='profile'?'Meu perfil profissional':labels[tab]; $('breadcrumb').textContent = labels[tab]; if(tableTitles[tab])$('table-title').textContent = tableTitles[tab];
  $('subtitle').textContent = tab === 'profile' ? 'Defina seus objetivos, experiência e preferências para encontrar oportunidades compatíveis.' : tab === 'runs' ? 'Compare o histórico e veja quais execuções contribuíram para seus resultados.' : 'Explore empresas, contatos e oportunidades em todas as suas coletas.';
  $('dashboard-view').hidden=tab==='profile'; $('profile-page').hidden=tab!=='profile';
  $('new-application').hidden=tab==='profile'; $('export').hidden=tab==='profile';
  $('run-prematch').hidden=tab!=='jobs';
  $('run-profile-analysis').hidden=tab!=='jobs';
  $('pre-match-filter').hidden=tab!=='jobs';
  $('description-quality-filter').hidden=tab!=='jobs';
  $('ai-eligibility-filter').hidden=tab!=='jobs';
  $('application-recommendation-filter').hidden=tab!=='jobs';
  $('user-decision-filter').hidden=tab!=='jobs';
  $('review-queue-filter').hidden=tab!=='jobs';
  if(tab==='profile') { persist(); return; }
  queryResult = query(data.records, f, tab);
  $('dedup-help').textContent = tab === 'emails' ? 'Uma ocorrência por e-mail, entre empresas e execuções.' : ['jobs','applications'].includes(tab) ? 'Uma ocorrência por URL de vaga. O acompanhamento é compartilhado entre execuções.' : tab === 'careers' ? 'Uma ocorrência por URL de carreira.' : tab === 'runs' ? 'Execuções são únicas; esta opção não se aplica aqui.' : 'Uma ocorrência por domínio, entre execuções.';
  $('dedup').disabled = tab === 'runs'; $('keep').disabled = !f.dedup || tab === 'runs';
  const active = activeFilters(f);
  $('filter-count').textContent = active.length ? `(${active.length})` : '';
  $('chips').innerHTML = active.map((a,i) => `<button class="chip" data-chip="${i}" title="Remover filtro">${esc(a.label)} ×</button>`).join('');
  $('chips').querySelectorAll('button').forEach(b => b.onclick = () => {
    const a = active[Number(b.dataset.chip)];
    if (a.key === 'run') { for (const o of $('run').options) if (o.value === a.value) o.selected = false; }
    else $(a.key).value = '';
    page = 1; render();
  });
  const distinct = [...new Map(queryResult.rows.map(r => [r.id, r])).values()];
  const metrics = [
    ['Empresas',new Set(distinct.map(r => norm(r.domain))).size,`${count(distinct.length)} observações no recorte`,'▦'],
    ['Sites acessíveis',new Set(distinct.filter(r => r.alive).map(r => norm(r.domain))).size,'Conforme status da coleta','◉'],
    ['E-mails',tab === 'emails' ? queryResult.rows.length : distinct.reduce((n,r) => n+r.emails.length,0),'Contatos encontrados · não verificados','＠'],
    ['Vagas / candidaturas',['jobs','applications'].includes(tab) ? queryResult.rows.length : distinct.reduce((n,r) => n+r.jobs.length,0),'Inclui cadastros de interesse','▣'],
  ];
  $('stats').innerHTML = metrics.map(([label,value,detail,icon]) => `<article class="stat"><div class="stat-label">${label}</div><span class="stat-icon">${icon}</span><strong>${count(value)}</strong><small>${detail}</small></article>`).join('');
  $('charts').hidden = tab !== 'overview';
  if (tab === 'overview') renderCharts(distinct);
  renderApplicationOverview(data,f,tab==='overview');
  current = tab === 'runs' ? runRows(queryResult.rows, f) : queryResult.rows;
  const size = Number($('page-size').value), maxPage = Math.max(1, Math.ceil(current.length/size));
  page = Math.min(page, maxPage);
  const start = (page-1)*size, visible = current.slice(start,start+size);
  $('result-count').textContent = tab === 'runs'
    ? `${count(current.length)} execuções · contagens correspondem ao recorte filtrado`
    : `${count(current.length)} resultados · ${count(queryResult.hidden)} repetidos ocultos · ${count(new Set(queryResult.rows.map(r=>r.run_id)).size)} execuções`;
  renderTable(visible);
  $('empty').hidden = current.length > 0;
  $('page-info').textContent = current.length ? `${start+1}–${Math.min(start+size,current.length)} de ${count(current.length)}` : '0 resultados';
  $('page-number').textContent = `${page} / ${maxPage}`;
  $('prev').disabled = page === 1; $('next').disabled = page >= maxPage;
  $('export').disabled = !current.length;
  persist();
}
function runRows(rows, f) {
  const groups = new Map();
  for (const r of rows) { if (!groups.has(r.run_id)) groups.set(r.run_id, []); groups.get(r.run_id).push(r); }
  let runs = data.runs.filter(r => groups.has(r.id) || (!data.records.some(x => x.run_id === r.id) && !activeFilters(f).length)).map(r => {
    const matching = groups.get(r.id) || [];
    return {...r, matching:matching.length, matchedJobs:matching.reduce((n,x)=>n+x.jobs.length,0), matchedEmails:matching.reduce((n,x)=>n+x.emails.length,0),matchedPages:matching.reduce((n,x)=>n+x.pages,0)};
  });
  runs.sort((a,b) => f.sort === 'oldest' ? a.date.localeCompare(b.date) : f.sort === 'name' || f.sort === 'domain' ? a.id.localeCompare(b.id) : f.sort === 'jobs' ? b.matchedJobs-a.matchedJobs : f.sort === 'emails' ? b.matchedEmails-a.matchedEmails : f.sort === 'pages' ? b.matchedPages-a.matchedPages : b.date.localeCompare(a.date));
  return runs;
}
function renderCharts(rows) {
  const access = new Map(), runs = new Map();
  for (const r of rows) { const key = r.status === 'OK' ? 'Acessível' : r.status === 'OK_BROWSER' ? 'Via navegador' : r.status === 'ANTI_BOT' ? 'Anti-bot' : 'Outros / sem resposta'; access.set(key,(access.get(key)||0)+1); runs.set(r.run_id,(runs.get(r.run_id)||0)+1); }
  const chart = (title,subtitle,entries) => `<article class="chart"><h3>${title}</h3><p>${subtitle}</p>${entries.length ? entries.map(([label,value])=>`<div class="bar-row"><span title="${esc(label)}">${esc(label.length>22 ? label.slice(0,20)+'…' : label)}</span><div class="bar-track"><div class="bar" data-value="${value}" data-max="${Math.max(...entries.map(e=>e[1]),1)}"></div></div><strong>${count(value)}</strong></div>`).join('') : '<p>Sem dados neste recorte.</p>'}</article>`;
  $('charts').innerHTML = chart('Acessibilidade dos sites','Distribuição das observações filtradas',[...access]) + chart('Origem dos resultados','Observações por execução · até 5 execuções',[...runs].sort((a,b)=>b[1]-a[1]).slice(0,5));
  $('charts').querySelectorAll('.bar').forEach(bar => bar.style.width = `${100*Number(bar.dataset.value)/Number(bar.dataset.max)}%`);
}
function renderTable(rows) {
  const jobTab=['jobs','applications'].includes(tab);
  const cols = tab === 'runs' ? ['Execução','Data','Estado','Domínios no recorte','E-mails','Vagas','IA',''] : jobTab ? ['Vaga / candidatura','Empresa','Status da candidatura','Score de perfil',...(tab==='jobs'?['Qualidade da descrição','Elegibilidade IA','Recomendação','Minha decisão']:[]),'Pré-match','Prioridade','Apliquei em','Próximo retorno','Execução',''] : tab === 'emails' ? ['E-mail','Empresa','Status','Execução',''] : tab === 'careers' ? ['Página de carreira','Empresa','Status','Execução',''] : ['Empresa','Categoria','Status','E-mails','Vagas','Páginas','Execução',''];
  $('thead').innerHTML = `<tr>${cols.map(c=>`<th scope="col">${c}</th>`).join('')}</tr>`;
  const n = value => `<span class="count ${!value?'zero':''}">${count(value)}</span>`;
  $('tbody').innerHTML = rows.map((r,i) => {
    const run = `<span>${esc(dateLabel(r.date))}</span><span class="subtext" title="${esc(r.run_id)}">${esc(r.run_id)}</span>`;
    const category = `<span class="badge">${esc((r.type || '').startsWith('Unclassified') ? 'Não classificado' : r.type)}</span>`;
    let cells;
    if (tab === 'runs') cells = [`<strong>${esc(r.id)}</strong><span class="subtext">${esc(r.version ? 'Versão '+r.version : 'Versão não informada')}</span>`,dateLabel(r.date,true),runState(r),`${n(r.matching)}<span class="subtext">${r.count} no total da execução</span>`,n(r.matchedEmails),n(r.matchedJobs),`<span class="badge">${aiLabel(r.ai)}</span>`];
    else if (jobTab) cells = [`<div class="item-link">${link(r.item.url,r.item.title || 'Título não informado')}</div>`,company(r),applicationBadge(r.item),profileScoreCell(r.item,i),...(tab==='jobs'?[descriptionQualityBadge(r.item),aiEligibilityBadge(r.item),decisionBadge(r.item,i),userDecisionBadge(r.item,i)]:[]),preMatchBadge(r.item),`<span class="badge">${esc({low:'Baixa',normal:'Normal',high:'Alta'}[r.item.application?.priority] || 'Normal')}</span>`,esc(r.item.application?.applied_at || '—'),esc(r.item.application?.follow_up_at || '—'),run];
    else if (tab === 'emails') cells = [`<span class="item-link">${esc(r.item)}</span><button class="copy" data-copy="${esc(r.item)}">Copiar</button>`,company(r),statusBadge(r),run];
    else if (tab === 'careers') cells = [`<div class="item-link">${link(r.item)}</div>`,company(r),statusBadge(r),run];
    else cells = [company(r),category,statusBadge(r),n(r.emails.length),n(r.jobs.length),n(r.pages),run];
    return `<tr>${cells.map(c=>`<td>${c}</td>`).join('')}<td><button class="detail-button" data-detail="${i}">${jobTab?'Acompanhar':'Detalhes'} →</button></td></tr>`;
  }).join('');
  $('tbody').querySelectorAll('[data-detail]').forEach(b=>b.onclick=()=>tab==='runs' ? showRun(rows[Number(b.dataset.detail)]) : jobTab ? openApplication(rows[Number(b.dataset.detail)]) : showDetail(rows[Number(b.dataset.detail)]));
  $('tbody').querySelectorAll('[data-score-detail]').forEach(b=>b.onclick=()=>showProfileAnalysis(rows[Number(b.dataset.scoreDetail)]));
  $('tbody').querySelectorAll('[data-application-decision]').forEach(b=>b.onclick=()=>showApplicationDecision(rows[Number(b.dataset.applicationDecision)]));
  $('tbody').querySelectorAll('[data-copy]').forEach(b=>b.onclick=()=>copy(b.dataset.copy));
}
function profileScoreCell(job,index) {
  if(!job.profile_analysis)return scoreBadge(job);
  return `<button type="button" class="detail-button score-value" data-score-detail="${index}" title="Ver detalhes da análise">${esc(job.profile_analysis.profile_score)}<small> / 100</small></button>`;
}
function preMatchBadge(job) {
  const match=job.pre_match;
  if(!match)return '<span class="badge">Não executado</span>';
  const labels={strong_candidate:'Forte candidato',possible_candidate:'Possível candidato',weak_candidate:'Fraco candidato'};
  const css=match.classification==='strong_candidate'?'good':match.classification==='weak_candidate'?'warn':'';
  return `<span class="badge ${css}" title="Pré-score interno: ${esc(match.pre_score)}/100 · triagem local">${esc(labels[match.classification]||'Pré-match')}</span>`;
}
function descriptionQualityBadge(job) {
  const quality=job.description_quality;
  const labels={complete:'Completa',partial:'Parcial',insufficient:'Insuficiente'};
  if(!quality)return '<span class="badge">Não avaliada</span>';
  const css=quality.status==='complete'?'good':quality.status==='insufficient'?'warn':'';
  return `<span class="badge ${css}" title="${esc(quality.reason)} · ${esc(quality.score)}/100">${esc(labels[quality.status]||'Não avaliada')}</span>`;
}
function aiEligibilityBadge(job) {
  const result=job.ai_analysis_eligibility;
  const labels={eligible:'Elegível',review:'Revisar',excluded:'Excluída'};
  if(!result)return '<span class="badge">Não avaliada</span>';
  const details=[result.reason,...(result.signals||[]),...(result.exclusion_signals||[])].filter(Boolean).join(' · ');
  const css=result.status==='eligible'?'good':result.status==='excluded'?'warn':'';
  return `<span class="badge ${css}" title="${esc(details)}">${esc(labels[result.status]||'Não avaliada')}</span>`;
}
const decisionLabels={prioritize:'Priorizar',consider:'Considerar',review:'Revisar',low_priority:'Baixa prioridade'};
const manualDecisionLabels={not_reviewed:'Não revisada',want_to_analyze:'Quero analisar',want_to_apply:'Quero me candidatar',do_not_apply:'Não vou me candidatar',archived:'Arquivada'};
function decisionBadge(job,index=null){
  const result=job.application_decision;
  const value=result?.recommendation||'unscored';
  const label=decisionLabels[value]||'Não avaliada';
  const css=value==='prioritize'?'good':value==='low_priority'?'warn':'';
  return `<button type="button" class="badge ${css} decision-badge" ${index===null?'':'data-application-decision="'+index+'"'} title="${esc(result?.primary_reason||'Abrir detalhes da recomendação')}">${esc(label)}</button>`;
}
function userDecisionBadge(job,index=null){
  const result=job.user_decision||{user_decision:'not_reviewed'};
  const value=result.user_decision||'not_reviewed';
  return `<button type="button" class="badge manual-decision-badge" ${index===null?'':'data-application-decision="'+index+'"'} title="Decisão manual do usuário">${esc(manualDecisionLabels[value]||manualDecisionLabels.not_reviewed)}</button>`;
}
function section(title, content) { return `<section class="detail-section"><h3>${esc(title)}</h3>${content}</section>`; }
function showDetail(r) {
  visibleProfileAnalysis=null;$('copy-profile-analysis').hidden=true;$('export-profile-analysis').hidden=true;
  $('detail-title').textContent = r.name;
  const historyRows = data.records.filter(x=>norm(x.domain)===norm(r.domain)).sort((a,b)=>b.date.localeCompare(a.date));
  $('detail-content').innerHTML = `<div>${statusBadge(r)} <span class="badge">${esc(r.type)}</span></div><div class="detail-meta"><div><small>Domínio</small>${link(r.canonical || 'https://'+r.domain,r.domain)}</div><div><small>Execução</small>${esc(r.run_id)}</div><div><small>Data da coleta</small>${esc(dateLabel(r.date,true))}</div><div><small>Páginas visitadas</small>${count(r.pages)}</div></div>`
    + section('Descrição', `<p>${esc(r.description || 'Descrição não gerada nesta coleta.')}</p>`)
    + (r.error ? section('Erro registrado', `<p>${esc(r.error)}</p>`) : '')
    + section(`E-mails (${r.emails.length})`, r.emails.length ? `<ul>${r.emails.map(e=>`<li>${esc(e)}</li>`).join('')}</ul><p class="inline-note">O crawler não registra a página de origem individual nem verifica a existência da caixa de e-mail.</p>` : '<p>Nenhum e-mail encontrado nesta coleta.</p>')
    + section(`Carreiras (${r.careers.length})`,`<ul>${r.careers.map(u=>`<li>${link(u)}</li>`).join('')}</ul>`)
    + section(`Vagas e candidaturas (${r.jobs.length})`,`<ul>${r.jobs.map((j,i)=>`<li>${link(j.url,j.title || 'Título não informado')} ${applicationBadge(j)} <button class="detail-button" data-company-job="${i}">Acompanhar</button><small>${esc(j.url)}</small></li>`).join('')}</ul>`)
    + section(`Evidências (${r.evidence.length})`,`<ul>${r.evidence.map(u=>`<li>${link(u)}</li>`).join('')}</ul>`)
    + section(`Histórico do domínio (${historyRows.length} observações)`,`<p class="inline-note">Histórico completo, independente dos filtros atuais. Use uma execução para ver seus resultados.</p><ul>${historyRows.map(x=>`<li class="history-item"><button class="detail-button" data-history="${esc(x.id)}">${esc(dateLabel(x.date,true))}</button><span>${statusBadge(x)} · ${x.emails.length} e-mails · ${x.jobs.length} vagas</span></li>`).join('')}</ul>`);
  $('detail-content').querySelectorAll('[data-history]').forEach(b=>b.onclick=()=>showDetail(data.records.find(x=>x.id===b.dataset.history)));
  $('detail-content').querySelectorAll('[data-company-job]').forEach(b=>b.onclick=()=>{$('detail').close();openApplication({...r,item:r.jobs[Number(b.dataset.companyJob)]});});
  if (!$('detail').open) $('detail').showModal();
}
function showRun(r) {
  visibleProfileAnalysis=null;$('copy-profile-analysis').hidden=true;$('export-profile-analysis').hidden=true;
  $('detail-title').textContent = 'Execução · '+dateLabel(r.date,true);
  const aiAvailable=r.ai_available===null||r.ai_available===undefined?'Não informado':r.ai_available?'Confirmada no preflight':'Não disponível';
  $('detail-content').innerHTML = `<div class="detail-meta"><div><small>Identificador</small>${esc(r.id)}</div><div><small>Estado</small>${runState(r)}${r.resumed?'<span class="subtext">Execução retomada</span>':''}</div><div><small>Última gravação registrada²</small>${esc(dateLabel(r.finished,true))}</div><div><small>IA configurada¹</small>${aiLabel(r.ai)}</div><div><small>API de IA no início</small>${esc(aiAvailable)}</div><div><small>Modelo informado</small>${esc(r.model || 'Não informado')}</div><div><small>Arquivo de entrada</small>${esc(r.input || 'Não informado')}</div><div><small>Domínios carregados</small>${r.count}</div><div><small>Páginas visitadas</small>${r.pages}</div><div><small>Erros registrados</small>${r.errors}</div></div><p class="inline-note">¹ “IA configurada” registra a intenção da execução; “API de IA no início” registra o resultado do preflight. ² Execuções antigas podem não informar seu estado. As contagens representam a última gravação disponível.</p>${section('Resultados',`<p>${r.alive} acessíveis · ${r.emails} e-mails · ${r.jobs} vagas/candidaturas</p>`)}<button class="button primary" id="use-run">Ver empresas desta execução</button>`;
  $('use-run').onclick=()=>{clear(false); for(const o of $('run').options)o.selected=o.value===r.id;tab='companies';page=1;$('detail').close();render();};
  $('detail').showModal();
}
function showProfileAnalysis(row) {
  const job=row.item,analysis=job.profile_analysis;
  if(!analysis)return;
  visibleProfileAnalysis={job:{...job,name:row.name},analysis};
  $('copy-profile-analysis').hidden=false;$('export-profile-analysis').hidden=false;
  const fitLabels={excellent:'Excelente',strong:'Forte',moderate:'Moderada',weak:'Fraca',poor:'Baixa'};
  const statusLabels={met:'Atendido',partially_met:'Parcialmente atendido',not_met:'Não atendido',unknown:'Desconhecido'};
  const dimLabels={role_fit:'Cargo e responsabilidades',experience_fit:'Experiência',skills_fit:'Competências',seniority_fit:'Senioridade',domain_fit:'Domínio/setor',work_model_fit:'Modelo de trabalho',location_fit:'Localização/elegibilidade',compensation_fit:'Remuneração',contract_fit:'Contrato',language_fit:'Idiomas'};
  const list=(values,empty='Nenhum item registrado.')=>values?.length?`<ul>${values.map(item=>`<li>${esc(item)}</li>`).join('')}</ul>`:`<p class="inline-note">${empty}</p>`;
  const requirements=(values)=>values?.length?`<ul>${values.map(item=>`<li><strong>${esc(item.requirement)}</strong> · ${esc(statusLabels[item.status]||item.status)}<br>${esc(item.evidence)}</li>`).join('')}</ul>`:'<p class="inline-note">Nenhum requisito identificado.</p>';
  const dimensions=Object.entries(analysis.dimensions||{}).map(([key,value])=>`<tr><td>${esc(dimLabels[key]||key)}</td><td>${esc(statusLabels[value.status]||value.status)}</td><td>${value.score==null?'—':`${esc(value.score)} / 100`}</td><td>${esc(value.evidence.join(' · ')||'—')}</td><td>${esc(value.gaps.join(' · ')||'—')}</td><td>${esc(value.uncertainties.join(' · ')||'—')}</td></tr>`).join('');
  const career=analysis.career_value||{};
  $('detail-title').textContent=`Análise de perfil · ${job.title||'Vaga'}`;
  $('detail-content').innerHTML=`<div class="detail-meta"><div><small>Empresa</small>${esc(job.company||row.name)}</div><div><small>Score de perfil</small><strong>${esc(analysis.profile_score)} / 100 · ${esc(fitLabels[analysis.overall_fit]||analysis.overall_fit)}</strong></div><div><small>Perfil analisado</small>${esc(analysis.profile_version_id)}</div><div><small>Modelo · rubrica · data</small>${esc(analysis.model)} · ${esc(analysis.rubric_version)} · ${esc(dateLabel(analysis.analyzed_at,true))}</div></div>`
    +section('Resumo',`<p>${esc(analysis.summary)}</p>`)
    +section('Pontos fortes',list(analysis.strengths))
    +section('Lacunas',list(analysis.gaps))
    +section('Experiência transferível',list(analysis.transferable_experience))
    +section('Requisitos obrigatórios',requirements(analysis.mandatory_requirements))
    +section('Requisitos desejáveis',requirements(analysis.preferred_requirements))
    +section('Incertezas',list(analysis.unknowns))
    +section('Dimensões da rubrica',dimensions?`<div class="table-scroll"><table><thead><tr><th>Dimensão</th><th>Status</th><th>Score</th><th>Evidências</th><th>Lacunas</th><th>Incertezas</th></tr></thead><tbody>${dimensions}</tbody></table></div>`:'<p class="inline-note">Sem dimensões registradas.</p>')
    +section('Valor profissional/carreira',`<p><strong>${esc({high:'Alto',medium:'Médio',low:'Baixo',unknown:'Desconhecido'}[career.rating]||career.rating||'Desconhecido')}</strong> · ${esc(career.rationale||'')}</p><p>Alinhamentos: ${esc(career.aligned_goals?.join(' · ')||'—')}</p><p>Considerações: ${esc(career.cautions?.join(' · ')||'—')}</p>`)
    +section('Por que recebeu esta nota',`<p>${esc(analysis.recommendation_reasoning)}</p>`);
  if(!$('detail').open)$('detail').showModal();
}
function showApplicationDecision(row){
  visibleProfileAnalysis=null;$('copy-profile-analysis').hidden=true;$('export-profile-analysis').hidden=true;
  const job=row.item||{},result=job.application_decision||{},manual=job.user_decision||{user_decision:'not_reviewed',version:0,history:[]};
  const riskLabels={ok:'OK',attention:'Atenção',unknown:'Desconhecido',conflict:'Conflito'};
  const riskNames={location:'Localização',work_model:'Modelo de trabalho',work_authorization:'Autorização de trabalho',
    hard_requirements:'Requisitos obrigatórios',contract:'Contrato',compensation:'Remuneração',language:'Idioma'};
  const list=(values,empty='Nenhum sinal registrado.')=>values?.length?`<ul>${values.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:`<p class="inline-note">${empty}</p>`;
  const risks=Object.entries(riskNames).map(([key,label])=>{const risk=result.risks?.[key]||{status:'unknown',reason:'Informação não disponível.'};return `<li><strong>${esc(label)}: ${esc(riskLabels[risk.status]||'Desconhecido')}</strong><small>${esc(risk.reason||'')}</small></li>`;}).join('');
  const career=result.career_value;
  const careerText=career?`${({high:'Alto',medium:'Médio',low:'Baixo',unknown:'Desconhecido'}[career.rating]||career.rating||'Desconhecido')} · ${career.rationale||''}`:'Profile Fit ainda não disponível.';
  const queueLabels={review_first:'Revisar primeiro',review_later:'Revisar depois',insufficient_data:'Dados insuficientes'};
  const queueGroup=reviewQueueGroup(job);
  const queueBadge=queueGroup?`<span class="badge review-queue-badge">Fila de revisão: ${esc(queueLabels[queueGroup])}</span>`:'';
  const reasons={do_not_apply:{low_fit:'Fit baixo',location:'Localização',compensation:'Remuneração',company:'Empresa',seniority:'Senioridade',technology:'Tecnologia',not_interested:'Não me interessa',other:'Outro'},want_to_apply:{strong_fit:'Fit forte',career_value:'Valor de carreira',interesting_company:'Empresa interessante',strategic_transition:'Transição estratégica',other:'Outro'}};
  const reasonOptions=(decision,selected)=>`<option value="">Sem motivo</option>${Object.entries(reasons[decision]||{}).map(([key,label])=>`<option value="${key}" ${selected===key?'selected':''}>${label}</option>`).join('')}`;
  $('detail-title').textContent=`Decisão de candidatura · ${job.title||'Vaga'}`;
  $('detail-content').innerHTML=section('RECOMENDAÇÃO',`<p>${decisionBadge(job)} ${queueBadge} <span class="subtext">Regra ${esc(result.rule_version||'—')} · ${esc(dateLabel(result.generated_at,true))}</span></p>`)
    +section('POR QUÊ',`<p>${esc(result.primary_reason||'Ainda não avaliada.')}</p>`)
    +section('A FAVOR',list(result.positive_signals))
    +section('CONTRA',list(result.negative_signals))
    +section('PRECISA CONFIRMAR',list(result.unknown_signals))
    +section('RISCOS PRÁTICOS',`<ul>${risks}</ul>`)
    +section('VALOR DE CARREIRA',`<p>${esc(careerText)}</p>`)
    +section('MINHA DECISÃO',`<form id="user-decision-form" class="decision-form"><label>Minha decisão<select name="user_decision">${Object.entries(manualDecisionLabels).map(([key,label])=>`<option value="${key}" ${manual.user_decision===key?'selected':''}>${label}</option>`).join('')}</select></label><label>Motivo<select name="reason">${reasonOptions(manual.user_decision,manual.reason)}</select></label><label>Nota (opcional)<textarea name="note" maxlength="1000" rows="3">${esc(manual.note||'')}</textarea></label><button type="submit" class="button primary">Salvar decisão</button><span class="inline-note">Alterada em ${esc(manual.updated_at?dateLabel(manual.updated_at,true):'—')}. Esta decisão não marca uma candidatura como enviada.</span></form>`)
    +section('HISTÓRICO DA DECISÃO MANUAL',manual.history?.length?`<ul>${manual.history.map(event=>`<li>${esc(dateLabel(event.at,true))}<small>${esc(manualDecisionLabels[event.changes?.from?.user_decision]||'Não revisada')} → ${esc(manualDecisionLabels[event.changes?.to?.user_decision]||'Não revisada')}${event.changes?.to?.reason?` · motivo: ${esc(event.changes.to.reason)}`:''}</small></li>`).join('')}</ul>`:'<p class="inline-note">Ainda não há alterações manuais registradas.</p>');
  const form=$('user-decision-form');
  form.elements.user_decision.onchange=()=>{const selected=form.elements.user_decision.value;form.elements.reason.innerHTML=reasonOptions(selected,'');};
  form.onsubmit=async event=>{
    event.preventDefault();const button=form.querySelector('[type=submit]');button.disabled=true;
    try{
      const response=await fetch('/api/application-decision',{method:'POST',headers:{'Content-Type':'application/json','X-Site-Intel':'dashboard'},
        body:JSON.stringify({url:job.url,decision:{user_decision:form.elements.user_decision.value,reason:form.elements.reason.value,note:form.elements.note.value,version:manual.version||0}})});
      const saved=await response.json();if(!response.ok)throw new Error(saved.error||'Não foi possível salvar a decisão.');
      for(const record of data.records)for(const currentJob of record.jobs||[])if(identity({item:currentJob},'jobs')===identity(row,'jobs'))currentJob.user_decision=saved;
      render();showApplicationDecision({...row,item:{...job,user_decision:saved}});toast('Decisão manual salva.');
    }catch(error){toast(error.message);}
    finally{button.disabled=false;}
  };
  if(!$('detail').open)$('detail').showModal();
}
function exportCsv() {
  let headers, rows;
  if (tab === 'runs') {
    headers=['Execução','Data','Última gravação','Concluída','Retomada','Domínios no recorte','Domínios totais','E-mails no recorte','Vagas no recorte','IA configurada','API de IA disponível','Modelo','Erros totais'];
    rows=current.map(r=>[r.id,r.date,r.finished,r.completed,r.resumed,r.matching,r.count,r.matchedEmails,r.matchedJobs,aiLabel(r.ai),r.ai_available,r.model,r.errors]);
  } else {
    const commonHeaders=['Domínio','Empresa','Execução','Data','Status','Acessível','Categoria','IA configurada','Modelo','Versão','Arquivo de entrada'];
    const common=r=>[r.domain,r.name,r.run_id,r.date,r.status,r.alive?'YES':'NO',r.type,aiLabel(r.ai),r.model,r.version,r.input_file];
    if(['jobs','applications'].includes(tab)){
      headers=[...commonHeaders,'Título','URL da vaga','Status candidatura','Prioridade','Aplicação','Resposta','Entrevista','Proposta','Contratação','Rejeição','Etapa rejeição','Próximo retorno','Contato','Canal','Currículo enviado','Notas','Feedback','Atualizado em','Score geral','Score habilidades','Score experiência','Score senioridade','Score localização','Score idiomas','Justificativa score','Pontos fortes','Lacunas','Perfil ID','Perfil versão','Modelo score','Critério score','Data análise'];
      rows=current.map(r=>{const a=r.item.application || {},s=r.item.score || {};return [...common(r),r.item.title,r.item.url,statuses[a.status] || 'Não aplicada',a.priority,a.applied_at,a.response_at,a.interview_at,a.offer_at,a.hired_at,a.rejected_at,a.rejection_stage,a.follow_up_at,a.contact,a.channel,a.resume_version,a.notes,a.rejection_reason,a.updated_at,s.total,s.skills,s.experience,s.seniority,s.location,s.language,s.explanation,(s.strengths || []).join(' ; '),(s.gaps || []).join(' ; '),s.profile_id,s.profile_version,s.model,s.rubric_version,s.analyzed_at];});
    }
    else if(tab==='emails'){headers=[...commonHeaders,'E-mail'];rows=current.map(r=>[...common(r),r.item]);}
    else if(tab==='careers'){headers=[...commonHeaders,'URL de carreira'];rows=current.map(r=>[...common(r),r.item]);}
    else{headers=[...commonHeaders,'URL canônica','Descrição','E-mails','Carreiras','URLs de vagas','Títulos de vagas','Páginas','Evidências','Erro'];rows=current.map(r=>[...common(r),r.canonical,r.description,r.emails.join(' ; '),r.careers.join(' ; '),r.jobs.map(j=>j.url).join(' ; '),r.jobs.map(j=>j.title).join(' ; '),r.pages,r.evidence.join(' ; '),r.error]);}
  }
  const url=URL.createObjectURL(new Blob([makeCsv(headers,rows)],{type:'text/csv;charset=utf-8'}));
  const a=document.createElement('a');a.href=url;a.download=`site-intel-${tab}-${new Date().toISOString().slice(0,10)}.csv`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  toast(`${count(rows.length)} linhas exportadas com os filtros atuais.`);
}
function clear(doRender=true) {
  for(const key of fields) $(key).value=key==='sort'?'newest':key==='keep'?'latest':'';
  for(const o of $('run').options)o.selected=!o.value;
  $('dedup').checked=true;page=1;if(doRender)render();
}
let toastTimeout;
function toast(text){$('toast').textContent=text;$('toast').hidden=false;clearTimeout(toastTimeout);toastTimeout=setTimeout(()=>$('toast').hidden=true,3500);}
async function copy(text){try{await navigator.clipboard.writeText(text);toast('E-mail copiado.');}catch{toast('Não foi possível copiar. Selecione o e-mail para copiar manualmente.');}}
async function materialsRequest(path,body){
  const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Site-Intel':'dashboard'},body:JSON.stringify(body)});
  const result=await response.json();if(!response.ok)throw new Error(result.error||'Não foi possível processar os materiais.');return result;
}
async function openMaterials(url,regenerate=false){
  materialsState={url,regenerate,preview:null,selected:null};
  $('materials-preview').innerHTML='<p class="inline-note">Carregando prévia…</p>';$('materials-history').innerHTML='';$('materials-output').innerHTML='';
  $('materials-dialog').showModal();
  try{await refreshMaterialsPreview(regenerate);}catch(error){$('materials-preview').innerHTML=`<div class="notice">${esc(error.message)}</div>`;}
}
async function refreshMaterialsPreview(regenerate=materialsState?.regenerate||false){
  if(!materialsState)return;
  const body={url:materialsState.url,language:$('materials-language').value,regenerate};
  const preview=await materialsRequest('/api/application-materials/preview',body);
  materialsState={...materialsState,...body,preview,regenerate,selected:preview.cached_material||preview.history?.find(item=>item.status==='success')||null};
  renderMaterialsPanel();
}
function downloadText(name,text){
  const blob=new Blob([text],{type:'text/plain;charset=utf-8'}),url=URL.createObjectURL(blob),anchor=document.createElement('a');
  anchor.href=url;anchor.download=name;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function renderMaterialsPanel(){
  const preview=materialsState?.preview;if(!preview)return;
  const cacheText=preview.cached_material?'Pacote reutilizável disponível · 0 chamadas novas'
    :preview.cache_available?'Há uma geração anterior, mas os inputs mudaram · 1 nova chamada para gerar'
    :preview.new_calls?'Nenhum pacote compatível · 1 nova chamada':'Nenhuma chamada necessária';
  const languageLabel=preview.selected_language==='auto'?`Automático · ${preview.language==='en'?'Inglês':'Português'}`:({en:'Inglês',pt:'Português'}[preview.language]||preview.language);
  $('materials-preview').innerHTML=`<div class="detail-meta"><div><small>Vaga</small>${esc(preview.job_title||'Desconhecido')}</div><div><small>Empresa</small>${esc(preview.company||'Desconhecida')}</div><div><small>Perfil</small>${esc(preview.profile_version_id||'Desconhecido')}</div><div><small>Descrição</small>${esc({complete:'Completa',partial:'Parcial',insufficient:'Insuficiente'}[preview.quality]||'Desconhecida')}</div><div><small>Profile Fit</small>${preview.profile_fit_available?'Disponível':'Não disponível'}</div><div><small>Idioma · Modelo</small>${esc(languageLabel)} · ${esc(preview.model||'Desconhecido')}</div><div><small>Materiais</small>Cover Letter · Cold Email Subject · Cold Email Body</div><div><small>Cache</small>${esc(cacheText)}</div></div>${preview.warning?`<div class="notice">${esc(preview.warning)}</div>`:''}<p class="inline-note">Versão ${esc(preview.rule_version)} · Chamadas novas: ${preview.new_calls}</p>`;
  const generations=(preview.history||[]).filter(item=>item.status==='success');
  $('materials-history').innerHTML=generations.length?`<label>Histórico de gerações<select id="materials-generation-select">${generations.map((item,index)=>`<option value="${item.id}" ${materialsState.selected?.id===item.id?'selected':''}>v${generations.length-index} · ${esc(dateLabel(item.created_at,true))} · ${esc(item.model)} · ${esc(item.language)}</option>`).join('')}</select></label>`:'';
  $('materials-output').innerHTML=materialsState.selected?renderMaterialsGeneration(materialsState.selected):'';
  $('use-materials-cache').hidden=!preview.cached_material||preview.blocked;
  $('generate-materials').hidden=preview.blocked||preview.new_calls!==1||preview.regenerate;
  $('generate-materials').textContent='Confirmar e gerar (1 chamada)';
  $('regenerate-materials').hidden=preview.blocked||!generations.length||preview.regenerate;
  if(preview.regenerate&&!preview.blocked){$('generate-materials').hidden=false;$('generate-materials').textContent='Confirmar regeneração (1 chamada)';}
}
function renderMaterialsGeneration(item){
  const email=`Subject: ${item.cold_email_subject}\n\n${item.cold_email_body}`;
  const oldInputs=item.input_fingerprint!==materialsState.preview.confirmation_token;
  return `${oldInputs?'<div class="notice">Exibindo uma geração histórica feita com inputs diferentes dos atuais.</div>':''}<section class="detail-section materials-output"><h3>COVER LETTER</h3><pre>${esc(item.cover_letter)}</pre><div class="form-actions"><button class="button secondary" data-material-copy="cover" type="button">Copiar Cover Letter</button><button class="button secondary" data-material-export="cover" type="button">Exportar TXT</button></div></section><section class="detail-section materials-output"><h3>COLD EMAIL</h3><p><strong>Subject:</strong> ${esc(item.cold_email_subject)}</p><pre>${esc(item.cold_email_body)}</pre><div class="form-actions"><button class="button secondary" data-material-copy="subject" type="button">Copiar assunto</button><button class="button secondary" data-material-copy="body" type="button">Copiar corpo</button><button class="button secondary" data-material-copy="email" type="button">Copiar e-mail completo</button><button class="button secondary" data-material-export="email" type="button">Exportar TXT</button></div><p class="inline-note">${esc(dateLabel(item.created_at,true))} · ${esc(item.model)} · ${esc(item.language==='en'?'Inglês':'Português')} · ${esc(item.rule_version)} · Profile Fit usado: ${item.profile_fit_hash?'sim':'não'}</p></section>`;
}
async function submitMaterials(regenerate=false){
  const preview=materialsState?.preview;if(!preview)return;
  const exactReuse=Boolean(preview.cached_material&&!regenerate);
  if(!exactReuse&&!window.confirm(`Será feita 1 chamada ao modelo ${preview.model} para gerar Cover Letter e Cold Email.\n\nConfirmar?`))return;
  $('generate-materials').disabled=true;$('use-materials-cache').disabled=true;$('regenerate-materials').disabled=true;
  try{
    const result=await materialsRequest('/api/application-materials/generate',{url:materialsState.url,language:$('materials-language').value,
      regenerate,confirmation_token:preview.confirmation_token});
    materialsState.selected=result.generation;
    await refreshMaterialsPreview(false);
    materialsState.selected=result.generation;renderMaterialsPanel();
    toast(`Materiais: 1 | Novas chamadas: ${result.new_calls} | Reutilizados: ${result.reused?1:0} | Sucesso: 1 | Falhas: 0`);
  }catch(error){$('materials-preview').insertAdjacentHTML('beforeend',`<div class="notice">Materiais: 1 · Falhas: 1 · ${esc(error.message)}</div>`);}
  finally{$('generate-materials').disabled=false;$('use-materials-cache').disabled=false;$('regenerate-materials').disabled=false;}
}
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{tab=b.dataset.tab;page=1;render();if(tab==='profile'){if(profileState)renderProfileForm();else loadProfile();}});
for(const key of [...fields,'run','dedup','page-size']) $(key).addEventListener(key==='search'||$(key).tagName==='INPUT'&&$(key).type!=='checkbox'?'input':'change',()=>{page=1;render();});
$('run').addEventListener('change',()=>{if([...$('run').selectedOptions].some(o=>o.value))$('run').options[0].selected=false;});
$('advanced-toggle').onclick=()=>{$('advanced').hidden=!$('advanced').hidden;$('advanced-toggle').setAttribute('aria-expanded',String(!$('advanced').hidden));};
$('clear').onclick=()=>clear();$('empty-clear').onclick=()=>clear();$('refresh').onclick=()=>load(true);$('export').onclick=exportCsv;
$('prev').onclick=()=>{page--;render();};$('next').onclick=()=>{page++;render();};
$('auto').onchange=persist;
initTracking({esc,link,dateLabel,toast,reload:()=>load(),navigate:(next,stage='')=>{tab=next;page=1;if(stage){$('applicationStatus').value='';$('milestone').value=stage;}render();}});
$('new-application').onclick=()=>openApplication();
$('run-prematch').onclick=async()=>{
  const button=$('run-prematch');const urls=[...new Set(current.map(row=>row.item?.url).filter(Boolean))];
  if(!urls.length){toast('Nenhuma vaga neste recorte.');return;}
  button.disabled=true;button.textContent='Calculando…';
  try{
    const response=await fetch('/api/pre-match',{method:'POST',headers:{'Content-Type':'application/json','X-Site-Intel':'dashboard'},body:JSON.stringify({urls})});
    const result=await response.json();if(!response.ok)throw new Error(result.error||'Falha no pré-match.');
    toast(result.message);await load();
  }catch(error){toast(error.message);}
  finally{button.disabled=false;button.textContent='Pré-match local';}
};
$('run-profile-analysis').onclick=async()=>{
  const button=$('run-profile-analysis'),progress=$('profile-fit-progress');
  const urls=[...new Set(current.map(row=>row.item?.url).filter(Boolean))];
  if(!urls.length){toast('Nenhuma vaga neste recorte.');return;}
  const post=async(path,body)=>{
    const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Site-Intel':'dashboard'},body:JSON.stringify(body)});
    const result=await response.json();if(!response.ok&&response.status!==409)throw new Error(result.error||'Falha na solicitação.');
    return {response,result};
  };
  button.disabled=true;button.textContent='Preparando…';progress.hidden=false;
  try{
    const {result:preview}=await post('/api/profile-fit/preview',{urls});
    const qualitySummary=`Qualidade: ${preview.total} | Completas: ${preview.complete} | Parciais: ${preview.partial} | Insuficientes: ${preview.insufficient}`;
    const eligibilitySummary=`Elegibilidade IA: ${preview.total} | Elegíveis: ${preview.eligibility_eligible} | Revisar: ${preview.eligibility_review} | Excluídas: ${preview.eligibility_excluded} | Reutilizadas: ${preview.eligibility_reused}`;
    const analysisSummary=`Análises reutilizáveis: ${preview.reusable} | Novas chamadas necessárias: ${preview.new} | Ignoradas por descrição insuficiente: ${preview.ignored_insufficient} | Ignoradas por hard mismatch: ${preview.ignored_excluded}`;
    progress.textContent=`${qualitySummary} · ${eligibilitySummary} · ${analysisSummary} · ${preview.model} · ${preview.rubric_version}`;
    if(preview.new&&!preview.api_configured)throw new Error('OPENAI_API_KEY não está configurada. Nenhuma análise foi iniciada.');
    if(preview.new&&!window.confirm(`${qualitySummary}\n${eligibilitySummary}\n${analysisSummary}\n\nModelo: ${preview.model}\nRubrica: ${preview.rubric_version}\n\nIniciar as chamadas pagas?`))return;
    const started=await post('/api/profile-fit/start',{urls,expected_new:preview.new,plan_token:preview.plan_token});
    if(started.response.status===409||started.result.changed){
      progress.textContent=`A prévia mudou: ${started.result.total} vagas | ${started.result.reusable} reutilizáveis | ${started.result.new} novas. Revise e clique novamente para confirmar.`;return;
    }
    const task=started.result;
    while(true){
      const response=await fetch(`/api/profile-fit/progress?id=${encodeURIComponent(task.id)}`,{cache:'no-store'});
      const state=await response.json();if(!response.ok)throw new Error(state.error||'Não foi possível consultar o progresso.');
      progress.textContent=state.running
        ?`Análise em andamento: ${state.done}/${state.total} · novas ${state.new} · reutilizadas ${state.reused} · sucesso ${state.success} · falhas ${state.failures}${state.current?` · ${state.current}`:''}`
        :`${state.summary||state.fatal_error||'Análise finalizada.'}${state.errors?.length?` · Erros: ${state.errors.map(item=>`${item.job}: ${item.error}`).join(' | ')}`:''}`;
      if(!state.running){if(state.fatal_error)throw new Error(state.fatal_error);toast(state.summary||'Análise finalizada.');await load();break;}
      await new Promise(resolve=>setTimeout(resolve,900));
    }
  }catch(error){progress.hidden=false;progress.textContent=error.message;toast(error.message);}
  finally{button.disabled=false;button.textContent='Analisar com IA';}
};
$('close-detail').onclick=()=>$('detail').close();
$('close-materials').onclick=()=>$('materials-dialog').close();
$('application-content').addEventListener('click',event=>{
  const button=event.target.closest('[data-generate-materials]');if(button)openMaterials(button.dataset.generateMaterials);
});
$('materials-language').onchange=()=>refreshMaterialsPreview(false).catch(error=>toast(error.message));
$('refresh-materials-preview').onclick=()=>refreshMaterialsPreview(false).catch(error=>toast(error.message));
$('use-materials-cache').onclick=()=>submitMaterials(false);
$('generate-materials').onclick=()=>submitMaterials(Boolean(materialsState?.regenerate));
$('regenerate-materials').onclick=()=>refreshMaterialsPreview(true).catch(error=>toast(error.message));
$('materials-history').addEventListener('change',event=>{
  if(event.target.id!=='materials-generation-select')return;
  materialsState.selected=materialsState.preview.history.find(item=>String(item.id)===event.target.value)||null;renderMaterialsPanel();
});
$('materials-output').addEventListener('click',async event=>{
  const button=event.target.closest('[data-material-copy],[data-material-export]');if(!button||!materialsState?.selected)return;
  const item=materialsState.selected,kind=button.dataset.materialCopy||button.dataset.materialExport;
  const value=materialsText(item,kind);
  if(button.hasAttribute('data-material-copy')){
    try{await navigator.clipboard.writeText(value);toast('Conteúdo copiado.');}catch{toast('Não foi possível copiar o conteúdo.');}
  }else downloadText(materialsFilename(materialsState.preview.company,materialsState.preview.job_title,kind),value);
});
$('copy-profile-analysis').onclick=async()=>{
  if(!visibleProfileAnalysis)return;
  try{await navigator.clipboard.writeText(analysisText(visibleProfileAnalysis.job,visibleProfileAnalysis.analysis));toast('Análise copiada.');}
  catch{toast('Não foi possível copiar a análise para a área de transferência.');}
};
$('export-profile-analysis').onclick=()=>{
  if(!visibleProfileAnalysis)return;
  const {job,analysis}=visibleProfileAnalysis;
  const blob=new Blob([analysisText(job,analysis)],{type:'text/plain;charset=utf-8'});
  const url=URL.createObjectURL(blob),anchor=document.createElement('a');anchor.href=url;anchor.download=safeFilename(job);anchor.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
};
$('detail').addEventListener('click',e=>{if(e.target===$('detail')){const rect=$('detail').getBoundingClientRect();if(e.clientX<rect.left||e.clientX>rect.right||e.clientY<rect.top||e.clientY>rect.bottom)$('detail').close();}});
async function loadProfile(){
  try{const response=await fetch('/api/profile',{cache:'no-store'});const value=await response.json();if(!response.ok)throw new Error(value.error||'Falha ao carregar perfil.');profileState=value;renderProfileForm();}
  catch(error){$('profile-meta-info').innerHTML=`<p class="notice">${esc(error.message)}</p>`;}
}
function profileSelect(name,label,value,options){return `<label class="profile-field">${label}<select name="${name}">${options.map(([v,text])=>`<option value="${v}" ${value===v?'selected':''}>${text}</option>`).join('')}</select></label>`;}
function profileText(name,label,value='',type='text'){return `<label class="profile-field">${label}<input name="${name}" type="${type}" value="${esc(value)}"></label>`;}
function profileArea(name,label,value='',hint='Um item por linha'){return `<label class="profile-field profile-wide">${label}<textarea name="${name}" rows="3" placeholder="${hint}">${esc((value||[]).join('\n'))}</textarea></label>`;}
function profileSection(title,content){return `<section class="profile-card"><h2>${title}</h2><div class="profile-fields">${content}</div></section>`;}
function renderProfileForm(profile=profileState.profile){
  const p=profile||{};const resume=p.resume;
  $('profile-meta-info').innerHTML=`<span class="badge ${profileState.version?'good':''}">${profileState.version?`Perfil v${profileState.version}`:'Perfil ainda não salvo'}</span><span>Atualizado ${esc(profileState.updated_at?dateLabel(profileState.updated_at,true):'—')}</span>${resume?`<span>Currículo: <a href="/api/profile/resume">${esc(resume.original_name)}</a></span>`:'<span>Nenhum currículo associado</span>'}`;
  const modes=p.work_modes||{};const modeOptions=[['preferred','Preferido'],['acceptable','Aceitável'],['unwanted','Não desejo']];
  const goals=profileSection('Objetivo profissional',profileArea('desired_roles','Cargos desejados',p.desired_roles)+profileArea('interests','Áreas de interesse',p.interests)+profileText('seniority','Senioridade desejada',p.seniority)+profileArea('employer_types','Tipos de empresa / setores',p.employer_types)+profileArea('excluded_roles','Cargos que não interessam',p.excluded_roles)+`<label class="switch-label profile-switch"><input name="related_roles_open" type="checkbox" ${p.related_roles_open!==false?'checked':''}><span class="switch"></span>Estou aberto a cargos relacionados</label>`);
  const location=profileSection('Modelo de trabalho e localização',profileText('current_location','Localização atual',p.current_location)+profileSelect('work_remote','Remoto',modes.remote||'acceptable',modeOptions)+profileSelect('work_hybrid','Híbrido',modes.hybrid||'acceptable',modeOptions)+profileSelect('work_onsite','Presencial',modes.onsite||'acceptable',modeOptions)+profileArea('regions','Países / regiões onde aceita trabalhar',p.regions)+`<fieldset class="profile-field profile-wide"><legend>Restrições obrigatórias de modelo</legend>${[['remote','Remoto'],['hybrid','Híbrido'],['onsite','Presencial']].map(([key,label])=>`<label class="profile-check"><input type="checkbox" name="work_mode_restrictions" value="${key}" ${(p.work_mode_restrictions||[]).includes(key)?'checked':''}> Não aceitar ${label.toLowerCase()}</label>`).join('')}</fieldset><label class="switch-label profile-switch"><input name="relocation" type="checkbox" ${p.relocation?'checked':''}><span class="switch"></span>Disponível para relocação</label>`);
  const salary=profileSection('Remuneração',profileText('currency','Moeda (ex.: USD, BRL)',p.currency)+profileText('salary_min','Salário mínimo',p.salary_min,'number')+profileText('salary_desired','Salário desejado',p.salary_desired,'number')+profileSelect('salary_period','Período',p.salary_period||'annual',[['annual','Anual'],['monthly','Mensal'],['hourly','Por hora']])+profileSelect('salary_flexibility','Flexibilidade',p.salary_flexibility||'reference',[['rigid','Rígido'],['flexible','Flexível'],['reference','Apenas referência']]));
  const hiring=profileSection('Contratação',profileArea('contract_types','Tipos aceitáveis (um por linha)',p.contract_types,'Full-time, CLT, Contractor/PJ…'));
  const experience=profileSection('Experiência e competências',`<label class="profile-field profile-wide">Resumo profissional<textarea name="professional_summary" rows="4">${esc(p.professional_summary)}</textarea></label>${profileText('experience_years','Anos de experiência',p.experience_years,'number')}${profileArea('work_history','Histórico profissional',p.work_history,'Cargo · empresa · período · resumo')}${profileArea('skills','Competências',p.skills,'Competência · nível · experiência')}${profileArea('technologies','Tecnologias / ferramentas',p.technologies)}${profileArea('domain_knowledge','Conhecimentos de domínio',p.domain_knowledge)}${profileArea('languages','Idiomas',p.languages,'Idioma · nível')}${profileArea('education','Formação',p.education)}${profileArea('certifications','Certificações',p.certifications)}`);
  const free=profileSection('Instruções livres para análise',`<label class="profile-field profile-wide">O que mais o sistema deve saber ao avaliar uma oportunidade?<textarea name="analysis_notes" rows="5">${esc(p.analysis_notes)}</textarea></label>`);
  const cv=profileSection('Currículo',`${resume?`<div class="profile-wide resume-current">Associado: <a href="/api/profile/resume">${esc(resume.original_name)}</a><small>Enviado em ${esc(dateLabel(resume.uploaded_at,true))} · ${esc(resume.type==='application/pdf'?'PDF':'DOCX')} · perfil v${esc(resume.profile_version)}</small></div>`:'<p class="inline-note profile-wide">Nenhum currículo foi enviado.</p>'}<label class="profile-field profile-wide">Anexar ou substituir currículo<input name="resume" type="file" accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"><small>PDF ou DOCX, até 15 MB. O conteúdo não será interpretado.</small></label>`);
  $('profile-form-content').innerHTML=`<div class="profile-grid">${goals}${location}${salary}${hiring}${experience}${free}${cv}</div>`;
}
$('profile-export').onclick=()=>{
  const blob=new Blob([exportProfileJson(profileState)],{type:'application/json;charset=utf-8'});
  const url=URL.createObjectURL(blob);const anchor=document.createElement('a');anchor.href=url;
  anchor.download=`perfil-profissional-${new Date().toISOString().slice(0,10)}.json`;anchor.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
};
$('profile-import').onclick=()=>$('profile-json-file').click();
$('profile-json-file').onchange=async event=>{
  const file=event.target.files[0];if(!file)return;
  try{
    if(file.size>2*1024*1024)throw new Error('O JSON deve ter no máximo 2 MB.');
    const staged=importProfileJson(await file.text(),profileState.profile);
    renderProfileForm(staged);
    toast('Perfil importado para revisão. Clique em Salvar perfil para persistir.');
  }catch(error){toast(error.message);}
  finally{event.target.value='';}
};
$('profile-form').addEventListener('submit',async event=>{
  event.preventDefault();const form=event.currentTarget;const value={...profileState.profile};
  for(const key of ['desired_roles','interests','employer_types','excluded_roles','regions','contract_types','work_history','skills','technologies','domain_knowledge','languages','education','certifications'])value[key]=form.elements[key].value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
  for(const key of ['seniority','current_location','currency','salary_min','salary_desired','salary_period','salary_flexibility','professional_summary','experience_years','analysis_notes'])value[key]=form.elements[key].value.trim();
  value.related_roles_open=form.elements.related_roles_open.checked;value.relocation=form.elements.relocation.checked;
  value.work_mode_restrictions=[...form.querySelectorAll('[name=work_mode_restrictions]:checked')].map(x=>x.value);
  value.work_modes={remote:form.elements.work_remote.value,hybrid:form.elements.work_hybrid.value,onsite:form.elements.work_onsite.value};
  const body=new FormData();body.append('profile',JSON.stringify(value));if(form.elements.resume.files[0])body.append('resume',form.elements.resume.files[0]);
  const button=form.querySelector('button[type=submit]');button.disabled=true;
  try{const response=await fetch('/api/profile',{method:'POST',headers:{'X-Site-Intel':'dashboard'},body});const result=await response.json();if(!response.ok)throw new Error(result.error||'Não foi possível salvar.');profileState=result;renderProfileForm();toast('Perfil salvo.');}
  catch(error){toast(error.message);}
  finally{button.disabled=false;}
});
document.addEventListener('keydown',e=>{if(e.key==='/'&&!['INPUT','SELECT','TEXTAREA'].includes(document.activeElement.tagName)&&!$('detail').open&&!$('application-dialog').open){e.preventDefault();$('search').focus();}});
if(Object.keys(filterLabels).some(k=>!['search','status','type'].includes(k)&&restored[k])){$('advanced').hidden=false;$('advanced-toggle').setAttribute('aria-expanded','true');}
load();setInterval(()=>{if($('auto').checked&&!document.hidden&&!$('detail').open&&!$('application-dialog').open)load();},30000);
