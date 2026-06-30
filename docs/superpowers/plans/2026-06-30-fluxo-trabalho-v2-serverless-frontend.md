# Fluxo de Trabalho — Implementação V2 (Serverless + Frontend)

> Baseado na **varredura prática** do produto de referência `legaltech-aws` (FastAPI + Next.js)
> em `localhost:3000`, feita em 2026-06-30 com login real e teste ponta a ponta.
> Objetivo: guiar a construção da **cópia serverless** (`contrato_visto_backend`) + a
> **integração do frontend** a ela.

## 0. Princípio
O `legaltech-aws` é a **referência canônica** (produto pronto). Estamos **copiando-o em
serverless** (AWS Lambda + API Gateway + PG18/RLS multi-tenant). O frontend Next.js é o
mesmo; muda apenas para qual backend ele aponta.

---

## 1. O que a varredura CONFIRMOU (referência)

**Funciona de verdade (FastAPI `/api/v1/*` + PostgreSQL):**
- Auth: `POST /auth/login` (dev_jwt), `GET /me`.
- Clientes: `POST /clients` (201) — CRUD real.
- Casos: criar (caso rápido) + detalhe (abas Visão geral, Partes, Documentos, Timeline, Triagem, Relatório).
- **Wizard "Novo Pedido"** (5 etapas): **`POST /requests` (201)** cria atomicamente, por `case_id`:
  `request` (+ `price_snapshot`) + `case` (status `AWAITING_TRIAGE`) + `case_parties` +
  `document` + **plano de triagem (8 `triage_modules`)** + **timeline de eventos**.
- Triagem: 8 módulos técnicos gerados (providers `mock_document_parser`, `mock_ocr`,
  `mock_ai_summary`, `mock_serasa`, `mock_procon`, `mock_escavador`, `mock_ai_report`),
  status `AGUARDANDO TRIAGEM`, `attempts=0`, `required` sim/não.
- Pricing: `GET /pricing` (catálogo), `GET/PUT /pricing/config` (override + **version**),
  `GET /pricing/config/limit-check` (limite de casos), `POST /pricing/estimate` (total dos
  módulos). Override aplica no cálculo do produto em tempo real (validado).
- Partes com **PII mascarada** (CPF/e-mail/telefone) — LGPD.
- Documento com `OCR: not_started · IA: not_started`.
- Timeline com eventos (system/user): pedido criado, caso criado, parte adicionada,
  documento anexado, plano de triagem criado, wizard concluído.

**Mock / roadmap (declarado na UI):** execução dos módulos (OCR/Serasa/Procon/Escavador/IA),
geração de relatório por IA + PDF, admin real de usuários/RBAC/auditoria, Cognito,
notificações e-mail/WhatsApp, SQS.

---

## 2. Estado da cópia serverless (hoje)
- **Fundação V2**: Parte 1A + 1B + 2 concluídas (multi-tenant org-scoped, RLS por org +
  FORCE + FKs compostas; **152 testes** no PG18). Migrações 005–009.
- **Tabelas estruturais já criadas**: `organizations`, `requests` (+`price_snapshot`,
  `idempotency_key`), `request_code_sequences`, `case_parties`, `triage_modules`,
  `provider_results`, `agent_executions`, `external_queries_cache`, `pricing_configs`.
- **Núcleo migrado**: users/auth (JWT custom), clients, cases, case_results, documents, search/RAG.

---

## 3. GAP (o que falta) — backend + frontend

| Área | Referência faz | Minha cópia | Falta |
|---|---|---|---|
| Fundação 1C | RLS em chunks/embeddings; audit com org | tabelas core multi-tenant | `organization_id`+RLS em `document_chunks`/`document_embeddings`; `audit_log.organization_id` |
| **timeline_events** | tabela de eventos por caso | **não existe** | criar tabela + escrita de eventos |
| **Pedido/Wizard** | `POST /requests` orquestra tudo | só as tabelas | **endpoint orquestrador** + `request_code` sequencial |
| Partes | CRUD `case_parties` + PII mask | tabela | endpoints + masking |
| Triagem | plano (8 módulos) na criação + execução | tabela | gerador do plano (catálogo→módulos) + execução |
| Pricing | catálogo+config+estimate+limit-check | tabela `pricing_configs` | **endpoints** + catálogo em código |
| Documentos | upload + status OCR/IA | upload S3 presigned | ingestão real (OCR→markdown→chunks→embeddings) |
| Filas | (mock) | — | SQS+DLQ+idempotência |
| Adapters externos | (mock) escavador/serasa/procon/targetdata/cnj | — | Protocol+Mock+Real+factory + `external_queries_cache` |
| Agentes IA | (mock) ai_summary/ai_report | — | execução + `agent_executions` + revisão humana |
| **Frontend** | aponta p/ FastAPI `localhost:8000` | — | apontar p/ **API Gateway serverless** + alinhar contratos |

---

## 4. FLUXO DE TRABALHO (fases ordenadas)

### BACKEND SERVERLESS

**Fase A — Fechar Fundação V2 (Parte 1C)** [pequena]
- Migração 010: `organization_id` + RLS/FORCE em `document_chunks`/`document_embeddings`
  (+ FK composta); `audit.audit_log.organization_id` + função `log_audit`.
