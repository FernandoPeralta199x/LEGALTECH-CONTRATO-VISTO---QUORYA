# Perfis de Acesso (multi-tenant B2B2C) — Especificação técnica

> Status: **PROPOSTA / não implementado**. Escrito por Claude a partir do schema real
> (`organizations`, `users`) e das decisões do produto. Nada foi alterado.
> Par natural do [[COGNITO_MIGRATION_DESIGN]] (perfil/tenant/role vêm do banco).

## 1. Objetivo e princípio

A firma (QUORYA / Contrato Visto) é a **operadora** da plataforma. Clientes externos
(empresas e pessoas físicas) passam a ter **login próprio** e enxergam **só os seus dados**.

**Decisão-mestra:** cada cliente externo é uma **organização** (tenant). Assim o isolamento
cliente↔cliente reusa a **RLS por `organization_id`** que o backend já impõe — sem inventar
uma camada nova de escopo.

## 2. Os três perfis + matriz de telas

| Tela / grupo | **Administrador** (firma) | **Empresarial** (empresa) | **Cliente comum** (indivíduo) |
|---|:--:|:--:|:--:|
| Dashboard | ✅ | ✅ | — |
| Novo Pedido | ✅ | ✅ | ✅ **simplificado** (sem "Ajuste os módulos") |
| Casos | ✅ (todas as orgs) | ✅ (da empresa) | ✅ (só os dele) |
| Documentos | ✅ | ✅ (da empresa) | — |
| Analista (fila de triagem) | ✅ | — ⚠️ | — |
| Relatórios | ✅ | ✅ (da empresa) | ✅ (só os dele) |
| Clientes | ✅ | — ⚠️ | — |
| **Administração** | ✅ | ❌ | ❌ |
| **Financeiro** | ✅ | ❌ | ❌ |
| Configurações | ✅ (completo) | ✅ | ✅ (só Aparência e segurança) |

- **Administrador**: acesso a **tudo**; são **exatamente 3 usuários** (você + 2), na org `operador`.
- **Empresarial**: tudo **exceto Administração e Financeiro**.
- **Cliente comum**: Novo Pedido (simplificado), Casos (dele), Relatórios (dele), Configurações (aparência/segurança). Sempre **1 usuário**.

> ⚠️ **Recomendação a confirmar:** "Analista" (fila interna de triagem) e "Clientes" (roster de clientes da
> firma) são **ferramentas de operador** e, para um tenant externo, ficariam vazios/sem sentido sob a RLS.
> Sugiro **NÃO** exibi-los para Empresarial (mesmo estando fora de Admin/Financeiro). A matriz acima já
> reflete essa sugestão (—⚠️). Se preferir o literal "tudo exceto Admin/Financeiro", me avise.

## 3. Modelo de dados (colunas reais)

### 3.1 `organizations` (+2 colunas; já tem `document`)
```sql
-- migrations/028_org_type_document_and_user_perfil.sql (implementada)
ALTER TABLE public.organizations
  ADD COLUMN type          varchar(20) NOT NULL DEFAULT 'operador',   -- operador | empresarial | individual
  ADD COLUMN document_type varchar(8);                                 -- CNPJ | CPF (NULL para operador legado)
ALTER TABLE public.organizations
  ADD CONSTRAINT organizations_type_chk
  CHECK (type IN ('operador','empresarial','individual'));
-- unicidade do documento por TIPO (um CNPJ = uma org empresarial; um CPF = uma org individual)
CREATE UNIQUE INDEX ux_org_document
  ON public.organizations (document_type, document)
  WHERE document IS NOT NULL AND deleted_at IS NULL;
```
- `document` (já existe) guarda o **CNPJ** (empresarial) ou **CPF** (individual).
- A org `operador` (a firma) fica `type='operador'` (default cobre a org atual).

### 3.2 `users` (+1 coluna)
```sql
ALTER TABLE public.users
  ADD COLUMN perfil varchar(20);   -- administrador | empresarial | cliente_comum
ALTER TABLE public.users
  ADD CONSTRAINT users_perfil_chk
  CHECK (perfil IN ('administrador','empresarial','cliente_comum'));
```
- **Coerência perfil ↔ tipo da org** (imposta na aplicação e/ou por trigger):
  `administrador` só em org `operador`; `empresarial` só em `empresarial`; `cliente_comum` só em `individual`.
- **Teto de 3 administradores** (regra de negócio): recusar o 4º usuário com `perfil='administrador'`.

## 4. Três eixos de autorização (ortogonais) — a forma profissional

| Eixo | Fonte | Decide |
|---|---|---|
| `organization_id` | banco / token | **Isolamento de dados** (RLS `tenant_tx`) — já existe |
| `role` (admin/analyst/viewer) | banco / token | Permissão de **escrita**/operação — continua igual |
| **`perfil`** (novo) | banco / token | **Quais telas** + escopo de navegação |

- **Autorização por tela = server-side.** Mapa `perfil → telas permitidas` no backend
  (fonte da verdade); a sidebar do frontend **espelha**, nunca decide sozinha (SEC-FE).
- O `perfil` é propagado como claim (hoje no JWT HS256; amanhã resolvido do banco pelo authorizer Cognito — ver [[COGNITO_MIGRATION_DESIGN]]).

## 5. Escopo de dados

