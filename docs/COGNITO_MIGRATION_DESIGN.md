# Migração para AWS Cognito — Desenho técnico + checklist (Etapa 1 e 2)

> Status: **PROPOSTA / não implementado**. Escrito por Claude a partir da leitura do código real
> (backend `contrato_visto_backend-main`, frontend `contrato_visto_frontend`).
> Nada foi alterado. Serve para avaliar o esforço antes de decidir.

## 0. Contexto e princípio

O **frontend já está com o BFF pronto** (cookie HttpOnly cifrado, CSRF, origem, proxy `/api/v1/*`,
SSRF-hardening; token fora do JS). Hoje o cookie sela o **JWT HS256 que o próprio backend emite**.
Ou seja: o BFF é a **costura (seam)** que embrulha o token do backend.

O **backend** ainda é o Identity Provider (emite HS256 `token_use:"dev"` em `src/handlers/users.py:133`;
authorizer valida HS256 em `src/authorizers/jwt_authorizer.py`). A migração Cognito troca **a fonte da
identidade**: Cognito passa a autenticar e emitir tokens (RS256/OIDC), e o **banco continua a autoridade
final** para `user_id`, `organization_id`, `role` e `status` — o token só carrega o `sub`.

**Regra permanente:** tenant e permissões **derivam do banco** (por `cognito_sub`), nunca de grupos do
Cognito nem de claims não confiáveis. A RLS por org (`tenant_tx`) segue sendo o ponto de imposição.

---

## ETAPA 1 — Infraestrutura (AWS + Vercel), sem tocar código de negócio

### 1.1 Cognito User Pool (staging primeiro; produção é pool SEPARADO)

| Config | Valor recomendado |
|---|---|
| Sign-in | e-mail (username = e-mail) |
| Self-service signup | **DESLIGADO** (onboarding por convite de admin) |
| Password policy | mín. 12, maiúscula+minúscula+número+símbolo |
| MFA | **obrigatório para admin** antes de dados reais (TOTP); opcional p/ demais no piloto |
| Verificação de e-mail | ligada (Cognito cuida) |
| Atributos | e-mail (obrigatório). NÃO guardar CPF/dados jurídicos no Cognito |
| Token validity | access **curto** (5–15 min), id curto, refresh 1–8 h |
| Advanced security | ligar (detecção de credencial comprometida) na produção |

### 1.2 App Client (para o BFF)

- Tipo **confidencial** (com client secret) — o BFF é server-side, guarda o secret.
- Flow: **Authorization Code + PKCE**. Desabilitar Implicit e ROPC (senha direta).
- Callback URL: `https://<app>/api/auth/callback` (staging e prod distintos).
- Logout URL: `https://<app>/api/auth/logout` (ou a URL pós-logout).
- Scopes: `openid email` (+ `profile` se necessário). Sem scopes desnecessários.

### 1.3 Managed Login (Hosted UI)

- Domínio Cognito (ex.: `auth-staging.quorya...`) — domínio **separado** de app e API.
- Branding mínimo depois; primeiro fazer funcionar o fluxo.

### 1.4 Sessão do BFF — DECISÃO (ver §4.A)

- **Opção A (mínima, recomendada p/ começar):** reusar o `sessionCookie.ts` atual e **selar o
  `access_token` do Cognito** (AES-256-GCM) no cookie. Zero infra nova.
- **Opção B (alvo do doc, mais forte):** cookie com **id opaco** + tokens em **DynamoDB**
  (PK `session_id`, TTL, expiração por inatividade/absoluta, revogação no logout). Dá revogação
  server-side instantânea. Requer tabela + role IAM.
- Recomendação: **A no piloto, B como follow-up** antes de produção com dados reais.

### 1.5 Vercel ↔ AWS (só se Opção B / DynamoDB / Cognito Admin API)

- **Vercel OIDC Federation** → IAM Role assumida por token OIDC (sem `AWS_ACCESS_KEY_ID` fixo na Vercel).
- IAM Role de **menor privilégio**: só as ações necessárias (ex.: `dynamodb:GetItem/PutItem/DeleteItem`
  na tabela de sessão; `cognito-idp:AdminCreateUser` se o onboarding usar Admin API).

### 1.6 Variáveis de ambiente (nomes; valores são seus)

