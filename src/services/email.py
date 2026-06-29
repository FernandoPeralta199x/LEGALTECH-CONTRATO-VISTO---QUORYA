"""Serviço de e-mail (reset de senha).

Backends:
- ``ses``: envio real via AWS SES (boto3). Use em staging/produção.
- ``log`` (default em dev): NÃO envia; apenas registra o evento. NUNCA loga o
  token de reset (é segredo). ``enforce_production_safety`` bloqueia este backend
  em ambiente produtivo.

O backend é escolhido por ``EMAIL_BACKEND``. O remetente por ``EMAIL_FROM`` e o
domínio do link por ``APP_RESET_URL_BASE``.
"""
import json
import logging
import os

logger = logging.getLogger()


class EmailService:
    def __init__(self):
        self.backend = os.getenv("EMAIL_BACKEND", "log").lower()
        self.sender = os.getenv("EMAIL_FROM", "no-reply@contrato-visto.local")
        self.reset_url_base = os.getenv(
            "APP_RESET_URL_BASE", "https://app.contrato-visto.local/reset"
        )

    def send_reset_password_email(self, to_email, reset_token, user_name=None):
        """Envia o link de reset. Retorna ``True`` se enviado/registrado."""
        reset_link = f"{self.reset_url_base}?token={reset_token}"
        if self.backend == "ses":
            return self._send_ses(to_email, reset_link, user_name)
        # backend 'log'/dev: não envia e NÃO registra o token.
        logger.info(json.dumps({
            "event": "PASSWORD_RESET_EMAIL_SKIPPED", "backend": self.backend,
        }))
        return True

    def _send_ses(self, to_email, reset_link, user_name=None):
        import boto3  # import tardio: só quando o backend real é usado
        client = boto3.client("ses", region_name=os.getenv("AWS_REGION", "us-east-1"))
        greeting = f"Olá {user_name}," if user_name else "Olá,"
        body_text = (
            f"{greeting}\n\nRecebemos um pedido para redefinir sua senha. "
            f"Use o link abaixo (válido por 1 hora):\n\n{reset_link}\n\n"
            "Se você não solicitou, ignore este e-mail."
        )
        try:
            client.send_email(
                Source=self.sender,
                Destination={"ToAddresses": [to_email]},
                Message={
                    "Subject": {"Data": "Redefinição de senha — Contrato Visto"},
                    "Body": {"Text": {"Data": body_text}},
                },
            )
            return True
        except Exception as e:
            logger.error(json.dumps({
                "event": "PASSWORD_RESET_EMAIL_ERROR", "error": type(e).__name__,
            }))
            return False


email_service = EmailService()
