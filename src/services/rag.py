"""Serviço de RAG (Retrieval-Augmented Generation)"""
import logging
import json
import os
from typing import List, Dict

logger = logging.getLogger()

class RAGService:
    """Serviço de busca vetorial com pgvector"""
    
    def __init__(self, db):
        self.db = db
        self.embedding_dimension = 1536
    
    def generate_embeddings(self, text: str) -> List[float]:
        """Gerar embeddings usando OpenAI"""
        try:
            import openai
            
            api_key = os.getenv('OPENAI_API_KEY')
            if not api_key:
                logger.warning("OPENAI_API_KEY não configurado")
                return None
            
            client = openai.OpenAI(api_key=api_key)
            
            response = client.embeddings.create(
                model="text-embedding-3-small",
                input=text
            )
            
            return response.data[0].embedding
        
        except Exception as e:
            logger.error(f"Erro ao gerar embeddings: {str(e)}")
            return None
    
    def store_embedding(self, document_id: str, text: str, embedding: List[float]):
        """Armazenar embedding no pgvector"""
        try:
            query = """
            INSERT INTO public.document_embeddings (document_id, content, embedding)
            VALUES (%s, %s, %s)
            ON CONFLICT (document_id) DO UPDATE SET embedding = EXCLUDED.embedding
            """
            
            self.db.execute_update(query, (
                document_id,
                text,
                json.dumps(embedding)
            ))
            
            logger.info(f"Embedding armazenado: {document_id}")
        
        except Exception as e:
            logger.error(f"Erro ao armazenar embedding: {str(e)}")
    
    def search_similar(self, query_text: str, case_id: str, top_k: int = 5) -> List[Dict]:
        """Buscar documentos similares usando busca vetorial"""
        try:
            # Gerar embedding da query
            query_embedding = self.generate_embeddings(query_text)
            
            if not query_embedding:
                logger.warning("Não foi possível gerar embedding da query")
                return []
            
            # Buscar similaridade no pgvector
            search_query = """
            SELECT 
                de.document_id,
                d.file_name,
                d.case_id,
                1 - (de.embedding <-> %s::vector) as similarity_score
            FROM public.document_embeddings de
            JOIN public.documents d ON de.document_id = d.id
            WHERE d.case_id = %s
            ORDER BY de.embedding <-> %s::vector
            LIMIT %s
            """
            
            results = self.db.execute_query(search_query, (
                json.dumps(query_embedding),
                case_id,
                json.dumps(query_embedding),
                top_k
            ))
            
            return [dict(r) for r in results]
        
        except Exception as e:
            logger.error(f"Erro na busca vetorial: {str(e)}")
            return []
    
    def get_context_for_llm(self, query_text: str, case_id: str) -> str:
        """Obter contexto para alimentar LLM"""
        try:
            similar_docs = self.search_similar(query_text, case_id, top_k=3)
            
            context = "Documentos relevantes encontrados:\n\n"
            
            for doc in similar_docs:
                context += f"- {doc['file_name']} (similaridade: {doc['similarity_score']:.2%})\n"
            
            return context
        
        except Exception as e:
            logger.error(f"Erro ao gerar contexto: {str(e)}")
            return ""
