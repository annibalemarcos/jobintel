# JobIntel

Job Search & Application Intelligence

## Dashboard local

Abra o painel com:

```powershell
.\dashboard.bat
```

Com o terminal aberto, acesse **http://127.0.0.1:8765** no navegador.
O painel usa somente o Python e não precisa instalar pacotes adicionais.
Para trocar a porta: `.\dashboard.bat --port 8766`.
Para ler saídas fora do projeto: `.\dashboard.bat --data-root "E:\minhas_coletas"`.

O dashboard descobre automaticamente as execuções nas subpastas do projeto,
incluindo `output_teste`, e atualiza os dados a cada 30 segundos quando visível.
O acompanhamento de candidaturas é editável; a coleta continua sendo executada por `run.bat`.

### Acompanhamento de candidaturas

Na **Visão geral**, o painel mostra aplicações, respostas, entrevistas, propostas,
contratações e rejeições. As etapas são acumuladas: uma candidatura rejeitada depois
de uma entrevista continua contando como uma entrevista obtida. Clique em um indicador
para filtrar as candidaturas que alcançaram aquela etapa.

Em **Vagas**, use **Acompanhar** para marcar status, prioridade, datas, contatos,
currículo enviado, notas, feedback, etapa da rejeição e próximo retorno.
A área **Candidaturas** lista somente vagas cujo acompanhamento foi salvo.
**Cadastrar vaga** permite incluir uma oportunidade manualmente.
É possível editar ou excluir o acompanhamento com confirmação, sem apagar a coleta.

Os dados são salvos no banco local `.dashboard_data/applications.sqlite3`, não no
navegador nem nos CSVs. A mesma URL compartilha acompanhamento entre execuções.
Para backup, encerre o dashboard e copie esse arquivo. Para usar outro banco:
`.\dashboard.bat --tracking-db "E:\meu_acompanhamento\applications.sqlite3"`.

As colunas de score (geral e por critério), justificativa, pontos fortes, lacunas,
perfil versionado e origem da análise estão preparadas para a próxima etapa.
Todos os scores ficam **Não analisado** até existir uma análise real.
Filtros por status, etapa alcançada, resposta, prioridade, datas da aplicação,
retorno pendente e score são combináveis com os filtros de coleta.
Consulte [PROFILE_SCORING.md](PROFILE_SCORING.md) para a estrutura de integração futura.

- Visão geral, empresas, vagas/candidaturas, contatos, páginas de carreira e histórico de execuções.
- Busca sem distinção de maiúsculas ou acentos, com todos os termos combinados.
- Filtros por uma ou várias execuções (Ctrl/Cmd), datas inclusivas, categoria, status,
  empresa/domínio, acessibilidade, IA configurada, modelo, versão, arquivo de entrada,
  presença de contatos/vagas/carreiras/erros,
  conteúdo de e-mails, títulos e URLs, e contagens de páginas, vagas e e-mails.
- Ordenação por mais recente, mais antigo, nome, domínio ou quantidade de resultados.
- **Ocultar repetidos**: mantém a ocorrência mais recente (ou mais antiga) entre os
  resultados que passaram pelos filtros. Empresas são únicas por domínio; contatos por
  endereço de e-mail; vagas e carreiras por URL. A ordenação não altera qual ocorrência
  é mantida. O painel não mescla dados de datas diferentes nem apaga os arquivos originais.
- Detalhes com links, evidências, erros e histórico completo de cada domínio.
- Exportação CSV de todas as linhas filtradas, incluindo as regras de repetição,
  independentemente da página exibida. Filtros ficam na URL, permitindo salvar a consulta.

`results_all.csv` é a fonte principal; `results_valid.csv` é usado apenas quando o primeiro
não existe. `checkpoint.json` serve como alternativa quando não há esses CSVs. `jobs.csv`
preserva a correspondência entre títulos e URLs. Uma execução com arquivos inválidos
gera aviso no painel. Resultados em pastas ocultas, ambientes virtuais e links simbólicos
não são carregados.

As datas exibidas vêm dos arquivos do crawler, que podem não ter fuso horário explícito.
O campo `finished_at` representa a última gravação registrada, não uma confirmação de
conclusão. “IA habilitada” informa a configuração registrada, não o sucesso da API.
Uma página de carreira ou uma candidatura espontânea não comprova vaga aberta.

Verificações do dashboard:

```powershell
py -m unittest discover -s tests
node tests/dashboard_logic.mjs
# Com o navegador Playwright instalado, validar CRUD em um banco temporário:
.\.venv\Scripts\python.exe tests/tracking_browser.py
```

