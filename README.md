# Scrapper Juris — importador local de jurisprudência do STJ

[![CI](https://github.com/joel299/scrapper-juris/actions/workflows/tests.yml/badge.svg)](https://github.com/joel299/scrapper-juris/actions/workflows/tests.yml)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![SQLite](https://img.shields.io/badge/SQLite-local%20storage-003B57?logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![HTML5](https://img.shields.io/badge/HTML5-interface-E34F26?logo=html5&logoColor=white)](https://developer.mozilla.org/docs/Web/HTML)
[![CSS3](https://img.shields.io/badge/CSS3-responsive-1572B6?logo=css3&logoColor=white)](https://developer.mozilla.org/docs/Web/CSS)
[![JavaScript](https://img.shields.io/badge/JavaScript-vanilla-F7DF1E?logo=javascript&logoColor=222)](https://developer.mozilla.org/docs/Web/JavaScript)
[![STJ Dados Abertos](https://img.shields.io/badge/Fonte-STJ%20Dados%20Abertos-8A6D3B)](https://dadosabertos.web.stj.jus.br/)

Aplicação web local para descobrir, baixar, validar e importar arquivos JSON/ZIP públicos de **Espelhos de Acórdãos** do Superior Tribunal de Justiça (STJ). Mantém histórico das execuções e permite acompanhar registros capturados, persistidos, duplicados e com falha.

> Espelhos de Acórdãos são registros estruturados de jurisprudência. Este projeto não representa uma base completa de processos, movimentações ou peças processuais.

## O que faz

- Cadastra páginas oficiais de conjuntos de dados do STJ.
- Descobre links JSON e ZIP publicados nas páginas cadastradas.
- Executa cada captura em segundo plano e mantém seu estado em SQLite.
- Valida respostas HTTP e o formato do conteúdo; não aceita uma página HTML como JSON.
- Lê JSON e arquivos JSON dentro de ZIP em memória, preservando os campos originais.
- Rejeita caminhos inseguros e arquivos ZIP com expansão ou quantidade excessiva.
- Calcula SHA-256, registra proveniência e faz upsert idempotente por tribunal, conjunto e identificador do registro.
- Exibe execuções, recursos, contadores, duplicidades e falhas em uma interface responsiva.

A coleta usa apenas HTTPS e domínios oficiais do STJ permitidos pelo servidor. Redirecionamentos também são validados. Se a fonte negar acesso, a tentativa é interrompida; não há contorno de bloqueios. Os limites atuais são 64 MiB por download, 256 MiB descompactados e 5.000 arquivos por ZIP.

## Requisitos

- Python 3.11 ou superior.
- Driver PostgreSQL `psycopg` instalado pelas dependências do projeto.
- Acesso à rede para buscar conjuntos publicados no Portal de Dados Abertos do STJ.
- Navegador moderno para a interface.

## Como executar

Clone o repositório e inicie o servidor:

```bash
git clone https://github.com/joel299/scrapper-juris.git
cd scrapper-juris
python -m pip install -e .
python -m app.server
```

Acesse <http://127.0.0.1:8000>. O servidor escuta somente em loopback por segurança. Não o exponha diretamente à internet: esta versão não oferece autenticação remota nem TLS.

### Configurar o PostgreSQL com segurança

1. Copie `.env.example` para `.env` (ou use o `.env` local criado no projeto).
2. Defina `DATABASE_URL` com a URI do PostgreSQL/Supabase. Não envie a URI pelo chat e não a cole no formulário web.
3. Configure `DATABASE_SCHEMA` (padrão `public`) e mantenha `PGSSLMODE=require`, salvo se seu provedor exigir `verify-full`.
4. Restrinja as permissões do usuário da conexão ao mínimo necessário. Para a fase de inspeção, acesso de leitura aos metadados basta; não conceda escrita até aprovarmos o mapeamento.
5. Reinicie o servidor depois de alterar `.env`, abra **Nova captura**, teste a conexão e use **Inspecionar tabelas/colunas**.

`.env` é excluído do Git. Não compartilhe o conteúdo dele nem inclua uma URI real em capturas, issues ou logs. Uma URI contém senha, host e nome do banco; se ela for exposta, revogue a credencial e gere outra.

Variáveis adicionais:

- `PORT`: porta HTTP local (padrão `8000`).
- `SCRAPER_DATA_DIR`: diretório de persistência local do histórico (padrão `./data`).

A base local de histórico é criada em `data/scraper.sqlite3`. Ela permanece distinta do PostgreSQL de destino.

## Como usar

1. Configure `DATABASE_URL` no arquivo `.env` e reinicie o servidor.
2. Em **Nova captura**, teste a conexão PostgreSQL (a URI nunca aparece no navegador).
3. Use **Inspecionar tabelas/colunas** e forneça a definição do schema seguindo [`DATABASE_SCHEMA_GUIDE.md`](DATABASE_SCHEMA_GUIDE.md).
4. A gravação de execuções no PostgreSQL permanece bloqueada até aprovar a tabela de destino, o mapeamento de campos, a chave de idempotência e as permissões mínimas.

O SQLite local guarda fontes e histórico do protótipo; não é o banco PostgreSQL de destino. O teste não grava no banco externo.

## API local

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/api/health` | Estado do servidor e indicação de URI PostgreSQL configurada (sem expor a URI) |
| `GET` | `/api/database/schema` | Lista tabelas e colunas visíveis no schema configurado, sem ler linhas |
| `GET` | `/api/sources` | Lista fontes cadastradas localmente |
| `POST` | `/api/sources` | Cadastra `{ "source_url": "https://...", "dataset": "..." }` |
| `GET` | `/api/runs` | Lista execuções locais recentes |
| `POST` | `/api/runs` | Inicia execução local com `{ "source_ids": ["..."] }`; fica bloqueada se `DATABASE_URL` está configurada, até confirmar mapeamento de escrita |
| `GET` | `/api/runs/{id}` | Consulta execução, recursos e itens |
| `POST` | `/api/connection/test` | Testa SQLite e, se configurado, conexão PostgreSQL; não grava dados |

## Testes e verificações

Execute os testes unitários com a biblioteca padrão:

```bash
python -m unittest discover -s tests -v
```

A suíte cobre validação de URLs e redirecionamentos, conteúdo HTML inesperado, preservação de campos JSON, segurança de ZIP e persistência idempotente. Os testes usam dados sintéticos e não consultam serviços externos.

## Estrutura do projeto

```text
app/server.py          Servidor HTTP, API, ingestão e persistência SQLite
static/index.html      Interface web responsiva
tests/                 Testes automatizados
data/                  Banco SQLite local (não versionado)
.github/workflows/     Verificação contínua
pyproject.toml         Metadados e dependências do projeto
.env.example           Modelo de configuração (sem credenciais reais)
DATABASE_SCHEMA_GUIDE.md Informações de schema necessárias para habilitar escrita
SECURITY.md            Política de reporte de vulnerabilidades
```

## Limitações conhecidas

A aplicação pode testar uma conexão PostgreSQL e inspecionar nomes e tipos de tabelas/colunas visíveis à role. A gravação de acórdãos no PostgreSQL ainda está desabilitada até o schema real ser revisado e o destino/mapeamento/idempotência serem confirmados. Fontes e histórico local ainda usam SQLite. O protótipo não oferece autenticação/autorização remota, fila durável, cancelamento ou worker separado; tarefas em segundo plano usam threads do processo e são interrompidas quando o servidor encerra.

A descoberta busca links `.json` e `.zip` em páginas oficiais. Estruturas JSON não reconhecidas são rejeitadas ou mantidas como payload original. Nenhum outro tribunal é declarado como suportado. A captura de conjuntos grandes requer validação do formato e dos limites antes da execução.

## Segurança

- Nunca envie a URI, senha ou tokens pelo chat, navegador, issue, log ou commit; mantenha-os no `.env` local, ignorado pelo Git.
- Não exponha o servidor fora de loopback sem implementar autenticação, autorização e TLS.
- Use role PostgreSQL de privilégio mínimo; até revisar o schema, conexão somente para teste/inspeção.
- O acesso às fontes externas é restrito por allowlist de host, esquema e porta, inclusive em redirecionamentos.
- Consulte `SECURITY.md` para reportar vulnerabilidades.
