# Prompt para Claude — implementar as correções da auditoria de segurança

## 1. Objetivo

Implemente, de forma incremental e verificável, as correções dos achados confirmados da auditoria de segurança do backend **Contrato Visto**.

Não aplique os achados cegamente. A auditoria foi executada sobre a revisão imutável:

```text
23eb871aa4aabb173495afd1ffe393c4c9fd3914
```

No momento em que este roteiro foi criado, o repositório estava em:

```text
branch: fix/pricing-sync-sweep
HEAD:   7167bf43e96c650761bc81725ef682b5369ac2bc
Git:    limpo
```

Existem commits posteriores à auditoria. Antes de modificar qualquer arquivo, revalide cada achado contra o `HEAD` atual. Se já estiver corrigido, não faça uma segunda implementação: registre a evidência e acrescente ou ajuste somente o teste de regressão que ainda estiver faltando.

## 2. Projeto ativo

- Nome: `contrato_visto_backend-main`
- Caminho: `X:\QUOARYA\FASE FINAL   28 06 2026\contrato_visto_backend-main`
- Produto: backend de uma LegalTech SaaS multi-tenant.
- Stack: Python 3.11, AWS Lambda, API Gateway, Serverless Framework, PostgreSQL 18, RLS, S3, SQS e JWT HS256.
- Maturidade declarada: MVP local, pré-Fase 7/AWS.
- Dados sensíveis: usuários, PII, contratos, documentos, relatórios jurídicos, pagamentos e dados segregados por organização.
- Ambiente autorizado: somente código e testes locais. Não operar staging ou produção.

Relatórios de referência, somente leitura:

```text
C:\Users\fguta\AppData\Local\Temp\codex-security-scans-8aeGX6\contrato_visto_backend-main\23eb871aa4aabb173495afd1ffe393c4c9fd3914_20260711T014324Z_j73u34bv\report.md
C:\Users\fguta\AppData\Local\Temp\codex-security-scans-8aeGX6\contrato_visto_backend-main\23eb871aa4aabb173495afd1ffe393c4c9fd3914_20260711T014324Z_j73u34bv\hardening\hardening.md
```

Se esses arquivos temporários não existirem, use os achados e critérios incorporados neste documento. Não interrompa o diagnóstico apenas por isso.

## 3. Regras absolutas

1. Responda e produza o relatório técnico em português do Brasil.
2. Não leia nem altere o `.env` real.
3. Não revele ou registre tokens, senhas, JWTs, CPF, CNPJ, e-mail, documentos ou contratos reais.
4. Não instale pacotes.
5. Não execute Docker.
6. Não execute, crie ou altere migration sem aprovação específica do usuário.
7. Não execute comandos que alterem banco de dados. A suíte só pode ser executada após confirmar que `conftest.py` aponta exclusivamente para o banco de teste e aborta para o banco de desenvolvimento.
8. Não faça deploy nem altere recursos AWS, CI/CD ou infraestrutura cloud sem aprovação específica.
9. Não faça `commit`, `push`, `pull`, `merge`, `rebase`, troca de branch ou reset.
10. Não modifique arquivos fora deste repositório.
11. Não faça refatoração ampla, troca de biblioteca ou alteração de arquitetura junto com as correções.
12. Não implemente rate limiting em memória do processo Lambda. Isso não é controle confiável porque cada instância possui estado isolado e descartável.
13. Não confie em `organization_id`, `user_id`, `role`, permissões, status de pagamento, proveniência ou tamanho de arquivo informados pelo frontend.
14. Preserve RLS, isolamento por organização, contratos públicos da API e sanitização dos logs.
15. Toda correção deve ter teste de regressão. Não declare um achado corrigido apenas por leitura visual do patch.
16. Se uma correção exigir migration, nova infraestrutura ou decisão de produto, pare apenas aquele achado, documente a proposta e continue nos achados independentes.
17. Não use mocks para mascarar comportamento que será diferente em produção. Mocks de teste devem ser explicitamente identificados.

## 4. Gate inicial obrigatório

Execute somente comandos de leitura:

```powershell
git status -sb
git status --short
git log --oneline -10
git remote -v
git rev-parse HEAD
git diff --name-only 23eb871aa4aabb173495afd1ffe393c4c9fd3914..HEAD
```

Leia no mínimo:

```text
README.md
HANDOFF.md
serverless.yml
src/authorizers/jwt_authorizer.py
src/utils/auth.py
src/utils/safety.py
src/handlers/users.py
src/handlers/pix.py
src/handlers/triage.py
src/handlers/reports.py
src/handlers/documents.py
src/services/case_lifecycle.py
src/services/report_generator.py
src/services/storage.py
src/services/document_ingestion.py
src/schemas/document_schemas.py
```

Também localize os testes, schemas e migrations que definem os comportamentos afetados. Não leia `.env`.

Se o worktree já estiver sujo antes das suas alterações:

1. pare sem editar;
2. liste modificados e não rastreados;
3. agrupe por tema;
4. informe o risco de sobreposição;
5. aguarde autorização do usuário.

## 5. Revalidação obrigatória dos achados

Monte uma tabela antes de implementar:

| ID | Estado atual | Evidência no HEAD | Teste existente | Ação |
|---|---|---|---|---|
| SEC-01 a SEC-15 | `CONFIRMADO`, `JÁ_CORRIGIDO`, `NÃO_APLICÁVEL` ou `DEPENDE_DE_RUNTIME` | arquivo e linhas | nome do teste ou ausência | corrigir, testar, pular ou propor |

Use `git show 23eb871aa4aabb173495afd1ffe393c4c9fd3914:<arquivo>` somente para compreender a evidência original. O código atual é a fonte de verdade da implementação.

Para marcar `JÁ_CORRIGIDO`, prove simultaneamente:

- que a fonte controlada pelo atacante continua representada corretamente;
- que o controle atual fecha o caminho específico;
- que o sink ou impacto não permanece alcançável;
- que existe teste de regressão adequado, ou crie apenas esse teste;
- que a correção não depende apenas do frontend.

## 6. Estratégia de implementação

Trabalhe em quatro lotes. Dentro de cada lote:

1. revalide os achados;
2. apresente um plano curto com arquivos e riscos;
3. altere o menor número possível de arquivos;
4. adicione testes de regressão;
5. execute os testes direcionados;
6. execute `git diff --check`;
7. revise o diff antes de iniciar o lote seguinte.

Não misture refatoração cosmética com correção de segurança.

---

## 7. Lote A — identidade, autorização e revogação

### SEC-01 — administrador revogado ainda lista usuários

- Severidade auditada: média.
- Evidência original: `src/authorizers/jwt_authorizer.py:61-90` e `src/handlers/users.py` na listagem administrativa.
- Falha: o authorizer valida assinatura/expiração, mas propaga `role` e `organization_id` históricos sem consultar o estado atual da conta.
- Resultado esperado: um usuário desativado ou rebaixado perde acesso administrativo na próxima requisição privilegiada.

### SEC-02 — administrador revogado restaura o próprio privilégio

- Severidade auditada: média.
- Evidência original: atualização de usuário em `src/handlers/users.py`.
- Falha: o próprio alvo pode reutilizar um JWT antigo de administrador para restaurar `role` ou `status`.
- Resultado esperado: o ator deve estar atualmente ativo, pertencer à organização confiável e possuir papel administrativo atual no banco.

### SEC-03 — administrador revogado desativa outros usuários

- Severidade auditada: média.
- Evidência original: remoção/desativação em `src/handlers/users.py`.
- Resultado esperado: mutações administrativas devem validar o estado atual do ator, não apenas as claims emitidas anteriormente.

### SEC-04 — administrador revogado ainda lê usuário individual

- Severidade auditada: média.
- Evidência original: leitura individual em `src/handlers/users.py`.
- Resultado esperado: a leitura administrativa de PII deve exigir ator atualmente ativo e autorizado.

### SEC-05 — redefinição de senha não revoga JWTs existentes

- Severidade auditada: média.
- Evidência original: `src/handlers/users.py` troca o hash da senha e consome tokens de reset, mas JWTs antigos continuam válidos.
- Resultado esperado: após reset de senha, todos os tokens de sessão emitidos anteriormente devem ser rejeitados.