**BFF (Vercel):** `COGNITO_DOMAIN`, `COGNITO_CLIENT_ID`, `COGNITO_CLIENT_SECRET`, `COGNITO_ISSUER`,
`APP_ORIGIN`, `API_BASE_URL`, `AUTH_COOKIE_SECRET` (≥32, ≠ segredo do backend), `BFF_MAX_BODY_BYTES`
(+ `AWS_REGION`, `SESSION_TABLE` se Opção B). Remover `NEXT_PUBLIC_API_BASE_URL` antiga.
**Backend (AWS):** `COGNITO_USER_POOL_ID`, `COGNITO_CLIENT_ID` (audience), `COGNITO_ISSUER`,
`AWS_REGION`, `AUTH_MODE` (`hs256`|`cognito`). **Nenhum** segredo como `NEXT_PUBLIC_*`.

### ✅ Checklist Etapa 1
- [ ] User Pool de **staging** criado (signup off, password policy, verificação de e-mail)
- [ ] MFA TOTP habilitado; **obrigatório p/ admin** (grupo/condição)
- [ ] App Client confidencial (Code+PKCE, callback/logout URLs, scopes mínimos)
- [ ] Domínio Managed Login (separado de app/API)
- [ ] DECISÃO A vs B registrada; se B: tabela DynamoDB (PK `session_id`, TTL) criada
- [ ] Vercel OIDC → IAM Role de menor privilégio (só se B/Admin API)
- [ ] WAF + throttling no API Gateway (pode ser follow-up, mas antes de prod)
- [ ] Alarmes CloudWatch + CloudTrail ligados no ambiente
- [ ] Variáveis definidas em Vercel e no stage do backend (sem segredos em `NEXT_PUBLIC_*`)
- [ ] Nada em produção ainda; staging isolado de prod (pools e banco distintos)

---

## ETAPA 2 — Backend: validar Cognito e resolver o principal pelo banco

### 2.1 Migration `users.cognito_sub`  (próximo número livre — ex.: 029; 026=pix_charges, 027=pix_webhook, 028=perfis)

```sql
-- migrations/029_users_cognito_sub.sql
ALTER TABLE public.users ADD COLUMN cognito_sub varchar(255);
CREATE UNIQUE INDEX ux_users_cognito_sub ON public.users (cognito_sub)
  WHERE cognito_sub IS NOT NULL;         -- único quando presente; nullable no modo duplo
```
- Nullable durante a migração (usuários legados ainda sem `cognito_sub`).
- Aplicar no dev + rebuild do banco de teste (`tools/setup_test_db.py`), como as migrations anteriores.

### 2.2 Novo authorizer Cognito (`src/authorizers/cognito_authorizer.py`)

Mesmo **contrato de saída** do authorizer atual (injeta `user_id/role/organization_id/iat` no `context`)
para que os handlers e o `get_user_from_event` (context.py) **fiquem intactos**. O que muda por dentro:

1. Baixar e **cachear o JWKS** do Cognito (`{COGNITO_ISSUER}/.well-known/jwks.json`), indexado por `kid`;
   refetch em `kid` desconhecido.
2. Validar o `access_token`: **RS256**, `kid`, `iss` == `COGNITO_ISSUER`, `client_id`/audience,
   `token_use === "access"`, `exp` (`options={"require":["exp"]}`).
3. Extrair **somente** o `sub` (cognito_sub).
4. **Resolver no banco:** `SELECT id, organization_id, role, status FROM users WHERE cognito_sub = %s`.
5. Se não achar **ou** `status != 'active'` → **deny** (403).
6. Injetar `context = { user_id, role, organization_id, iat }` (mesmo shape de hoje).

> Onde resolver o `sub→usuário`: **no authorizer** (Opção A, recomendada) — mantém o shape do `context`
> e os handlers inalterados; o cache de authorizer do API Gateway amortiza a consulta. Alternativa:
> resolver no `get_user_from_event`; toca o caminho dos handlers, não recomendado.

`load_active_caller`/`assert_active_admin` (context.py:126/155) **continuam** fazendo o recheck de
revogação por-requisição nas rotas sensíveis — já existem, não mudam.

### 2.3 Modo duplo `AUTH_MODE` (cutover seguro)

- Um **único** módulo authorizer que despacha por `AUTH_MODE`: `cognito` (novo) | `hs256` (legado local).
  Mantém a fiação do `serverless.yml` (`custom.jwtAuthorizer`) intacta — só troca a lógica interna.
- **Fail-closed:** em staging/produção, `AUTH_MODE` ausente/ inválido → **boot interrompido**
  (mesmo padrão do `JWT_SECRET_KEY` atual). Legado só vale localmente e é removido ao fim.

### 2.4 Login / cadastro / onboarding

