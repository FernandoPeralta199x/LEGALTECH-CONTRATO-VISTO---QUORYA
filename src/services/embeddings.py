"""Geração de embeddings (1536 dims) para busca vetorial (pgvector).

Backends:
- ``openai``: API real (text-embedding-3-small). Use em staging/produção.
- ``mock`` (default em dev): vetor DETERMINÍSTICO derivado do texto, normalizado.
  Permite testar a busca vetorial no PG18 sem chamar a OpenAI. NÃO faz rede.
  ``enforce_production_safety`` bloqueia este backend em ambiente produtivo.

Config: ``EMBEDDINGS_BACKEND``, ``EMBEDDINGS_MODEL``, ``OPENAI_API_KEY``.
"""
import hashlib
import os
import random

DIM = 1536


class EmbeddingsService:
    def __init__(self):
        self.backend = os.getenv("EMBEDDINGS_BACKEND", "mock").lower()
        self.model = os.getenv("EMBEDDINGS_MODEL", "text-embedding-3-small")

    def embed(self, text: str):
        if self.backend == "openai":
            return self._openai(text)
        return self._mock(text)

    def _mock(self, text: str):
        """Vetor determinístico e normalizado a partir do texto (dev/testes)."""
        seed = int.from_bytes(hashlib.sha256((text or "").encode("utf-8")).digest(), "big")
        rnd = random.Random(seed)
        vec = [rnd.uniform(-1.0, 1.0) for _ in range(DIM)]
        norm = sum(x * x for x in vec) ** 0.5 or 1.0
        return [x / norm for x in vec]

    def _openai(self, text: str):
        import openai  # import tardio: só quando o backend real é usado
        client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        resp = client.embeddings.create(model=self.model, input=text)
        return resp.data[0].embedding


def to_vector_literal(embedding) -> str:
    """Serializa uma lista de floats no literal aceito pelo pgvector: ``[a,b,...]``."""
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


embeddings_service = EmbeddingsService()