### Direção técnica do lote A

Prefira um controle central e reutilizável que carregue o usuário atual usando o `user_id` validado e confirme:

- usuário existente;
- `status='active'`;
- vínculo atual com a organização;
- papel atual necessário à operação;
- correspondência entre organização confiável do token e registro persistido;
- ausência de cache de autorização que mantenha papel/status obsoletos.

Não aceite tenant ou papel do corpo/query como fonte de verdade. Verifique a configuração de cache do Lambda Authorizer/API Gateway.

Para SEC-05, primeiro procure um mecanismo existente como `session_version`, `authz_version`, `password_changed_at`, `tokens_valid_after` ou `jti`. Se não existir uma forma persistente e atômica de revogação, não invente uma solução parcial. Documente a alteração de schema/migration necessária e solicite aprovação específica antes de criá-la.

### Testes mínimos do lote A

- JWT emitido para admin; conta rebaixada; listagem negada imediatamente.
- JWT emitido para admin; conta desativada; update/delete/get administrativo negado.
- Admin antigo não consegue restaurar o próprio papel/status.
- Usuário ativo da organização correta continua acessando o que seu papel permite.
- Usuário de outra organização continua bloqueado.
- JWT emitido antes do reset de senha é negado após o reset.
- JWT emitido após nova autenticação funciona conforme o papel atual.

---

## 8. Lote B — abuso de rotas públicas

### SEC-06 — `POST /users/login` sem rate limiting

- Severidade auditada: média.
- Resultado esperado: limitar tentativas por origem e por identidade normalizada, com respostas seguras e sem enumeração de conta.

### SEC-07 — `POST /auth/login` sem rate limiting independente

- Severidade auditada: média.
- O alias é uma superfície pública própria e não pode contornar o controle da rota anterior.

### SEC-08 — forgot-password sem cooldown

- Severidade auditada: média.
- Falha: chamadas repetidas podem substituir tokens pendentes e provocar spam ou invalidação contínua do fluxo legítimo.
- Resultado esperado: cooldown atômico, resposta uniforme para conta existente/inexistente e envio de e-mail somente quando permitido.

### SEC-09 — signup público cria tenants/admins sem limite

- Severidade auditada: média.
- Resultado esperado: política explícita de onboarding, limite resistente a concorrência e proteção contra abuso de recursos e ocupação de e-mail.

### Direção técnica do lote B

- Procure primeiro um mecanismo durável existente: API Gateway/WAF, storage atômico compartilhado ou política de onboarding já definida.
- Não implemente contador global em memória, dicionário no módulo ou cache local da Lambda.
- Não introduza CAPTCHA, serviço externo, nova tabela, migration ou infraestrutura sem aprovação específica.
- Se o controle correto depender da Fase 7/AWS, entregue uma proposta exata de configuração e testes, marque o achado como dependente de infraestrutura e não finja que foi corrigido no runtime local.
- Forgot-password pode ser corrigido com estado persistente existente apenas se a operação for atômica e resistente a concorrência.
- Preserve mensagens uniformes para evitar enumeração de usuários.

### Testes mínimos do lote B

- limite compartilhado entre os dois aliases de login, quando a política for comum;
- limite por conta e origem conforme a arquitetura escolhida;
- concorrência não permite ultrapassar o limite;
- conta inexistente e existente produzem resposta pública indistinguível;
- forgot-password dentro do cooldown não troca o token válido nem dispara novo envio;
- signup bloqueia abuso sem confiar em headers facilmente forjáveis como única identidade.

---

## 9. Lote C — pagamento e proveniência jurídica

### SEC-10 — reembolso mantém entitlement de serviço pago

- Severidade auditada: baixa.
- Evidência original: transição para `REFUNDED` em `src/handlers/pix.py`, enquanto o gate continua reconhecendo o pedido como pago.
- Resultado esperado: o reembolso revoga de forma consistente o direito de iniciar novas operações pagas, preservando histórico e auditoria.

Defina explicitamente o comportamento para operações já concluídas. Não apague evidência jurídica ou financeira histórica.

### SEC-11 — triagem fica liberada quando `PAYMENT_GATE` está ausente

