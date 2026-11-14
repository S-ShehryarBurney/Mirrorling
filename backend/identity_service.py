from datetime import datetime, timezone
from uuid import UUID, uuid4

from pymongo.errors import DuplicateKeyError

from memory_models import (
    BUILT_IN_COMPANION_IDS,
    CompanionDocument,
    CompanionKind,
    FirstMeetingStatus,
    MemoryMode,
    RelationshipDocument,
    UserDocument,
)
from memory_store import BUILT_IN_COMPANIONS


class IdentityResourceNotFound(Exception):
    pass


class DuplicateRelationship(Exception):
    pass


class InvalidFirstMeetingTransition(Exception):
    pass


class RelationshipUpdateConflict(Exception):
    pass


def _normalize_bson_datetimes(value):
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, dict):
        return {
            key: _normalize_bson_datetimes(item)
            for key, item in value.items()
            if key != "_id"
        }
    if isinstance(value, list):
        return [_normalize_bson_datetimes(item) for item in value]
    return value


def _model_from_document(model, document):
    return model.model_validate(_normalize_bson_datetimes(document))


def _canonical_user_id(user_id: str) -> str:
    return str(UUID(user_id))


def _canonical_companion_id(companion_id: str) -> str:
    if companion_id in BUILT_IN_COMPANION_IDS:
        return companion_id
    return str(UUID(companion_id))


def _require_user(database, user_id: str) -> dict:
    user_id = _canonical_user_id(user_id)
    document = database["users"].find_one({"user_id": user_id})
    if document is None:
        raise IdentityResourceNotFound("User not found")
    return document


def create_user(database) -> UserDocument:
    user = UserDocument(user_id=str(uuid4()))
    database["users"].insert_one(user.model_dump(mode="python"))
    return user


def get_user(database, user_id: str) -> UserDocument:
    user_id = _canonical_user_id(user_id)
    document = database["users"].find_one({"user_id": user_id})
    if document is None:
        raise IdentityResourceNotFound("User not found")
    return _model_from_document(UserDocument, document)


def _companion_query(database, user_id: str, companion_id: str) -> dict | None:
    if companion_id in BUILT_IN_COMPANION_IDS:
        return database["companions"].find_one(
            {
                "companion_id": companion_id,
                "kind": CompanionKind.BUILT_IN.value,
                "owner_user_id": None,
            }
        )

    try:
        companion_id = str(UUID(companion_id))
    except (TypeError, ValueError, AttributeError):
        return None

    return database["companions"].find_one(
        {
            "companion_id": companion_id,
            "kind": CompanionKind.CUSTOM.value,
            "owner_user_id": _canonical_user_id(user_id),
        }
    )


def get_companion(database, user_id: str, companion_id: str) -> CompanionDocument:
    _require_user(database, user_id)
    document = _companion_query(database, user_id, companion_id)
    if document is None:
        raise IdentityResourceNotFound("Companion not found")
    return _model_from_document(CompanionDocument, document)


def list_companions(database, user_id: str) -> list[CompanionDocument]:
    _require_user(database, user_id)
    collection = database["companions"]
    documents = list(collection.find({"kind": CompanionKind.BUILT_IN.value}))
    documents.extend(
        collection.find(
            {
                "kind": CompanionKind.CUSTOM.value,
                "owner_user_id": _canonical_user_id(user_id),
            }
        )
    )
    companions = [
        _model_from_document(CompanionDocument, document)
        for document in documents
    ]
    return sorted(companions, key=lambda companion: companion.name.casefold())


def create_custom_companion(
    database,
    user_id: str,
    name: str,
    personality_description: str,
) -> CompanionDocument:
    user_id = _canonical_user_id(user_id)
    _require_user(database, user_id)
    companion = CompanionDocument(
        companion_id=str(uuid4()),
        owner_user_id=user_id,
        name=name,
        kind=CompanionKind.CUSTOM,
        personality_description=personality_description,
    )
    database["companions"].insert_one(companion.model_dump(mode="python"))
    return companion


def create_relationship(
    database,
    user_id: str,
    companion_id: str,
    memory_mode_override: MemoryMode | None = None,
) -> RelationshipDocument:
    user_id = _canonical_user_id(user_id)
    _require_user(database, user_id)
    companion = _companion_query(database, user_id, companion_id)
    if companion is None:
        raise IdentityResourceNotFound("Companion not found")

    relationship = RelationshipDocument(
        user_id=user_id,
        companion_id=companion["companion_id"],
        memory_mode_override=memory_mode_override,
    )
    try:
        database["relationships"].insert_one(
            relationship.model_dump(mode="python")
        )
    except DuplicateKeyError as error:
        raise DuplicateRelationship from error
    return relationship


