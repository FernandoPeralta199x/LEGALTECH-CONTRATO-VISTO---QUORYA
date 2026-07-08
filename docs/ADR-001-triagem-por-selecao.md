# ADR-001: Triagem executa apenas os módulos comerciais selecionados

**Status:** Aceito
**Data:** 2026-07-08
**Decisores:** Fernando (produto) · sessão Claude (implementação)

## Contexto

A triagem (SP3) executava o roteiro COMPLETO do produto: `create_request`
(`src/handlers/requests.py`) inseria em `triage_modules` o resultado de
`plan_for_product(product_type)` — um plano FIXO por produto — enquanto a
seleção de módulos do wizard (`selected_modules`, normalizada por
`normalize_selected_modules`/CVS-008) era usada **apenas** para o preço
(`estimate` → `price_snapshot`). `run_case_triage`
(`src/services/triage_runner.py`) executa todas as linhas de `triage_modules`,
cada uma consumindo `query_provider` (adapters mock hoje; APIs reais e pagas na
Fase 7).

Consequência: um pedido de `analise_contratual` só com os obrigatórios
(`ia_deepseek` + `analise_contratual_ia`) gerava e executaria `serasa`,
`procon` e `escavador` — conectores cujos módulos comerciais (`serasa_procon`
R$ 59, `escavador` R$ 60) **não foram comprados**. Execução divergia da
cobrança: cliente pagava pelo que selecionou, sistema rodava tudo.

Agravante estrutural: os dois lados falam vocabulários distintos sem mapeamento —
módulos COMERCIAIS (`src/services/pricing/config.py:MODULES`: escavador,
targetdata, ia_deepseek, serasa_procon, analise_contratual_ia, revisao_humana,
reuniao_equipe) vs módulos TÉCNICOS de triagem (`triage_plan.py`: serasa,
procon, escavador, document_parser, ocr, contract_risk_analysis, …).

## Decisão

Cada `TriageModuleDefinition` declara **`billing_module`** — o código do módulo
comercial que o habilita (`None` = infraestrutura básica do produto, sempre
executa). A nova função **`plan_for_selection(product, selected)`** filtra o
roteiro: entra o módulo técnico cujo `billing_module` é `None` ou está na
seleção normalizada. `create_request` passa a usar `plan_for_selection` com a
**mesma lista** usada na cobrança — execução e billing não podem divergir por
construção. `plan_for_product` permanece como catálogo (roteiro completo) e não
deve decidir execução.

## Opções consideradas

### A: mapear `billing_module` no plano e filtrar na CRIAÇÃO (escolhida)
| Dimensão | Avaliação |
|---|---|
| Complexidade | Baixa (1 campo + 1 função + 1 linha no handler) |
| Consistência billing↔execução | Automática (mesma lista normalizada) |
| UI | Aba de triagem passa a refletir só o comprado (correto) |
| Migração de schema | Nenhuma |

**Prós:** fonte única de verdade no plano; linhas de `triage_modules` já nascem
corretas (auditoria e UI coerentes); runner intocado.
**Contras:** exige manter o mapeamento ao criar novos módulos.

### B: filtrar na EXECUÇÃO (runner consulta o `price_snapshot`)
**Prós:** não muda a criação. **Contras:** `triage_modules` continuaria com
linhas que nunca rodarão ("planejado" fantasma na UI/auditoria); o runner
precisaria conhecer billing; qualquer novo executor teria de repetir o filtro.

### C: unificar os vocabulários comercial/técnico
**Prós:** elimina o mapeamento. **Contras:** refactor amplo (planos, UI,
agregado, testes) desproporcional ao bug; perde a distinção legítima
1 comercial → N técnicos (`serasa_procon` → `serasa` + `procon`).

## Mapeamento (fonte: `src/services/triage_plan.py`)

| Produto | Técnico | `billing_module` |
|---|---|---|
| dados_partes | parties_validation | `None` (infra) |
| dados_partes | serasa, procon | serasa_procon |
| dados_partes | escavador | escavador |
| dados_partes | reputation_summary, ai_summary | ia_deepseek |
| consulta_objeto | object_analysis | `None` (infra) |
| consulta_objeto | public_search | escavador |
| consulta_objeto | document_summary, ai_summary | ia_deepseek |
| analise_contratual | document_parser, ocr | `None` (infra de leitura) |
| analise_contratual | contract_risk_analysis, obligations_mapping | analise_contratual_ia |
| analise_contratual | serasa, procon | serasa_procon |
| analise_contratual | escavador | escavador |
| analise_contratual | ai_report | ia_deepseek |
| reuniao_advogado | preliminary_questions, documents_checklist | reuniao_equipe |
| reuniao_advogado | case_summary, ai_briefing | ia_deepseek |
| reuniao_advogado | lawyer_briefing | revisao_humana |
| (todos os produtos) | targetdata | targetdata (ligado 2026-07-08) |

Semântica: o `required` do módulo TÉCNICO vira exibição/roteiro; quem decide
execução é o `billing_module` (ex.: `serasa` era `required=True` no plano de
`dados_partes`, mas sem `serasa_procon` comprado não roda — regra de negócio).

## Consequências

- **Fica garantido:** nenhum conector/API é consumido sem o módulo comercial
  correspondente comprado (crítico na Fase 7, quando os adapters viram APIs pagas).
- **Fica mais fácil:** auditar um caso (as linhas de `triage_modules` são
  exatamente o que rodará).
- **Passa a ser regra:** qualquer NOVO caminho que crie `triage_modules` deve
  usar `plan_for_selection` (hoje só o `create_request` cria).
- **Casos antigos** (criados antes desta correção) mantêm o plano cheio no
  banco de dev — recriar pedidos pelo wizard para testar o comportamento novo.
- **`targetdata` (RESOLVIDO 2026-07-08):** o mapeamento expôs que era vendido
  (required em `dados_partes`, R$ 39) mas não tinha módulo técnico — pago e nunca
  executado. Decisão do usuário: LIGAR. Adicionado o módulo técnico `targetdata`
  (provider `mock_targetdata`, já com binding no registry + interpretação no
  `triage_runner`) aos 4 planos onde é comprável; roda só quando comprado.
- **Sem módulo técnico por design:** `revisao_humana` e `reuniao_equipe` são
  etapas HUMANAS pós-triagem (sem conector) — registrado por clareza.

## Verificação

- Unit (puro, sem banco): `tests/test_triage_plan.py` — 8/8 (vermelho→verde).
- E2E (local_server + PG18/RLS): pedido `analise_contratual` com seleção mínima
  → 5 módulos, sem serasa/procon/escavador; com `serasa_procon` + `escavador`
  → 8 módulos, conectores presentes.
- Integração (`tests/test_requests_handlers.py`, `tests/test_case_detail_handlers.py`)
  atualizada para as novas contagens + 2 testes novos; **rodar apenas com o
  banco de teste separado** (a suíte trunca o banco de dev).
