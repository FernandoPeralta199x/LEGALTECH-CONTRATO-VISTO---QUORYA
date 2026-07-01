"""Abstração de fila (SP1) — publica DocumentProcessingJob na SQS (AWS) ou processa
inline (dev/local). Espelha modules/queues da referência adaptado ao serverless:
na AWS o disparo SQS->Lambda entrega os Records ao worker; em dev não há SQS, então
o endpoint de enfileiramento processa o job de forma síncrona após publicar (no-op).

Backend escolhido por env QUEUE_BACKEND ('sqs' | 'inline', default 'inline').
"""
from __future__ import annotations

import os

from src.schemas.queue_schemas import DocumentProcessingJob


def queue_backend() -> str:
    return "sqs" if os.environ.get("QUEUE_BACKEND", "").lower() == "sqs" else "inline"


class InlineQueueClient:
    """Sem SQS: publish é no-op; o chamador processa o job inline (dev)."""
    backend = "inline"

    def publish(self, job: DocumentProcessingJob) -> None:
        return None


class SqsQueueClient:
    """Publica o job na SQS (boto3). Usado quando QUEUE_BACKEND=sqs (AWS)."""
    backend = "sqs"

    def __init__(self, queue_url: str, region_name: str | None = None, client=None) -> None:
        self.queue_url = queue_url
        if client is not None:
            self.client = client
        else:
            import boto3  # import tardio: só na AWS/quando há SQS
            self.client = boto3.client("sqs", region_name=region_name)

    def publish(self, job: DocumentProcessingJob) -> None:
        self.client.send_message(QueueUrl=self.queue_url, MessageBody=job.model_dump_json())


def create_queue_client(client=None):
    """Fábrica do cliente de fila conforme o env (sqs vs inline)."""
    if queue_backend() == "sqs":
        url = os.environ.get("DOCUMENT_PROCESSING_QUEUE_URL")
        if not url:
            raise ValueError("DOCUMENT_PROCESSING_QUEUE_URL é obrigatório quando QUEUE_BACKEND=sqs")
        return SqsQueueClient(url, region_name=os.environ.get("AWS_REGION"), client=client)
    return InlineQueueClient()
