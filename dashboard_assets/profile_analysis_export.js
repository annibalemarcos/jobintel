const unknown = value => value === null || value === undefined || value === '' ? 'Desconhecido' : String(value);
const listText = values => Array.isArray(values) && values.length ? values.map(value=>`- ${value}`).join('\n') : 'Desconhecido';
const statusLabels = {met:'Atendido',partially_met:'Parcialmente atendido',not_met:'Não atendido',unknown:'Desconhecido'};
const fitLabels = {excellent:'Excelente',strong:'Forte',moderate:'Moderada',weak:'Fraca',poor:'Baixa'};
const dimensionLabels = {role_fit:'Cargo e responsabilidades',experience_fit:'Experiência',skills_fit:'Competências',seniority_fit:'Senioridade',domain_fit:'Domínio/setor',work_model_fit:'Modelo de trabalho',location_fit:'Localização/elegibilidade',compensation_fit:'Remuneração',contract_fit:'Contrato',language_fit:'Idiomas'};

export function analysisText(job={}, analysis={}) {
  const requirements=(items)=>Array.isArray(items)&&items.length?items.map(item=>`- ${unknown(item.requirement)} — ${statusLabels[item.status]||unknown(item.status)}\n  Evidência: ${unknown(item.evidence)}`).join('\n'):'Desconhecido';
  const dimensions=Object.entries(analysis.dimensions||{}).map(([key,item])=>[
    dimensionLabels[key]||key,
    `Status: ${statusLabels[item.status]||unknown(item.status)}`,
    `Score: ${item.score===null||item.score===undefined?'Desconhecido':`${item.score} / 100`}`,
    `Evidências:\n${listText(item.evidence)}`,
    `Lacunas:\n${listText(item.gaps)}`,
    `Incertezas:\n${listText(item.uncertainties)}`
  ].join('\n')).join('\n\n')||'Desconhecido';
  const career=analysis.career_value||{};
  return [
    'ANÁLISE DE PERFIL',
    `Vaga: ${unknown(job.title||analysis.job_title)}`,
    `Empresa: ${unknown(job.company||analysis.company||job.name)}`,
    `URL: ${unknown(job.url||analysis.job_url)}`,
    `Localização: ${unknown(job.location)}`,
    `Data de publicação: ${unknown(job.date_posted)}`,
    `Score de perfil: ${unknown(analysis.profile_score)} / 100`,
    `Classificação: ${fitLabels[analysis.overall_fit]||unknown(analysis.overall_fit)}`,
    `Perfil analisado: ${unknown(analysis.profile_version_id)}`,
    `Modelo: ${unknown(analysis.model)}`,
    `Rubrica: ${unknown(analysis.rubric_version)}`,
    `Data da análise: ${unknown(analysis.analyzed_at)}`,
    '',
    'RESUMO',unknown(analysis.summary),'',
    'PONTOS FORTES',listText(analysis.strengths),'',
    'LACUNAS',listText(analysis.gaps),'',
    'EXPERIÊNCIA TRANSFERÍVEL',listText(analysis.transferable_experience),'',
    'REQUISITOS OBRIGATÓRIOS',requirements(analysis.mandatory_requirements),'',
    'REQUISITOS DESEJÁVEIS',requirements(analysis.preferred_requirements),'',
    'INCERTEZAS',listText(analysis.unknowns),'',
    'DIMENSÕES DA RUBRICA',dimensions,'',
    'VALOR PROFISSIONAL / CARREIRA',
    `Avaliação: ${{high:'Alto',medium:'Médio',low:'Baixo',unknown:'Desconhecido'}[career.rating]||unknown(career.rating)}`,
    `Justificativa: ${unknown(career.rationale)}`,
    `Alinhamentos:\n${listText(career.aligned_goals)}`,
    `Considerações:\n${listText(career.cautions)}`,'',
    'POR QUE RECEBEU ESTA NOTA',unknown(analysis.recommendation_reasoning)
  ].join('\n');
}

export function safeFilename(job={}) {
  const slug=value=>String(value||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase()
    .replace(/[^a-z0-9]+/g,'_').replace(/^_+|_+$/g,'');
  const parts=[slug(job.company||job.name),slug(job.title)].filter(Boolean).join('_').slice(0,120).replace(/_+$/,'');
  return `profile_analysis${parts?'_'+parts:''}.txt`;
}
