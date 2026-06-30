# Rodar o frontend (Next.js) contra o backend serverless — local

Permite usar o **mesmo frontend** do `legaltech-aws` apontando para a cópia
**serverless** (`contrato_visto_backend`), sem AWS nem `serverless-offline`,
através de um **dev-server HTTP** que roteia para os handlers Lambda.

> DEV-ONLY. O `tools/local_server.py` simula o JWT Authorizer e o roteamento do
> API Gateway; **não** é usado em produção (excluído do empacotamento).

## Pré-requisitos
- PostgreSQL 18 local de pé (container `cv-pg18`, porta 5433) com as migrações 005–011 aplicadas.
- `.venv` do backend criado e `.env` configurado (DB + `JWT_SECRET_KEY`).
- Node/npm para o frontend (`X:\QUOARYA\legaltech-aws\apps\frontend`).

## 1. Subir o dev-server (porta 8000)
```bash
cd "X:\QUOARYA\FASE FINAL   28 06 2026\contrato_visto_backend-main"
.venv\Scripts\python.exe tools\local_server.py
# -> http://127.0.0.1:8000  (aceita /api/v1/*)
```
Variáveis opcionais: `PORT` (default 8000), `HOST` (default 127.0.0.1).

## 2. Subir o frontend próprio (porta 3000)
Frontend do produto: `X:\QUOARYA\FASE FINAL   28 06 2026\contrato_visto_frontend`
(cópia standalone do Next.js, independente da referência `legaltech-aws`). O
`.env.local` já aponta para `http://127.0.0.1:8000`; o Next reescreve `/api/v1/*`
para esse destino.
```bash
cd "X:\QUOARYA\FASE FINAL   28 06 2026\contrato_visto_frontend"
npm install   # primeira vez (node_modules não é versionado)
npm run dev   # -> http://localhost:3000
```

## 3. Criar um usuário e logar
O signup cria uma organização nova e o usuário como **admin** dela:
```bash
curl -X POST http://127.0.0.1:8000/api/v1/users \
  -H "Content-Type: application/json" \
  -d '{"email":"voce@empresa.com","password":"Senha123","name":"Você"}'
```
Depois faça login na tela (`/login`) com esse e-mail/senha.

## O que funciona contra o serverless (hoje)
- **Auth**: `POST /auth/login` (shape `{access_token, token_type, expires_in, user}`), `GET /auth/me`.
- **Dashboard**: `GET /dashboard/stats`.
- **Clientes**: CRUD.
- **Wizard "Novo Pedido"**: `POST /requests` (cria caso + partes + documento + plano de triagem + timeline).
- **Detalhe do caso**: `GET /cases/{id}` (enriquecido), `/parties` (PII mascarada), `/timeline`, `/triage`.
- **Pricing**: `GET /pricing`, `POST /pricing/estimate`, `GET/PUT /pricing/config`, `GET /pricing/config/limit-check`.

## O que ainda NÃO tem backend (cai em mock/erro no frontend)
- Processamento de documentos (OCR/IA), execução da triagem, relatórios, admin avançado.
  O frontend tem `NEXT_PUBLIC_ENABLE_API_MOCK_FALLBACK=true` e pode exibir dados mock nessas telas.

## Notas
- O dev-server usa a conexão única do `psycopg2` (não thread-safe) protegida por lock — adequado para 1 usuário em dev.
- Erros do backend retornam `{error: "mensagem"}`; o `apiClient` do frontend exibe a mensagem genérica de status nesse caso (o envelope de erro detalhado é um item de hardening).
