from collections import defaultdict
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError, ServerSelectionTimeoutError

import identity_routes
from identity_routes import router as identity_router
from identity_service import (
    DuplicateRelationship,
    IdentityResourceNotFound,
    InvalidFirstMeetingTransition,
    create_custom_companion,
    create_relationship,
    create_user,
    get_companion,
    get_user,
    list_relationships,
    update_first_meeting_status,
    update_last_interaction,
)
from memory_models import (
    BUILT_IN_COMPANION_IDS,
    CompanionDocument,
    CompanionKind,
    FirstMeetingStatus,
)
from memory_store import BUILT_IN_COMPANIONS


class FakeCollection:
    def __init__(self):
        self.documents = []

    def insert_one(self, document):
        if "user_id" in document and "companion_id" not in document:
            unique_key = (document["user_id"],)
            fields = ("user_id",)
        elif "kind" in document:
            unique_key = (document["companion_id"],)
            fields = ("companion_id",)
        elif "companion_id" in document and "first_meeting_status" in document:
            unique_key = (document["user_id"], document["companion_id"])
            fields = ("user_id", "companion_id")
        else:
            unique_key = None
            fields = ()

        if unique_key is not None and any(
            tuple(existing[field] for field in fields) == unique_key
            for existing in self.documents
        ):
            raise DuplicateKeyError("duplicate key")

        stored = deepcopy(document)
        stored["_id"] = len(self.documents) + 1
        self.documents.append(stored)
        return SimpleNamespace(inserted_id=stored["_id"])

    def find_one(self, query):
        return next(
            (document for document in self.documents if self._matches(document, query)),
            None,
        )

    def find(self, query):
        return [
            document
            for document in self.documents
            if self._matches(document, query)
        ]

    def update_one(self, query, update):
        document = self.find_one(query)
        if document is None:
            return SimpleNamespace(matched_count=0)
        document.update(deepcopy(update["$set"]))
        return SimpleNamespace(matched_count=1)

    @staticmethod
    def _matches(document, query):
        return all(document.get(field) == value for field, value in query.items())


class FakeDatabase:
    def __init__(self):
        self.collections = defaultdict(FakeCollection)

    def __getitem__(self, collection_name):
        return self.collections[collection_name]


@pytest.fixture
def database():
    database = FakeDatabase()
    companions = database["companions"]
    for data in BUILT_IN_COMPANIONS:
        built_in = CompanionDocument(
            **data,
            owner_user_id=None,
            kind=CompanionKind.BUILT_IN,
        )
        companions.insert_one(built_in.model_dump(mode="python"))
    return database


@pytest.fixture
def api_client(database):
    app = FastAPI()
    app.include_router(identity_router)
    app.state.database = database
    with TestClient(app) as client:
        yield client


def test_user_creation_and_retrieval_keep_stable_identity(database):
    created = create_user(database)
    first_retrieval = get_user(database, created.user_id)
    second_retrieval = get_user(database, created.user_id)

    assert first_retrieval.user_id == created.user_id
    assert second_retrieval.user_id == created.user_id
    assert second_retrieval.memory_preferences.default_mode is None
    assert len(database["users"].documents) == 1


def test_invalid_user_uuid_is_rejected(database):
    with pytest.raises(ValueError):
        get_user(database, "not-a-uuid")


@pytest.mark.parametrize("companion_id", ["moss", "nova", "echo", "zen"])
def test_builtin_relationship_starts_without_memory_mode(database, companion_id):
    user = create_user(database)
    relationship = create_relationship(database, user.user_id, companion_id)

    assert relationship.companion_id == companion_id
    assert relationship.first_meeting_status == FirstMeetingStatus.NOT_STARTED
    assert relationship.memory_mode_override is None


def test_custom_companion_is_accessible_only_to_its_owner(database):
    owner = create_user(database)
    other_user = create_user(database)
    companion = create_custom_companion(
        database,
        owner.user_id,
        "Custom",
        "Curious and thoughtful.",
    )

    assert companion.companion_id not in BUILT_IN_COMPANION_IDS
    assert get_companion(database, owner.user_id, companion.companion_id).owner_user_id == owner.user_id
    with pytest.raises(IdentityResourceNotFound):
        get_companion(database, other_user.user_id, companion.companion_id)
    with pytest.raises(IdentityResourceNotFound):
        create_relationship(database, other_user.user_id, companion.companion_id)


def test_duplicate_relationship_is_rejected(database):
    user = create_user(database)
    create_relationship(database, user.user_id, "nova")

    with pytest.raises(DuplicateRelationship):
        create_relationship(database, user.user_id, "nova")


