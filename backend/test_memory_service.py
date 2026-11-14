from collections import defaultdict
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from identity_routes import router as identity_router
from identity_service import (
    IdentityResourceNotFound,
    create_custom_companion,
    create_relationship,
    create_user,
    update_relationship_memory_mode,
    update_user_memory_mode,
)
from memory_models import MemoryMode
from memory_routes import router as memory_router
from memory_service import (
    InvalidMemoryScope,
    MemoryCreateInput,
    MemoryWriteStatus,
    ObservationCreateInput,
    create_memory,
    get_effective_memory_mode,
    record_observation,
)
from memory_store import initialize_memory_store


class FakeCollection:
    def __init__(self):
        self.documents = []
        self.indexes = []

    def create_index(self, keys, **options):
        self.indexes.append((keys, options))
        return options["name"]

    def insert_one(self, document):
        stored = deepcopy(document)
        stored["_id"] = len(self.documents) + 1
        self.documents.append(stored)
        return SimpleNamespace(inserted_id=stored["_id"])

    def find_one(self, query):
        return next(
            (document for document in self.documents if self._matches(document, query)),
            None,
        )

    def find(self, query, projection=None):
        return [
            document
            for document in self.documents
            if self._matches(document, query)
        ]

    def update_one(self, query, update, upsert=False):
        document = self.find_one(query)
        if document is None:
            if upsert and "$setOnInsert" in update:
                self.insert_one(update["$setOnInsert"])
                return SimpleNamespace(matched_count=0, upserted_id=1)
            return SimpleNamespace(matched_count=0, upserted_id=None)
        for field, value in update.get("$set", {}).items():
            target = document
            parts = field.split(".")
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = deepcopy(value)
        return SimpleNamespace(matched_count=1, upserted_id=None)

    @staticmethod
    def _matches(document, query):
        return all(document.get(field) == value for field, value in query.items())


class FakeDatabase:
    def __init__(self):
        self.collections = defaultdict(FakeCollection)

    def __getitem__(self, collection_name):
        return self.collections[collection_name]


@pytest.fixture
def memory_context():
    database = FakeDatabase()
    initialize_memory_store(database)
    user = create_user(database)
    other_user = create_user(database)
    for companion_id in ("moss", "nova", "echo", "zen"):
        create_relationship(database, user.user_id, companion_id)
        create_relationship(database, other_user.user_id, companion_id)
    return database, user, other_user


def memory_input(**overrides):
    values = {
        "scope": "shared",
        "companion_id": None,
        "memory_type": "profile",
        "content": "The user likes quiet mornings.",
        "source": {
            "type": "explicit_user_statement",
            "companion_id": "moss",
            "session_id": "session-1",
        },
    }
    values.update(overrides)
    return MemoryCreateInput(**values)


def observation_input(**overrides):
    values = {
        "companion_id": "moss",
        "pattern_key": "morning_routine",
        "observation_summary": "The user mentioned a morning routine.",
        "session_id": "session-1",
    }
    values.update(overrides)
    return ObservationCreateInput(**values)


def test_effective_mode_precedence_and_safe_default(memory_context):
    database, user, _ = memory_context
    assert get_effective_memory_mode(database, user.user_id, "moss") == (
        MemoryMode.ASK_BEFORE_REMEMBERING
    )

    update_user_memory_mode(database, user.user_id, MemoryMode.REMEMBER_USEFUL_THINGS)
    assert get_effective_memory_mode(database, user.user_id, "moss") == (
        MemoryMode.REMEMBER_USEFUL_THINGS
    )

    update_relationship_memory_mode(
        database,
        user.user_id,
        "moss",
        MemoryMode.DONT_REMEMBER,
    )
    assert get_effective_memory_mode(database, user.user_id, "moss") == (
        MemoryMode.DONT_REMEMBER
    )

    update_relationship_memory_mode(database, user.user_id, "moss", None)
    assert get_effective_memory_mode(database, user.user_id, "moss") == (
        MemoryMode.REMEMBER_USEFUL_THINGS
    )


def test_all_permission_modes_and_ask_default(memory_context):
    database, user, _ = memory_context
    pending = create_memory(database, user.user_id, memory_input())
    assert pending.status == MemoryWriteStatus.APPROVAL_REQUIRED
    assert database["memories"].documents == []

    update_user_memory_mode(database, user.user_id, MemoryMode.REMEMBER_USEFUL_THINGS)
    saved = create_memory(database, user.user_id, memory_input())
    assert saved.status == MemoryWriteStatus.CREATED
    assert len(database["memories"].documents) == 1

    update_user_memory_mode(database, user.user_id, MemoryMode.DONT_REMEMBER)
    disabled = create_memory(database, user.user_id, memory_input(user_approved=True))
    assert disabled.status == MemoryWriteStatus.MEMORY_DISABLED
    assert len(database["memories"].documents) == 1


