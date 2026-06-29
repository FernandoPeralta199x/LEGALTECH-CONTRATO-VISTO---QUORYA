# Documentação do Banco de Dados: contrato_visto

## Tabela: case_results

| Coluna | Tipo de Dado | Aceita Nulo? |
| --- | --- | --- |
| created_by | uuid | YES |
| id | uuid | NO |
| case_id | uuid | NO |
| result_type | character varying | YES |
| result_title | character varying | YES |
| result_data | jsonb | YES |
| risk_level | character varying | YES |
| confidence_score | numeric | YES |
| summary_text | text | YES |
| detailed_findings | text | YES |
| recommendations | text | YES |
| created_at | timestamp without time zone | YES |

## Tabela: cases

| Coluna | Tipo de Dado | Aceita Nulo? |
| --- | --- | --- |
| case_type | character varying | NO |
| status | character varying | YES |
| priority | character varying | YES |
| created_at | timestamp without time zone | YES |
| completed_at | timestamp without time zone | YES |
| metadata | jsonb | YES |
| created_by | uuid | YES |
| assigned_to | uuid | YES |
| estimated_completion_date | timestamp without time zone | YES |
| id | uuid | NO |
| client_id | uuid | NO |

## Tabela: cases_with_latest_result

| Coluna | Tipo de Dado | Aceita Nulo? |
| --- | --- | --- |
| result_rank | bigint | YES |
| id | uuid | YES |
| client_id | uuid | YES |
| case_type | character varying | YES |
| status | character varying | YES |
| created_at | timestamp without time zone | YES |
| latest_result_id | uuid | YES |
| result_type | character varying | YES |
| risk_level | character varying | YES |
| result_created_at | timestamp without time zone | YES |

## Tabela: clients

| Coluna | Tipo de Dado | Aceita Nulo? |
| --- | --- | --- |
| address_street | character varying | YES |
| phone | character varying | YES |
| email | character varying | YES |
| document_number | character varying | NO |
| status | character varying | YES |
| is_active | boolean | YES |
| updated_at | timestamp without time zone | YES |
| created_at | timestamp without time zone | YES |
| address_zip | character varying | YES |
| address_state | character varying | YES |
| legal_name | character varying | NO |
| document_type | character varying | YES |
| id | uuid | NO |
| address_city | character varying | YES |

## Tabela: document_chunks

| Coluna | Tipo de Dado | Aceita Nulo? |
| --- | --- | --- |
| chunk_index | integer | YES |
| start_page | integer | YES |
| end_page | integer | YES |
| created_at | timestamp without time zone | YES |
| id | bigint | NO |
| document_id | uuid | NO |
| chunk_text | text | NO |

## Tabela: document_embeddings

| Coluna | Tipo de Dado | Aceita Nulo? |
| --- | --- | --- |
| embedding | USER-DEFINED | YES |
| segment_text | text | NO |
| chunk_id | bigint | YES |
| document_id | uuid | NO |
| id | bigint | NO |
| created_at | timestamp without time zone | YES |
| embedding_model | character varying | YES |
| segment_type | character varying | YES |

## Tabela: documents

| Coluna | Tipo de Dado | Aceita Nulo? |
| --- | --- | --- |
| created_at | timestamp without time zone | YES |
| case_id | uuid | NO |
| s3_url | character varying | NO |
| file_name | character varying | NO |
| file_type | character varying | YES |
| file_size_bytes | integer | YES |
| file_hash | character varying | YES |
| ocr_status | character varying | YES |
| ocr_result | text | YES |
| extraction_status | character varying | YES |
| document_classification | character varying | YES |
| document_issue_date | timestamp without time zone | YES |
| document_expiry_date | timestamp without time zone | YES |
| id | uuid | NO |
| uploaded_by | uuid | YES |
| s3_path | character varying | YES |

## Tabela: documents_with_embeddings

| Coluna | Tipo de Dado | Aceita Nulo? |
| --- | --- | --- |
| last_embedding_date | timestamp without time zone | YES |
| embedding_count | bigint | YES |
| document_classification | character varying | YES |
| file_name | character varying | YES |
| case_id | uuid | YES |
| id | uuid | YES |

## Tabela: password_resets

| Coluna | Tipo de Dado | Aceita Nulo? |
| --- | --- | --- |
| created_at | timestamp without time zone | YES |
| id | character varying | NO |
| user_id | character varying | NO |
| token | character varying | NO |
| expires_at | timestamp without time zone | NO |

## Tabela: users

| Coluna | Tipo de Dado | Aceita Nulo? |
| --- | --- | --- |
| created_at | timestamp without time zone | YES |
| updated_at | timestamp without time zone | YES |
| id | character varying | NO |
| email | character varying | NO |
| password_hash | character varying | NO |
| name | character varying | NO |
| role | character varying | NO |
| status | character varying | YES |

## Tabela: webhooks

| Coluna | Tipo de Dado | Aceita Nulo? |
| --- | --- | --- |
| status | character varying | YES |
| updated_at | timestamp without time zone | YES |
| id | integer | NO |
| client_id | character varying | NO |
| event_type | character varying | NO |
| url | character varying | NO |
| created_at | timestamp without time zone | YES |

