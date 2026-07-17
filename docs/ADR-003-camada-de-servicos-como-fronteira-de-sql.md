# ADR-003: `src/services/<domínio>/` é a camada onde vive o SQL

**Status:** Aceito
**Data:** 2026-07-16
**Decisores:** Fernando (produto) · sessão Claude (implementação)

## Contexto

Coexistem hoje duas arquiteturas de acesso a dados no backend, por evolução
temporal (ARQ-03 da auditoria FULL):

- **Handlers financeiros (Fases 4-6, os mais recentes)** têm **0 SQL**: só fazem
  auth (`@require_user`/`@require_role`), parse (`resolve_range`/`parse_pagination`),
  delegam a `src/services/financial/*` e respondem. Ex.: `financial_api_costs.py`.
- **Handlers antigos** (`cases.py` 21 `cur.execute`, `users.py` 16, `requests.py`
  13) misturam SQL, regra de negócio e serialização no próprio handler.

Não existe `src/repositories/`; a camada limpa que já existe é `src/services/`.
A convenção **de fato** é o padrão dos financeiros, mas **não estava escrita** —
então cada feature nova reabre a decisão de "onde vai o SQL?" e o código diverge
por autor/data.

Ressalva honesta (verificação adversarial da auditoria): os `services/financial/*`
operam sobre um cursor `psycopg2` já em `tenant_tx` passado pelo handler — **não**
são um seam de mock/inversão de dependência, e a suíte é de INTEGRAÇÃO (PG18 + RLS
real, ver [ADR-002](ADR-002-banco-de-teste-separado.md)) por escolha de projeto: a
isolação por org é RLS **no banco**, então testar sem banco derrotaria a garantia.
O ganho da camada, portanto, **não** é testabilidade-por-mock; é **fronteira**:
um único lugar por domínio onde o SQL mora, legível e migrável.

## Decisão

1. **`src/services/<domínio>/` é a única camada que contém SQL.** Handlers fazem
   auth + parse + delegação + resposta. `services/financial/*` é a referência.
2. **Proibido SQL novo em handler para features novas.** Um handler novo que
   precise de banco cria/estende um módulo em `services/`.
3. **Migração oportunista, não big-bang.** NÃO refatorar `cases.py`/`users.py`
   agora: são caminhos centrais (RLS/`tenant_tx`), já decompostos e cobertos por
   testes de integração; mover 21 queries às vésperas da Fase 7 troca risco
   conhecido por desconhecido. Quando um handler antigo for tocado por outro
   motivo, extrair aquelas queries para `services/`. Primeiro alvo natural quando
   houver janela: `requests.py` (o menor dos antigos).

Precedente já aplicado nesta direção: [ADR-002] e a extração da regra de período
fiscal para `services/financial/period.py` (ARQ-02), que tirou regra de domínio de
dentro do handler.

## Opções consideradas

### A: escrever a regra + parar a sangria + migrar oportunista (escolhida)
**Prós:** custo ~zero, sem risco de runtime; o padrão bom vira descobrível; impede
o antipadrão crescer. **Contras:** a dívida antiga (`cases`/`users`) só some aos poucos.

### B: introduzir `src/repositories/` com inversão de dependência agora
**Contras:** reescrever os caminhos centrais RLS às vésperas do deploy; a suíte de
integração não se beneficia de mock; ganho não supera o risco (§16.3 da auditoria).

### C: não documentar (status quo)
**Contras:** cada feature reabre a decisão; a divergência por autor/data continua.

## Consequências

- **Regra explícita:** novas features colocam SQL em `services/<domínio>/`.
- **Trava incremental sugerida (CI da Fase 7):** contar `cur.execute` por arquivo
  em `src/handlers/` e falhar se um handler **aumentar** sua contagem sobre um
  baseline versionado — permite migrar para baixo, bloqueia crescer. Baseline atual:
  `cases.py`=21, `users.py`=16, `requests.py`=13, `case_results.py`=8, `clients.py`=7,
  `documents.py`=7, `case_parties.py`=6, `dashboard.py`=5, `payments.py`=4,
  `pricing.py`=3; financeiros e `worker.py`=0.
- **Aceitação de cada extração futura:** paridade — pytest do handler afetado verde
  antes e depois, mesma resposta HTTP (status + corpo) para os mesmos inputs.
- **Não é dívida crítica:** o lado antigo está funcionalmente correto e decomposto
  (ex.: `cases.get_case_aggregate` só orquestra serializers; pricing já é "repo único"
  em `services/pricing`). Isto é higiene de fronteira, não correção de bug.