@pytest.mark.parametrize(
    ("mode", "unapproved_status"),
    [
        (MemoryMode.REMEMBER_USEFUL_THINGS, MemoryWriteStatus.APPROVAL_REQUIRED),
        (MemoryMode.ASK_BEFORE_REMEMBERING, MemoryWriteStatus.APPROVAL_REQUIRED),
        (MemoryMode.DONT_REMEMBER, MemoryWriteStatus.MEMORY_DISABLED),
    ],
)
def test_sensitive_memory_never_saves_without_approval(
    memory_context,
    mode,
    unapproved_status,
):
    database, user, _ = memory_context
    update_user_memory_mode(database, user.user_id, mode)
    rejected = create_memory(
        database,
        user.user_id,
        memory_input(sensitivity="sensitive", content="private content"),
    )

    assert rejected.status == unapproved_status
    assert database["memories"].documents == []
    assert rejected.memory is None
    assert "private content" not in rejected.model_dump_json()


@pytest.mark.parametrize(
    "mode",
    [MemoryMode.REMEMBER_USEFUL_THINGS, MemoryMode.ASK_BEFORE_REMEMBERING],
)
def test_sensitive_memory_stores_approval_provenance(memory_context, mode):
    database, user, _ = memory_context
    update_user_memory_mode(database, user.user_id, mode)
    result = create_memory(
        database,
        user.user_id,
        memory_input(
            sensitivity="sensitive",
            user_approved=True,
        ),
    )

    assert result.status == MemoryWriteStatus.CREATED
    assert result.memory.explicit_sensitive_approval is True
    assert database["memories"].documents[0]["explicit_sensitive_approval"] is True


def test_shared_and_companion_memory_scope_and_provenance(memory_context):
    database, user, _ = memory_context
    update_user_memory_mode(database, user.user_id, MemoryMode.REMEMBER_USEFUL_THINGS)

    shared = create_memory(database, user.user_id, memory_input())
    companion = create_memory(
        database,
        user.user_id,
        memory_input(scope="companion", companion_id="moss"),
    )

    assert shared.memory.companion_id is None
    assert shared.memory.created_by_companion_id == "moss"
    assert companion.memory.companion_id == "moss"
    assert companion.memory.created_by_companion_id == "moss"

    with pytest.raises(InvalidMemoryScope):
        create_memory(
            database,
            user.user_id,
            memory_input(
                scope="companion",
                companion_id="nova",
            ),
        )


@pytest.mark.parametrize("memory_type", ["profile", "episodic", "relationship"])
def test_supported_non_familiarity_memory_types(memory_context, memory_type):
    database, user, _ = memory_context
    update_user_memory_mode(database, user.user_id, MemoryMode.REMEMBER_USEFUL_THINGS)

    result = create_memory(
        database,
        user.user_id,
        memory_input(memory_type=memory_type),
    )

    assert result.status == MemoryWriteStatus.CREATED
    assert result.memory.memory_type == memory_type


def test_cross_user_custom_companion_is_rejected(memory_context):
    database, user, other_user = memory_context
    custom = create_custom_companion(
        database,
        other_user.user_id,
        "Private companion",
        "Thoughtful.",
    )
    request = memory_input(
        source={
            "type": "explicit_user_statement",
            "companion_id": custom.companion_id,
            "session_id": "session-2",
        },
    )

    with pytest.raises(IdentityResourceNotFound):
        create_memory(database, user.user_id, request)


def test_invalid_memory_type_and_scope_are_rejected_before_service(memory_context):
    with pytest.raises(ValidationError):
        memory_input(memory_type="conversation")
    with pytest.raises(ValidationError):
        memory_input(scope="shared", companion_id="moss")
    with pytest.raises(ValidationError):
        memory_input(scope="companion", companion_id=None)


def test_confirmed_familiarity_memory_requires_confirmation_source(memory_context):
    with pytest.raises(ValidationError):
        memory_input(memory_type="familiarity")

    request = memory_input(
        memory_type="familiarity",
        source={
            "type": "user_confirmation",
            "companion_id": "moss",
            "session_id": "session-1",
        },
    )
    database, user, _ = memory_context
    update_user_memory_mode(database, user.user_id, MemoryMode.REMEMBER_USEFUL_THINGS)
    result = create_memory(database, user.user_id, request)
    assert result.memory.last_confirmed_at is not None


