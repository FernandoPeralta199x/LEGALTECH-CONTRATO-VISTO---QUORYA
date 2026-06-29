--
-- PostgreSQL database dump
--

\restrict 0s6JqmfqdJo8nFNedqwRqSvc1qxuRgPcy4iwMu0VNDwloFcXk70c0ewCHmg7CRQ

-- Dumped from database version 18.3
-- Dumped by pg_dump version 18.4 (Ubuntu 18.4-1.pgdg24.04+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: audit; Type: SCHEMA; Schema: -; Owner: dbadmin
--

CREATE SCHEMA audit;


ALTER SCHEMA audit OWNER TO dbadmin;

--
-- Name: integration; Type: SCHEMA; Schema: -; Owner: dbadmin
--

CREATE SCHEMA integration;


ALTER SCHEMA integration OWNER TO dbadmin;

--
-- Name: system; Type: SCHEMA; Schema: -; Owner: dbadmin
--

CREATE SCHEMA system;


ALTER SCHEMA system OWNER TO dbadmin;

--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: EXTENSION vector; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION vector IS 'pgvector 0.8.1 - Utilizado para busca vetorial (RAG) com embeddings da OpenAI (1536 dimensões). ATENÇÃO: Utilizar índice HNSW para melhor performance.';


--
-- Name: log_audit(); Type: FUNCTION; Schema: audit; Owner: dbadmin
--

CREATE FUNCTION audit.log_audit() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    INSERT INTO audit.audit_log (
        user_id, action, resource_type, resource_id,
        change_before, change_after, created_at
    ) VALUES (
        current_setting('app.user_id')::UUID,
        TG_ARGV[0],
        TG_TABLE_NAME,
        NEW.id,
        row_to_json(OLD),
        row_to_json(NEW),
        NOW()
    );
    RETURN NEW;
END;
$$;


ALTER FUNCTION audit.log_audit() OWNER TO dbadmin;

--
-- Name: cleanup_expired_cache(); Type: FUNCTION; Schema: integration; Owner: dbadmin
--

CREATE FUNCTION integration.cleanup_expired_cache() RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
    DELETE FROM integration.api_cache
    WHERE expires_at < NOW();
END;
$$;


ALTER FUNCTION integration.cleanup_expired_cache() OWNER TO dbadmin;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: audit_log; Type: TABLE; Schema: audit; Owner: dbadmin
--

CREATE TABLE audit.audit_log (
    id bigint NOT NULL,
    user_id uuid,
    system_username character varying(50),
    action character varying(100) NOT NULL,
    resource_type character varying(50) NOT NULL,
    resource_id uuid,
    change_before jsonb,
    change_after jsonb,
    ip_address inet,
    user_agent character varying(500),
    api_endpoint character varying(500),
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE audit.audit_log OWNER TO dbadmin;

--
-- Name: audit_log_id_seq; Type: SEQUENCE; Schema: audit; Owner: dbadmin
--

CREATE SEQUENCE audit.audit_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE audit.audit_log_id_seq OWNER TO dbadmin;

--
-- Name: audit_log_id_seq; Type: SEQUENCE OWNED BY; Schema: audit; Owner: dbadmin
--

ALTER SEQUENCE audit.audit_log_id_seq OWNED BY audit.audit_log.id;


--
-- Name: compliance_events; Type: TABLE; Schema: audit; Owner: dbadmin
--

CREATE TABLE audit.compliance_events (
    id bigint NOT NULL,
    event_type character varying(100),
    resource_type character varying(50),
    resource_id uuid,
    event_details jsonb,
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE audit.compliance_events OWNER TO dbadmin;

--
-- Name: compliance_events_id_seq; Type: SEQUENCE; Schema: audit; Owner: dbadmin
--

CREATE SEQUENCE audit.compliance_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE audit.compliance_events_id_seq OWNER TO dbadmin;

--
-- Name: compliance_events_id_seq; Type: SEQUENCE OWNED BY; Schema: audit; Owner: dbadmin
--

ALTER SEQUENCE audit.compliance_events_id_seq OWNED BY audit.compliance_events.id;


--
-- Name: data_access_log; Type: TABLE; Schema: audit; Owner: dbadmin
--

CREATE TABLE audit.data_access_log (
    id bigint NOT NULL,
    user_id uuid NOT NULL,
    resource_type character varying(50),
    resource_id uuid,
    access_type character varying(20),
    accessed_at timestamp without time zone DEFAULT now(),
    pii_accessed boolean DEFAULT false
);


ALTER TABLE audit.data_access_log OWNER TO dbadmin;

--
-- Name: data_access_log_id_seq; Type: SEQUENCE; Schema: audit; Owner: dbadmin
--

CREATE SEQUENCE audit.data_access_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE audit.data_access_log_id_seq OWNER TO dbadmin;

--
-- Name: data_access_log_id_seq; Type: SEQUENCE OWNED BY; Schema: audit; Owner: dbadmin
--

ALTER SEQUENCE audit.data_access_log_id_seq OWNED BY audit.data_access_log.id;


--
-- Name: api_cache; Type: TABLE; Schema: integration; Owner: dbadmin
--

CREATE TABLE integration.api_cache (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    source_type character varying(50) NOT NULL,
    cache_key character varying(255) NOT NULL,
    cache_value jsonb,
    ttl_seconds integer,
    created_at timestamp without time zone DEFAULT now(),
    expires_at timestamp without time zone
);


ALTER TABLE integration.api_cache OWNER TO dbadmin;

--
-- Name: external_queries; Type: TABLE; Schema: integration; Owner: dbadmin
--

CREATE TABLE integration.external_queries (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    case_id uuid NOT NULL,
    source_type character varying(50) NOT NULL,
    query_type character varying(50),
    query_params jsonb NOT NULL,
    response jsonb,
    response_status character varying(20),
    http_status_code integer,
    error_message text,
    execution_time_ms integer,
    created_at timestamp without time zone DEFAULT now(),
    completed_at timestamp without time zone
);


ALTER TABLE integration.external_queries OWNER TO dbadmin;

--
-- Name: case_results; Type: TABLE; Schema: public; Owner: dbadmin
--

CREATE TABLE public.case_results (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    case_id uuid NOT NULL,
    result_type character varying(50),
    result_title character varying(255),
    result_data jsonb,
    risk_level character varying(10),
    confidence_score numeric(3,2),
    summary_text text,
    detailed_findings text,
    recommendations text,
    created_at timestamp without time zone DEFAULT now(),
    created_by uuid
);


ALTER TABLE public.case_results OWNER TO dbadmin;

--
-- Name: cases; Type: TABLE; Schema: public; Owner: dbadmin
--

CREATE TABLE public.cases (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    client_id uuid NOT NULL,
    case_type character varying(50) NOT NULL,
    status character varying(20) DEFAULT 'open'::character varying,
    priority character varying(20) DEFAULT 'normal'::character varying,
    created_at timestamp without time zone DEFAULT now(),
    completed_at timestamp without time zone,
    metadata jsonb,
    created_by uuid,
    assigned_to uuid,
    estimated_completion_date timestamp without time zone
);


ALTER TABLE public.cases OWNER TO dbadmin;

--
-- Name: cases_with_latest_result; Type: VIEW; Schema: public; Owner: dbadmin
--

CREATE VIEW public.cases_with_latest_result AS
 SELECT c.id,
    c.client_id,
    c.case_type,
    c.status,
    c.created_at,
    cr.id AS latest_result_id,
    cr.result_type,
    cr.risk_level,
    cr.created_at AS result_created_at,
    row_number() OVER (PARTITION BY c.id ORDER BY cr.created_at DESC) AS result_rank
   FROM (public.cases c
     LEFT JOIN public.case_results cr ON ((c.id = cr.case_id)));


ALTER VIEW public.cases_with_latest_result OWNER TO dbadmin;

--
-- Name: clients; Type: TABLE; Schema: public; Owner: dbadmin
--

CREATE TABLE public.clients (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    legal_name character varying(255) NOT NULL,
    document_type character varying(10),
    document_number character varying(20) NOT NULL,
    email character varying(255),
    phone character varying(20),
    address_street character varying(255),
    address_city character varying(100),
    address_state character varying(2),
    address_zip character varying(10),
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now(),
    is_active boolean DEFAULT true,
    status character varying(50) DEFAULT 'active'::character varying,
    CONSTRAINT clients_status_check CHECK (((status)::text = ANY ((ARRAY['active'::character varying, 'inactive'::character varying])::text[])))
);


ALTER TABLE public.clients OWNER TO dbadmin;

--
-- Name: document_chunks; Type: TABLE; Schema: public; Owner: dbadmin
--

CREATE TABLE public.document_chunks (
    id bigint NOT NULL,
    document_id uuid NOT NULL,
    chunk_index integer,
    chunk_text text NOT NULL,
    start_page integer,
    end_page integer,
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.document_chunks OWNER TO dbadmin;

--
-- Name: document_chunks_id_seq; Type: SEQUENCE; Schema: public; Owner: dbadmin
--

CREATE SEQUENCE public.document_chunks_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.document_chunks_id_seq OWNER TO dbadmin;

--
-- Name: document_chunks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: dbadmin
--

ALTER SEQUENCE public.document_chunks_id_seq OWNED BY public.document_chunks.id;


--
-- Name: document_embeddings; Type: TABLE; Schema: public; Owner: dbadmin
--

CREATE TABLE public.document_embeddings (
    id bigint NOT NULL,
    document_id uuid NOT NULL,
    chunk_id bigint,
    segment_text text NOT NULL,
    embedding public.vector(1536),
    segment_type character varying(50),
    embedding_model character varying(50),
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.document_embeddings OWNER TO dbadmin;

--
-- Name: COLUMN document_embeddings.embedding; Type: COMMENT; Schema: public; Owner: dbadmin
--

COMMENT ON COLUMN public.document_embeddings.embedding IS 'Vetor de 1536 dimensões gerado pelo modelo text-embedding-3-small (OpenAI) para busca semântica via RAG.';


--
-- Name: document_embeddings_id_seq; Type: SEQUENCE; Schema: public; Owner: dbadmin
--

CREATE SEQUENCE public.document_embeddings_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.document_embeddings_id_seq OWNER TO dbadmin;

--
-- Name: document_embeddings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: dbadmin
--

ALTER SEQUENCE public.document_embeddings_id_seq OWNED BY public.document_embeddings.id;


--
-- Name: documents; Type: TABLE; Schema: public; Owner: dbadmin
--

CREATE TABLE public.documents (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    case_id uuid NOT NULL,
    s3_url character varying(500) NOT NULL,
    file_name character varying(255) NOT NULL,
    file_type character varying(10),
    file_size_bytes integer,
    file_hash character varying(64),
    ocr_status character varying(20),
    ocr_result text,
    extraction_status character varying(20),
    document_classification character varying(50),
    document_issue_date timestamp without time zone,
    document_expiry_date timestamp without time zone,
    created_at timestamp without time zone DEFAULT now(),
    uploaded_by uuid,
    s3_path character varying(500)
);


ALTER TABLE public.documents OWNER TO dbadmin;

--
-- Name: documents_with_embeddings; Type: VIEW; Schema: public; Owner: dbadmin
--

CREATE VIEW public.documents_with_embeddings AS
 SELECT d.id,
    d.case_id,
    d.file_name,
    d.document_classification,
    count(de.id) AS embedding_count,
    max(de.created_at) AS last_embedding_date
   FROM (public.documents d
     LEFT JOIN public.document_embeddings de ON ((d.id = de.document_id)))
  GROUP BY d.id, d.case_id, d.file_name, d.document_classification;


ALTER VIEW public.documents_with_embeddings OWNER TO dbadmin;

--
-- Name: password_resets; Type: TABLE; Schema: public; Owner: dbadmin
--

CREATE TABLE public.password_resets (
    id character varying(255) DEFAULT (gen_random_uuid())::text NOT NULL,
    user_id character varying(255) NOT NULL,
    token character varying(255) NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.password_resets OWNER TO dbadmin;

--
-- Name: users; Type: TABLE; Schema: public; Owner: dbadmin
--

CREATE TABLE public.users (
    id character varying(36) NOT NULL,
    email character varying(255) NOT NULL,
    password_hash character varying(255) NOT NULL,
    name character varying(255) NOT NULL,
    role character varying(50) NOT NULL,
    status character varying(50) DEFAULT 'active'::character varying,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now(),
    CONSTRAINT users_role_check CHECK (((role)::text = ANY ((ARRAY['admin'::character varying, 'analyst'::character varying, 'viewer'::character varying])::text[]))),
    CONSTRAINT users_status_check CHECK (((status)::text = ANY ((ARRAY['active'::character varying, 'inactive'::character varying])::text[])))
);


ALTER TABLE public.users OWNER TO dbadmin;

--
-- Name: webhooks; Type: TABLE; Schema: public; Owner: dbadmin
--

CREATE TABLE public.webhooks (
    id integer NOT NULL,
    client_id character varying(36) NOT NULL,
    event_type character varying(100) NOT NULL,
    url character varying(500) NOT NULL,
    status character varying(50) DEFAULT 'active'::character varying,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.webhooks OWNER TO dbadmin;

--
-- Name: webhooks_id_seq; Type: SEQUENCE; Schema: public; Owner: dbadmin
--

CREATE SEQUENCE public.webhooks_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.webhooks_id_seq OWNER TO dbadmin;

--
-- Name: webhooks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: dbadmin
--

ALTER SEQUENCE public.webhooks_id_seq OWNED BY public.webhooks.id;


--
-- Name: agent_executions; Type: TABLE; Schema: system; Owner: dbadmin
--

CREATE TABLE system.agent_executions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    case_id uuid NOT NULL,
    agent_type character varying(50) NOT NULL,
    agent_version character varying(20),
    status character varying(20),
    input_data jsonb,
    output_data jsonb,
    error_message text,
    started_at timestamp without time zone,
    completed_at timestamp without time zone,
    duration_seconds integer,
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE system.agent_executions OWNER TO dbadmin;

--
-- Name: settings; Type: TABLE; Schema: system; Owner: dbadmin
--

CREATE TABLE system.settings (
    key character varying(100) NOT NULL,
    value jsonb NOT NULL,
    description text,
    updated_at timestamp without time zone DEFAULT now()
);


ALTER TABLE system.settings OWNER TO dbadmin;

--
-- Name: task_queue; Type: TABLE; Schema: system; Owner: dbadmin
--

CREATE TABLE system.task_queue (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    case_id uuid,
    task_type character varying(50),
    task_priority integer DEFAULT 0,
    payload jsonb,
    status character varying(20) DEFAULT 'pending'::character varying,
    retry_count integer DEFAULT 0,
    max_retries integer DEFAULT 3,
    scheduled_for timestamp without time zone,
    completed_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE system.task_queue OWNER TO dbadmin;

--
-- Name: users; Type: TABLE; Schema: system; Owner: dbadmin
--

CREATE TABLE system.users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    username character varying(100) NOT NULL,
    email character varying(255) NOT NULL,
    password_hash character varying(255) NOT NULL,
    role character varying(50) NOT NULL,
    mfa_enabled boolean DEFAULT false,
    active boolean DEFAULT true,
    created_at timestamp without time zone DEFAULT now(),
    last_login timestamp without time zone
);


ALTER TABLE system.users OWNER TO dbadmin;

--
-- Name: audit_log id; Type: DEFAULT; Schema: audit; Owner: dbadmin
--

ALTER TABLE ONLY audit.audit_log ALTER COLUMN id SET DEFAULT nextval('audit.audit_log_id_seq'::regclass);


--
-- Name: compliance_events id; Type: DEFAULT; Schema: audit; Owner: dbadmin
--

ALTER TABLE ONLY audit.compliance_events ALTER COLUMN id SET DEFAULT nextval('audit.compliance_events_id_seq'::regclass);


--
-- Name: data_access_log id; Type: DEFAULT; Schema: audit; Owner: dbadmin
--

ALTER TABLE ONLY audit.data_access_log ALTER COLUMN id SET DEFAULT nextval('audit.data_access_log_id_seq'::regclass);


--
-- Name: document_chunks id; Type: DEFAULT; Schema: public; Owner: dbadmin
--

ALTER TABLE ONLY public.document_chunks ALTER COLUMN id SET DEFAULT nextval('public.document_chunks_id_seq'::regclass);


--
-- Name: document_embeddings id; Type: DEFAULT; Schema: public; Owner: dbadmin
--

ALTER TABLE ONLY public.document_embeddings ALTER COLUMN id SET DEFAULT nextval('public.document_embeddings_id_seq'::regclass);


--
-- Name: webhooks id; Type: DEFAULT; Schema: public; Owner: dbadmin
--

ALTER TABLE ONLY public.webhooks ALTER COLUMN id SET DEFAULT nextval('public.webhooks_id_seq'::regclass);


--
-- Name: audit_log audit_log_pkey; Type: CONSTRAINT; Schema: audit; Owner: dbadmin
--

ALTER TABLE ONLY audit.audit_log
    ADD CONSTRAINT audit_log_pkey PRIMARY KEY (id);


--
-- Name: compliance_events compliance_events_pkey; Type: CONSTRAINT; Schema: audit; Owner: dbadmin
--

ALTER TABLE ONLY audit.compliance_events
    ADD CONSTRAINT compliance_events_pkey PRIMARY KEY (id);


--
-- Name: data_access_log data_access_log_pkey; Type: CONSTRAINT; Schema: audit; Owner: dbadmin
--

ALTER TABLE ONLY audit.data_access_log
    ADD CONSTRAINT data_access_log_pkey PRIMARY KEY (id);


--
-- Name: api_cache api_cache_pkey; Type: CONSTRAINT; Schema: integration; Owner: dbadmin
--

ALTER TABLE ONLY integration.api_cache
    ADD CONSTRAINT api_cache_pkey PRIMARY KEY (id);


--
-- Name: api_cache api_cache_source_type_cache_key_key; Type: CONSTRAINT; Schema: integration; Owner: dbadmin
--

ALTER TABLE ONLY integration.api_cache
    ADD CONSTRAINT api_cache_source_type_cache_key_key UNIQUE (source_type, cache_key);


--
-- Name: external_queries external_queries_pkey; Type: CONSTRAINT; Schema: integration; Owner: dbadmin
--

ALTER TABLE ONLY integration.external_queries
    ADD CONSTRAINT external_queries_pkey PRIMARY KEY (id);


--
-- Name: case_results case_results_pkey; Type: CONSTRAINT; Schema: public; Owner: dbadmin
--

ALTER TABLE ONLY public.case_results
    ADD CONSTRAINT case_results_pkey PRIMARY KEY (id);


--
-- Name: cases cases_pkey; Type: CONSTRAINT; Schema: public; Owner: dbadmin
--

ALTER TABLE ONLY public.cases
    ADD CONSTRAINT cases_pkey PRIMARY KEY (id);


--
-- Name: clients clients_document_number_key; Type: CONSTRAINT; Schema: public; Owner: dbadmin
--

ALTER TABLE ONLY public.clients
    ADD CONSTRAINT clients_document_number_key UNIQUE (document_number);


--
-- Name: clients clients_pkey; Type: CONSTRAINT; Schema: public; Owner: dbadmin
--

ALTER TABLE ONLY public.clients
    ADD CONSTRAINT clients_pkey PRIMARY KEY (id);


--
-- Name: document_chunks document_chunks_pkey; Type: CONSTRAINT; Schema: public; Owner: dbadmin
--

ALTER TABLE ONLY public.document_chunks
    ADD CONSTRAINT document_chunks_pkey PRIMARY KEY (id);


--
-- Name: document_embeddings document_embeddings_pkey; Type: CONSTRAINT; Schema: public; Owner: dbadmin
--

ALTER TABLE ONLY public.document_embeddings
    ADD CONSTRAINT document_embeddings_pkey PRIMARY KEY (id);


--
-- Name: documents documents_pkey; Type: CONSTRAINT; Schema: public; Owner: dbadmin
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_pkey PRIMARY KEY (id);


--
-- Name: password_resets password_resets_pkey; Type: CONSTRAINT; Schema: public; Owner: dbadmin
--

ALTER TABLE ONLY public.password_resets
    ADD CONSTRAINT password_resets_pkey PRIMARY KEY (id);


--
-- Name: password_resets password_resets_token_key; Type: CONSTRAINT; Schema: public; Owner: dbadmin
--

ALTER TABLE ONLY public.password_resets
    ADD CONSTRAINT password_resets_token_key UNIQUE (token);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: dbadmin
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: dbadmin
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: webhooks webhooks_pkey; Type: CONSTRAINT; Schema: public; Owner: dbadmin
--

ALTER TABLE ONLY public.webhooks
    ADD CONSTRAINT webhooks_pkey PRIMARY KEY (id);


--
-- Name: agent_executions agent_executions_pkey; Type: CONSTRAINT; Schema: system; Owner: dbadmin
--

ALTER TABLE ONLY system.agent_executions
    ADD CONSTRAINT agent_executions_pkey PRIMARY KEY (id);


--
-- Name: settings settings_pkey; Type: CONSTRAINT; Schema: system; Owner: dbadmin
--

ALTER TABLE ONLY system.settings
    ADD CONSTRAINT settings_pkey PRIMARY KEY (key);


--
-- Name: task_queue task_queue_pkey; Type: CONSTRAINT; Schema: system; Owner: dbadmin
--

ALTER TABLE ONLY system.task_queue
    ADD CONSTRAINT task_queue_pkey PRIMARY KEY (id);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: system; Owner: dbadmin
--

ALTER TABLE ONLY system.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: system; Owner: dbadmin
--

ALTER TABLE ONLY system.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: users users_username_key; Type: CONSTRAINT; Schema: system; Owner: dbadmin
--

ALTER TABLE ONLY system.users
    ADD CONSTRAINT users_username_key UNIQUE (username);


--
-- Name: idx_access_resource; Type: INDEX; Schema: audit; Owner: dbadmin
--

CREATE INDEX idx_access_resource ON audit.data_access_log USING btree (resource_type, resource_id);


--
-- Name: idx_access_user; Type: INDEX; Schema: audit; Owner: dbadmin
--

CREATE INDEX idx_access_user ON audit.data_access_log USING btree (user_id, accessed_at DESC);


--
-- Name: idx_audit_action; Type: INDEX; Schema: audit; Owner: dbadmin
--

CREATE INDEX idx_audit_action ON audit.audit_log USING btree (action);


--
-- Name: idx_audit_log_created_at; Type: INDEX; Schema: audit; Owner: dbadmin
--

CREATE INDEX idx_audit_log_created_at ON audit.audit_log USING btree (created_at);


--
-- Name: idx_audit_log_user_id; Type: INDEX; Schema: audit; Owner: dbadmin
--

CREATE INDEX idx_audit_log_user_id ON audit.audit_log USING btree (user_id);


--
-- Name: idx_audit_resource; Type: INDEX; Schema: audit; Owner: dbadmin
--

CREATE INDEX idx_audit_resource ON audit.audit_log USING btree (resource_type, resource_id);


--
-- Name: idx_audit_time; Type: INDEX; Schema: audit; Owner: dbadmin
--

CREATE INDEX idx_audit_time ON audit.audit_log USING btree (created_at DESC);


--
-- Name: idx_audit_user; Type: INDEX; Schema: audit; Owner: dbadmin
--

CREATE INDEX idx_audit_user ON audit.audit_log USING btree (user_id, created_at DESC);


--
-- Name: idx_compliance_events; Type: INDEX; Schema: audit; Owner: dbadmin
--

CREATE INDEX idx_compliance_events ON audit.compliance_events USING btree (created_at DESC);


--
-- Name: idx_data_access_log_user_id; Type: INDEX; Schema: audit; Owner: dbadmin
--

CREATE INDEX idx_data_access_log_user_id ON audit.data_access_log USING btree (user_id);


--
-- Name: idx_cache_expires; Type: INDEX; Schema: integration; Owner: dbadmin
--

CREATE INDEX idx_cache_expires ON integration.api_cache USING btree (expires_at);


--
-- Name: idx_external_queries_case; Type: INDEX; Schema: integration; Owner: dbadmin
--

CREATE INDEX idx_external_queries_case ON integration.external_queries USING btree (case_id);


--
-- Name: idx_external_queries_created; Type: INDEX; Schema: integration; Owner: dbadmin
--

CREATE INDEX idx_external_queries_created ON integration.external_queries USING btree (created_at DESC);


--
-- Name: idx_external_queries_source; Type: INDEX; Schema: integration; Owner: dbadmin
--

CREATE INDEX idx_external_queries_source ON integration.external_queries USING btree (source_type);


--
-- Name: idx_case_results_case; Type: INDEX; Schema: public; Owner: dbadmin
--

CREATE INDEX idx_case_results_case ON public.case_results USING btree (case_id);


--
-- Name: idx_case_results_case_id; Type: INDEX; Schema: public; Owner: dbadmin
--

CREATE INDEX idx_case_results_case_id ON public.case_results USING btree (case_id);


--
-- Name: idx_case_results_risk; Type: INDEX; Schema: public; Owner: dbadmin
--

CREATE INDEX idx_case_results_risk ON public.case_results USING btree (risk_level);


--
-- Name: idx_case_results_type; Type: INDEX; Schema: public; Owner: dbadmin
--

CREATE INDEX idx_case_results_type ON public.case_results USING btree (result_type);


--
-- Name: idx_cases_client; Type: INDEX; Schema: public; Owner: dbadmin
--

CREATE INDEX idx_cases_client ON public.cases USING btree (client_id);


--
-- Name: idx_cases_created; Type: INDEX; Schema: public; Owner: dbadmin
--

CREATE INDEX idx_cases_created ON public.cases USING btree (created_at DESC);


--
-- Name: idx_cases_status; Type: INDEX; Schema: public; Owner: dbadmin
--

CREATE INDEX idx_cases_status ON public.cases USING btree (status);


--
-- Name: idx_cases_type; Type: INDEX; Schema: public; Owner: dbadmin
--

CREATE INDEX idx_cases_type ON public.cases USING btree (case_type);


--
-- Name: idx_chunks_document; Type: INDEX; Schema: public; Owner: dbadmin
--

CREATE INDEX idx_chunks_document ON public.document_chunks USING btree (document_id);


--
-- Name: idx_clients_document; Type: INDEX; Schema: public; Owner: dbadmin
--

CREATE INDEX idx_clients_document ON public.clients USING btree (document_number);


--
-- Name: idx_clients_email; Type: INDEX; Schema: public; Owner: dbadmin
--

CREATE INDEX idx_clients_email ON public.clients USING btree (email);


--
-- Name: idx_documents_case; Type: INDEX; Schema: public; Owner: dbadmin
--

CREATE INDEX idx_documents_case ON public.documents USING btree (case_id);


--
-- Name: idx_documents_s3; Type: INDEX; Schema: public; Owner: dbadmin
--

CREATE INDEX idx_documents_s3 ON public.documents USING btree (s3_url);


--
-- Name: idx_documents_type; Type: INDEX; Schema: public; Owner: dbadmin
--

CREATE INDEX idx_documents_type ON public.documents USING btree (file_type);


--
-- Name: idx_embeddings; Type: INDEX; Schema: public; Owner: dbadmin
--

CREATE INDEX idx_embeddings ON public.document_embeddings USING ivfflat (embedding public.vector_cosine_ops) WITH (lists='100');


--
-- Name: idx_embeddings_cosine; Type: INDEX; Schema: public; Owner: dbadmin
--

CREATE INDEX idx_embeddings_cosine ON public.document_embeddings USING hnsw (embedding public.vector_cosine_ops);


--
-- Name: idx_embeddings_document; Type: INDEX; Schema: public; Owner: dbadmin
--

CREATE INDEX idx_embeddings_document ON public.document_embeddings USING btree (document_id);


--
-- Name: idx_embeddings_type; Type: INDEX; Schema: public; Owner: dbadmin
--

CREATE INDEX idx_embeddings_type ON public.document_embeddings USING btree (segment_type);


--
-- Name: idx_embeddings_vector; Type: INDEX; Schema: public; Owner: dbadmin
--

CREATE INDEX idx_embeddings_vector ON public.document_embeddings USING ivfflat (embedding public.vector_cosine_ops) WITH (lists='100');


--
-- Name: idx_password_resets_expires; Type: INDEX; Schema: public; Owner: dbadmin
--

CREATE INDEX idx_password_resets_expires ON public.password_resets USING btree (expires_at);


--
-- Name: idx_password_resets_token; Type: INDEX; Schema: public; Owner: dbadmin
--

CREATE INDEX idx_password_resets_token ON public.password_resets USING btree (token);


--
-- Name: idx_users_email; Type: INDEX; Schema: public; Owner: dbadmin
--

CREATE INDEX idx_users_email ON public.users USING btree (email);


--
-- Name: idx_users_status; Type: INDEX; Schema: public; Owner: dbadmin
--

CREATE INDEX idx_users_status ON public.users USING btree (status);


--
-- Name: idx_webhooks_client_id; Type: INDEX; Schema: public; Owner: dbadmin
--

CREATE INDEX idx_webhooks_client_id ON public.webhooks USING btree (client_id);


--
-- Name: idx_agent_case; Type: INDEX; Schema: system; Owner: dbadmin
--

CREATE INDEX idx_agent_case ON system.agent_executions USING btree (case_id);


--
-- Name: idx_agent_status; Type: INDEX; Schema: system; Owner: dbadmin
--

CREATE INDEX idx_agent_status ON system.agent_executions USING btree (status);


--
-- Name: idx_agent_type; Type: INDEX; Schema: system; Owner: dbadmin
--

CREATE INDEX idx_agent_type ON system.agent_executions USING btree (agent_type);


--
-- Name: idx_tasks_case; Type: INDEX; Schema: system; Owner: dbadmin
--

CREATE INDEX idx_tasks_case ON system.task_queue USING btree (case_id);


--
-- Name: idx_tasks_status; Type: INDEX; Schema: system; Owner: dbadmin
--

CREATE INDEX idx_tasks_status ON system.task_queue USING btree (status, scheduled_for);


--
-- Name: idx_users_email; Type: INDEX; Schema: system; Owner: dbadmin
--

CREATE INDEX idx_users_email ON system.users USING btree (email);


--
-- Name: idx_users_username; Type: INDEX; Schema: system; Owner: dbadmin
--

CREATE INDEX idx_users_username ON system.users USING btree (username);


--
-- Name: cases audit_cases_delete; Type: TRIGGER; Schema: public; Owner: dbadmin
--

CREATE TRIGGER audit_cases_delete AFTER DELETE ON public.cases FOR EACH ROW EXECUTE FUNCTION audit.log_audit('DELETE');


--
-- Name: cases audit_cases_update; Type: TRIGGER; Schema: public; Owner: dbadmin
--

CREATE TRIGGER audit_cases_update AFTER UPDATE ON public.cases FOR EACH ROW EXECUTE FUNCTION audit.log_audit('UPDATE');


--
-- Name: documents audit_documents_delete; Type: TRIGGER; Schema: public; Owner: dbadmin
--

CREATE TRIGGER audit_documents_delete AFTER DELETE ON public.documents FOR EACH ROW EXECUTE FUNCTION audit.log_audit('DELETE');


--
-- Name: external_queries external_queries_case_id_fkey; Type: FK CONSTRAINT; Schema: integration; Owner: dbadmin
--

ALTER TABLE ONLY integration.external_queries
    ADD CONSTRAINT external_queries_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id) ON DELETE CASCADE;


--
-- Name: case_results case_results_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: dbadmin
--

ALTER TABLE ONLY public.case_results
    ADD CONSTRAINT case_results_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id) ON DELETE CASCADE;


--
-- Name: cases cases_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: dbadmin
--

ALTER TABLE ONLY public.cases
    ADD CONSTRAINT cases_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id) ON DELETE CASCADE;


--
-- Name: document_chunks document_chunks_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: dbadmin
--

ALTER TABLE ONLY public.document_chunks
    ADD CONSTRAINT document_chunks_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE CASCADE;


--
-- Name: document_embeddings document_embeddings_chunk_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: dbadmin
--

ALTER TABLE ONLY public.document_embeddings
    ADD CONSTRAINT document_embeddings_chunk_id_fkey FOREIGN KEY (chunk_id) REFERENCES public.document_chunks(id) ON DELETE CASCADE;


--
-- Name: document_embeddings document_embeddings_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: dbadmin
--

ALTER TABLE ONLY public.document_embeddings
    ADD CONSTRAINT document_embeddings_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE CASCADE;


--
-- Name: documents documents_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: dbadmin
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id) ON DELETE CASCADE;


--
-- Name: password_resets password_resets_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: dbadmin
--

ALTER TABLE ONLY public.password_resets
    ADD CONSTRAINT password_resets_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: agent_executions agent_executions_case_id_fkey; Type: FK CONSTRAINT; Schema: system; Owner: dbadmin
--

ALTER TABLE ONLY system.agent_executions
    ADD CONSTRAINT agent_executions_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id) ON DELETE CASCADE;


--
-- Name: task_queue task_queue_case_id_fkey; Type: FK CONSTRAINT; Schema: system; Owner: dbadmin
--

ALTER TABLE ONLY system.task_queue
    ADD CONSTRAINT task_queue_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id) ON DELETE CASCADE;


--
-- Name: audit_log; Type: ROW SECURITY; Schema: audit; Owner: dbadmin
--

ALTER TABLE audit.audit_log ENABLE ROW LEVEL SECURITY;

--
-- Name: cases case_owner_access; Type: POLICY; Schema: public; Owner: dbadmin
--

CREATE POLICY case_owner_access ON public.cases FOR SELECT USING (((created_by = (current_setting('app.user_id'::text))::uuid) OR (current_setting('app.user_role'::text) = 'admin'::text)));


--
-- Name: case_results; Type: ROW SECURITY; Schema: public; Owner: dbadmin
--

ALTER TABLE public.case_results ENABLE ROW LEVEL SECURITY;

--
-- Name: cases; Type: ROW SECURITY; Schema: public; Owner: dbadmin
--

ALTER TABLE public.cases ENABLE ROW LEVEL SECURITY;

--
-- Name: documents; Type: ROW SECURITY; Schema: public; Owner: dbadmin
--

ALTER TABLE public.documents ENABLE ROW LEVEL SECURITY;

--
-- PostgreSQL database dump complete
--

\unrestrict 0s6JqmfqdJo8nFNedqwRqSvc1qxuRgPcy4iwMu0VNDwloFcXk70c0ewCHmg7CRQ

