# Schema necessário para habilitar gravação PostgreSQL

A conexão e a inspeção de metadados já estão disponíveis. A aplicação ainda bloqueia execuções quando `DATABASE_URL` está configurada: não gravaremos no PostgreSQL até validar o destino, o mapeamento e a estratégia de duplicidade.

## O que preciso receber

Envie somente a definição do schema, sem linhas/dados e sem a URI de conexão. Para mapear a origem e o destino, preciso de:

1. **Tabelas relevantes**: `schema.tabela` e DDL `CREATE TABLE` de `jurisprudence_documents` (ou da tabela que guarda `source_url`) e da tabela que deverá receber os espelhos.
2. **Colunas**: nome, tipo, nulabilidade, default/identity, chaves primárias e estrangeiras.
3. **Restrições e índices únicos**: especialmente a chave que identifica um registro de origem e pode garantir idempotência.
4. **Regras de escrita**: inserir somente, atualizar registros existentes, ou inserir sem atualizar; indicar quais colunas a aplicação pode alterar.
5. **Supabase/PostgreSQL Row Level Security (RLS)**: se habilitada, envie as políticas relevantes e permissões da role de conexão (sem segredos). Informe se a conexão será `service_role`, usuário técnico dedicado ou outro papel; não envie token/chave.
6. **Proveniência necessária**: onde gravar tribunal, conjunto/dataset, ID original STJ, URLs da página e do recurso, licença quando publicada, data de captura, hash e payload original JSON.

Uma captura da tela da inspeção de tabelas/colunas ajuda, mas para constraints, índices e políticas também preciso do DDL correspondente. Exporte somente schema, nunca dados de produção.

## Preparação recomendada

Crie ou escolha um usuário técnico de privilégio mínimo. Para a fase atual, basta conexão TLS e leitura dos metadados. Depois do mapeamento aprovado, conceda somente os privilégios de escrita necessários nas tabelas/colunas de destino. Evite credenciais `service_role` com bypass de RLS quando houver alternativa operacional segura.

O fluxo de ingestão precisará:

- ler apenas os identificadores e `source_url` necessários na tabela de fontes;
- gravar em um destino semanticamente adequado, sem alterar colunas alheias;
- identificar registros de forma estável (`tribunal` + `dataset` + `source_record_id`, ou chave equivalente já existente);
- usar restrição única compatível com essa identidade para impedir duplicações concorrentes;
- registrar recursos e execuções sem apagar nem sobrescrever dados preexistentes;
- tratar dados existentes e colisões antes de propor qualquer migração.

Não execute migrations nem dê permissões amplas antes da revisão. Se você não souber como exportar o schema, use a inspeção do painel, ou peça ao administrador um dump de schema-only das tabelas relevantes, com ownership e grants sensíveis removidos. Não cole aqui o arquivo `.env`, URI, senhas, tokens ou registros reais.