def list_relationships(database, user_id: str) -> list[RelationshipDocument]:
    user_id = _canonical_user_id(user_id)
    _require_user(database, user_id)
    documents = database["relationships"].find({"user_id": user_id})
    return [
        _model_from_document(RelationshipDocument, document)
        for document in documents
    ]


def get_relationship(
    database,
    user_id: str,
    companion_id: str,
) -> RelationshipDocument:
    return _get_relationship(database, user_id, companion_id)


def update_user_memory_mode(
    database,
    user_id: str,
    memory_mode: MemoryMode | None,
) -> UserDocument:
    user_id = _canonical_user_id(user_id)
    _require_user(database, user_id)
    database["users"].update_one(
        {"user_id": user_id},
        {
            "$set": {
                "memory_preferences.default_mode": (
                    memory_mode.value if memory_mode is not None else None
                ),
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )
    return get_user(database, user_id)


def update_relationship_memory_mode(
    database,
    user_id: str,
    companion_id: str,
    memory_mode: MemoryMode | None,
) -> RelationshipDocument:
    relationship = _get_relationship(database, user_id, companion_id)
    database["relationships"].update_one(
        {
            "user_id": relationship.user_id,
            "companion_id": relationship.companion_id,
        },
        {
            "$set": {
                "memory_mode_override": (
                    memory_mode.value if memory_mode is not None else None
                ),
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )
    return _get_relationship(
        database,
        relationship.user_id,
        relationship.companion_id,
    )


def _get_relationship(database, user_id: str, companion_id: str) -> RelationshipDocument:
    user_id = _canonical_user_id(user_id)
    try:
        companion_id = _canonical_companion_id(companion_id)
    except (TypeError, ValueError, AttributeError) as error:
        raise IdentityResourceNotFound("Relationship not found") from error
    _require_user(database, user_id)
    document = database["relationships"].find_one(
        {"user_id": user_id, "companion_id": companion_id}
    )
    if document is None:
        raise IdentityResourceNotFound("Relationship not found")
    return _model_from_document(RelationshipDocument, document)


def update_last_interaction(
    database,
    user_id: str,
    companion_id: str,
) -> RelationshipDocument:
    user_id = _canonical_user_id(user_id)
    try:
        companion_id = _canonical_companion_id(companion_id)
    except (TypeError, ValueError, AttributeError) as error:
        raise IdentityResourceNotFound("Relationship not found") from error
    _require_user(database, user_id)
    now = datetime.now(timezone.utc)
    result = database["relationships"].update_one(
        {"user_id": user_id, "companion_id": companion_id},
        {"$set": {"last_interaction_at": now, "updated_at": now}},
    )
    if result.matched_count == 0:
        raise IdentityResourceNotFound("Relationship not found")
    return _get_relationship(database, user_id, companion_id)


def update_first_meeting_status(
    database,
    user_id: str,
    companion_id: str,
    new_status: FirstMeetingStatus,
) -> RelationshipDocument:
    current = _get_relationship(database, user_id, companion_id)
    if current.first_meeting_status == new_status:
        return current

    now = datetime.now(timezone.utc)
    allowed_transition = {
        FirstMeetingStatus.NOT_STARTED: FirstMeetingStatus.IN_PROGRESS,
        FirstMeetingStatus.IN_PROGRESS: FirstMeetingStatus.COMPLETED,
    }.get(current.first_meeting_status)
    if allowed_transition != new_status:
        raise InvalidFirstMeetingTransition(
            f"Cannot transition from {current.first_meeting_status.value} "
            f"to {new_status.value}"
        )

    fields = {
        "first_meeting_status": new_status.value,
        "updated_at": now,
    }
    if new_status == FirstMeetingStatus.IN_PROGRESS:
        fields["first_meeting_started_at"] = (
            current.first_meeting_started_at or now
        )
    else:
        fields["first_meeting_completed_at"] = (
            current.first_meeting_completed_at or now
        )

    result = database["relationships"].update_one(
        {
            "user_id": current.user_id,
            "companion_id": current.companion_id,
            "first_meeting_status": current.first_meeting_status.value,
        },
        {"$set": fields},
    )
    if result.matched_count == 0:
        raise RelationshipUpdateConflict(
            "Relationship status changed concurrently"
        )
    return _get_relationship(database, current.user_id, current.companion_id)