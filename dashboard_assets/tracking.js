import {query, reached, applicationLabels} from './logic.js';

export const statuses=applicationLabels;
const dateFields={applied_at:'Data da aplicação',response_at:'Data da resposta',interview_at:'Data da entrevista',offer_at:'Data da proposta',hired_at:'Data da contratação',rejected_at:'Data da rejeição',follow_up_at:'Próximo retorno / follow-up'};
const textFields={notes:'Anotações',contact:'Contato / recrutador',channel:'Canal da aplicação',rejection_reason:'Motivo / feedback',resume_version:'Currículo / versão enviada'};
const priorities={low:'Baixa',normal:'Normal',high:'Alta'};
const rejectionStages={unknown:'Não informada',before_interview:'Antes da entrevista',after_interview:'Após entrevista',after_offer:'Após proposta'};
let env;
export function initTracking(options){env=options;document.getElementById('close-application').onclick=()=>document.getElementById('application-dialog').close();}
export function applicationBadge(job){
  const a=job.application || {};
  const css=['interview','offer','hired'].includes(a.status)?'good':a.status==='rejected'?'bad':['applied','responded'].includes(a.status)?'warn':'';
  return `<span class="badge ${css}">${env.esc(statuses[a.status] || statuses.new)}</span>`;
}
export function scoreBadge(job){const score=job.score?.total;return score==null?'<span class="score-empty" title="A análise de perfil será implementada na próxima etapa">Não analisado</span>':`<span class="score-value">${Number(score).toFixed(0)}<small> / 100</small></span>`;}
export function renderApplicationOverview(data, filters, visible){
  const root=document.getElementById('application-overview');root.hidden=!visible;if(!visible)return;
  const jobs=query(data.records,{...filters,dedup:true,keep:'latest'},'jobs').rows;
  const tracked=jobs.filter(r=>r.item.application?.tracked);
  const cards=[['Apliquei','applied'],['Recebi resposta','responded'],['Consegui entrevista','interview'],['Recebi proposta','offer'],['Contratado','hired'],['Rejeitado','rejected']];
  const recent=[...tracked].sort((a,b)=>(b.item.application.updated_at || '').localeCompare(a.item.application.updated_at || '')).slice(0,8);
  const today=new Date().toLocaleDateString('en-CA');
  const pending=tracked.filter(r=>r.item.application.follow_up_at&&r.item.application.follow_up_at<=today&&!['rejected','hired','withdrawn','closed'].includes(r.item.application.status)).length;
  root.innerHTML=`<div class="tracking-heading"><div><div class="eyebrow">SEU PROCESSO SELETIVO</div><h2>Acompanhamento de candidaturas</h2><p>${tracked.length} vagas acompanhadas no recorte · etapas acumuladas, sem contar URLs repetidas${pending?` · ${pending} retornos pendentes`:''}</p></div><button id="all-applications" class="button secondary">Ver candidaturas →</button></div><div class="pipeline">${cards.map(([label,key])=>`<button class="pipeline-card ${key==='rejected'?'negative':''}" data-stage="${key}"><strong>${tracked.filter(r=>key==='rejected'?r.item.application.status==='rejected':reached(r.item.application,key)).length}</strong><span>${label}</span></button>`).join('')}</div><div class="tracking-table table-scroll"><table><thead><tr><th>Vaga</th><th>Empresa</th><th>Status atual</th><th>Score de perfil</th><th>Próximo retorno</th><th></th></tr></thead><tbody>${recent.map((r,i)=>`<tr><td><div class="item-link">${env.link(r.item.url,r.item.title)}</div></td><td>${env.esc(r.name)}</td><td>${applicationBadge(r.item)}</td><td>${scoreBadge(r.item)}</td><td>${env.esc(r.item.application.follow_up_at || '—')}</td><td><button class="detail-button" data-track="${i}">Atualizar →</button></td></tr>`).join('')}</tbody></table></div>${!recent.length?'<div class="tracking-empty">Seu acompanhamento começa aqui. Abra uma vaga e marque “Apliquei”, ou cadastre uma oportunidade manualmente.<button id="start-tracking" class="button primary">Escolher uma vaga →</button></div>':''}<p class="tracking-footnote">Score reservado para a próxima etapa de análise do seu perfil. Nenhuma nota foi gerada automaticamente.</p>`;
  root.querySelector('#all-applications').onclick=()=>env.navigate('applications');
  root.querySelector('#start-tracking')?.addEventListener('click',()=>env.navigate('jobs'));
  root.querySelectorAll('[data-stage]').forEach(b=>b.onclick=()=>env.navigate('applications',b.dataset.stage));
  root.querySelectorAll('[data-track]').forEach(b=>b.onclick=()=>openApplication(recent[Number(b.dataset.track)]));
}
export function openApplication(row=null){
  const $=id=>document.getElementById(id),job=row?.item || {}, a=job.application || {},score=job.score || {};
  const isNewManual=!row,canEdit=isNewManual||a.source==='manual';
  const e=env.esc;
  $('application-title').textContent=isNewManual?'Cadastrar vaga':job.title || 'Candidatura';
  const options=(values,current)=>Object.entries(values).map(([value,label])=>`<option value="${value}" ${current===value?'selected':''}>${e(label)}</option>`).join('');
  const dateInput=(key,label)=>`<label>${label}<input id="app-${key}" name="${key}" type="date" value="${e(a[key] || '')}"></label>`;
  $('application-content').innerHTML=`<form id="application-form"><div id="application-error" class="notice" hidden role="alert"></div><div class="application-form-grid"><label class="full">URL da vaga<input id="app-url" type="url" required maxlength="4000" value="${e(job.url || '')}" ${isNewManual?'':'readonly'}></label><label class="full">Título da vaga<input id="app-title" required maxlength="500" value="${e(job.title || '')}" ${canEdit?'':'readonly'}></label><label>Empresa<input id="app-company" required maxlength="500" value="${e(a.company || row?.name || '')}" ${canEdit?'':'readonly'}></label><label>Domínio da empresa<input id="app-domain" required maxlength="500" value="${e(a.domain || row?.domain || '')}" ${canEdit?'':'readonly'}></label><label>Status atual<select id="app-status">${options(statuses,a.status || 'saved')}</select></label><label>Prioridade<select id="app-priority">${options(priorities,a.priority || 'normal')}</select></label>${Object.entries(dateFields).map(([k,v])=>dateInput(k,v)).join('')}<label>Etapa da rejeição<select id="app-rejection_stage">${options(rejectionStages,a.rejection_stage || 'unknown')}</select></label>${Object.entries(textFields).map(([key,label])=>key==='notes'||key==='rejection_reason'?`<label class="full">${label}<textarea id="app-${key}" rows="${key==='notes'?4:2}" maxlength="${key==='notes'?20000:2000}">${e(a[key] || '')}</textarea></label>`:`<label>${label}<input id="app-${key}" maxlength="500" value="${e(a[key] || '')}"></label>`).join('')}</div><div class="form-actions"><button id="save-application" class="button primary" type="submit">Salvar acompanhamento</button>${a.tracked?'<button id="delete-application" class="button danger" type="button">Excluir acompanhamento</button>':''}<button id="cancel-application" class="button secondary" type="button">Cancelar</button></div><div id="delete-confirmation" class="notice" hidden><p>Excluir as anotações, datas e histórico desta candidatura? A vaga coletada e eventuais análises de perfil serão preservadas. Um cadastro exclusivamente manual sairá da lista.</p><button id="confirm-delete" type="button" class="button danger">Sim, excluir acompanhamento</button><button id="cancel-delete" type="button" class="button secondary">Manter</button></div></form><section class="score-panel"><h3>Compatibilidade com seu perfil</h3><p class="inline-note">${score.total==null?'Ainda não analisada. Os campos estão preparados para uma análise futura de IA.':'Análise registrada para o perfil '+e(score.profile_id)+' · versão '+e(score.profile_version)}</p><div class="score-grid">${[['total','Score geral'],['skills','Habilidades'],['experience','Experiência'],['seniority','Senioridade'],['location','Localização'],['language','Idiomas']].map(([key,label])=>`<div><small>${label}</small><strong>${score[key]==null?'—':Number(score[key]).toFixed(0)+' / 100'}</strong></div>`).join('')}</div><p>${e(score.explanation || 'Justificativa, pontos fortes, lacunas e evidências serão exibidos aqui após a análise.')}</p>${score.total!=null?`<p>Pontos fortes: ${e((score.strengths || []).join(' · '))}</p><p>Lacunas: ${e((score.gaps || []).join(' · '))}</p><p class="inline-note">Modelo: ${e(score.model || '—')} · Critério: ${e(score.rubric_version || '—')} · Analisado em: ${e(score.analyzed_at || '—')}</p>`:''}</section>${!isNewManual&&job.url?`<section class="detail-section"><h3>Materiais de candidatura</h3><p class="inline-note">Gere Cover Letter e Cold Email para esta vaga após revisar a prévia. Nada será enviado automaticamente.</p><button class="button secondary" data-generate-materials="${e(job.url)}" type="button">Gerar materiais</button></section>`:''}<section class="detail-section"><h3>Histórico de alterações</h3><ul>${(a.history || []).map(event=>`<li><small>${e(env.dateLabel(event.at,true))}</small>${Object.entries(event.changes).map(([key,change])=>`${e(key==='status'?'Status':dateFields[key] || textFields[key] || key)}: ${e(key==='status'?statuses[change.from] || '—':change.from || '—')} → ${e(key==='status'?statuses[change.to] || change.to:change.to || '—')}`).join('<br>')}</li>`).join('') || '<li>Nenhuma alteração registrada.</li>'}</ul></section>`;
  $('app-status').onchange=()=>{
    const status=$('app-status').value,today=new Date().toLocaleDateString('en-CA');
    const set=key=>{if(!$('app-'+key).value)$('app-'+key).value=today;};
    if(['applied','responded','interview','offer','hired','rejected'].includes(status))set('applied_at');
    if(['responded','interview','offer','hired','rejected'].includes(status))set('response_at');
    if(['offer','hired','rejected'].includes(status))set(status==='offer'?'offer_at':status==='hired'?'hired_at':'rejected_at');
  };
  if(isNewManual)$('app-url').addEventListener('change',()=>{if(!$('app-domain').value)try{$('app-domain').value=new URL($('app-url').value).hostname;}catch{}});
  $('cancel-application').onclick=()=>$('application-dialog').close();
  const showError=error=>{$('application-error').hidden=false;$('application-error').textContent=error.message;$('application-error').scrollIntoView({block:'nearest'});};
  let saving=false;
  $('application-form').onsubmit=async event=>{
    event.preventDefault();if(saving)return;saving=true;$('save-application').disabled=true;
    try{
      const application={status:$('app-status').value,priority:$('app-priority').value,rejection_stage:$('app-rejection_stage').value};
      for(const key of [...Object.keys(dateFields),...Object.keys(textFields)])application[key]=$('app-'+key).value;
      await mutate('POST',{url:$('app-url').value,title:$('app-title').value,company:$('app-company').value,domain:$('app-domain').value,version:a.version || 0,manual:isNewManual,application});
      $('application-dialog').close();await env.reload();env.toast('Acompanhamento salvo.');
    }catch(error){showError(error);}finally{saving=false;$('save-application').disabled=false;}
  };
  if(a.tracked){
    $('delete-application').onclick=()=>$('delete-confirmation').hidden=false;
    $('cancel-delete').onclick=()=>$('delete-confirmation').hidden=true;
    $('confirm-delete').onclick=async()=>{if(saving)return;saving=true;$('confirm-delete').disabled=true;try{await mutate('DELETE',{url:job.url,version:a.version});$('application-dialog').close();await env.reload();env.toast('Acompanhamento excluído.');}catch(error){showError(error);}finally{saving=false;$('confirm-delete').disabled=false;}};
  }
  if(!$('application-dialog').open)$('application-dialog').showModal();
}
async function mutate(method,body){
  const response=await fetch('/api/applications',{method,headers:{'Content-Type':'application/json','X-Site-Intel':'dashboard'},body:JSON.stringify(body)});
  const result=await response.json();if(!response.ok)throw new Error(result.error || 'Não foi possível salvar.');
}
