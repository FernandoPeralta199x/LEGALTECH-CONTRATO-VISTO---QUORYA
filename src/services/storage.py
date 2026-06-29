"""Armazenamento de documentos (S3) via URLs pré-assinadas.

O upload NÃO passa pela Lambda: o handler devolve uma URL pré-assinada (PUT) e o
cliente envia o arquivo direto ao S3; o download usa URL pré-assinada (GET).

Backends:
- ``s3``: AWS S3 real (boto3). Use em staging/produção.
- ``local`` (default em dev): retorna URLs fictícias para testes; NÃO acessa rede.
  ``enforce_production_safety`` bloqueia este backend em ambiente produtivo.

Config: ``STORAGE_BACKEND``, ``DOCUMENTS_BUCKET``, ``PRESIGNED_EXPIRES`` (s),
``AWS_REGION``.
"""
import os
import re


class StorageService:
    def __init__(self):
        self.backend = os.getenv("STORAGE_BACKEND", "local").lower()
        self.bucket = os.getenv("DOCUMENTS_BUCKET", "contrato-visto-documents")
        self.expires = int(os.getenv("PRESIGNED_EXPIRES", "900"))

    @staticmethod
    def build_key(case_id: str, doc_id: str, file_name: str) -> str:
        """Chave S3 do objeto. `file_name` é só rótulo final; o caminho usa IDs.

        Sanitiza o nome (sem traversal/separadores); usa fallback se ficar vazio.
        """
        base = os.path.basename(str(file_name)).replace("\\", "/").split("/")[-1]
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", base).strip("._") or "arquivo"
        return f"cases/{case_id}/documents/{doc_id}/{safe_name}"

    def object_url(self, key: str) -> str:
        """URL canônica (s3://bucket/key) guardada em `documents.s3_url`."""
        return f"s3://{self.bucket}/{key}"

    def presign_put(self, key: str, content_type: str = None) -> str:
        if self.backend == "s3":
            params = {"Bucket": self.bucket, "Key": key}
            if content_type:
                params["ContentType"] = content_type
            return self._client().generate_presigned_url(
                "put_object", Params=params, ExpiresIn=self.expires
            )
        return f"https://local-storage.invalid/{self.bucket}/{key}?op=put"

    def presign_get(self, key: str) -> str:
        if self.backend == "s3":
            return self._client().generate_presigned_url(
                "get_object", Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=self.expires,
            )
        return f"https://local-storage.invalid/{self.bucket}/{key}?op=get"

    def _client(self):
        import boto3  # import tardio: só quando o backend real é usado
        return boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))


storage_service = StorageService()
