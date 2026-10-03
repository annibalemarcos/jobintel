# Estrutura preparada para análise de perfil

A análise ainda não é executada. Nenhum perfil foi inferido e nenhum score é fabricado.
O dashboard mostra **Não analisado** e valores `null` enquanto não houver análise real.

## Persistência

O banco `.dashboard_data/applications.sqlite3` usa SQLite da biblioteca padrão do Python.
Ele é independente dos CSVs do crawler e das execuções. A chave é uma URL de vaga
normalizada: host em minúsculas, porta padrão removida, fragmento removido, barra final
normalizada e parâmetros ordenados. Títulos e datas de execução não são identificadores.
Alterações na própria URL/path de uma vaga não são unificadas automaticamente.

- `applications`: snapshot da vaga, status, prioridade, datas, contato, notas,
  motivo/etapa da rejeição, currículo usado e versão para impedir sobrescrita concorrente.
- `application_events`: histórico de mudanças de campos, com timestamp UTC.
- `candidate_profiles`: perfil versionado (`id`, `version`), resumo, habilidades,
  experiência, preferências e data da atualização. Estrutura vazia por enquanto.
- `job_scores`: histórico por vaga + versão de perfil; notas 0–100 de compatibilidade
  geral, habilidades, experiência, senioridade, localização e idiomas; justificativa,
  pontos fortes, lacunas, evidências, modelo, versão dos critérios e timestamp.

As colunas numéricas permitem `NULL` para critérios não avaliados. Score zero significa
zero real, não ausência de análise. O dashboard lê a análise mais recente por vaga;
o histórico de scores é mantido na tabela. Excluir acompanhamento não exclui scores.

## Próxima etapa

Antes de gerar scores, coletar o perfil/currículo do usuário, definir critérios e seus
pesos e registrar uma versão de perfil. Coletar o conteúdo e requisitos de cada vaga
(o crawler atual guarda título e URL, não a descrição completa). A análise precisa
registrar fontes, incertezas, rubric_version e modelo. Persistir usando uma transação,
sem modificar candidaturas. Uma futura alteração de perfil deve gerar uma nova versão
e permitir recalcular sem apagar scores anteriores.

Já existem apresentação dos scores, filtros por presença/intervalo, ordenação, campos
de justificativa e exportação no dashboard. Ainda não existe endpoint de geração,
chamada de IA, upload de currículo ou controle de pesos.

## API do acompanhamento

- `GET /api/data`: coletas com `jobs[].key`, `application` e `score`, incluindo cadastros
  pessoais que não estão nas coletas atuais.
- `POST /api/applications`: URL, título, empresa, domínio, `version` e objeto `application`.
  `manual: true` indica cadastro manual inicial. Atualizações exigem a versão atual.
- `DELETE /api/applications`: URL e versão atual. Exclui acompanhamento e histórico,
  preserva CSVs e análises. Exige confirmação no dashboard.

Gravações exigem JSON, Origin do próprio dashboard e `X-Site-Intel: dashboard`.
Conflitos retornam 409; entradas inválidas retornam 400. Não há integração externa.
