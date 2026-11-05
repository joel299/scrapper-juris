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
- Nenhuma dependência Python de terceiros para executar o servidor ou os testes.
- Acesso à rede para buscar conjuntos publicados no Portal de Dados Abertos do STJ.
- Navegador moderno para a interface.

## Como executar

Clone o repositório e inicie o servidor:

```bash
git clone https://github.com/joel299/scrapper-juris.git
cd scrapper-juris
python -m app.server
```

Acesse <http://127.0.0.1:8000>. O servidor escuta somente em loopback por segurança. Não o exponha diretamente à internet: esta versão não oferece autenticação remota nem TLS.

### Configuração

- `PORT`: porta HTTP local (padrão `8000`).
- `SCRAPER_DATA_DIR`: diretório de persistência local (padrão `./data`).
- `SCRAPER_HOST`: permanece limitado a `127.0.0.1`, `localhost` ou `::1`; o servidor recusa outros endereços.

Exemplo:

```bash
PORT=8080 SCRAPER_DATA_DIR="$HOME/.local/share/scrapper-juris" python -m app.server
```

A base é criada automaticamente em `data/scraper.sqlite3`. Ela é ignorada pelo Git e não deve ser commitada.

## Como usar

1. Clique em **Nova captura** e teste o armazenamento SQLite local.
2. Cadastre uma página de conjunto oficial, por exemplo: `https://dadosabertos.web.stj.jus.br/dataset/espelhos-de-acordaos-primeira-secao`.
3. Selecione a fonte e inicie a captura.
4. Acompanhe o processamento no painel e abra uma execução para consultar itens e proveniência.

O teste de conexão verifica apenas o SQLite local; ele não configura conexão com Supabase ou PostgreSQL.

## API local

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/api/health` | Estado do servidor e armazenamento local |
| `GET` | `/api/sources` | Lista fontes cadastradas |
| `POST` | `/api/sources` | Cadastra `{ "source_url": "https://...", "dataset": "..." }` |
| `GET` | `/api/runs` | Lista execuções recentes |
| `POST` | `/api/runs` | Inicia execução com `{ "source_ids": ["..."] }` |
| `GET` | `/api/runs/{id}` | Consulta execução, recursos e itens |
| `POST` | `/api/connection/test` | Testa acesso ao SQLite local |

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
pyproject.toml         Metadados e requisitos do projeto
SECURITY.md            Política de reporte de vulnerabilidades
```

## Limitações conhecidas

Esta versão é executada localmente e usa SQLite. Ainda não se integra a outro sistema, não lê fontes de um banco externo, não oferece autenticação/autorização remota, fila durável, cancelamento de execução ou worker separado. O processamento em segundo plano ocorre em threads do próprio processo; reiniciar o servidor interrompe tarefas em andamento.

A descoberta inicial busca links `.json` e `.zip` em páginas oficiais. Estruturas JSON não reconhecidas são rejeitadas ou mantidas como payload original; nenhum outro tribunal ou produto jurídico é declarado como suportado. A captura de conjuntos grandes deve ser avaliada antes de iniciada.

## Segurança

- Nunca envie credenciais reais a esta aplicação local nem a inclua em issues, logs ou commits.
- Não exponha o servidor fora de loopback sem implementar autenticação, autorização e TLS.
- O acesso às fontes externas é restrito por allowlist de host, esquema e porta, inclusive em redirecionamentos.
- Consulte `SECURITY.md` para reportar vulnerabilidades.
