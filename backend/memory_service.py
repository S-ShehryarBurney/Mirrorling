from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field, field_validator, model_validator

from identity_service import (
    IdentityResourceNotFound,
    get_companion,
    get_relationship,
    get_user,
)
from memory_models import (
    FamiliarityCandidateStatus,
    MemoryDocument,
    MemoryMode,
    MemoryObservationDocument,
    MemoryScope,
    MemorySensitivity,
    MemorySource,
    MemorySourceType,
    MemoryType,
    MemoryModel,
)


class MemoryWriteStatus(StrEnum):
    CREATED = "created"
    APPROVAL_REQUIRED = "approval_required"
    MEMORY_DISABLED = "memory_disabled"


class MemoryCreateInput(MemoryModel):
    scope: MemoryScope
    companion_id: str | None = None
    memory_type: MemoryType
    content: str = Field(min_length=1, max_length=4000)
    source: MemorySource
    sensitivity: MemorySensitivity = MemorySensitivity.NORMAL
    user_approved: bool = False
    tags: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def validate_scope_and_familiarity_source(self):
        if self.scope == MemoryScope.SHARED and self.companion_id is not None:
            raise ValueError("shared memories cannot have a companion_id")
        if self.scope == MemoryScope.COMPANION and self.companion_id is None:
            raise ValueError("companion memories require a companion_id")
        if (
            self.memory_type == MemoryType.FAMILIARITY
            and self.source.type != MemorySourceType.USER_CONFIRMATION
        ):
            raise ValueError(
                "familiarity memories require explicit user confirmation"
            )
        return self