- **Login deixa de passar pelo backend.** O BFF faz Code+PKCE com o Cognito; o backend `/api/v1/auth/login`
  (HS256) fica **desabilitado no modo cognito** (rota removida ou 410). `create_access_token`/HS256
  (`utils/auth.py`) é **aposentado** no cutover.
- **Cadastro dividido (idempotente):**
  1. Cognito cria a credencial + verifica e-mail (self-signup off → **AdminCreateUser** por convite).
  2. Backend cria a linha `users` (org, perfil, role, onboarding) e **vincula `cognito_sub`**.
  - Convite com prazo + auditoria. Nenhuma senha/hash trafega ao frontend.
- **Migração dos usuários existentes:** para cada `users` legado, criar o usuário no Cognito
  (AdminCreateUser, e-mail já verificado) e gravar o `cognito_sub` de volta. Hashes bcrypt antigos só
  são removidos **após** cutover + plano de rollback.

### 2.5 Limpeza pós-cutover
- Remover `create_access_token` (HS256) e o handler de `login` HS256.
- Remover o ramo `hs256` do authorizer e o `AUTH_MODE` legado.
- Remover `token_use:"dev"` e claims HS256.

### ✅ Checklist Etapa 2
- [ ] Migration `026_users_cognito_sub.sql` (unique parcial) aplicada em dev + banco de teste
- [ ] `cognito_authorizer` com validação RS256/JWKS (kid/iss/aud/token_use/exp) + cache de JWKS
- [ ] Resolução `cognito_sub → user_id/org/role/status` no banco; deny se ausente/inativo
- [ ] `context` mantém o shape atual (handlers e `get_user_from_event` inalterados)
- [ ] `load_active_caller`/`assert_active_admin` seguem no recheck de revogação
- [ ] `AUTH_MODE` (fail-closed em staging/prod); dispatch único mantendo `custom.jwtAuthorizer`
- [ ] Login HS256 desabilitado no modo cognito; onboarding dividido (Cognito credencial + backend org)
- [ ] Script de migração de usuários legados → Cognito + gravação de `cognito_sub`
- [ ] Testes: authorizer Cognito (JWKS mock), **isolamento multi-tenant**, usuário inativo, modo duplo
- [ ] pytest completo verde no banco de teste + `pip-audit`

---

## Frontend — o que muda (a costura BFF é ~90% reaproveitada)

| Peça | Muda? |
|---|---|
| `api/auth/login/route.ts` | **Muda** → inicia Code+PKCE no Cognito (redirect ao Managed Login) |
| **novo** `api/auth/callback/route.ts` | **Criar** → valida `state`/`nonce`/PKCE, troca `code` por tokens |
| `server/auth/sessionCookie.ts` | Opção A: selar o token Cognito (mínimo) · Opção B: id opaco + DynamoDB |
| `server/backend/client.ts` | encaminhar o `access_token` do Cognito no `Bearer` |
| `login/page.tsx` | botão "Entrar" → redireciona ao Cognito (fim do form de senha) |
| `requestSecurity`, proxy `backend/[...path]`, `routeSession`, `sessionUser`, `useSession`, CSP/HSTS | **Reaproveita** (CSRF/origem/SSRF/proxy/sessão ficam) |

---

## §4. Decisões que precisam de você

**A. Sessão do BFF:** (A) selar o token Cognito no cookie [mínimo, sem infra] vs (B) id opaco +
DynamoDB [revogação server-side, +infra]. → Recomendo **A no piloto, B antes de produção real**.

**B. Onboarding:** convite por admin (AdminCreateUser) [recomendado] vs self-signup Cognito.

**C. MFA:** obrigatório p/ admin já no staging? → Recomendo **sim**.

**D. Escopo agora:** só o **desenho** (este doc) ou já iniciar a Etapa 1 (infra AWS/Vercel — que é sua,
pois envolve segredos/console) e/ou a Etapa 2 (código do backend — que eu posso implementar em
`AUTH_MODE=cognito` com o legado preservado)?

## §5. Riscos / armadilhas
- **Latência do authorizer** com lookup no banco → usar cache do authorizer do API Gateway e pool/RDS Proxy.
- **JWKS**: cachear e tolerar rotação de chave (refetch em `kid` novo); nunca hardcodar chave.
- **Cutover**: manter `AUTH_MODE` duplo só o tempo mínimo; nunca deixar os dois indefinidamente (o doc externo alerta).
- **Migração de usuários**: idempotente + rollback; não apagar hashes bcrypt antes do cutover confirmado.
- **Não** confiar em grupos do Cognito como tenant; o banco é a autoridade.
- **Nada** de dados reais antes dos testes multi-tenant + restauração de backup (Etapa 4/5 do plano).