- **Empresarial** → RLS por org (todos os funcionários da mesma org/CNPJ veem os casos **da empresa**). ✓ já existe.
- **Cliente comum** → própria org (1 usuário) ⇒ org-scope = user-scope. ✓ já existe.
- **Administrador (firma) — "operar como org" (cross-tenant, auditado):**
  - A firma precisa processar casos de **todas** as orgs de clientes.
  - Caminho **explícito**: o backend permite ao staff `administrador` **assumir o tenant de uma org-cliente** dentro de uma transação (`tenant_tx` com o org-alvo), **registrando na trilha de auditoria** qual operador acessou qual org.
  - NUNCA um bypass silencioso da RLS. Minimização + auditoria (LGPD — dados jurídicos).
  - Implementação: endpoint/consulta "listar orgs de clientes" (só administrador) + parâmetro de org-alvo validado (a org precisa ser `type in (empresarial,individual)`), com log `OPERATOR_IMPERSONATION`.

## 6. Onboarding / cadastro

- **Administrador**: criados pela firma (seed/convite interno). **Máx. 3**.
- **Empresarial**: cadastro **exige CNPJ** → cria a org `empresarial` (se o CNPJ é novo) ou **associa** o usuário à org existente daquele CNPJ (mescla funcionários). Convite de funcionário com prazo + auditoria.
- **Cliente comum**: cadastro com **CPF** → cria a org `individual` com **1 usuário**.
- Validar formato + unicidade de CNPJ/CPF; reusar o mascaramento LGPD já existente na leitura.

## 7. Novo Pedido simplificado (cliente comum)

- Pular a etapa **"Ajuste os módulos da simulação"**.
- Usar um **preset padrão** por produto (o catálogo/pricing do backend já define os módulos obrigatórios do produto — `plan_for_product`/catálogo). Fluxo: escolhe produto → confirma → paga.
- Empresarial e Administrador seguem com o wizard completo (com ajuste de módulos).

## 8. Frontend (o que muda)

- **Sidebar/nav dirigida por `perfil`** (grupos e itens filtrados) — fonte: um mapa `perfil→telas` recebido do backend (não hardcode).
- Guards por rota: além de `AuthGuard`/`AdminGuard`, um `PerfilGuard` (Admin+Financeiro só `administrador`).
- **Casos/Relatórios**: já vêm filtrados pela RLS/escopo do backend; a UI não precisa re-filtrar (mas some com ações não permitidas).
- **Novo Pedido**: variação de fluxo por `perfil` (pula a etapa de módulos p/ `cliente_comum`).
- **Configurações** reduzida p/ cliente comum (Aparência e segurança).

## 9. Casa com o Cognito

`perfil` + `organization_id` + `role` vêm do **banco** (resolvidos por `cognito_sub` no authorizer). O Cognito só autentica; **o banco é a autoridade** de tenant/perfil/permissão. Grupos do Cognito **não** são fonte de tenant. Ver [[COGNITO_MIGRATION_DESIGN]].

## 10. Testes obrigatórios (antes de dados reais)

- **Isolamento cliente↔cliente** (LGPD-crítico): org A não lê/escreve dados de org B (RLS) — em Casos, Documentos, Relatórios, Pedidos.
- **Escopo por perfil**: `empresarial`/`cliente_comum` recebem 403 em Administração/Financeiro (server-side, não só UI).
- **Cliente comum** não vê casos de outro cliente comum.
- **"Operar como org"**: só `administrador`; toda impersonação auditada; org-alvo validada.
- **Teto de 3 admins**: 4º admin recusado.
- **Onboarding**: CNPJ duplicado mescla na org certa; CPF cria org individual.

## 11. Riscos / atenção

- Isolamento é a linha de vida (dados jurídicos). Impor no **backend**; testar acesso cruzado.
- "Operar como org" é poder — **auditar + minimizar**.
- Self-service (cliente cria pedido/paga) amplia superfície → validação de entrada + rate-limit no onboarding/pedido.
- Muitas orgs pequenas (1 por cliente comum) — ok para RLS; observar índices/perf.

## 12. Checklist de implementação (proposto)

**Backend**
- [ ] Migration: `organizations.type` + `document_type` (+ unique por doc) e `users.perfil` (+ check) + coerência perfil↔type
- [ ] Claim `perfil` no login/authorizer; `require_perfil(...)` (mapa perfil→telas/rotas) server-side
- [ ] Escopo já é RLS; endpoint "listar orgs de clientes" (admin) + "operar como org" (impersonação auditada)
- [ ] Onboarding: cadastro empresarial (CNPJ→org, mescla funcionários) e cliente comum (CPF→org, 1 user); teto 3 admins
- [ ] Novo Pedido: preset padrão p/ cliente_comum (sem ajuste de módulos)
- [ ] Testes: isolamento multi-tenant + escopo por perfil + impersonação + onboarding

**Frontend**
- [ ] Nav/sidebar por `perfil` (do backend) + `PerfilGuard` nas rotas
- [ ] Tela **Administração → Configuração de Perfil**: CRUD de usuários + atribuir perfil (empresarial exige CNPJ; cliente comum CPF)
- [ ] Novo Pedido simplificado p/ cliente_comum
- [ ] Configurações reduzida (aparência/segurança) p/ cliente comum
- [ ] E2E logado por perfil (admin / empresarial / cliente comum)

## 13. Decisões confirmadas
- Cada cliente = tenant (org). ✅
- Firma vê clientes via **"operar como org"** (auditado). ✅
- Cliente comum = **1 usuário**. ✅
- Empresarial **e** Cliente comum **não** acessam **Administração** nem **Financeiro**; acesso total = só os **3 administradores**. ✅
- Empresarial por **CNPJ** (mescla funcionários); Cliente comum por **CPF**. ✅

## 14. Aberto (a confirmar)
- Exibir **Analista/Clientes** para Empresarial? (recomendo **não** — são telas de operador).
- Empresarial: todo funcionário vê **todos** os casos da empresa (recomendo sim, simples) ou há papéis internos na empresa (fase 2)?