def test_observation_permissions_and_utc_day(memory_context):
    database, user, _ = memory_context
    pending = record_observation(database, user.user_id, observation_input())
    assert pending.status == MemoryWriteStatus.APPROVAL_REQUIRED
    assert database["memory_observations"].documents == []

    update_user_memory_mode(database, user.user_id, MemoryMode.REMEMBER_USEFUL_THINGS)
    recorded = record_observation(
        database,
        user.user_id,
        observation_input(observed_at="2026-03-05T23:30:00-02:00"),
    )
    assert recorded.status == MemoryWriteStatus.CREATED
    assert recorded.observation.observed_at.isoformat() == "2026-03-06T01:30:00+00:00"
    assert recorded.observation.observation_day.isoformat() == "2026-03-06"
    assert set(database["memory_observations"].documents[0]) == {
        "observation_id",
        "user_id",
        "companion_id",
        "pattern_key",
        "observation_summary",
        "observed_at",
        "observation_day",
        "session_id",
        "schema_version",
        "_id",
    }


def test_observations_require_approval_or_respect_disabled_mode(memory_context):
    database, user, _ = memory_context
    approved = record_observation(
        database,
        user.user_id,
        observation_input(user_approved=True),
    )
    assert approved.status == MemoryWriteStatus.CREATED

    update_relationship_memory_mode(
        database,
        user.user_id,
        "moss",
        MemoryMode.DONT_REMEMBER,
    )
    disabled = record_observation(
        database,
        user.user_id,
        observation_input(user_approved=True),
    )
    assert disabled.status == MemoryWriteStatus.MEMORY_DISABLED
    assert len(database["memory_observations"].documents) == 1


def test_sensitive_or_raw_observation_fields_are_rejected(memory_context):
    with pytest.raises(ValidationError):
        observation_input(sensitivity="sensitive")
    with pytest.raises(ValidationError):
        observation_input(transcript="raw conversation")


@pytest.fixture
def api_client(memory_context):
    database, _, _ = memory_context
    app = FastAPI()
    app.include_router(identity_router)
    app.include_router(memory_router)
    app.state.database = database
    app.state.memory_store_initialized = True
    with TestClient(app) as client:
        yield client


def test_memory_api_permission_results_and_mode_updates(api_client, memory_context):
    database, user, _ = memory_context
    response = api_client.post(
        f"/users/{user.user_id}/memories",
        json=memory_input().model_dump(mode="json"),
    )
    assert response.status_code == 202
    assert response.json()["status"] == "approval_required"
    assert database["memories"].documents == []

    updated = api_client.patch(
        f"/users/{user.user_id}/memory-preference",
        json={"default_mode": "remember_useful_things"},
    )
    assert updated.status_code == 200
    saved = api_client.post(
        f"/users/{user.user_id}/memories",
        json=memory_input().model_dump(mode="json"),
    )
    assert saved.status_code == 201
    assert saved.json()["status"] == "created"

    override = api_client.patch(
        f"/users/{user.user_id}/relationships/moss/memory-mode",
        json={"memory_mode_override": "dont_remember"},
    )
    assert override.status_code == 200
    observation = api_client.post(
        f"/users/{user.user_id}/observations",
        json=observation_input(user_approved=True).model_dump(mode="json"),
    )
    assert observation.status_code == 200
    assert observation.json()["status"] == "memory_disabled"
    assert database["memory_observations"].documents == []


def test_memory_api_requires_sensitive_approval_without_echoing_content(
    api_client,
    memory_context,
):
    database, user, _ = memory_context
    update_user_memory_mode(database, user.user_id, MemoryMode.REMEMBER_USEFUL_THINGS)
    body = memory_input(
        sensitivity="sensitive",
        content="sensitive content must not be echoed",
    ).model_dump(mode="json")

    response = api_client.post(f"/users/{user.user_id}/memories", json=body)

    assert response.status_code == 202
    assert response.json()["status"] == "approval_required"
    assert "sensitive content must not be echoed" not in response.text
    assert database["memories"].documents == []


def test_memory_api_is_unavailable_when_store_initialization_failed(api_client):
    api_client.app.state.memory_store_initialized = False
    user_id = api_client.post("/users").json()["user_id"]

    response = api_client.post(
        f"/users/{user_id}/memories",
        json=memory_input().model_dump(mode="json"),
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "Memory storage is unavailable"