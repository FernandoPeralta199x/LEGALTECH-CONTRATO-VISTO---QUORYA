"""Serviço de analytics e reporting"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List

logger = logging.getLogger()

class AnalyticsService:
    """Serviço de analytics e relatórios"""
    
    def __init__(self, db):
        self.db = db
    
    def get_case_stats(self, client_id: str) -> Dict:
        """Obter estatísticas de casos"""
        try:
            query = """
            SELECT 
                COUNT(*) as total_cases,
                SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) as open_cases,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed_cases,
                AVG(EXTRACT(EPOCH FROM (updated_at - created_at))) as avg_duration_seconds
            FROM public.cases
            WHERE client_id = %s
            """
            
            result = self.db.execute_query_one(query, (client_id,))
            
            return {
                'total_cases': result['total_cases'],
                'open_cases': result['open_cases'],
                'completed_cases': result['completed_cases'],
                'avg_duration_hours': round(result['avg_duration_seconds'] / 3600, 2) if result['avg_duration_seconds'] else 0
            }
        
        except Exception as e:
            logger.error(f"Erro ao obter estatísticas: {str(e)}")
            return {}
    
    def get_user_activity(self, user_id: str, days: int = 30) -> List[Dict]:
        """Obter atividade do usuário nos últimos N dias"""
        try:
            query = """
            SELECT 
                DATE(created_at) as date,
                action,
                COUNT(*) as count
            FROM audit.audit_log
            WHERE user_id = %s
            AND created_at >= NOW() - INTERVAL '%s days'
            GROUP BY DATE(created_at), action
            ORDER BY DATE(created_at) DESC
            """
            
            results = self.db.execute_query(query, (user_id, days))
            
            return [dict(r) for r in results]
        
        except Exception as e:
            logger.error(f"Erro ao obter atividade: {str(e)}")
            return []
    
    def get_compliance_report(self, start_date: str, end_date: str) -> Dict:
        """Gerar relatório de compliance (LGPD)"""
        try:
            query = """
            SELECT 
                user_id,
                COUNT(*) as pii_accesses,
                ARRAY_AGG(DISTINCT data_type) as data_types_accessed
            FROM audit.data_access_log
            WHERE pii_accessed = true
            AND created_at BETWEEN %s AND %s
            GROUP BY user_id
            """
            
            results = self.db.execute_query(query, (start_date, end_date))
            
            return {
                'period': f"{start_date} to {end_date}",
                'total_pii_accesses': sum(r['pii_accesses'] for r in results),
                'users_accessed_pii': len(results),
                'details': [dict(r) for r in results]
            }
        
        except Exception as e:
            logger.error(f"Erro ao gerar relatório: {str(e)}")
            return {}
