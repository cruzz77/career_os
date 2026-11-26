"""Core schema: every table in blueprint section 5 except the eval_* tables (section 5.1).

Frozen DDL, generated from the models and reviewed line by line. Amended once before release
(blueprint v0.3.4: job lifecycle columns, section 5.1 amendment). From release R1 onwards it is
immutable: every schema change is a new migration.

Revision ID: 0001_core
Revises:
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001_core"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# pg_search is created by 0002_bm25_index, which owns the BM25 index.
_EXTENSIONS: tuple[str, ...] = (
    "CREATE EXTENSION IF NOT EXISTS citext",
    "CREATE EXTENSION IF NOT EXISTS vector",
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE EXTENSION IF NOT EXISTS unaccent",
)

# A trigger rather than ORM onupdate: bulk and raw-SQL updates (the ingestion CLI) bump it too.
_SET_UPDATED_AT_FUNCTION = """
CREATE FUNCTION set_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END
$$
"""

_TABLES: tuple[str, ...] = (
    """
CREATE TABLE companies (
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_companies PRIMARY KEY (id),
    CONSTRAINT uq_companies_normalized_name UNIQUE (normalized_name),
    CONSTRAINT ck_companies_name_not_blank CHECK (length(btrim(name)) > 0),
    CONSTRAINT ck_companies_normalized_name_not_blank CHECK (length(normalized_name) > 0)
)
""",
    """
CREATE TABLE ingestion_runs (
    source TEXT NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    finished_at TIMESTAMP WITH TIME ZONE,
    fetched INTEGER DEFAULT 0 NOT NULL,
    inserted INTEGER DEFAULT 0 NOT NULL,
    updated INTEGER DEFAULT 0 NOT NULL,
    unchanged INTEGER DEFAULT 0 NOT NULL,
    failed INTEGER DEFAULT 0 NOT NULL,
    expired INTEGER DEFAULT 0 NOT NULL,
    purged INTEGER DEFAULT 0 NOT NULL,
    notes TEXT,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_ingestion_runs PRIMARY KEY (id),
    CONSTRAINT ck_ingestion_runs_counts_non_negative CHECK (fetched >= 0 AND inserted >= 0 AND updated >= 0 AND unchanged >= 0 AND failed >= 0 AND expired >= 0 AND purged >= 0),
    CONSTRAINT ck_ingestion_runs_finished_after_started CHECK (finished_at IS NULL OR finished_at >= started_at)
)
""",
    """
CREATE TABLE skills (
    canonical_name TEXT NOT NULL,
    category TEXT,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_skills PRIMARY KEY (id),
    CONSTRAINT ck_skills_canonical_name_not_blank CHECK (length(btrim(canonical_name)) > 0)
)
""",
    """
CREATE UNIQUE INDEX uq_skills_lower_canonical_name ON skills (lower(canonical_name))
""",
    """
CREATE TABLE users (
    email CITEXT NOT NULL,
    password_hash TEXT NOT NULL,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_users PRIMARY KEY (id),
    CONSTRAINT uq_users_email UNIQUE (email),
    CONSTRAINT ck_users_email_length CHECK (length(email) BETWEEN 3 AND 254)
)
""",
    """
CREATE TABLE conversations (
    user_id UUID NOT NULL,
    title TEXT,
    summary TEXT,
    pending_patch JSONB,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_conversations PRIMARY KEY (id),
    CONSTRAINT ck_conversations_pending_patch_is_object CHECK (pending_patch IS NULL OR jsonb_typeof(pending_patch) = 'object'),
    CONSTRAINT fk_conversations_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
)
""",
    """
CREATE INDEX ix_conversations_user_id ON conversations (user_id)
""",
    """