def test_first_meeting_transitions_preserve_timestamps(database):
    user = create_user(database)
    create_relationship(database, user.user_id, "echo")

    in_progress = update_first_meeting_status(
        database,
        user.user_id,
        "echo",
        FirstMeetingStatus.IN_PROGRESS,
    )
    completed = update_first_meeting_status(
        database,
        user.user_id,
        "echo",
        FirstMeetingStatus.COMPLETED,
    )
    repeated = update_first_meeting_status(
        database,
        user.user_id,
        "echo",
        FirstMeetingStatus.COMPLETED,
    )

    assert in_progress.first_meeting_started_at is not None
    assert completed.first_meeting_status == FirstMeetingStatus.COMPLETED
    assert completed.first_meeting_started_at == in_progress.first_meeting_started_at
    assert completed.first_meeting_completed_at is not None
    assert repeated.first_meeting_started_at == completed.first_meeting_started_at
    assert repeated.first_meeting_completed_at == completed.first_meeting_completed_at
    with pytest.raises(InvalidFirstMeetingTransition):
        update_first_meeting_status(
            database,
            user.user_id,
            "echo",
            FirstMeetingStatus.IN_PROGRESS,
        )


def test_relationships_are_isolated_and_interactions_update_timestamps(database):
    first_user = create_user(database)
    second_user = create_user(database)
    create_relationship(database, first_user.user_id, "zen")

    assert len(list_relationships(database, first_user.user_id)) == 1
    assert list_relationships(database, second_user.user_id) == []

    updated = update_last_interaction(database, first_user.user_id, "zen")
    assert updated.last_interaction_at is not None
    assert updated.updated_at == updated.last_interaction_at


def test_user_api_creation_retrieval_and_invalid_uuid(api_client):
    created = api_client.post("/users")
    assert created.status_code == 201

    user_id = created.json()["user_id"]
    first_read = api_client.get(f"/users/{user_id}")
    second_read = api_client.get(f"/users/{user_id}")
    assert first_read.status_code == 200
    assert first_read.json()["user_id"] == second_read.json()["user_id"]
    assert first_read.json()["memory_preferences"]["default_mode"] is None
    assert api_client.get("/users/not-a-uuid").status_code == 422


def test_malformed_stored_user_returns_generic_unavailable_response(
    api_client,
    database,
):
    user = create_user(database)
    database["users"].documents[-1]["memory_preferences"] = {
        "default_mode": "invalid-mode"
    }

    response = api_client.get(f"/users/{user.user_id}")

    assert response.status_code == 503
    assert response.json()["detail"] == "Stored identity data is invalid"
    assert "invalid-mode" not in response.text


def test_identity_database_failure_returns_generic_unavailable_response(
    api_client,
    database,
    monkeypatch,
):
    user = create_user(database)
    monkeypatch.setattr(
        identity_routes,
        "get_user",
        lambda *_: (_ for _ in ()).throw(
            ServerSelectionTimeoutError("mongodb://secret@example")
        ),
    )

    response = api_client.get(f"/users/{user.user_id}")

    assert response.status_code == 503
    assert response.json()["detail"] == "Identity storage is temporarily unavailable"
    assert "secret" not in response.text


def test_relationship_api_duplicate_and_custom_owner_scoping(api_client):
    owner_id = api_client.post("/users").json()["user_id"]
    other_user_id = api_client.post("/users").json()["user_id"]

    builtin_relationship = api_client.post(
        f"/users/{owner_id}/relationships",
        json={"companion_id": "moss"},
    )
    assert builtin_relationship.status_code == 201
    assert builtin_relationship.json()["memory_mode_override"] is None
    assert len(
        api_client.get(f"/users/{owner_id}/relationships").json()
    ) == 1
    assert api_client.get(
        f"/users/{other_user_id}/relationships"
    ).json() == []
    duplicate = api_client.post(
        f"/users/{owner_id}/relationships",
        json={"companion_id": "moss"},
    )
    assert duplicate.status_code == 409

    custom = api_client.post(
        f"/users/{owner_id}/companions",
        json={
            "name": "Private companion",
            "personality_description": "Patient and curious.",
        },
    )
    assert custom.status_code == 201
    companion_id = custom.json()["companion_id"]
    assert api_client.get(
        f"/users/{other_user_id}/companions/{companion_id}"
    ).status_code == 404
    assert api_client.post(
        f"/users/{other_user_id}/relationships",
        json={"companion_id": companion_id},
    ).status_code == 404


def test_first_meeting_api_transitions_and_interaction_timestamp(api_client):
    user_id = api_client.post("/users").json()["user_id"]
    relationship_url = f"/users/{user_id}/relationships/zen"
    created = api_client.post(
        f"/users/{user_id}/relationships",
        json={"companion_id": "zen"},
    )
    assert created.status_code == 201

    started = api_client.patch(
        f"{relationship_url}/first-meeting",
        json={"status": "in_progress"},
    ).json()
    completed = api_client.patch(
        f"{relationship_url}/first-meeting",
        json={"status": "completed"},
    ).json()
    assert completed["first_meeting_status"] == "completed"
    assert completed["first_meeting_started_at"] == started[
        "first_meeting_started_at"
    ]
    assert completed["first_meeting_completed_at"] is not None

    interaction = api_client.post(f"{relationship_url}/interactions")
    assert interaction.status_code == 200
    assert interaction.json()["last_interaction_at"] is not None
    invalid_transition = api_client.patch(
        f"{relationship_url}/first-meeting",
        json={"status": "in_progress"},
    )
    assert invalid_transition.status_code == 409