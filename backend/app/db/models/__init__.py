"""Importing this package registers every model on Base.metadata (Alembic relies on it)."""

from app.db.base import Base
from app.db.models.conversation import (
    AgentRun,
    Conversation,
    Message,
    ToolCall,
    UserPreference,
)
from app.db.models.identity import (
    Skill,
    SkillAlias,
    User,
    UserProfile,
    UserSession,
    UserSkill,
)
from app.db.models.jobs import (
    Company,
    IngestionRun,
    Job,
    JobEmbedding,
    JobSkill,
    RecommendationLog,
    SavedJob,
)

__all__ = [
    "AgentRun",
    "Base",
    "Company",
    "Conversation",
    "IngestionRun",
    "Job",
    "JobEmbedding",
    "JobSkill",
    "Message",
    "RecommendationLog",
    "SavedJob",
    "Skill",
    "SkillAlias",
    "ToolCall",
    "User",
    "UserPreference",
    "UserProfile",
    "UserSession",
    "UserSkill",
]