class ObservationCreateInput(MemoryModel):
    companion_id: str
    pattern_key: str = Field(min_length=1, max_length=80)
    observation_summary: str = Field(min_length=1, max_length=240)
    session_id: str = Field(min_length=1, max_length=160)
    observed_at: datetime | None = None
    user_approved: bool = False
    sensitivity: MemorySensitivity = MemorySensitivity.NORMAL

    @field_validator("observed_at")
    @classmethod
    def require_timezone_on_observed_at(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("observed_at must include a timezone")
            return value.astimezone(timezone.utc)
        return value

    @field_validator("sensitivity")
    @classmethod
    def observations_must_be_non_sensitive(
        cls,
        value: MemorySensitivity,
    ) -> MemorySensitivity:
        if value != MemorySensitivity.NORMAL:
            raise ValueError("familiarity observations must be non-sensitive")
        return value


class MemoryWriteResult(MemoryModel):
    status: MemoryWriteStatus
    detail: str
    memory: MemoryDocument | None = None


class ObservationWriteResult(MemoryModel):
    status: MemoryWriteStatus
    detail: str
    observation: MemoryObservationDocument | None = None


class MemoryCorrectionInput(MemoryModel):
    content: str | None = Field(default=None, min_length=1, max_length=4000)
    memory_type: MemoryType | None = None
    tags: list[str] | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def require_a_correction(self):
        if not any(
            value is not None
            for value in (self.content, self.memory_type, self.tags)
        ):
            raise ValueError("At least one memory field must be corrected")
        return self


class MemoryDeletionResult(MemoryModel):
    memories_deleted: int = 0
    observations_deleted: int = 0
    candidates_deleted: int = 0


class InvalidMemoryScope(Exception):
    pass


def get_effective_memory_mode(
    database,
    user_id: str,
    companion_id: str,
) -> MemoryMode:
    user = get_user(database, user_id)
    get_companion(database, user_id, companion_id)
    relationship = get_relationship(database, user_id, companion_id)
    return _resolve_effective_memory_mode(user, relationship)


def _resolve_effective_memory_mode(user, relationship) -> MemoryMode:
    return (
        relationship.memory_mode_override
        or user.memory_preferences.default_mode
        or MemoryMode.ASK_BEFORE_REMEMBERING
    )


def create_memory(
    database,
    user_id: str,
    request: MemoryCreateInput,
    *,
    memory_id: str | None = None,
) -> MemoryWriteResult:
    user = get_user(database, user_id)
    source = request.source
    companion_id = source.companion_id
    get_companion(database, user.user_id, companion_id)
    relationship = get_relationship(database, user.user_id, companion_id)

    if request.scope == MemoryScope.SHARED:
        if request.companion_id is not None:
            raise InvalidMemoryScope("Shared memories cannot target a companion")
    elif request.scope == MemoryScope.COMPANION:
        if request.companion_id is None:
            raise InvalidMemoryScope("Companion memories require a companion_id")
        if request.companion_id != companion_id:
            raise InvalidMemoryScope(
                "A companion cannot write to another companion's private memory"
            )

    mode = _resolve_effective_memory_mode(user, relationship)
    if mode == MemoryMode.DONT_REMEMBER:
        return MemoryWriteResult(
            status=MemoryWriteStatus.MEMORY_DISABLED,
            detail="Memory storage is disabled for this relationship.",
        )

    requires_approval = (
        mode == MemoryMode.ASK_BEFORE_REMEMBERING
        or request.sensitivity == MemorySensitivity.SENSITIVE
    )
    if requires_approval and not request.user_approved:
        return MemoryWriteResult(
            status=MemoryWriteStatus.APPROVAL_REQUIRED,
            detail="Explicit user approval is required before saving this memory.",
        )

    now = datetime.now(timezone.utc)
    document = MemoryDocument(
        memory_id=memory_id or str(uuid4()),
        user_id=user.user_id,
        scope=request.scope,
        companion_id=request.companion_id,
        created_by_companion_id=companion_id,
        memory_type=request.memory_type,
        content=request.content,
        source=source,
        sensitivity=request.sensitivity,
        explicit_sensitive_approval=(
            request.sensitivity == MemorySensitivity.SENSITIVE
            and request.user_approved
        ),
        tags=request.tags,
        created_at=now,
        updated_at=now,
        last_confirmed_at=(
            now if request.memory_type == MemoryType.FAMILIARITY else None
        ),
    )
    database["memories"].insert_one(document.model_dump(mode="python"))
    return MemoryWriteResult(
        status=MemoryWriteStatus.CREATED,
        detail="Memory saved.",
        memory=document,
    )


def record_observation(
    database,
    user_id: str,
    request: ObservationCreateInput,
) -> ObservationWriteResult:
    user = get_user(database, user_id)
    companion = get_companion(database, user.user_id, request.companion_id)
    relationship = get_relationship(
        database,
        user.user_id,
        companion.companion_id,
    )
    mode = _resolve_effective_memory_mode(user, relationship)

    if mode == MemoryMode.DONT_REMEMBER:
        return ObservationWriteResult(
            status=MemoryWriteStatus.MEMORY_DISABLED,
            detail="Memory storage is disabled for this relationship.",
        )
    if (
        mode == MemoryMode.ASK_BEFORE_REMEMBERING
        and not request.user_approved
    ):
        return ObservationWriteResult(
            status=MemoryWriteStatus.APPROVAL_REQUIRED,
            detail="Explicit user approval is required before retaining this observation.",
        )

    observed_at = request.observed_at or datetime.now(timezone.utc)
    observation = MemoryObservationDocument(
        observation_id=str(uuid4()),
        user_id=user.user_id,
        companion_id=companion.companion_id,
        pattern_key=request.pattern_key,
        observation_summary=request.observation_summary,
        observed_at=observed_at,
        observation_day=observed_at.date(),
        session_id=request.session_id,
    )
    observation_document = observation.model_dump(mode="python")
    observation_document["observation_day"] = (
        observation.observation_day.isoformat()
    )
    database["memory_observations"].insert_one(observation_document)
    from familiarity_service import evaluate_observation_for_candidate

    evaluate_observation_for_candidate(database, observation)
    return ObservationWriteResult(
        status=MemoryWriteStatus.CREATED,
        detail="Observation recorded.",
        observation=observation,
    )


def correct_memory(
    database,
    user_id: str,
    memory_id: str,
    correction: MemoryCorrectionInput,
) -> MemoryDocument:
    user = get_user(database, user_id)
    try:
        memory_id = str(UUID(memory_id))
    except (TypeError, ValueError, AttributeError) as error:
        raise IdentityResourceNotFound("Memory not found") from error

    collection = database["memories"]
    stored = collection.find_one({"user_id": user.user_id, "memory_id": memory_id})
    if stored is None:
        raise IdentityResourceNotFound("Memory not found")
    memory = _memory_document(stored)

    now = datetime.now(timezone.utc)
    updates = {
        field: getattr(correction, field)
        for field in ("content", "memory_type", "tags")
        if field in correction.model_fields_set
        and getattr(correction, field) is not None
    }
    source = memory.source.model_copy(
        update={"type": MemorySourceType.USER_CORRECTION}
    )
    updates.update(
        {
            "source.type": MemorySourceType.USER_CORRECTION.value,
            "updated_at": now,
            "last_confirmed_at": now,
        }
    )
    result = collection.update_one(
        {"user_id": user.user_id, "memory_id": memory.memory_id},
        {"$set": updates},
    )
    if result.matched_count == 0:
        raise IdentityResourceNotFound("Memory not found")
    model_updates = {
        field: value
        for field, value in updates.items()
        if field in {"content", "memory_type", "tags"}
    }
    model_updates.update(
        {"source": source, "updated_at": now, "last_confirmed_at": now}
    )
    return memory.model_copy(update=model_updates)


def _memory_document(document: dict) -> MemoryDocument:
    normalized = {key: value for key, value in document.items() if key != "_id"}
    for field in ("created_at", "updated_at", "last_confirmed_at"):
        value = normalized.get(field)
        if isinstance(value, datetime) and value.tzinfo is None:
            normalized[field] = value.replace(tzinfo=timezone.utc)
    return MemoryDocument.model_validate(normalized)


def delete_memory(
    database,
    user_id: str,
    memory_id: str,
) -> MemoryDeletionResult:
    user = get_user(database, user_id)
    try:
        memory_id = str(UUID(memory_id))
    except (TypeError, ValueError, AttributeError) as error:
        raise IdentityResourceNotFound("Memory not found") from error
    query = {"user_id": user.user_id, "memory_id": memory_id}
    document = database["memories"].find_one(query)
    if document is None:
        raise IdentityResourceNotFound("Memory not found")

    memory = _memory_document(document)
    deleted = database["memories"].delete_one(query).deleted_count
    if deleted == 0:
        raise IdentityResourceNotFound("Memory not found")

    candidate_query = {
        "user_id": user.user_id,
        "confirmed_memory_id": memory.memory_id,
    }
    if memory.scope == MemoryScope.COMPANION:
        observations_deleted = database["memory_observations"].delete_many(
            {
                "user_id": user.user_id,
                "companion_id": memory.companion_id,
            }
        ).deleted_count
        candidate_query = {
            "user_id": user.user_id,
            "companion_id": memory.companion_id,
            "$or": [
                {"status": FamiliarityCandidateStatus.PENDING.value},
                {"confirmed_memory_id": memory.memory_id},
            ],
        }
    else:
        observations_deleted = 0
    candidates_deleted = database["familiarity_candidates"].delete_many(
        candidate_query
    ).deleted_count
    return MemoryDeletionResult(
        memories_deleted=deleted,
        observations_deleted=observations_deleted,
        candidates_deleted=candidates_deleted,
    )


def delete_shared_memories(database, user_id: str) -> MemoryDeletionResult:
    user = get_user(database, user_id)
    memories = database["memories"]
    query = {"user_id": user.user_id, "scope": MemoryScope.SHARED.value}
    memory_ids = [
        document["memory_id"]
        for document in memories.find(query, {"memory_id": 1})
    ]
    deleted = memories.delete_many(query).deleted_count
    candidates_deleted = 0
    if memory_ids:
        candidates_deleted = database["familiarity_candidates"].delete_many(
            {
                "user_id": user.user_id,
                "confirmed_memory_id": {"$in": memory_ids},
            }
        ).deleted_count
    return MemoryDeletionResult(
        memories_deleted=deleted,
        candidates_deleted=candidates_deleted,
    )


def delete_companion_memories(
    database,
    user_id: str,
    companion_id: str,
) -> MemoryDeletionResult:
    user = get_user(database, user_id)
    companion = get_companion(database, user.user_id, companion_id)
    get_relationship(database, user.user_id, companion.companion_id)
    memories = database["memories"]
    query = {
        "user_id": user.user_id,
        "scope": MemoryScope.COMPANION.value,
        "companion_id": companion.companion_id,
    }
    memory_ids = [
        document["memory_id"]
        for document in memories.find(query, {"memory_id": 1})
    ]
    deleted = memories.delete_many(query).deleted_count
    observations_deleted = database["memory_observations"].delete_many(
        {"user_id": user.user_id, "companion_id": companion.companion_id}
    ).deleted_count
    candidate_filter = {
        "user_id": user.user_id,
        "companion_id": companion.companion_id,
        "$or": [
            {"status": FamiliarityCandidateStatus.PENDING.value},
            {"confirmed_memory_id": {"$in": memory_ids}},
        ],
    }
    candidates_deleted = database["familiarity_candidates"].delete_many(
        candidate_filter
    ).deleted_count
    return MemoryDeletionResult(
        memories_deleted=deleted,
        observations_deleted=observations_deleted,
        candidates_deleted=candidates_deleted,
    )


def delete_all_user_memory(database, user_id: str) -> MemoryDeletionResult:
    user = get_user(database, user_id)
    return MemoryDeletionResult(
        memories_deleted=database["memories"].delete_many(
            {"user_id": user.user_id}
        ).deleted_count,
        observations_deleted=database["memory_observations"].delete_many(
            {"user_id": user.user_id}
        ).deleted_count,
        candidates_deleted=database["familiarity_candidates"].delete_many(
            {"user_id": user.user_id}
        ).deleted_count,
    )