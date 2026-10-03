export function materialsText(generation,kind){
  if(!generation)return '';
  if(kind==='cover')return generation.cover_letter||'';
  if(kind==='subject')return generation.cold_email_subject||'';
  if(kind==='body')return generation.cold_email_body||'';
  if(kind==='email')return `Subject: ${generation.cold_email_subject||''}\n\n${generation.cold_email_body||''}`;
  throw new Error('Tipo de material desconhecido.');
}

export function materialsFilename(company,title,kind){
  const suffix=kind==='cover'?'cover-letter':kind==='email'?'cold-email':null;
  if(!suffix)throw new Error('Tipo de arquivo desconhecido.');
  const parts=[company,title].map(value=>String(value||'').normalize('NFKD').replace(/[\u0300-\u036f]/g,'')
    .toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-+|-+$/g,'')).filter(Boolean);
  const slug=parts.join('_').slice(0,100)||'vaga';
  return `${slug}_${suffix}.txt`;
}
