import psycopg2
from psycopg2.extras import RealDictCursor
import logging

logger = logging.getLogger()

class Database:
    def __init__(self, host, user, password, database, port=5432):
        self.host = host
        self.user = user
        self.password = password
        self.database = database
        self.port = port
        self.connection = None
    
    def connect(self):
        """Conectar ao PostgreSQL"""
        try:
            self.connection = psycopg2.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.database,
                port=self.port
            )
            logger.info("Conexão com banco estabelecida")
        except Exception as e:
            logger.error(f"Erro ao conectar: {str(e)}")
            raise
    
    def disconnect(self):
        """Desconectar do PostgreSQL"""
        if self.connection:
            self.connection.close()
            logger.info("Conexão fechada")
    
    def __enter__(self):
        """Context Manager - entrada"""
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context Manager - saída (SEMPRE executado)"""
        self.disconnect()
        if exc_type:
            logger.error(f"Erro no context: {exc_val}")
            return False
        return True
    
    def execute_query(self, query, params=None):
        """Executar query que retorna múltiplas linhas"""
        try:
            cursor = self.connection.cursor(cursor_factory=RealDictCursor)
            cursor.execute(query, params)
            results = cursor.fetchall()
            cursor.close()
            return results
        except Exception as e:
            logger.error(f"Erro na query: {str(e)}")
            raise
    
    def execute_query_one(self, query, params=None):
        """Executar query que retorna uma linha"""
        try:
            cursor = self.connection.cursor(cursor_factory=RealDictCursor)
            cursor.execute(query, params)
            result = cursor.fetchone()
            cursor.close()
            return result
        except Exception as e:
            logger.error(f"Erro na query: {str(e)}")
            raise
    
    def execute_update(self, query, params=None):
        """Executar INSERT, UPDATE, DELETE"""
        try:
            cursor = self.connection.cursor()
            cursor.execute(query, params)
            self.connection.commit()
            cursor.close()
            logger.info(f"Query executada com sucesso")
        except Exception as e:
            self.connection.rollback()
            logger.error(f"Erro na atualização: {str(e)}")
            raise

# Instância global
db = Database(
    host=os.getenv('DB_HOST'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME'),
    port=int(os.getenv('DB_PORT', 5432))
)