import os
import sys
from dotenv import load_dotenv

# Carregar .env EXPLICITAMENTE
load_dotenv(dotenv_path='.env', verbose=True)

# Debug: Mostrar variáveis carregadas
print("=== VARIÁVEIS CARREGADAS ===")
print(f"DB_HOST: {os.getenv('DB_HOST')}")
print(f"DB_USER: {os.getenv('DB_USER')}")
print(f"DB_NAME: {os.getenv('DB_NAME')}")
print(f"DB_PORT: {os.getenv('DB_PORT')}")
print("=" * 40)

# Agora tentar conectar
try:
    import psycopg2
    
    host = os.getenv('DB_HOST')
    user = os.getenv('DB_USER')
    password = os.getenv('DB_PASS')
    database = os.getenv('DB_NAME')
    port = os.getenv('DB_PORT', 5432)
    
    print(f"\nTentando conectar a:")
    print(f"  Host: {host}")
    print(f"  User: {user}")
    print(f"  Database: {database}")
    print(f"  Port: {port}")
    print()
    
    # Conectar
    conn = psycopg2.connect(
        host=host,
        user=user,
        password=password,
        database=database,
        port=int(port),
        connect_timeout=5
    )
    
    cursor = conn.cursor()
    cursor.execute("SELECT version();")
    version = cursor.fetchone()
    
    print(f"✓ CONECTADO! Versão: {version[0]}")
    
    # Contar tabelas
    cursor.execute("""
        SELECT table_name FROM information_schema.tables 
        WHERE table_schema = 'public'
    """)
    tables = cursor.fetchall()
    print(f"✓ Tabelas no banco: {len(tables)}")
    for table in tables:
        print(f"  - {table[0]}")
    
    cursor.close()
    conn.close()
    print("\n✓ TESTE BEM-SUCEDIDO!")
    
except Exception as e:
    print(f"✗ ERRO: {str(e)}")
    print(f"\nTipo do erro: {type(e).__name__}")
    sys.exit(1)