CREATE TABLE jobs (
    source TEXT NOT NULL,
    source_board TEXT NOT NULL,
    external_id TEXT NOT NULL,
    company_id UUID,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    skills_text TEXT DEFAULT '' NOT NULL,
    location_city TEXT,
    location_country TEXT,
    work_mode TEXT DEFAULT 'unknown' NOT NULL,
    employment_type TEXT DEFAULT 'unknown' NOT NULL,
    seniority TEXT DEFAULT 'unknown' NOT NULL,
    posted_at TIMESTAMP WITH TIME ZONE,
    expires_at TIMESTAMP WITH TIME ZONE,
    status TEXT DEFAULT 'active' NOT NULL,
    expired_at TIMESTAMP WITH TIME ZONE,
    expired_reason TEXT,
    last_verified_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    content_hash BYTEA NOT NULL,
    canonical_hash BYTEA NOT NULL,
    first_seen_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    last_seen_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    is_synthetic BOOLEAN DEFAULT false NOT NULL,
    source_url TEXT,
    search_tsv TSVECTOR GENERATED ALWAYS AS (setweight(to_tsvector('english'::regconfig, coalesce(title, '')), 'A') || setweight(to_tsvector('english'::regconfig, coalesce(skills_text, '')), 'B') || setweight(to_tsvector('english'::regconfig, coalesce(description, '')), 'C')) STORED NOT NULL,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_jobs PRIMARY KEY (id),
    CONSTRAINT uq_jobs_source_external_id UNIQUE (source, external_id),
    CONSTRAINT ck_jobs_work_mode CHECK (work_mode IN ('remote', 'hybrid', 'onsite', 'unknown')),
    CONSTRAINT ck_jobs_employment_type CHECK (employment_type IN ('internship', 'full_time', 'part_time', 'contract', 'unknown')),
    CONSTRAINT ck_jobs_seniority CHECK (seniority IN ('intern', 'junior', 'mid', 'senior', 'unknown')),
    CONSTRAINT ck_jobs_status CHECK (status IN ('active', 'expired')),
    CONSTRAINT ck_jobs_content_hash_length CHECK (octet_length(content_hash) = 32),
    CONSTRAINT ck_jobs_canonical_hash_length CHECK (octet_length(canonical_hash) = 32),
    CONSTRAINT ck_jobs_expires_after_posted CHECK (expires_at IS NULL OR posted_at IS NULL OR expires_at >= posted_at),
    CONSTRAINT ck_jobs_seen_in_order CHECK (last_seen_at >= first_seen_at),
    CONSTRAINT ck_jobs_expired_reason CHECK (expired_reason IN ('missing_from_source', 'source_not_found', 'past_expires_at', 'stale_not_seen', 'takedown')),
    CONSTRAINT ck_jobs_expired_fields_iff_expired CHECK ((status = 'expired') = (expired_at IS NOT NULL AND expired_reason IS NOT NULL)),
    CONSTRAINT ck_jobs_expired_at_with_reason CHECK ((expired_at IS NULL) = (expired_reason IS NULL)),
    CONSTRAINT ck_jobs_source_board_not_blank CHECK (length(btrim(source_board)) > 0),
    CONSTRAINT fk_jobs_company_id_companies FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE RESTRICT
)
""",
    """
CREATE INDEX ix_jobs_active_employment_type_seniority ON jobs (employment_type, seniority) WHERE status = 'active'
""",
    """
CREATE INDEX ix_jobs_active_last_seen_at ON jobs (last_seen_at) WHERE status = 'active'
""",
    """
CREATE INDEX ix_jobs_active_source_source_board ON jobs (source, source_board) WHERE status = 'active'
""",
    """
CREATE INDEX ix_jobs_active_work_mode_location_city ON jobs (work_mode, location_city) WHERE status = 'active'
""",
    """
CREATE INDEX ix_jobs_canonical_hash ON jobs (canonical_hash)
""",
    """
CREATE INDEX ix_jobs_company_id ON jobs (company_id)
""",
    """
CREATE INDEX ix_jobs_expired_expired_at ON jobs (expired_at) WHERE status = 'expired'
""",
    """
CREATE INDEX ix_jobs_search_tsv ON jobs USING gin (search_tsv)
""",
    """
