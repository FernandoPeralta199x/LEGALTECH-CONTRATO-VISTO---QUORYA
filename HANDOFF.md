# HANDOFF — Contrato Visto (sessões 2026-07-06 → 2026-07-08)

> Cobre os DOIS repositórios:
> - **Backend**: `contrato_visto_backend-main` (branch `feat/fundacao-v2-multitenant`)
> - **Frontend**: `contrato_visto_frontend` (branch `main`)

---

## 0. Sessão 2026-07-08 — correções críticas (mais recente)

**3 commits no backend `feat/fundacao-v2-multitenant` — SEM push:**
| SHA | Mensagem |
|---|---|
| `22b6e87` | fix(triage): executa apenas os módulos comerciais selecionados (ADR-001) |
| `addb089` | test(infra): banco de teste separado — elimina o TRUNCATE no dev (ADR-002) |
| `c5dd17a` | feat(triage): liga o módulo targetdata (comprado → roda) nos 4 planos |

1. **BUG GRAVE corrigido — a triagem só roda o que foi comprado** (`docs/ADR-001-triagem-por-selecao.md`). `create_request` gravava `triage_modules` a partir do plano FIXO do produto (`plan_for_product`), ignorando a seleção do wizard — que só ia para o preço. Conectores não comprados (serasa/procon/escavador) rodariam e, na Fase 7, consumiriam **APIs pagas indevidamente**. Correção: cada `TriageModuleDefinition` declara `billing_module` (`None` = infra do produto); nova `plan_for_selection(product, selected)` filtra pela MESMA lista normalizada da cobrança (CVS-008). **Regra nova:** todo caminho que crie `triage_modules` deve usar `plan_for_selection`. Provado E2E nos 4 produtos + execução real (pagamento mock → `/triage/run` → só providers comprados).
2. **Banco de teste separado — hazard do TRUNCATE ELIMINADO** (`docs/ADR-002-banco-de-teste-separado.md`). `contrato_visto_test` (clone schema-only do dev). `pytest tests/` usa esse banco e **NÃO toca o dev** (provado: baseline do dev idêntico após rodar 334 testes). Peças: `tools/setup_test_db.py` (reconstrói: drop/create + clone + seed das 2 orgs-base), `tests/_dbadmin.py` (conexão admin única, lê `DB_NAME`; 17 arquivos deixaram de hardcodar o dev), `conftest.py` (força `DB_NAME`→teste + **ABORTA a suíte se apontar p/ o dev**). Recriar: `python -m tools.setup_test_db`.
3. **targetdata ligado** (decisão do usuário): era vendido (obrigatório em `dados_partes`, R$ 39) mas nunca executava; agora tem módulo técnico (provider `mock_targetdata`, já com binding no registry) nos 4 planos e roda **só quando comprado**.

**Suíte hoje: 334 passed** (no banco de teste). Rodar a suíte revelou 2 asserts `8→5` residuais do ADR-001 (não executáveis antes) — corrigidos em `addb089`.

> ⚠️ **Correções de fato do HANDOFF anterior** (validadas nesta sessão): (a) §5 "maskPhone diverge" é **FALSO** — as duas implementações são idênticas (fuzz de 200k inputs); (b) §2 "pytest 27/27" era uma seleção parcial. Ver §2 e §5, já corrigidos abaixo.

---

## 1. Objetivo

Projeto: **Contrato Visto** — LegalTech serverless multi-tenant (Lambda Python 3.11 + API Gateway simulados localmente por `tools/local_server.py`; PG18 com RLS por organização; frontend Next.js 16). Fase atual: **MVP local**, qualidade/organização **pré-Fase 7 (AWS/HOPE)**.

Objetivos desta sessão (todos concluídos):
1. Reorganizar a tela `/admin/pricing` (layout aprovado: **F editor+prévia ao vivo + G barra fixa**) sem perder nada, + melhorias visuais aprovadas por mockup.
2. Varredura técnica de qualidade/código morto no frontend (protocolo `CLAUDE_VARREDURA_...md`) + execução do plano de correção.
3. Backlog da varredura: **god files** (`cases.ts`, `cases/[id]/page.tsx`, `get_case_aggregate`), **pip-audit**, **#4 dedup**, **#8 preço hardcoded**.

## 2. Estado atual

**Tudo verde:**
- Frontend: `tsc` 0 · `eslint --max-warnings=0` 0 · `next build` 0 · **92/92 testes**.
- Backend: `py_compile` OK · **pytest 334/334** (suíte inteira, no banco de teste `contrato_visto_test` — ver §0) · **pip-audit: "No known vulnerabilities found"**. (O "27/27" de 2026-07-07 era só a seleção `test_cases_handlers + aggregate`, não a suíte inteira.)
- E2E no navegador: pricing, página do caso (partes/validação), wizard — sem erros de app no console.

