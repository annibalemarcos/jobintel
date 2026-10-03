const LIST_FIELDS = ['desired_roles','interests','employer_types','excluded_roles','regions','contract_types',
  'work_mode_restrictions','work_history','skills','technologies','domain_knowledge','languages','education','certifications'];
const TEXT_FIELDS = ['seniority','current_location','currency','salary_period','salary_flexibility',
  'professional_summary','analysis_notes'];
const NUMBER_FIELDS = ['salary_min','salary_desired','experience_years'];
const MODES = ['preferred','acceptable','unwanted'];

export function exportProfileJson(state, exportedAt = new Date().toISOString()) {
  const profile = structuredClone(state.profile || {});
  if (profile.resume) {
    const {original_name,type,uploaded_at,profile_version,sha256} = profile.resume;
    profile.resume = {original_name,type,uploaded_at,profile_version,sha256};
  }
  return JSON.stringify({format:'crypto-site-intel-profile',schema_version:1,
    profile_version:state.version || 0,updated_at:state.updated_at || '',exported_at:exportedAt,profile},null,2);
}

export function importProfileJson(text, currentProfile) {
  let document;
  try { document=JSON.parse(text); }
  catch { throw new Error('O arquivo não contém um JSON válido.'); }
  if (!document || typeof document!=='object' || Array.isArray(document) ||
      document.format!=='crypto-site-intel-profile' || document.schema_version!==1 ||
      !document.profile || typeof document.profile!=='object' || Array.isArray(document.profile)) {
    throw new Error('Formato de perfil não reconhecido ou versão incompatível.');
  }
  const imported=document.profile;
  const next={...currentProfile,work_modes:{...(currentProfile.work_modes||{})}};
  for (const key of LIST_FIELDS) if (Object.hasOwn(imported,key)) {
    if (!Array.isArray(imported[key]) || imported[key].length>200 || imported[key].some(item=>typeof item!=='string' || item.length>1000))
      throw new Error(`O campo "${key}" deve ser uma lista de textos.`);
    if (key==='work_mode_restrictions' && imported[key].some(item=>!['remote','hybrid','onsite'].includes(item)))
      throw new Error('Restrições de modelo de trabalho inválidas.');
    next[key]=[...imported[key]];
  }
  for (const key of TEXT_FIELDS) if (Object.hasOwn(imported,key)) {
    const limits={seniority:120,current_location:300,currency:8,salary_period:20,salary_flexibility:30,
      professional_summary:10000,analysis_notes:20000};
    if (typeof imported[key]!=='string' || imported[key].length>limits[key]) throw new Error(`O campo "${key}" deve ser um texto válido.`);
    if (key==='salary_period' && !['annual','monthly','hourly'].includes(imported[key])) throw new Error('Período salarial inválido.');
    if (key==='salary_flexibility' && !['rigid','flexible','reference'].includes(imported[key])) throw new Error('Flexibilidade salarial inválida.');
    next[key]=imported[key];
  }
  for (const key of NUMBER_FIELDS) if (Object.hasOwn(imported,key)) {
    const value=imported[key];
    if (!(typeof value==='string' || typeof value==='number') || value!=='' && (!Number.isFinite(Number(value)) || Number(value)<0 || Number(value)>1_000_000_000))
      throw new Error(`O campo "${key}" deve ser um número não negativo.`);
    next[key]=String(value);
  }
  for (const key of ['related_roles_open','relocation']) if (Object.hasOwn(imported,key)) {
    if (typeof imported[key]!=='boolean') throw new Error(`O campo "${key}" deve ser verdadeiro ou falso.`);
    next[key]=imported[key];
  }
  if (Object.hasOwn(imported,'work_modes')) {
    const modes=imported.work_modes;
    if (!modes || typeof modes!=='object' || Array.isArray(modes)) throw new Error('Preferências de trabalho inválidas.');
    for (const key of ['remote','hybrid','onsite']) if (Object.hasOwn(modes,key)) {
      if (!MODES.includes(modes[key])) throw new Error(`Preferência inválida para "${key}".`);
      next.work_modes[key]=modes[key];
    }
  }
  // Resume metadata is descriptive only. Keep the physical resume currently associated with this profile.
  next.resume=currentProfile.resume || null;
  return next;
}
