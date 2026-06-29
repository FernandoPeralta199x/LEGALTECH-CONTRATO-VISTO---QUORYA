# src/utils/safety.py
import os
import logging

logger = logging.getLogger()

def enforce_production_safety():
    """
    Trava de segurança Fail-Closed para ambiente Serverless.
    Bloqueia a inicialização do container se houver configs inseguras.
    """
    APP_ENV = os.getenv("APP_ENV", "local")
    
    if APP_ENV in ["staging", "production"]:
        # 1. Validar se chaves padrão estão sendo usadas
        if os.getenv("JWT_SECRET_KEY") == "sua-chave-secreta":
            logger.critical("BOOT BLOQUEADO: Chave secreta padrão detectada em produção!")
            raise RuntimeError("Chave JWT insegura em ambiente produtivo.")
            
        # 2. Validar se existem Mocks ativos onde não devia
        if os-getenv("AI_ANALYSIS_BACKEND") == "mock":
            logger.critical("BOOT BLOQUEADO: Backend IA em modo MOCK detectado em produção!")
            raise RuntimeError("Mocks não são permitidos em produção.")
            
        # Adicione aqui outras checagens críticas do seu antigo config.py...