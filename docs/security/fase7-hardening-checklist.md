# Checklist de Endurecimento de Segurança — Fase 7 (Deploy AWS)

> Origem: auditoria de segurança/LGPD read-only de 2026-07-05 (varredura multi-agente
> + re-verificação manual). Todos os itens abaixo foram **confirmados no código real**,
> mas dependem da topologia AWS (API Gateway/WAF/CloudFront) ou de design de compliance,
> por isso NÃO foram corrigidos no ambiente local. Fechar **antes** de expor a API à internet.
>
> Itens já corrigidos localmente (fora deste checklist): A-04 (leitura de token fail-closed
> em produção), A-05b (`.gitignore` do frontend passa a ignorar `.env`), A-09 (docstring de
> `src/services/rag.py` alinhado à RLS por organização da migração 010).

---

## 🟡 A-01 — Rate limiting nas rotas públicas de autenticação `[MÉDIO / Confirmado]`

**Evidência:** `grep throttl|usagePlan|burstLimit|rateLimit|reservedConcurrency|WAF` em
`serverless.yml` → nenhum match. 6 rotas públicas (sem `authorizer`): `health`,
`create_user` (signup), `login` (`POST /users/login`), `authLogin` (`POST /auth/login`,
duplica o login), `forgotPassword`, `resetPassword`. O handler `src/handlers/users.py`
(login) não tem contagem de tentativas/lockout — única defesa é bcrypt(rounds=12) + hash
dummy anti-timing.

**Risco:** força-bruta/credential-stuffing sem limite; spam de e-mail de reset (abuso de
cota SES + assédio a terceiros); DoS econômico via signup (bcrypt + criação de org/usuário).
O throttle default de conta da AWS (~10k rps) é limite de **capacidade**, não defesa por-conta.

**Correção (duas camadas):**

- [ ] **Infra — API Gateway usage plan / throttle.** Adicionar ao `provider` do `serverless.yml`:
  ```yaml
  provider:
    apiGateway:
      usagePlan:
        throttle:
          rateLimit: 20      # req/s por rota (ajustar por carga real)
          burstLimit: 40
  ```
  E throttle mais estrito por-método nas 5 rotas de auth (via `serverless-api-gateway-throttling`
  plugin ou method settings), ex.: `login`/`authLogin`/`forgotPassword`/`resetPassword` = ~1 req/s.
- [ ] **Infra — AWS WAF (rate-based rule)** na frente do API Gateway: bloquear IP com > N req/5min
  nas rotas de auth.
- [ ] **App — anti-brute-force por conta.** Projetar com cuidado (lockout duro é abusável — um
  atacante trava a conta da vítima). Preferir: contador de falhas por (email, IP) com janela de
  tempo + backoff exponencial + CAPTCHA após N falhas. Implementar no `users.login`/`forgot_password`.
- [ ] **Remover rota duplicada** `authLogin` se `POST /auth/login` não for usada pelo frontend
  (dobra a superfície pública do login sem ganho).

**Aceite:** teste de carga contra `/users/login` com senha errada é limitado/bloqueado após o
limite; `/users/forgot-password` não permite flood de e-mails.

---

## 🔵 A-02 — CORS `Access-Control-Allow-Origin: *` `[BAIXO / Confirmado]`

**Evidência:** `src/utils/helpers.py:9` fixa `Origin: *`; `serverless.yml` usa `cors: true`
em todas as 56 rotas. O próprio comentário `helpers.py:8` já marca como pendência de Fase 7.

**Risco:** BAIXO e delimitado — auth é por **Bearer no header** (não cookie) e nunca se emite
`Access-Control-Allow-Credentials` (confirmado: zero ocorrências), então não há vetor automático
de CSRF/roubo de sessão. Risco residual: qualquer origem pode chamar a API com um token que já possua.

**Correção:**
- [ ] Substituir `*` por allowlist de origens confiáveis (domínio do frontend) via variável de
  ambiente, tanto em `CORS_HEADERS` (`helpers.py`) quanto no `cors:` do `serverless.yml`.
- [ ] **Não** habilitar `Allow-Credentials` junto com origin dinâmico sem validação estrita.

**Aceite:** preflight de origem não-allowlistada é rejeitado; origem do frontend passa.

---

## 🔵 A-05a — Advisory npm `postcss <8.5.10` (via `next`) `[BAIXO / Confirmado]`

**Evidência:** `npm audit` → `postcss <8.5.10` (XSS no stringify de CSS, GHSA-qx2v-qp2m-jg93)
na cópia embutida `next/node_modules/postcss` (8.4.31). O postcss direto do projeto já é ≥ 8.5.15.

