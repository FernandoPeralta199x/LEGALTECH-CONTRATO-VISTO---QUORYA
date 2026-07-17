"""Buckets de ``payment_status`` compartilhados pelos agregados financeiros (DRY).

Antes, ``_RECEIVED``/``_PENDING`` estavam triplicados em overview.py, services.py e
clients_revenue.py, com o risco de divergirem. Aqui ficam numa única fonte de verdade.

**PRC-01 (integridade financeira):** ``simulated`` é um pagamento MOCK — o seam de
desenvolvimento (``PaymentProvider`` mock); o dinheiro NUNCA entrou de fato. Contá-lo
como "dinheiro recebido" superdimensiona a receita (o Relatório Executivo chegava a
inflar ~5,4× o recebido nos dados de dev). Por isso ``RECEIVED`` é **só ``paid``**, em
TODOS os ambientes — o mock ``simulated`` é sempre reportado à parte (``simulated_cents``),
uma linha honesta "sem cobrança real", nunca embutido no recebido.

Os buckets particionam os 7 status sem sobreposição, então a soma reconcilia com o bruto:
``gross = received + pending + canceled + simulated + refunded``.
"""

RECEIVED = ["paid"]                     # dinheiro REAL que entrou (só pagamento confirmado)
PENDING = ["pending", "processing"]     # venda feita, ainda não paga
CANCELED = ["canceled", "expired", "failed"]
SIMULATED = ["simulated"]               # pagamento mock (dev) — reportado à parte, nunca no recebido