- `rag.store_chunk/store_embedding` recebem `organization_id`; ajustar chamadores.
- **+ `timeline_events`** (tabela org-scoped: `case_id`, `event_type`, `actor`, `payload`,
  `created_at`) — revelada pela varredura.

**Fase B — Pedido/Wizard (núcleo do produto)** [grande, alto valor]
- Endpoint **`POST /requests`** (handler + serviço) que, em UMA transação `tenant_tx`:
  cria `request` (gera `code` via `request_code_sequences`, `price_snapshot`), `case`
  (`AWAITING_TRIAGE`), `case_parties[]`, `document` (metadados), **plano de
  `triage_modules`** (a partir do produto+módulos), e registra `timeline_events`.
- `GET /requests/{id}`, `GET /cases/{id}` enriquecido (partes/docs/timeline/triagem).
- Endpoints `case_parties` (CRUD) + **PII masking** por papel.
- Endpoints `timeline` (listar eventos do caso).
- Regra: **documento obrigatório** no pedido (espelhar wizard etapa 2).

**Fase C — Pricing / Billing (SP5)** [média]
- Catálogo em código (`pricing/config.py`): 4 produtos × 7 módulos + matriz required/locked.
- Endpoints: `GET /pricing` (catálogo), `GET /pricing/config`, `PUT /pricing/config`
  (override + incrementa `version` + audit), `GET /pricing/config/limit-check`
  (bloqueia criar caso ao atingir `cases_limit`), `POST /pricing/estimate`.

**Fase D — SP2 Ingestão de documentos** [média]
- `POST /documents/{id}/process`: OCR (adapter mock|Textract) → **markdown normalizado no
  S3** → chunking → embeddings (**Bedrock Titan**) → `document_chunks`/`document_embeddings`.
- Idempotente (limpa e regera); atualiza `conversion_status`/`ocr_status`.

**Fase E — SP1 Fila assíncrona** [média]
- SQS + DLQ + retry + idempotência (`agent_executions.job_id` único por org). Worker pattern.
- `POST /documents/{id}/process` passa a **enfileirar**; triagem dispara jobs por módulo.

**Fase F — SP4 Adapters externos + evidência** [média]
- Adapters Protocol+Mock+Real+factory: Escavador, Serasa, Procon, TargetData, CNJ.
- `external_queries_cache` (UNIQUE org+provider+hash) + `provider_results`.

**Fase G — SP3 Agentes IA + execução da triagem + revisão** [grande]
- Executores dos módulos (consome fila): document_parser, ai_summary (Bedrock), ai_report.
- Atualiza `triage_modules.status`/`attempts`, grava `provider_results`, `timeline_events`.
- Revisão humana + relatório (upload do analista; geração IA opcional).

**Fase H — Deploy AWS (Fase 7)** [infra]
- RDS PG18 privado + Lambda VPC + RDS Proxy; S3; SQS/DLQ; Bedrock/Textract via VPC endpoint
  (sem NAT); SSM; (Cognito quando migrar auth). Ver `docs/PLANO_FASE7_DEPLOY.md`.

### FRONTEND (Next.js — o mesmo do `legaltech-aws`)

**Fase FE-1 — Apontar para o serverless**
- `NEXT_PUBLIC_API_BASE_URL` → endpoint do **API Gateway** (em vez de `localhost:8000`).
- Conferir o proxy `/api/v1/*` do Next (hoje encaminha ao FastAPI).

**Fase FE-2 — Alinhar contratos de auth**
- Login: hoje `POST /api/v1/auth/login` (dev_jwt). Nosso serverless: `POST /users/login`
  (JWT custom). Alinhar rota/shape (ou criar alias `/auth/login`) e `GET /me`.
- `organization_id` já viaja no JWT (Fundação V2).

**Fase FE-3 — Validar cada tela contra o serverless (smoke E2E)**
- Telas: login, dashboard, clientes, casos, wizard (`POST /requests`), detalhe (partes/docs/
  timeline/triagem/relatório), pricing, settings. Capturar divergências de payload.

**Fase FE-4 — Ajustes de shape**
- Onde o serverless divergir do contrato esperado pelo frontend, ajustar o **serializer do
  backend** (preferir adaptar o backend ao contrato do frontend já existente).

---

## 5. Ordem recomendada (dependências)
```
A (1C + timeline)  →  B (Pedido/Wizard)  →  C (Pricing)  →  D (Ingestão SP2)
                                                              ↓
                                   E (SQS) → F (Adapters) → G (Agentes/Revisão)  →  H (Deploy AWS)
Frontend: FE-1/FE-2 podem começar após B (há o que consumir); FE-3/FE-4 acompanham cada fase.
```
**Racional:** B (wizard) é o coração e destrava tudo (cria o plano de triagem que D–G
executam). C (pricing) é independente e rápido. D–G são os blocos "mock no referência" que
viram reais aqui (alguns com Bedrock/Textract). Frontend integra incrementalmente.

## 6. Itens menores observados (backlog)
- PII masking de partes por papel (LGPD).
- `request_code` sequencial por org/ano (`request_code_sequences`).
- Notificações (preferências) — frontend-local; envio real é roadmap.
- Admin de usuários por org: backend já tem (Fundação V2); falta UI consumir.
- Settings (tema/preferências) — frontend-local.