**Commits SEM push (aguardando `git push` manual do usuário):**
| Repo | SHA | Mensagem |
|---|---|---|
| frontend | `532d2ba` | refactor(cases): quebra o god-file cases.ts em 4 módulos |
| frontend | `2bd7480` | refactor(cases): extrai useCasePartiesEditor |
| frontend | `67d77db` | refactor(quality): dedup JWT/email/máscaras + preço fallback = estimativa |
| backend | `15c75e4` | refactor(cases): fatia get_case_aggregate em _agg_* |
| backend | `27f775b` | chore(tools): pip-audit via truststore + deps pinadas |
| backend | `22b6e87` | fix(triage): triagem só roda módulos comprados (ADR-001) — **sessão 2026-07-08** |
| backend | `addb089` | test(infra): banco de teste separado (ADR-002) — **sessão 2026-07-08** |
| backend | `c5dd17a` | feat(triage): liga o módulo targetdata — **sessão 2026-07-08** |

Já no remoto (pushados antes): frontend até `7d53d9e`; backend até `454b518` (⚠️ a `origin/main` do backend avançou via PR #2 `be329da` — a `main` local está behind 2; dar `fetch` antes de usá-la).

**Ambiente de dev (no ar ao fim da sessão):**
- Postgres docker `cv-pg18` :5433 · backend `tools/local_server.py` :8000 (56 rotas) · frontend `npm run dev` :3000.
- DB dev re-semeado via `python -m tools.seed_demo`: org demo com **Cliente Demo Ltda + caso `REQ-2026-0033`** (`40d35dba-133e-4510-825e-940e06929129`) + 1 documento. Login: `demo@contratovisto.com` / `DemoLocal#2026`.
- ⚠️ Backend **não tem hot-reload** — reiniciar `local_server.py` após editar handler Python.

## 3. Arquivos mexidos nesta sessão

**Frontend** (`main`):
- `src/services/cases.ts` (reescrito, só API) + **novos** `cases.dto.ts`, `cases.mappers.ts`, `cases.fallback.ts`
- `src/app/cases/[id]/page.tsx` + **novo** `src/lib/useCasePartiesEditor.ts`
- **novo** `src/lib/devJwt.ts`; `src/lib/validation.ts`, `src/services/auth.ts`, `src/lib/clientForm.ts` (dedup)
- `components/cases/wizard/{ModuleRow,ModulesStep,ProductCard}.tsx`, `lib/produtoConfig.ts` (preço = estimativa)
- Antes (já pushados): `src/app/admin/pricing/page.tsx`, `components/pricing/InstallmentConfigCard.tsx`, `src/app/globals.css`, `components/AppLayout.tsx`, `src/services/{documents,finalReports}.ts` (+testes), `types/domain.ts`, `components/cases/wizard/types.ts`

**Backend** (`feat/fundacao-v2-multitenant`):
- `src/handlers/cases.py` (refactor `get_case_aggregate` → helpers `_agg_*`)
- **novos** `tools/pip_audit.py`, `tools/seed_demo.py`; `requirements-dev.txt` (pip-audit==2.10.1, truststore==0.10.4)
- Antes (já pushados): `src/handlers/documents.py` (filtro `?classification=` + `metadata.kind`), `tests/test_documents_handlers.py`

## 4. O que mudou (resumo por tema)

1. **`/admin/pricing` reorganizado (F+G)**: coluna editor + prévia ao vivo (estimate real do backend) + barra fixa (status/Salvar/atalhos). Travas **P-1** (não salva sem método de pagamento) e **P-2** (aviso parcelamento sem cartão). Melhorias visuais aprovadas: parcelas agrupadas (à vista + grade), módulos com preço efetivo + selo padrão/personalizado + editar, limite compacto, parcelamento em 2 grupos, contraste AA (`--ok`/`--danger`), atalho "Observações" removido a pedido.
2. **Fix global de CSS**: `overflow-x: hidden → clip` em `html`/`body`/`.cv-app-shell` — `position: sticky` **nunca funcionara no app** e agora funciona (header incluso). `--accent` definido (`var(--teal)`) — estava indefinido em 7 arquivos.
3. **Varredura (4 fixes)**: upload valida `response.ok` (TDD; falha de storage não vira mais "sucesso"); filtro `?classification=final_report` front+back (contador de relatórios não infla mais); guarda por token de carga em `refreshCase` (sem estado stale ao trocar de caso); 11 tipos mortos removidos (knip 55→44).
4. **God files**: `cases.ts` 1586→387+306+697+299; `cases/[id]/page.tsx` 1701→1573 + hook 218; `get_case_aggregate` ~196→handler ~45 + 8 helpers (mesmo SQL/shape, RLS preservada).
5. **pip-audit**: causa-raiz do SSL = CA do proxy/AV fora do bundle do certifi → wrapper `tools/pip_audit.py` com **truststore** (cert store do Windows, sem desligar verificação). Auditoria limpa.
6. **#4 dedup**: `devJwt.ts` = decode único; `isValidEmail`/`maskCpf`/`maskCnpj` fonte única em `lib/cpfCnpj`. **Políticas de claims JWT seguem separadas de propósito** (form aceita role string; sessão exige `DEV_ROLES`).
7. **#8 preço**: `precoCents`/`computeProductBasePrice` do `produtoConfig` agora são **fallback marcado como estimativa** na UI (ModuleRow "estimada", ProductCard `~`, EstimateCard "estimativa local"); comentário contraditório corrigido (backend = fonte de verdade).
8. **`tools/seed_demo.py`**: re-semeia 1 cliente + 1 caso + 1 doc via handlers reais após o pytest truncar o dev.

## 5. O que falhou / limitações honestas

- **Scroll-spy do `/admin/pricing` não foi verificado visualmente**: a aba de automação fica `visibility: hidden` e o navegador suspende eventos de scroll/rAF nesse estado. A lógica confere por conta manual (DOM/posições), mas **falta confirmação numa aba ativa** — testar manualmente rolando a página.
- **Ponto "ativo/planejado" dos módulos (mockup #2 do pricing) NÃO implementado**: não existe campo de status confiável no catálogo; optei por não inventar status. Se quiser, requer flag explícita no backend.
- **`maskPhone` — a alegação anterior de divergência é FALSA** (corrigido 2026-07-08): as duas implementações (`lib/cpfCnpj.ts` regex vs `src/lib/clientForm.ts` slice) produzem saídas **idênticas** (provado por análise estática + fuzz de 200k inputs). Deduplicar é seguro; falta corrigir o comentário errado em `src/lib/clientForm.ts:57-59` (tarefa #10 do backlog).
- ~~**Suíte COMPLETA do pytest** trunca `users`/`pricing_configs`~~ **RESOLVIDO 2026-07-08** (ADR-002): a suíte agora usa o banco de teste `contrato_visto_test` e não toca o dev. O `seed_demo.py` deixou de ser rede de segurança urgente (vira ferramenta de reset intencional do dev).
- **Relatórios finais antigos** (enviados antes do fix de `classification`) não aparecem na lista filtrada — sem retrofill (irrelevante em dev, banco re-semeado).
- **`npm audit`: 2 moderadas em aberto** (postcss <8.5.10 XSS, transitivo do `next`) — o fix é breaking; aguardar bump do next.
- **God files residuais**: `cases/[id]/page.tsx` ainda tem 1573 linhas (só o subdomínio de partes saiu); `admin/pricing/page.tsx` cresceu p/ ~944 na reorg (candidato: extrair a Prévia em componente).
- **knip ainda lista** ~21 funções + 44 tipos "internal-only" (export redundante, cosmético — decisão: não tocar).

## 6. Fase 7 / HOPE (adiado de propósito — gate: usuário dizer "ativar HOPE")

- **S-04..S-07** do hardening: CORS restritivo, fluxo real de signup, IAM do SQS, Cognito real.
- **CSP com nonces** (hoje `unsafe-inline/eval` p/ Next dev — comentário em `next.config.js`).
- **S3 real**: o fix do upload (`response.ok`) só se manifesta de verdade com storage ligado — retestar na fase AWS; barra de progresso por bytes (XHR) e confirmação HeadObject de tamanho (S-02) também são da fase AWS.
- **Gateway de pagamento real**: `PAYMENT_MODE=mock` é dev-only; produção = hosted fields/iframe (SAQ A).
- **Deploy AWS completo** (infra serverless real) — task pendente #28.

## 7. Próximos passos (em ordem)

1. **Push** (usuário, manual): `frontend: git push origin main` (ahead 3) · `backend: git push origin feat/fundacao-v2-multitenant` (ahead **6** — inclui os 3 commits da sessão 2026-07-08). Depois `git fetch` no backend (a `origin/main` avançou via PR #2).
2. **Teste manual** das telas: `/admin/pricing` (rolar p/ conferir scroll-spy/barra fixa), wizard Novo Pedido (preços sem `~`; agora a triagem só cria os módulos comprados), caso REQ-2026-0033 (partes/documentos/relatório). ⚠️ Casos criados ANTES do fix da triagem mantêm o plano cheio — criar pedidos NOVOS pelo wizard para ver o comportamento correto.
3. ~~Banco de teste separado~~ **FEITO** (§0, ADR-002). Recriar quando o schema do dev mudar: `python -m tools.setup_test_db`. Rodar suíte: `python -m pytest tests/`.
4. (Opcional) **Estender `seed_demo.py`** — rebaixado a conveniência (não é mais rede de segurança); criar o caso demo via wizard (`create_request`) para ele ter pedido/preço e ser pagável (o `REQ-2026-0033` atual nasceu sem `request_id` — tela de pagamento fica intestável, 404 no POST /payment).
5. (Opcional) **Guarda de tela p/ caso sem pedido** (tarefa #18): backend devolve 404 "Caso não encontrado" enganoso; a tela mostra R$ 0,00 com "Confirmar" ativo.
6. (Opcional) God files residuais + dedup `maskPhone` (comentário) + `_serialize` morto em `documents.py` + validação server-side da trava P-1.
7. **Fase 7/HOPE** quando autorizada (seção 6).

---
*Atualizado em 2026-07-08 (base 2026-07-07). Regras permanentes: push manual pelo usuário; sem mock/fake data mascarando erro; backend é fonte de verdade; nunca aceitar organization_id/role do frontend; responder em português.*