- Severidade auditada: média.
- Evidência original: `src/services/case_lifecycle.py` assume modo permissivo por padrão.
- Resultado esperado: configuração ausente ou inválida deve falhar fechada. Apenas ambiente local explicitamente identificado pode usar bypass, se essa exceção for uma decisão existente e testada.

### SEC-12 — relatório fica liberado quando `PAYMENT_GATE` está ausente

- Severidade auditada: média.
- A geração do relatório deve usar o mesmo controle canônico e fail-closed da triagem.

### SEC-13 — relatório mock pode ser aprovado e concluir o caso

- Severidade auditada: média.
- Evidência original: `src/services/report_generator.py` identifica conteúdo mock, mas o fluxo de revisão permite aprovar e completar o caso.
- Resultado esperado: relatório mock/local/placeholder nunca pode atingir estado jurídico final ou concluir um caso.

### Direção técnica do lote C

- Centralize a política de entitlement e pagamento; evite branches divergentes para triagem e relatório.
- O estado financeiro deve vir do banco e da transação confiável, nunca do frontend.
- Trate `REFUNDED` como sem entitlement para novas execuções.
- Exija `PAYMENT_GATE=hard` fora de desenvolvimento e faça `enforce_production_safety` rejeitar configuração ausente, inválida ou permissiva.
- Proveniência do relatório deve ser persistida pelo backend e não ser controlável pelo corpo da requisição.
- Estados mock, local, placeholder ou limitação equivalente devem bloquear aprovação e conclusão.
- Preserve revisão humana como requisito adicional, nunca como substituto da verificação de proveniência.
- Gere auditoria sanitizada para transições sensíveis.

### Testes mínimos do lote C

- triagem e relatório retornam bloqueio de pagamento quando a variável está ausente;
- valor inválido do gate falha fechado;
- pagamento confirmado libera operações permitidas;
- reembolso bloqueia nova triagem e novo relatório;
- relatório mock pode existir apenas como rascunho de desenvolvimento, mas não pode ser aprovado nem concluir o caso;
- relatório real ainda exige revisão humana;
- transições preservam tenant, auditoria e idempotência.

---

## 10. Lote D — integridade e limites de upload

### SEC-14 — upload presigned não impõe tamanho real

- Severidade auditada: média.
- Evidência original: `src/schemas/document_schemas.py` valida apenas o tamanho declarado; `src/services/storage.py` gera `put_object` presigned sem validar os bytes efetivamente armazenados.
- Resultado esperado: o limite deve ser aplicado no storage e reconfirmado antes de enfileirar ou processar o objeto.

### SEC-15 — URL PUT reutilizável permite substituir documento processado

- Severidade auditada: média.
- Evidência original: processamento e download resolvem a mesma chave mutável, sem vínculo obrigatório com versão, checksum ou identidade imutável do objeto.
- Resultado esperado: o conteúdo processado, revisado e posteriormente baixado deve ser exatamente o mesmo objeto imutável.

### Direção técnica do lote D

- Avalie presigned POST com `content-length-range`, ou mecanismo equivalente realmente aplicado pelo S3.
- Depois do upload, valide por `HeadObject` o tamanho, content type, ETag/checksum e, quando disponível, VersionId.
- Não confie em `file_size`, `content_type`, ETag ou checksum fornecidos apenas pelo cliente.
- Use chave única por tentativa de upload; não reutilize uma chave final durante uma janela em que ela ainda possa ser sobrescrita.
- Registre no banco a identidade imutável do objeto aceito e faça processamento/download usarem essa identidade.
- Só mude o estado do documento para pronto após validação atômica.
- Rejeite processamento de objeto ausente, grande demais, com tipo incompatível ou identidade divergente.
- Não torne o bucket público.
- Se Versioning, Object Lock, nova policy de bucket ou alteração de infraestrutura forem necessários, não os aplique sem aprovação; entregue a configuração proposta separadamente.

### Testes mínimos do lote D