CREATE TABLE sessions (
    user_id UUID NOT NULL,
    token_hash BYTEA NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    last_seen_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    user_agent TEXT,
    ip INET,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_sessions PRIMARY KEY (id),
    CONSTRAINT uq_sessions_token_hash UNIQUE (token_hash),
    CONSTRAINT ck_sessions_token_hash_length CHECK (octet_length(token_hash) = 32),
    CONSTRAINT ck_sessions_expires_after_created CHECK (expires_at > created_at),
    CONSTRAINT fk_sessions_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
)
""",
    """
CREATE INDEX ix_sessions_expires_at ON sessions (expires_at)
""",
    """
CREATE INDEX ix_sessions_user_id ON sessions (user_id)
""",
    """
CREATE TABLE skill_aliases (
    alias TEXT NOT NULL,
    skill_id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_skill_aliases PRIMARY KEY (alias),
    CONSTRAINT ck_skill_aliases_alias_lowercase CHECK (alias = lower(alias)),
    CONSTRAINT ck_skill_aliases_alias_not_blank CHECK (length(btrim(alias)) > 0),
    CONSTRAINT fk_skill_aliases_skill_id_skills FOREIGN KEY(skill_id) REFERENCES skills (id) ON DELETE CASCADE
)
""",
    """
CREATE INDEX ix_skill_aliases_skill_id ON skill_aliases (skill_id)
""",
    """
CREATE TABLE user_profiles (
    user_id UUID NOT NULL,
    headline TEXT,
    experience_level TEXT,
    years_experience NUMERIC(4, 1),
    education JSONB DEFAULT '[]'::jsonb NOT NULL,
    experience JSONB DEFAULT '[]'::jsonb NOT NULL,
    projects JSONB DEFAULT '[]'::jsonb NOT NULL,
    interests TEXT[] DEFAULT '{}'::text[] NOT NULL,
    resume_text TEXT,
    resume_sha256 BYTEA,
    reviewed_at TIMESTAMP WITH TIME ZONE,
    profile_version INTEGER DEFAULT 1 NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_user_profiles PRIMARY KEY (user_id),
    CONSTRAINT ck_user_profiles_experience_level CHECK (experience_level IN ('student', 'intern', 'junior', 'mid', 'senior')),
    CONSTRAINT ck_user_profiles_years_experience_range CHECK (years_experience IS NULL OR years_experience BETWEEN 0 AND 80),
    CONSTRAINT ck_user_profiles_resume_sha256_length CHECK (resume_sha256 IS NULL OR octet_length(resume_sha256) = 32),
    CONSTRAINT ck_user_profiles_profile_version_positive CHECK (profile_version >= 1),
    CONSTRAINT ck_user_profiles_education_is_array CHECK (jsonb_typeof(education) = 'array'),
    CONSTRAINT ck_user_profiles_experience_is_array CHECK (jsonb_typeof(experience) = 'array'),
    CONSTRAINT ck_user_profiles_projects_is_array CHECK (jsonb_typeof(projects) = 'array'),
    CONSTRAINT fk_user_profiles_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
)
""",
    """
CREATE TABLE user_skills (
    user_id UUID NOT NULL,
    skill_id UUID NOT NULL,
    source TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_user_skills PRIMARY KEY (user_id, skill_id),
    CONSTRAINT ck_user_skills_source CHECK (source IN ('resume', 'chat', 'manual')),
    CONSTRAINT fk_user_skills_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT fk_user_skills_skill_id_skills FOREIGN KEY(skill_id) REFERENCES skills (id) ON DELETE CASCADE
)
""",
    """
CREATE INDEX ix_user_skills_skill_id ON user_skills (skill_id)
""",
    """
CREATE TABLE job_embeddings (
    job_id UUID NOT NULL,
    model_version TEXT NOT NULL,
    embedding VECTOR(384) NOT NULL,
    content_hash BYTEA NOT NULL,
    embedded_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_job_embeddings PRIMARY KEY (job_id, model_version),
    CONSTRAINT ck_job_embeddings_content_hash_length CHECK (octet_length(content_hash) = 32),
    CONSTRAINT ck_job_embeddings_model_version_not_blank CHECK (length(btrim(model_version)) > 0),
    CONSTRAINT fk_job_embeddings_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE
)
""",
    """
