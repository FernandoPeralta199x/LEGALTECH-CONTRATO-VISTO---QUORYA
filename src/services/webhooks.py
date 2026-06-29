"""Serviço de webhooks para eventos"""
import logging
import json
import os
from datetime import datetime
from typing import Dict, Any

logger = logging.getLogger()

class WebhookService:
    """Serviço de webhooks para notificações em tempo real"""
    
    def __init__(self, db):
        self.db = db
    
    def register_webhook(self, client_id: str, event_type: str, url: str) -> bool:
        """Registrar webhook para cliente"""
        try:
            query = """
            INSERT INTO public.webhooks (client_id, event_type, url, status, created_at)
            VALUES (%s, %s, %s, %s, %s)
            """
            
            self.db.execute_update(query, (
                client_id,
                event_type,
                url,
                'active',
                datetime.now().isoformat()
            ))
            
            logger.info(f"Webhook registrado: {client_id} - {event_type}")
            return True
        
        except Exception as e:
            logger.error(f"Erro ao registrar webhook: {str(e)}")
            return False
    
    def trigger_webhook(self, client_id: str, event_type: str, data: Dict[str, Any]):
        """Disparar webhook para todos os inscritos"""
        try:
            import requests
            
            # Buscar webhooks registrados
            query = """
            SELECT url FROM public.webhooks
            WHERE client_id = %s AND event_type = %s AND status = %s
            """
            
            webhooks = self.db.execute_query(query, (client_id, event_type, 'active'))
            
            payload = {
                'event_type': event_type,
                'timestamp': datetime.now().isoformat(),
                'data': data
            }
            
            for webhook in webhooks:
                try:
                    response = requests.post(
                        webhook['url'],
                        json=payload,
                        timeout=10,
                        headers={'Content-Type': 'application/json'}
                    )
                    
                    if response.status_code >= 400:
                        logger.warning(f"Webhook falhou: {webhook['url']} - {response.status_code}")
                
                except Exception as e:
                    logger.error(f"Erro ao disparar webhook: {str(e)}")
        
        except Exception as e:
            logger.error(f"Erro em trigger_webhook: {str(e)}")