- tamanho declarado pequeno e objeto real acima do limite é rejeitado;
- objeto no limite exato é aceito;
- processamento não inicia antes da confirmação do storage;
- segunda tentativa de upload não sobrescreve o objeto já aceito;
- alteração de ETag/checksum/VersionId entre validação e processamento é rejeitada;
- download resolve a mesma identidade processada;
- falhas de `HeadObject` não são tratadas como sucesso;
- tenant não consegue resolver chave ou versão de outra organização.

---

## 11. Arquivos prováveis

Modifique apenas os que forem necessários após a revalidação:

```text
src/authorizers/jwt_authorizer.py
src/utils/auth.py
src/utils/context.py
src/utils/safety.py
src/handlers/users.py
src/handlers/pix.py
src/handlers/triage.py
src/handlers/reports.py
src/handlers/documents.py
src/services/case_lifecycle.py
src/services/report_generator.py
src/services/storage.py
src/services/document_ingestion.py
src/schemas/document_schemas.py
serverless.yml
tests/test_users_handlers.py
tests/test_safety.py
tests/test_pix_handlers.py
tests/test_reports_handlers.py
tests/test_documents_handlers.py
tests/test_adapters.py
```

Os nomes reais dos testes podem ter mudado. Descubra-os com `rg --files tests` e não crie arquivos duplicados sem necessidade.

## 12. Validação permitida

Antes de rodar testes que usam banco, leia `conftest.py` e `tests/_dbadmin.py`. Confirme que existe proteção explícita contra o banco de desenvolvimento. Se não puder provar isso, não execute a suíte de banco.

Comandos preferenciais, adaptados ao ambiente existente:

```powershell
.\.venv\Scripts\python.exe -m compileall -q src tests
.\.venv\Scripts\python.exe -m pytest <testes-direcionados> -q
.\.venv\Scripts\python.exe -m pytest tests\test_safety.py -q
git diff --check
git status --short
git diff --stat
git diff
```

Não instale `pytest` se o ambiente não o possuir. Nesse caso, registre claramente:

```text
Não validado dinamicamente.
Motivo: dependência ou ambiente de teste indisponível; instalação não autorizada.
```

Não execute automaticamente:

```text
docker
serverless deploy
terraform
aws
psql
scripts de setup/seed/migration
pip install
npm install
git add
git commit
git push
git pull
```

## 13. Critérios de sucesso

O trabalho só está concluído quando:

1. os 15 achados foram reavaliados no `HEAD` atual;
2. cada achado tem disposição e evidência verificável;
3. todo achado confirmado e corrigível sem nova autorização recebeu correção robusta;
4. não existe controle de segurança apenas no frontend;
5. não existe limiter em memória fingindo proteção distribuída;
6. JWT revogado, usuário desativado e papel rebaixado são efetivamente bloqueados;
7. reset de senha invalida sessões anteriores, caso exista mecanismo persistente aprovado;
8. pagamento e proveniência falham fechados;
9. relatório mock não pode virar resultado jurídico final;
10. o storage valida tamanho real e vincula processamento/download ao objeto aceito;
11. testes direcionados passam ou a impossibilidade é declarada sem ocultação;
12. `git diff --check` passa;
13. nenhum segredo, PII ou dado jurídico foi exposto;
14. nenhuma alteração fora do escopo foi feita;
15. nenhum commit ou push foi realizado.

## 14. Formato obrigatório da entrega

Ao terminar, produza:

```text
1. Veredito direto
2. Revisão atual analisada
3. Tabela SEC-01 a SEC-15 com disposição final
4. Correções implementadas por lote
5. Arquivos criados
6. Arquivos modificados
7. Arquivos removidos
8. Comandos executados
9. Testes e resultados exatos
10. O que não foi validado
11. Achados que exigem migration, infraestrutura ou decisão do usuário
12. Riscos restantes
13. Como testar manualmente
14. Próximo passo recomendado
15. Confirmação de que não houve commit ou push
```

Para cada achado não corrigido, use:

```text
ID:
Estado:
Fato:
Evidência:
Risco restante:
Por que não foi alterado:
Autorização necessária:
Próxima ação segura:
```

Não declare o sistema pronto para staging ou produção enquanto houver achado confirmado sem mitigação comprovada, controle dependente de infraestrutura ainda ausente ou teste crítico não executado.