CREATE INDEX ix_job_embeddings_embedding_hnsw ON job_embeddings USING hnsw (embedding vector_cosine_ops)
""",
    """
CREATE TABLE job_skills (
    job_id UUID NOT NULL,
    skill_id UUID NOT NULL,
    requirement TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_job_skills PRIMARY KEY (job_id, skill_id),
    CONSTRAINT ck_job_skills_requirement CHECK (requirement IN ('required', 'preferred')),
    CONSTRAINT fk_job_skills_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE,
    CONSTRAINT fk_job_skills_skill_id_skills FOREIGN KEY(skill_id) REFERENCES skills (id) ON DELETE CASCADE
)
""",
    """
CREATE INDEX ix_job_skills_skill_id ON job_skills (skill_id)
""",
    """
CREATE TABLE messages (
    conversation_id UUID NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_call_id TEXT,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_messages PRIMARY KEY (id),
    CONSTRAINT ck_messages_role CHECK (role IN ('user', 'assistant', 'tool')),
    CONSTRAINT ck_messages_tool_call_id_iff_tool_role CHECK ((role = 'tool') = (tool_call_id IS NOT NULL)),
    CONSTRAINT fk_messages_conversation_id_conversations FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE
)
""",
    """
CREATE INDEX ix_messages_conversation_id_created_at ON messages (conversation_id, created_at)
""",
    """
CREATE TABLE recommendation_logs (
    user_id UUID NOT NULL,
    query TEXT NOT NULL,
    config_version TEXT NOT NULL,
    job_id UUID NOT NULL,
    rank INTEGER NOT NULL,
    score_breakdown JSONB NOT NULL,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_recommendation_logs PRIMARY KEY (id),
    CONSTRAINT ck_recommendation_logs_rank_positive CHECK (rank >= 1),
    CONSTRAINT ck_recommendation_logs_score_breakdown_is_object CHECK (jsonb_typeof(score_breakdown) = 'object'),
    CONSTRAINT fk_recommendation_logs_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT fk_recommendation_logs_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE
)
""",
    """
CREATE INDEX ix_recommendation_logs_job_id ON recommendation_logs (job_id)
""",
    """
CREATE INDEX ix_recommendation_logs_user_id ON recommendation_logs (user_id)
""",
    """
CREATE TABLE saved_jobs (
    user_id UUID NOT NULL,
    job_id UUID NOT NULL,
    note TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_saved_jobs PRIMARY KEY (user_id, job_id),
    CONSTRAINT fk_saved_jobs_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT fk_saved_jobs_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE
)
""",
    """
CREATE INDEX ix_saved_jobs_job_id ON saved_jobs (job_id)
""",
    """
CREATE TABLE agent_runs (
    message_id UUID NOT NULL,
    model TEXT NOT NULL,
    steps INTEGER NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    latency_ms INTEGER,
    status TEXT NOT NULL,
    error TEXT,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_agent_runs PRIMARY KEY (id),
    CONSTRAINT ck_agent_runs_status CHECK (status IN ('succeeded', 'failed', 'step_limit_exceeded')),
    CONSTRAINT ck_agent_runs_steps_non_negative CHECK (steps >= 0),
    CONSTRAINT ck_agent_runs_tokens_non_negative CHECK ((input_tokens IS NULL OR input_tokens >= 0) AND (output_tokens IS NULL OR output_tokens >= 0)),
    CONSTRAINT ck_agent_runs_latency_non_negative CHECK (latency_ms IS NULL OR latency_ms >= 0),
    CONSTRAINT ck_agent_runs_succeeded_has_no_error CHECK (status <> 'succeeded' OR error IS NULL),
    CONSTRAINT fk_agent_runs_message_id_messages FOREIGN KEY(message_id) REFERENCES messages (id) ON DELETE CASCADE
)
""",
    """
CREATE INDEX ix_agent_runs_message_id ON agent_runs (message_id)
""",
    """
