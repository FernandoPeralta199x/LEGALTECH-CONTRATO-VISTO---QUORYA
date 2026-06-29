"""Serviço de cache com Redis"""
import logging
import json
import os
from typing import Any, Optional
from datetime import timedelta

logger = logging.getLogger()

class CacheService:
    """Serviço de cache com Redis"""
    
    def __init__(self):
        try:
            import redis
            
            redis_host = os.getenv('REDIS_HOST', 'localhost')
            redis_port = int(os.getenv('REDIS_PORT', 6379))
            redis_db = int(os.getenv('REDIS_DB', 0))
            
            self.redis_client = redis.Redis(
                host=redis_host,
                port=redis_port,
                db=redis_db,
                decode_responses=True
            )
            
            # Testar conexão
            self.redis_client.ping()
            logger.info("✓ Conectado ao Redis")
            self.available = True
        
        except Exception as e:
            logger.warning(f"Redis não disponível: {str(e)}")
            self.available = False
    
    def get(self, key: str) -> Optional[Any]:
        """Obter valor do cache"""
        try:
            if not self.available:
                return None
            
            value = self.redis_client.get(key)
            
            if value:
                return json.loads(value)
            
            return None
        
        except Exception as e:
            logger.error(f"Erro ao obter do cache: {str(e)}")
            return None
    
    def set(self, key: str, value: Any, ttl_seconds: int = 3600):
        """Armazenar valor no cache"""
        try:
            if not self.available:
                return False
            
            self.redis_client.setex(
                key,
                ttl_seconds,
                json.dumps(value)
            )
            
            return True
        
        except Exception as e:
            logger.error(f"Erro ao armazenar no cache: {str(e)}")
            return False
    
    def delete(self, key: str):
        """Deletar valor do cache"""
        try:
            if not self.available:
                return False
            
            self.redis_client.delete(key)
            return True
        
        except Exception as e:
            logger.error(f"Erro ao deletar do cache: {str(e)}")
            return False
    
    def clear_pattern(self, pattern: str):
        """Deletar múltiplas chaves por padrão"""
        try:
            if not self.available:
                return False
            
            keys = self.redis_client.keys(pattern)
            if keys:
                self.redis_client.delete(*keys)
            
            return True
        
        except Exception as e:
            logger.error(f"Erro ao limpar cache: {str(e)}")
            return False

# Instância global
cache = CacheService()
