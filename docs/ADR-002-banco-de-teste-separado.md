# ADR-002: Banco de teste separado (elimina o TRUNCATE do banco de dev)

**Status:** Aceito
**Data:** 2026-07-08
**Decisores:** Fernando (produto) · sessão Claude (implementação)

## Contexto

A suíte pytest do backend é de INTEGRAÇÃO (PG18 + RLS real) e cada arquivo
`TRUNCA` tabelas para isolar seus casos. Não havia banco de teste: o `.env` e as
conexões de teste apontavam para o banco de DEV (`contrato_visto`). Consequências:

- Rodar `pytest` destruía o seed de dev (cliente/caso demo, `pricing_configs`,
  e — na suíte de users — o próprio usuário demo), exigindo re-semear à mão.
- O medo do TRUNCATE bloqueava rodar a suíte para validar mudanças (ex.: a
  correção da triagem, ADR-001, teve testes de integração atualizados mas
  **não executáveis** com segurança).
- Acoplamento oculto: os testes assumiam que o banco de dev já continha as
  organizações-base (`…0001` sistema, `…00ff` isolamento) por causa das FKs
  `organization_id -> organizations`. Nunca foram self-contained.

Detalhe estrutural que travava a solução simples: cada um dos ~17 arquivos de
teste tinha a conexão admin (superuser `dbadmin`, que bypassa RLS) **hardcoded**
com `dbname="contrato_visto"`. Não havia fonte única.

## Decisão

Banco separado `contrato_visto_test` no mesmo container, com o MESMO schema do
dev, e três peças:

1. **`tools/setup_test_db.py`** — (re)constrói o banco de teste de forma
   reproduzível: drop/create, clona o schema do dev (`pg_dump --schema-only`,
   garantindo paridade exata sem copiar dados) e semeia as 2 orgs-base
   (idempotente). Guardrail: aborta se `TEST_DB_NAME == DEV_DB_NAME`.
2. **`tests/_dbadmin.py`** — fonte ÚNICA da conexão admin dos testes; lê o
   `dbname` de `DB_NAME` (nunca hardcode). Os 17 arquivos passam a delegar a
   `admin_conn()`.
3. **`conftest.py`** — força `DB_NAME` para o banco de teste (redirecionando
   TANTO o app via env `DB_*` QUANTO o `_dbadmin`) e **aborta a coleta** se o
   alvo for o banco de dev (defesa em profundidade contra o TRUNCATE).

Tudo configurável por env: `PG_CONTAINER`, `DEV_DB_NAME`, `TEST_DB_NAME`,
`DB_ADMIN_USER`, `DB_ADMIN_PASS`.

## Opções consideradas

### A: banco de teste + clone de schema + redirect no conftest (escolhida)
| Dimensão | Avaliação |
|---|---|
| Isolamento do dev | Total (guardrail duplo: nome ≠ dev, e conftest aborta) |
| Paridade de schema | Exata (clone do dev; sem drift de re-aplicar migrations) |
| Fonte da conexão | Única (`tests/_dbadmin`) |
| Reprodutibilidade | `python -m tools.setup_test_db` |

**Prós:** destrava a suíte; dev intocável por construção; sem reescrever asserts.
**Contras:** clone é local (orquestra o container Docker); CI real virá na Fase 7.

### B: `.env.test` + trocar cada conexão hardcoded manualmente
**Contras:** 17 edições sem fonte única; fácil esquecer uma e truncar o dev; não
resolve o guardrail.

### C: transações com rollback por teste (sem TRUNCATE)
**Contras:** refactor profundo de toda a suíte (fixtures, savepoints); os handlers
abrem suas próprias transações (tenant_tx) — rollback externo não isola de forma
confiável. Desproporcional.

## Consequências

- **Fica garantido:** `pytest` nunca toca o dev. Provado: baseline do dev
  (cases=15, requests=13, triage_modules=67, users=1, …) **idêntico** após rodar
  a suíte inteira (332 testes, com todos os TRUNCATEs).
- **Destravou a verificação:** a suíte rodando pegou 2 asserts `8→5` que a
  correção da triagem (ADR-001) tinha deixado passar por não ser executável.
- **Regra nova:** conexão admin de teste só via `tests/_dbadmin.admin_conn`
  (nunca hardcode de `dbname`). Novo arquivo de teste segue o mesmo import.
- **Recriar o banco:** `python -m tools.setup_test_db` (idempotente). Necessário
  se o schema do dev mudar (nova migration) — o clone reflete o dev atual.
- **Seed mínimo:** só as 2 orgs-base. A migração 005 seed de org NÃO vem no clone
  schema-only; o script as recria. Nenhum teste trunca `organizations`.
- **Pendência de dev (fora deste ADR):** o seed do dev (`tools/seed_demo.py`)
  ainda não cobre recriar org/usuário demo + pricing após a suíte de users —
  mas isso agora é irrelevante para a suíte (que usa o banco separado); só
  importa se alguém ainda rodar pytest apontado ao dev (o guardrail impede).

## Verificação

- `python -m tools.setup_test_db` → 25 tabelas, 2 organizations.
- `pytest tests/` → **332 passed** (antes: 2 falhas de contagem herdadas do ADR-001).
- Baseline do dev antes vs. depois da suíte: **idêntico** (hazard eliminado).