**Por que não corrigido agora:** o único fix do `npm audit` é `--force`, que **rebaixa `next` de
16.x para 9.3.3** (quebra tudo). O advisory cobre a faixa `next 9.3.4 … 16.3.0`, então nem o patch
16.2.10 limpa. Impacto prático ~nulo: postcss roda em **build-time** sobre o CSS próprio (Tailwind),
não sobre input de usuário.

**Correção:**
- [ ] ⚠️ **NUNCA** rodar `npm audit fix --force` (rebaixa `next` para 9.3.3).
- [ ] Monitorar releases do `next`; atualizar quando uma versão embutir `postcss >= 8.5.10` para
  limpar o advisory. Reavaliar com `npm audit` a cada bump de `next`.

**Aceite:** `npm audit` sem vulnerabilidades moderadas de postcss após o bump upstream.

---

## ⚪ A-06 — IAM `ses:SendEmail` com `Resource: "*"` `[INFORMATIVO / Confirmado]`

**Evidência:** `serverless.yml` (bloco `iamRoleStatements`) usa `Resource: "*"` para
`rds:DescribeDBInstances`, `ses:SendEmail` e `logs:*`. S3/SQS/SSM já estão escopados.

**Nota:** `rds:Describe*` e `logs:*` **não suportam** escopo por recurso (`*` é a única forma
válida — não é misconfig). Só `ses:SendEmail` tem mérito real de least-privilege.

**Correção:**
- [ ] Escopar `ses:SendEmail` ao ARN da identidade SES do remetente
  (`arn:aws:ses:<region>:<acct>:identity/<from-address>`), evitando spoofing interno de remetente.

---

## 📋 A-03 — Auditoria de LEITURA de PII + máscara para analyst `[BAIXO / compliance — precisa de design]`

**Evidência:** `src/handlers/clients.py:210-243` (`_serialize`) devolve CPF/CNPJ/RG/e-mail/telefone/
endereço **crus** para todo papel exceto `viewer`. Não existe trilha de **leitura** de PII —
e isso é **sistêmico**: `SELECT` não dispara trigger no Postgres, então nenhuma tabela
(`clients`/`case_parties`/`cases`/`documents`) registra quem leu o quê. RLS já limita o alcance
à própria organização do usuário.

**Relevância:** LGPD art. 37 (registro das operações) + art. 46 (rastreabilidade). Em incidente,
não há como provar quem acessou/exportou dados de titulares.

**Correção (backlog de compliance, exige design):**
- [ ] Registrar evento explícito de leitura/exportação sensível (`CLIENT_READ` / `CLIENT_EXPORT`)
  na `audit.audit_log` a partir dos handlers `list_clients`/`get_client`.
- [ ] Avaliar mascarar `document_number`/`rg` por padrão também para `analyst`, liberando o CPF cru
  só sob permissão/escopo explícito **e logado**.
- [ ] Adicionar triggers de auditoria de UPDATE/DELETE em `public.clients` e `public.case_parties`
  (espelhando a migração 014), para rastrear alterações de PII.

---

## Itens INFORMATIVOS de higiene (sem urgência)

- [ ] **A-07** — `update_case.assigned_to` (`cases.py:453`) sem FK/checagem de org (só validação de
  formato UUID). Não afeta autorização (RLS barra cross-tenant); opcional validar `users.id` ativo
  da mesma org.
- [ ] **A-08** — Padronizar handlers `documents.py:230`/`case_parties.py:98,158`/`reports.py:101`
  para usar schema Pydantic (`extra="forbid"` + `Field(max_length=...)`) como os demais. Sem SQLi
  (tudo bind `%s`; body cap 1MB); só falta cap de tamanho por campo.
- [ ] **A-11** — Presign GET de S3 (`storage.py:48`) com `ResponseContentDisposition=attachment` como
  defesa em profundidade. XSS foi refutado (file_type allowlist sem svg/html; Content-Type assinado;
  presign cross-origin ao app), então é endurecimento menor.

---

## Camadas que passaram LIMPAS na auditoria (não requerem ação)

Isolamento multi-tenant (RLS + FORCE por organização) · RBAC/autorização · injeção SQL (100% bind
params) · segredos (SSM, nada hardcoded) · vazamento em logs (só `type(e).__name__`) · JWT (HS256
explícito, sem downgrade) · pagamento/cartão (PAN/CVV nunca saem do browser) · XSS-sinks no frontend
(sem `dangerouslySetInnerHTML`/`innerHTML` de conteúdo de usuário).
