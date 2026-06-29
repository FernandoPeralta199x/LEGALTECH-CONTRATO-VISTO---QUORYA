"""Serviço de rate limiting"""
import logging
import os
from datetime import datetime, timedelta

logger = logging.getLogger()

class RateLimitService:
    """Serviço de rate limiting com Redis"""
    
    def __init__(self, cache):
        self.cache = cache
        self.default_limit = 100  # requisições
        self.default_window = 3600  # segundos (1 hora)
    
    def check_limit(self, user_id: str, limit: int = None, window: int = None) -> tuple[bool, int]:
        """
        Verificar se usuário excedeu limite
        
        Returns:
            (allowed, remaining_requests)
        """
        try:
            limit = limit or self.default_limit
            window = window or self.default_window
            
            key = f"rate_limit:{user_id}"
            
            current_count = self.cache.get(key) or 0
            current_count += 1
            
            if current_count > limit:
                return False, 0
            
            # Atualizar cache com TTL
            self.cache.set(key, current_count, window)
            
            remaining = limit - current_count
            
            logger.info(f"Rate limit check: {user_id} - {current_count}/{limit}")
            
            return True, remaining
        
        except Exception as e:
            logger.error(f"Erro em rate limit: {str(e)}")
            # Permitir se cache falhar (falha aberta)
            return True, limit
