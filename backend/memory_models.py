from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SCHEMA_VERSION = 1
BUILT_IN_COMPANION_IDS = {"moss", "nova", "echo", "zen"}


class MemoryMode(StrEnum):
    REMEMBER_USEFUL_THINGS = "remember_useful_things"
    ASK_BEFORE_REMEMBERING = "ask_before_remembering"
    DONT_REMEMBER = "dont_remember"


class CompanionKind(StrEnum):
    BUILT_IN = "built_in"
    CUSTOM = "custom"


class MemoryScope(StrEnum):
    SHARED = "shared"
    COMPANION = "companion"


class MemoryType(StrEnum):
    PROFILE = "profile"
    EPISODIC = "episodic"
    RELATIONSHIP = "relationship"
    FAMILIARITY = "familiarity"


class MemorySourceType(StrEnum):
    EXPLICIT_USER_STATEMENT = "explicit_user_statement"
    USER_CONFIRMATION = "user_confirmation"
    USER_CORRECTION = "user_correction"


class MemorySensitivity(StrEnum):
    NORMAL = "normal"
    SENSITIVE = "sensitive"


class FamiliarityCandidateStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class FirstMeetingStatus(StrEnum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


def _uuid_string(value: str) -> str:
    try:
        return str(UUID(value))
    except (TypeError, ValueError, AttributeError) as error:
        raise ValueError("must be a valid UUID string") from error


def _companion_id(value: str) -> str:
    if value in BUILT_IN_COMPANION_IDS:
        return value
    return _uuid_string(value)


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include a timezone")
    return value.astimezone(timezone.utc)


class MemoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MemoryPreferences(MemoryModel):
    default_mode: MemoryMode | None = None
    sensitive_memory_requires_approval: Literal[True] = True


class UserDocument(MemoryModel):
    user_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    memory_preferences: MemoryPreferences = Field(default_factory=MemoryPreferences)
    schema_version: int = Field(default=SCHEMA_VERSION, ge=1)

    @field_validator("user_id")
    @classmethod
    def validate_user_id(cls, value: str) -> str:
        return _uuid_string(value)

    @field_validator("created_at", "updated_at")
    @classmethod
    def validate_timestamps(cls, value: datetime) -> datetime:
        return _utc_datetime(value)


class CompanionDocument(MemoryModel):
    companion_id: str
    owner_user_id: str | None
    name: str
    kind: CompanionKind
    personality_description: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    schema_version: int = Field(default=SCHEMA_VERSION, ge=1)

    @field_validator("companion_id")
    @classmethod
    def validate_companion_id(cls, value: str) -> str:
        return _companion_id(value)

    @field_validator("owner_user_id")
    @classmethod
    def validate_owner_user_id(cls, value: str | None) -> str | None:
        return _uuid_string(value) if value is not None else None

    @field_validator("created_at", "updated_at")
    @classmethod
    def validate_timestamps(cls, value: datetime) -> datetime:
        return _utc_datetime(value)

    @model_validator(mode="after")
    def validate_owner_and_kind(self):
        if self.kind == CompanionKind.BUILT_IN:
            if self.owner_user_id is not None:
                raise ValueError("built-in companions cannot have an owner")
            if self.companion_id not in BUILT_IN_COMPANION_IDS:
                raise ValueError("built-in companion ID must be a stable built-in ID")
        else:
            if self.owner_user_id is None:
                raise ValueError("custom companions require an owner")
            _uuid_string(self.companion_id)
        return self


class RelationshipDocument(MemoryModel):
    user_id: str
    companion_id: str
    memory_mode_override: MemoryMode | None = None
    first_meeting_status: FirstMeetingStatus = FirstMeetingStatus.NOT_STARTED
    first_meeting_started_at: datetime | None = None
    first_meeting_completed_at: datetime | None = None
    relationship_started_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    last_interaction_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    schema_version: int = Field(default=SCHEMA_VERSION, ge=1)

    @field_validator("user_id")
    @classmethod
    def validate_user_id(cls, value: str) -> str:
        return _uuid_string(value)

    @field_validator("companion_id")
    @classmethod
    def validate_companion_id(cls, value: str) -> str:
        return _companion_id(value)

    @field_validator(
        "first_meeting_started_at",
        "first_meeting_completed_at",
        "relationship_started_at",
        "last_interaction_at",
        "created_at",
        "updated_at",
    )
    @classmethod
    def validate_timestamps(cls, value: datetime | None) -> datetime | None:
        return _utc_datetime(value) if value is not None else None


class MemorySource(MemoryModel):
    type: MemorySourceType
    companion_id: str
    session_id: str = Field(min_length=1)
    familiarity_candidate_id: str | None = None

    @field_validator("companion_id")
    @classmethod
    def validate_companion_id(cls, value: str) -> str:
        return _companion_id(value)

    @field_validator("familiarity_candidate_id")
    @classmethod
    def validate_familiarity_candidate_id(cls, value: str | None) -> str | None:
        return _uuid_string(value) if value is not None else None


class MemoryDocument(MemoryModel):
    memory_id: str
    user_id: str
    scope: MemoryScope
    companion_id: str | None
    created_by_companion_id: str | None = None
    memory_type: MemoryType
    content: str = Field(min_length=1)
    source: MemorySource
    sensitivity: MemorySensitivity = MemorySensitivity.NORMAL
    explicit_sensitive_approval: bool = False
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_confirmed_at: datetime | None = None
    schema_version: int = Field(default=SCHEMA_VERSION, ge=1)

    @field_validator("memory_id", "user_id")
    @classmethod
    def validate_uuid_ids(cls, value: str) -> str:
        return _uuid_string(value)

    @field_validator("companion_id", "created_by_companion_id")
    @classmethod
    def validate_optional_companion_ids(cls, value: str | None) -> str | None:
        return _companion_id(value) if value is not None else None

    @field_validator("created_at", "updated_at", "last_confirmed_at")
    @classmethod
    def validate_timestamps(cls, value: datetime | None) -> datetime | None:
        return _utc_datetime(value) if value is not None else None

    @model_validator(mode="after")
    def validate_scope_and_approval(self):
        if self.scope == MemoryScope.SHARED and self.companion_id is not None:
            raise ValueError("shared memories cannot have a companion_id")
        if self.scope == MemoryScope.COMPANION and self.companion_id is None:
            raise ValueError("companion memories require a companion_id")
        if (
            self.sensitivity == MemorySensitivity.SENSITIVE
            and not self.explicit_sensitive_approval
        ):
            raise ValueError("sensitive memories require explicit user approval")
        return self


class MemoryObservationDocument(MemoryModel):
    observation_id: str
    user_id: str
    companion_id: str
    pattern_key: str = Field(min_length=1)
    observation_summary: str = Field(min_length=1)
    observed_at: datetime
    observation_day: date
    session_id: str = Field(min_length=1)
    schema_version: int = Field(default=SCHEMA_VERSION, ge=1)

    @field_validator("observation_id", "user_id")
    @classmethod
    def validate_uuid_ids(cls, value: str) -> str:
        return _uuid_string(value)

    @field_validator("companion_id")
    @classmethod
    def validate_companion_id(cls, value: str) -> str:
        return _companion_id(value)

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(cls, value: datetime) -> datetime:
        return _utc_datetime(value)

    @model_validator(mode="after")
    def validate_utc_observation_day(self):
        if self.observation_day != self.observed_at.date():
            raise ValueError("observation_day must match observed_at in UTC")
        return self


class FamiliarityCandidateDocument(MemoryModel):
    candidate_id: str
    user_id: str
    companion_id: str
    pattern_key: str = Field(min_length=1)
    observation_summary: str = Field(min_length=1, max_length=240)
    status: FamiliarityCandidateStatus = FamiliarityCandidateStatus.PENDING
    evidence_count: int = Field(ge=5)
    distinct_day_count: int = Field(ge=4)
    supporting_observation_ids: list[str] = Field(min_length=5)
    source_session_id: str = Field(min_length=1)
    first_observed_at: datetime
    last_observed_at: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    confirmed_at: datetime | None = None
    rejected_at: datetime | None = None
    confirmed_memory_id: str | None = None
    schema_version: int = Field(default=SCHEMA_VERSION, ge=1)

    @field_validator("candidate_id", "user_id", "supporting_observation_ids")
    @classmethod
    def validate_uuid_ids(cls, value):
        if isinstance(value, list):
            return [_uuid_string(item) for item in value]
        return _uuid_string(value)

    @field_validator("companion_id")
    @classmethod
    def validate_companion_id(cls, value: str) -> str:
        return _companion_id(value)

    @field_validator("confirmed_memory_id")
    @classmethod
    def validate_confirmed_memory_id(cls, value: str | None) -> str | None:
        return _uuid_string(value) if value is not None else None

    @field_validator(
        "first_observed_at",
        "last_observed_at",
        "created_at",
        "updated_at",
        "confirmed_at",
        "rejected_at",
    )
    @classmethod
    def validate_timestamps(cls, value: datetime | None) -> datetime | None:
        return _utc_datetime(value) if value is not None else None

    @model_validator(mode="after")
    def validate_evidence_counts(self):
        if self.distinct_day_count > self.evidence_count:
            raise ValueError("distinct_day_count cannot exceed evidence_count")
        if len(self.supporting_observation_ids) < self.evidence_count:
            raise ValueError("supporting observation IDs must cover all evidence")
        return self