CREATE TABLE user_preferences (
    user_id UUID NOT NULL,
    dimension TEXT NOT NULL,
    operator TEXT NOT NULL,
    value JSONB NOT NULL,
    strength TEXT NOT NULL,
    source TEXT NOT NULL,
    source_message_id UUID,
    valid_from TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    superseded_at TIMESTAMP WITH TIME ZONE,
    superseded_by UUID,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_user_preferences PRIMARY KEY (id),
    CONSTRAINT ck_user_preferences_dimension CHECK (dimension IN ('role', 'location', 'work_mode', 'employment_type', 'seniority', 'company_exclude', 'skill_interest', 'domain_interest')),
    CONSTRAINT ck_user_preferences_operator CHECK (operator IN ('include', 'exclude')),
    CONSTRAINT ck_user_preferences_strength CHECK (strength IN ('hard', 'soft')),
    CONSTRAINT ck_user_preferences_source CHECK (source IN ('resume', 'chat', 'ui')),
    CONSTRAINT ck_user_preferences_superseded_by_requires_superseded_at CHECK (superseded_by IS NULL OR superseded_at IS NOT NULL),
    CONSTRAINT ck_user_preferences_superseded_after_valid_from CHECK (superseded_at IS NULL OR superseded_at >= valid_from),
    CONSTRAINT fk_user_preferences_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT fk_user_preferences_source_message_id_messages FOREIGN KEY(source_message_id) REFERENCES messages (id) ON DELETE SET NULL,
    CONSTRAINT fk_user_preferences_superseded_by_user_preferences FOREIGN KEY(superseded_by) REFERENCES user_preferences (id) ON DELETE SET NULL
)
""",
    """
CREATE INDEX ix_user_preferences_active_user_id ON user_preferences (user_id) WHERE superseded_at IS NULL
""",
    """
CREATE INDEX ix_user_preferences_source_message_id ON user_preferences (source_message_id)
""",
    """
CREATE INDEX ix_user_preferences_superseded_by ON user_preferences (superseded_by)
""",
    """
CREATE INDEX ix_user_preferences_user_id ON user_preferences (user_id)
""",
    """
CREATE UNIQUE INDEX uq_user_preferences_active_single_valued ON user_preferences (user_id, dimension) WHERE superseded_at IS NULL AND dimension IN ('seniority')
""",
    """
CREATE TABLE tool_calls (
    agent_run_id UUID NOT NULL,
    tool_name TEXT NOT NULL,
    args JSONB NOT NULL,
    args_valid BOOLEAN NOT NULL,
    result JSONB,
    status TEXT NOT NULL,
    latency_ms INTEGER,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_tool_calls PRIMARY KEY (id),
    CONSTRAINT ck_tool_calls_status CHECK (status IN ('ok', 'error', 'rejected')),
    CONSTRAINT ck_tool_calls_invalid_args_rejected CHECK (args_valid OR status = 'rejected'),
    CONSTRAINT ck_tool_calls_latency_non_negative CHECK (latency_ms IS NULL OR latency_ms >= 0),
    CONSTRAINT fk_tool_calls_agent_run_id_agent_runs FOREIGN KEY(agent_run_id) REFERENCES agent_runs (id) ON DELETE CASCADE
)
""",
    """
CREATE INDEX ix_tool_calls_agent_run_id ON tool_calls (agent_run_id)
""",
)

_UPDATED_AT_TRIGGERS: tuple[str, ...] = (
    """
CREATE TRIGGER trg_companies_set_updated_at BEFORE UPDATE ON companies FOR EACH ROW EXECUTE FUNCTION set_updated_at()
""",
    """
CREATE TRIGGER trg_ingestion_runs_set_updated_at BEFORE UPDATE ON ingestion_runs FOR EACH ROW EXECUTE FUNCTION set_updated_at()
""",
    """
CREATE TRIGGER trg_skills_set_updated_at BEFORE UPDATE ON skills FOR EACH ROW EXECUTE FUNCTION set_updated_at()
""",
    """
CREATE TRIGGER trg_users_set_updated_at BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION set_updated_at()
""",
    """