V2 corrige os falsos positivos encontrados no primeiro teste.

## Principais mudanças
- e-mails são extraídos apenas de texto visível e `mailto:` (não de bundles JavaScript);
- validação de domínio/TLD, placeholders e endereços técnicos;
- e-mails de domínio alheio ao site são descartados, exceto provedores públicos comuns;
- `Careers URLs` só aceita URL validada cujo próprio endereço indica careers/jobs/hiring etc., ou ATS conhecido;
- `/about`, `/docs`, `/help`, `/pool`, `/rewards` não viram Careers só porque o texto menciona palavras ambíguas;
- ATS recebe um salto adicional controlado para tentar coletar vagas reais;
- páginas de challenge/anti-bot (`Just a moment...`, CAPTCHA etc.) não são marcadas como site acessível normal;
- vagas determinísticas sob `/careers/...` e `/jobs/...` são preservadas mesmo sem IA;
- preflight da OpenAI é feito uma única vez. Se billing/chave falhar, a IA é desativada para o run, evitando dezenas de erros/retries.

## Instalação
1. Rode `install_browser.bat` uma vez.
2. Copie `.env.example` para `.env`.
3. Coloque `OPENAI_API_KEY` localmente em `.env` (não compartilhe a chave em chat).

`OPENAI_MODEL` é opcional e, quando definido, substitui `ai_model` de `config.json`.
As demais opções do crawler ficam em `config.json`; argumentos de linha de comando
como `--model`, `--concurrency`, `--no-ai` e `--no-robots` têm precedência.

## Testar somente a API

```powershell
.\run.bat --test-api
```

Ou:

```powershell
.\.venv\Scripts\python.exe crawler.py --test-api
```

O comando termina com código `0` somente quando a API confirma o teste. Chave ausente,
billing indisponível, modelo inválido ou resposta inesperada terminam com código `1`.

## Repetir o teste dos 10 primeiros do zero

Use uma pasta de saída nova para não misturar checkpoint V1/V2:

```powershell
.\.venv\Scripts\python.exe crawler.py domains.csv --limit 10 --fresh --out output_v2
```

## Rodar sem IA enquanto o billing é corrigido

```powershell
.\.venv\Scripts\python.exe crawler.py domains.csv --limit 10 --fresh --out output_v2_noai --no-ai
```

## Retomar uma execução

Cada execução nova continua sendo criada em uma subpasta com timestamp. Para retomar
a execução mais recente que tenha `checkpoint.json`:

```powershell
.\.venv\Scripts\python.exe crawler.py domains.csv --out output_v2 --resume
```

Para escolher uma execução específica:

```powershell
.\.venv\Scripts\python.exe crawler.py domains.csv --resume "output_v2\2026-10-03_10-30-00"
```

`--resume` preserva a data inicial, ignora domínios já concluídos e grava os resultados
na mesma pasta. Checkpoints inválidos interrompem o comando em vez de serem sobrescritos.
`--resume` e `--fresh` não podem ser combinados.

## Coleta responsável e execução em lote

Por padrão, o crawler:

- respeita `robots.txt` para HTTP e para o fallback de navegador;
- identifica-se como `SiteIntelCrawler/2.1.4`;
- aplica intervalo mínimo por host;
- repete timeouts, erros de rede, HTTP 408/425/429 e erros 5xx com backoff;
- processa até três domínios em paralelo, mantendo somente um navegador e uma chamada
  de IA simultâneos por padrão.

Esses valores podem ser ajustados em `config.json`. `robots_fail_closed: false` permite
a coleta quando `robots.txt` está temporariamente indisponível; respostas 401/403 ao
arquivo continuam sendo tratadas como bloqueio. Use `--no-robots` somente quando houver
uma decisão explícita para ignorar as regras publicadas pelo site.

Antes dos 321 domínios, faça um lote pequeno:

```powershell
.\.venv\Scripts\python.exe crawler.py domains.csv --limit 20 --fresh --out output_validacao
```

Confira erros, bloqueios, e-mails e URLs de vaga no dashboard. Depois execute o lote
completo usando uma pasta própria e retome a mesma execução se houver interrupção.

## Saídas
- `results_all.csv`: auditoria completa, inclusive falhas/anti-bot.
- `results_valid.csv`: apenas sites efetivamente acessíveis.
- `checkpoint.json`: retomada.
- `run_info.json`: inclui `completed` e `resumed`, usados pelo dashboard para diferenciar
  execução concluída de execução em andamento ou interrompida.

Não rode os 321 antes de conferir um lote pequeno da versão atual.
