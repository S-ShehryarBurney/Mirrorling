from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from memory_models import (
    CompanionDocument,
    MemoryDocument,
    MemoryObservationDocument,
    RelationshipDocument,
    UserDocument,
)


def make_memory(**overrides):
    data = {
        "memory_id": str(uuid4()),
        "user_id": str(uuid4()),
        "scope": "shared",
        "companion_id": None,
        "memory_type": "profile",
        "content": "The user enjoys quiet mornings.",
        "source": {
            "type": "explicit_user_statement",
            "companion_id": "moss",
            "session_id": "session-1",
        },
    }
    data.update(overrides)
    return data


def test_user_document_validates_uuid_preferences_and_utc_timestamps():
    user_id = str(uuid4())
    created_at = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    user = UserDocument(
        user_id=user_id,
        created_at=created_at,
        updated_at=created_at,
        memory_preferences={"default_mode": "ask_before_remembering"},
    )

    assert user.user_id == user_id
    assert user.memory_preferences.default_mode == "ask_before_remembering"
    assert user.memory_preferences.sensitive_memory_requires_approval is True
    assert user.created_at.tzinfo == timezone.utc

    with pytest.raises(ValidationError):
        UserDocument(user_id="not-a-uuid")
    with pytest.raises(ValidationError):
        UserDocument(user_id=user_id, created_at=datetime(2026, 1, 1))


def test_custom_companion_requires_uuid_and_owner():
    owner_user_id = str(uuid4())
    companion_id = str(uuid4())
    companion = CompanionDocument(
        companion_id=companion_id,
        owner_user_id=owner_user_id,
        name="My companion",
        kind="custom",
        personality_description="Curious and thoughtful.",
    )
    assert companion.companion_id == companion_id
    assert companion.owner_user_id == owner_user_id

    with pytest.raises(ValidationError):
        CompanionDocument(
            companion_id="not-a-uuid",
            owner_user_id=owner_user_id,
            name="Invalid",
            kind="custom",
            personality_description="Curious.",
        )
    with pytest.raises(ValidationError):
        CompanionDocument(
            companion_id=companion_id,
            owner_user_id=None,
            name="Ownerless",
            kind="custom",
            personality_description="Curious.",
        )


@pytest.mark.parametrize(
    ("scope", "companion_id"),
    [("shared", "moss"), ("companion", None)],
)
def test_memory_scope_requires_consistent_companion_id(scope, companion_id):
    with pytest.raises(ValidationError):
        MemoryDocument(**make_memory(scope=scope, companion_id=companion_id))


def test_sensitive_memory_requires_explicit_approval():
    with pytest.raises(ValidationError, match="explicit user approval"):
        MemoryDocument(
            **make_memory(
                sensitivity="sensitive",
                explicit_sensitive_approval=False,
            )
        )

    approved = MemoryDocument(
        **make_memory(
            sensitivity="sensitive",
            explicit_sensitive_approval=True,
        )
    )
    assert approved.explicit_sensitive_approval is True


def test_memory_modes_and_types_reject_unknown_values():
    user_id = str(uuid4())
    with pytest.raises(ValidationError):
        UserDocument(
            user_id=user_id,
            memory_preferences={"default_mode": "always_remember"},
        )
    with pytest.raises(ValidationError):
        RelationshipDocument(user_id=user_id, companion_id="moss", memory_mode_override="nope")
    with pytest.raises(ValidationError):
        MemoryDocument(**make_memory(memory_type="chat_log"))


def test_shared_and_companion_memory_metadata_are_distinct():
    shared = MemoryDocument(**make_memory())
    companion_memory = MemoryDocument(
        **make_memory(scope="companion", companion_id="nova")
    )

    assert shared.scope == "shared"
    assert shared.companion_id is None
    assert companion_memory.scope == "companion"
    assert companion_memory.companion_id == "nova"
    assert companion_memory.source.companion_id == "moss"


def test_observation_day_matches_utc_observed_at():
    observed_at = datetime(2026, 2, 1, 1, tzinfo=timezone.utc)
    observation = MemoryObservationDocument(
        observation_id=str(uuid4()),
        user_id=str(uuid4()),
        companion_id="echo",
        pattern_key="morning_routine",
        observation_summary="The user mentioned their morning routine.",
        observed_at=observed_at,
        observation_day="2026-02-01",
        session_id="session-1",
    )
    assert observation.observation_day.isoformat() == "2026-02-01"

    with pytest.raises(ValidationError, match="observation_day"):
        MemoryObservationDocument(
            observation_id=str(uuid4()),
            user_id=str(uuid4()),
            companion_id="echo",
            pattern_key="morning_routine",
            observation_summary="The user mentioned their morning routine.",
            observed_at=observed_at,
            observation_day="2026-01-31",
            session_id="session-1",
        )