"""Serviço de auditoria para LGPD compliance"""
import logging
from datetime import datetime

logger = logging.getLogger()

def log_action(db, user_id: str, action: str, resource_type: str, resource_id: str, status: str = 'success'):
    """Registrar ação para auditoria"""
    try:
        query = """
        INSERT INTO audit.audit_log (user_id, action, resource_type, resource_id, status, accessed_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        """
        
        db.execute_update(query, (
            user_id,
            action,
            resource_type,
            resource_id,
            status,
            datetime.now().isoformat()
        ))
        
        logger.info(f"Auditoria: {user_id} {action} {resource_type} {resource_id}")
    
    except Exception as e:
        logger.error(f"Erro ao registrar auditoria: {str(e)}")

def log_pii_access(db, user_id: str, data_type: str):
    """Registrar acesso a dados pessoais (PII) - LGPD"""
    try:
        query = """
        INSERT INTO audit.data_access_log (user_id, resource_type, access_type, accessed_at, pii_accessed)
        VALUES (%s, %s, %s, %s, %s)
        """
        
        db.execute_update(query, (
            user_id,
            data_type,
            'read',
            datetime.now().isoformat(),
            True
        ))
        
        logger.warning(f"LGPD Alert: {user_id} acessou PII - {data_type}")
    
    except Exception as e:
        logger.error(f"Erro ao registrar data access: {str(e)}")

def get_audit_log(db, user_id: str = None, days: int = 30):
    """Obter logs de auditoria"""
    try:
        if user_id:
            query = """
            SELECT user_id, action, resource_type, resource_id, status, accessed_at
            FROM audit.audit_log
            WHERE user_id = %s
            AND accessed_at >= NOW() - INTERVAL '%s days'
            ORDER BY accessed_at DESC
            LIMIT 1000
            """
            results = db.execute_query(query, (user_id, days))
        else:
            query = """
            SELECT user_id, action, resource_type, resource_id, status, accessed_at
            FROM audit.audit_log
            WHERE accessed_at >= NOW() - INTERVAL '%s days'
            ORDER BY accessed_at DESC
            LIMIT 1000
            """
            results = db.execute_query(query, (days,))
        
        return [dict(r) for r in results]
    
    except Exception as e:
        logger.error(f"Erro ao obter audit log: {str(e)}")
        return []

def get_pii_access_log(db, days: int = 30):
    """Obter logs de acesso a PII (LGPD)"""
    try:
        query = """
        SELECT user_id, resource_type, accessed_at, pii_accessed
        FROM audit.data_access_log
        WHERE pii_accessed = true
        AND accessed_at >= NOW() - INTERVAL '%s days'
        ORDER BY accessed_at DESC
        LIMIT 1000
        """
        
        results = db.execute_query(query, (days,))
        return [dict(r) for r in results]
    
    except Exception as e:
        logger.error(f"Erro ao obter data access log: {str(e)}")
        return []
