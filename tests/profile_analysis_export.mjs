import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const source=readFileSync(new URL('../dashboard_assets/profile_analysis_export.js',import.meta.url),'utf8');
const {analysisText,safeFilename}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));

const job={title:'Manager, WFM / CX Operations',company:'Coinbase',url:'https://jobs.example/role',location:'São Paulo',date_posted:'2026-10-03'};
const dimension={status:'unknown',score:null,evidence:['Evidência objetiva'],gaps:['Lacuna registrada'],uncertainties:['Não informado']};
const analysis={profile_score:78,overall_fit:'strong',profile_version_id:'local-user:v3',model:'gpt-5.6-luna',
  rubric_version:'profile_fit_v1',analyzed_at:'2026-10-03T12:00:00Z',summary:'Resumo completo.',
  strengths:['Ponto forte'],gaps:['Lacuna'],transferable_experience:['Experiência transferível'],
  mandatory_requirements:[{requirement:'Liderança',status:'met',evidence:'Histórico profissional'}],
  preferred_requirements:[{requirement:'Inglês',status:'unknown',evidence:'Não consta na vaga'}],
  unknowns:['Remuneração desconhecida'],dimensions:Object.fromEntries([
    'role_fit','experience_fit','skills_fit','seniority_fit','domain_fit','work_model_fit',
    'location_fit','compensation_fit','contract_fit','language_fit'].map(key=>[key,dimension])),
  career_value:{rating:'medium',rationale:'Coerente com os objetivos.',aligned_goals:['Web3'],cautions:['Escopo incerto']},
  recommendation_reasoning:'Experiência transferível e requisitos atendidos.'};

const calls=[];
globalThis.fetch=async(...args)=>{calls.push(args);throw new Error('Exportação não deve acessar backend ou IA.');};
const text=analysisText(job,analysis);
assert.ok(text.includes('Vaga: Manager, WFM / CX Operations'));
assert.ok(text.includes('Empresa: Coinbase'));
assert.ok(text.includes('Score de perfil: 78 / 100'));
assert.ok(text.includes('Classificação: Forte'));
assert.ok(text.includes('Perfil analisado: local-user:v3'));
assert.ok(text.includes('Modelo: gpt-5.6-luna'));
assert.ok(text.includes('Rubrica: profile_fit_v1'));
assert.ok(text.includes('Data da análise: 2026-10-03T12:00:00Z'));
assert.ok(text.includes('Experiência transferível'));
assert.ok(text.includes('REQUISITOS OBRIGATÓRIOS'));
assert.ok(text.includes('REQUISITOS DESEJÁVEIS'));
assert.ok(text.includes('Compensação') || text.includes('Remuneração'));
assert.ok(text.includes('Status: Desconhecido'));
assert.ok(text.includes('Score: Desconhecido'));
assert.ok(text.includes('Evidências:\n- Evidência objetiva'));
assert.ok(text.includes('Lacunas:\n- Lacuna registrada'));
assert.ok(text.includes('Incertezas:\n- Não informado'));
for(const label of ['Cargo e responsabilidades','Experiência','Competências','Senioridade','Domínio/setor','Modelo de trabalho','Localização/elegibilidade','Remuneração','Contrato','Idiomas'])assert.ok(text.includes(label),`Missing dimension ${label}`);
assert.ok(text.includes('Web3'));
assert.ok(text.includes('POR QUE RECEBEU ESTA NOTA'));
assert.equal(safeFilename(job),'profile_analysis_coinbase_manager_wfm_cx_operations.txt');
assert.match(safeFilename({company:'../../Coin:base',title:'../Manager / WFM ?'}),/^profile_analysis_[a-z0-9_]+\.txt$/);
assert.equal(calls.length,0,'A exportação é local e não deve chamar APIs.');
console.log('Profile analysis export tests passed.');