CREATE TRIGGER trg_conversations_set_updated_at BEFORE UPDATE ON conversations FOR EACH ROW EXECUTE FUNCTION set_updated_at()
""",
    """
CREATE TRIGGER trg_jobs_set_updated_at BEFORE UPDATE ON jobs FOR EACH ROW EXECUTE FUNCTION set_updated_at()
""",
    """
CREATE TRIGGER trg_sessions_set_updated_at BEFORE UPDATE ON sessions FOR EACH ROW EXECUTE FUNCTION set_updated_at()
""",
    """
CREATE TRIGGER trg_skill_aliases_set_updated_at BEFORE UPDATE ON skill_aliases FOR EACH ROW EXECUTE FUNCTION set_updated_at()
""",
    """
CREATE TRIGGER trg_user_profiles_set_updated_at BEFORE UPDATE ON user_profiles FOR EACH ROW EXECUTE FUNCTION set_updated_at()
""",
    """
CREATE TRIGGER trg_user_skills_set_updated_at BEFORE UPDATE ON user_skills FOR EACH ROW EXECUTE FUNCTION set_updated_at()
""",
    """
CREATE TRIGGER trg_job_embeddings_set_updated_at BEFORE UPDATE ON job_embeddings FOR EACH ROW EXECUTE FUNCTION set_updated_at()
""",
    """
CREATE TRIGGER trg_job_skills_set_updated_at BEFORE UPDATE ON job_skills FOR EACH ROW EXECUTE FUNCTION set_updated_at()
""",
    """
CREATE TRIGGER trg_messages_set_updated_at BEFORE UPDATE ON messages FOR EACH ROW EXECUTE FUNCTION set_updated_at()
""",
    """
CREATE TRIGGER trg_recommendation_logs_set_updated_at BEFORE UPDATE ON recommendation_logs FOR EACH ROW EXECUTE FUNCTION set_updated_at()
""",
    """
CREATE TRIGGER trg_saved_jobs_set_updated_at BEFORE UPDATE ON saved_jobs FOR EACH ROW EXECUTE FUNCTION set_updated_at()
""",
    """
CREATE TRIGGER trg_agent_runs_set_updated_at BEFORE UPDATE ON agent_runs FOR EACH ROW EXECUTE FUNCTION set_updated_at()
""",
    """
CREATE TRIGGER trg_user_preferences_set_updated_at BEFORE UPDATE ON user_preferences FOR EACH ROW EXECUTE FUNCTION set_updated_at()
""",
    """
CREATE TRIGGER trg_tool_calls_set_updated_at BEFORE UPDATE ON tool_calls FOR EACH ROW EXECUTE FUNCTION set_updated_at()
""",
)

_DROP_TABLES: tuple[str, ...] = (
    """
DROP TABLE tool_calls
""",
    """
DROP TABLE user_preferences
""",
    """
DROP TABLE agent_runs
""",
    """
DROP TABLE saved_jobs
""",
    """
DROP TABLE recommendation_logs
""",
    """
DROP TABLE messages
""",
    """
DROP TABLE job_skills
""",
    """
DROP TABLE job_embeddings
""",
    """
DROP TABLE user_skills
""",
    """
DROP TABLE user_profiles
""",
    """
DROP TABLE skill_aliases
""",
    """
DROP TABLE sessions
""",
    """
DROP TABLE jobs
""",
    """
DROP TABLE conversations
""",
    """
DROP TABLE users
""",
    """
DROP TABLE skills
""",
    """
DROP TABLE ingestion_runs
""",
    """
DROP TABLE companies
""",
)


def upgrade() -> None:
    for statement in (
        *_EXTENSIONS,
        _SET_UPDATED_AT_FUNCTION,
        *_TABLES,
        *_UPDATED_AT_TRIGGERS,
    ):
        op.execute(statement)


def downgrade() -> None:
    # Extensions stay installed: the image may have created some before this migration ran,
    # and dropping one would remove state this migration never owned.
    for statement in (*_DROP_TABLES, "DROP FUNCTION set_updated_at()"):
        op.execute(statement)
