"""Adapters de serviços externos (camada serverless).

Espelha o formato do produto de referência `legaltech-aws`: cada adapter expõe um
``Protocol`` (interface), uma implementação **Mock** (dados fictícios p/ dev/local),
uma implementação **Real** (placeholder pronto para implementação na AWS — requer
contrato/API key) e uma fábrica ``create_*`` controlada por variáveis de ambiente
(``*_BACKEND`` = mock|real, ``*_API_KEY``).

Diferença em relação ao legaltech-aws: os métodos são **síncronos** (o backend
serverless é sync — psycopg2 + handlers nativos), não ``async``. A estrutura
(Protocol + Mock + Real + factory + ``AdapterResult``) é idêntica.
"""
