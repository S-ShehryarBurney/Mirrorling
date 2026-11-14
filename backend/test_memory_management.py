from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError
from pymongo.errors import ServerSelectionTimeoutError

from familiarity_service import (
    FamiliarityCandidateConflict,
    FamiliarityCandidateNotFound,
    confirm_candidate,
    evaluate_observation_for_candidate,
    get_pending_candidate_for_chat,
    list_pending_candidates,
    reject_candidate,
)
from identity_service import (
    IdentityResourceNotFound,
    create_custom_companion,
    create_relationship,
    create_user,
    update_user_memory_mode,
)
from memory_models import (
    FamiliarityCandidateStatus,
    MemoryMode,
    MemoryObservationDocument,
)
from memory_service import (
    MemoryCorrectionInput,
    correct_memory,
    create_memory,
    delete_all_user_memory,
    delete_companion_memories,
    delete_memory,
    delete_shared_memories,
    record_observation,
)
from memory_service import MemoryCreateInput, ObservationCreateInput
from memory_routes import router as memory_router
import memory_routes
from memory_store import initialize_memory_store


class Cursor(list):
    def sort(self, sort_spec):
        for field, direction in reversed(sort_spec):
            super().sort(
                key=lambda item: self._get(item, field),
                reverse=direction < 0,
            )
        return self

    def limit(self, value):
        return Cursor(self[:value])

    @staticmethod
    def _get(document, path):
        value = document
        for component in path.split("."):
            value = value.get(component) if isinstance(value, dict) else None
        return value


class FakeCollection:
    def __init__(self, name):
        self.name = name
        self.documents = []
        self.indexes = []
        self.next_id = 1

    def create_index(self, keys, **options):
        self.indexes.append((keys, options))
        return options["name"]

    def insert_one(self, document):
        constraints = []
        if self.name == "users":
            constraints = [("user_id",)]
        elif self.name == "companions":
            constraints = [("companion_id",)]
        elif self.name == "relationships":
            constraints = [("user_id", "companion_id")]
        elif self.name == "familiarity_candidates":
            constraints = [
                ("candidate_id",),
                ("user_id", "companion_id", "pattern_key"),
            ]
        elif self.name == "memories":
            constraints = [("memory_id",)]
        for fields in constraints:
            if any(
                all(existing.get(field) == document.get(field) for field in fields)
                for existing in self.documents
            ):
                raise DuplicateKeyError("duplicate key")
        source = document.get("source") or {}
        candidate_id = source.get("familiarity_candidate_id")
        if self.name == "memories" and candidate_id is not None:
            if any(
                (existing.get("source") or {}).get("familiarity_candidate_id")
                == candidate_id
                for existing in self.documents
            ):
                raise DuplicateKeyError("candidate memory already exists")
        stored = deepcopy(document)
        stored["_id"] = self.next_id
        self.next_id += 1
        self.documents.append(stored)
        return SimpleNamespace(inserted_id=stored["_id"])

    def find(self, query=None, projection=None):
        matches = [
            deepcopy(document)
            for document in self.documents
            if self._matches(document, query or {})
        ]
        if projection:
            included = {key for key, value in projection.items() if value == 1}
            matches = [
                {key: value for key, value in document.items() if key in included or key == "_id"}
                for document in matches
            ]
        return Cursor(matches)

    def find_one(self, query=None, projection=None, sort=None):
        documents = self.find(query, projection)
        if sort:
            documents.sort(sort)
        return documents[0] if documents else None

    def update_one(self, query, update, upsert=False):
        document = next(
            (item for item in self.documents if self._matches(item, query)),
            None,
        )
        if document is None:
            if not upsert:
                return SimpleNamespace(matched_count=0, upserted_id=None)
            inserted = deepcopy(update.get("$setOnInsert", {}))
            self.insert_one(inserted)
            return SimpleNamespace(matched_count=0, upserted_id=self.next_id - 1)
        for key, value in update.get("$set", {}).items():
            self._set(document, key, deepcopy(value))
        for key in update.get("$unset", {}):
            self._unset(document, key)
        return SimpleNamespace(matched_count=1, upserted_id=None)

    def delete_one(self, query):
        for index, document in enumerate(self.documents):
            if self._matches(document, query):
                del self.documents[index]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    def delete_many(self, query):
        before = len(self.documents)
        self.documents = [
            document
            for document in self.documents
            if not self._matches(document, query)
        ]
        return SimpleNamespace(deleted_count=before - len(self.documents))

    @classmethod
    def _matches(cls, document, query):
        for key, expected in query.items():
            if key == "$and":
                if not all(cls._matches(document, item) for item in expected):
                    return False
                continue
            if key == "$or":
                if not any(cls._matches(document, item) for item in expected):
                    return False
                continue
            actual = Cursor._get(document, key)
            if isinstance(expected, dict):
                for operator, value in expected.items():
                    if operator == "$gte" and not (actual is not None and actual >= value):
                        return False
                    if operator == "$lte" and not (actual is not None and actual <= value):
                        return False
                    if operator == "$in" and actual not in value:
                        return False
                    if operator == "$ne" and actual == value:
                        return False
                    if operator == "$type" and value == "string" and not isinstance(actual, str):
                        return False
            elif actual != expected:
                return False
        return True

    @staticmethod
    def _set(document, path, value):
        target = document
        parts = path.split(".")
        for component in parts[:-1]:
            target = target.setdefault(component, {})
        target[parts[-1]] = value

    @staticmethod
    def _unset(document, path):
        target = document
        parts = path.split(".")
        for component in parts[:-1]:
            target = target.get(component, {})
        target.pop(parts[-1], None)


class FakeDatabase:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection(name))


@pytest.fixture
def context():
    database = FakeDatabase()
    initialize_memory_store(database)
    user = create_user(database)
    other_user = create_user(database)
    create_relationship(database, user.user_id, "moss")
    create_relationship(database, user.user_id, "nova")
    create_relationship(database, other_user.user_id, "moss")
    update_user_memory_mode(database, user.user_id, MemoryMode.REMEMBER_USEFUL_THINGS)
    return database, user, other_user


@pytest.fixture
def api_client(context):
    database, _, _ = context
    app = FastAPI()
    app.include_router(memory_router)
    app.state.database = database
    app.state.memory_store_initialized = True
    with TestClient(app) as client:
        yield client


def add_observations(
    database,
    user_id,
    companion_id,
    pattern_key,
    now,
    day_offsets,
):
    observations = []
    for index, offset in enumerate(day_offsets):
        observed_at = now - timedelta(days=offset, hours=index)
        observation = MemoryObservationDocument(
            observation_id=str(uuid4()),
            user_id=user_id,
            companion_id=companion_id,
            pattern_key=pattern_key,
            observation_summary=f"Pattern evidence {index}.",
            observed_at=observed_at,
            observation_day=observed_at.date(),
            session_id=f"session-{index}",
        )
        document = observation.model_dump(mode="python")
        document["observation_day"] = observation.observation_day.isoformat()
        database["memory_observations"].insert_one(document)
        observations.append(observation)
    return observations


def test_candidate_threshold_five_observations_four_days_and_deduplication(context):
    database, user, _ = context
    now = datetime.now(timezone.utc)
    observations = add_observations(
        database,
        user.user_id,
        "moss",
        "morning_walks",
        now,
        [0, 1, 2, 6, 6],
    )

    candidate = evaluate_observation_for_candidate(
        database,
        observations[-1],
        now=now,
    )
    repeated = evaluate_observation_for_candidate(
        database,
        observations[-1],
        now=now,
    )

    assert candidate.status == FamiliarityCandidateStatus.PENDING
    assert candidate.evidence_count == 5
    assert candidate.distinct_day_count == 4
    assert len(candidate.supporting_observation_ids) == 5
    assert repeated.candidate_id == candidate.candidate_id
    assert len(database["familiarity_candidates"].documents) == 1


@pytest.mark.parametrize(
    "offsets",
    [[0, 1, 2, 3], [0, 0, 1, 1, 2], [8, 9, 10, 11, 12]],
)
def test_candidate_threshold_rejects_insufficient_count_days_or_window(
    context,
    offsets,
):
    database, user, _ = context
    now = datetime.now(timezone.utc)
    observations = add_observations(
        database,
        user.user_id,
        "moss",
        "reading",
        now,
        offsets,
    )

    candidate = evaluate_observation_for_candidate(
        database,
        observations[-1],
        now=now,
    )

    assert candidate is None
    assert database["familiarity_candidates"].documents == []


def test_threshold_does_not_combine_users_or_companions(context):
    database, user, other_user = context
    now = datetime.now(timezone.utc)
    observations = add_observations(
        database,
        user.user_id,
        "moss",
        "journaling",
        now,
        [0, 1, 2],
    )
    add_observations(
        database,
        user.user_id,
        "nova",
        "journaling",
        now,
        [3],
    )
    add_observations(
        database,
        other_user.user_id,
        "moss",
        "journaling",
        now,
        [4],
    )
    assert evaluate_observation_for_candidate(
        database,
        observations[-1],
        now=now,
    ) is None


def test_dont_remember_blocks_candidate_evaluation(context):
    database, user, _ = context
    now = datetime.now(timezone.utc)
    observations = add_observations(
        database,
        user.user_id,
        "moss",
        "daily_planning",
        now,
        [0, 1, 2, 5, 6],
    )
    update_user_memory_mode(database, user.user_id, MemoryMode.DONT_REMEMBER)

    assert evaluate_observation_for_candidate(
        database,
        observations[-1],
        now=now,
    ) is None
    assert database["familiarity_candidates"].documents == []


def test_ask_mode_only_accumulates_explicitly_approved_observations(context):
    database, user, _ = context
    update_user_memory_mode(database, user.user_id, MemoryMode.ASK_BEFORE_REMEMBERING)
    now = datetime.now(timezone.utc)
    for index, day_offset in enumerate((0, 1, 2, 5, 6)):
        request = ObservationCreateInput(
            companion_id="moss",
            pattern_key="weekend_plans",
            observation_summary="The user mentioned planning the weekend.",
            session_id=f"ask-session-{index}",
            observed_at=now - timedelta(days=day_offset),
        )
        pending = record_observation(database, user.user_id, request)
        assert pending.status == "approval_required"
    assert database["memory_observations"].documents == []
    assert database["familiarity_candidates"].documents == []

    for index, day_offset in enumerate((0, 1, 2, 5, 6)):
        record_observation(
            database,
            user.user_id,
            ObservationCreateInput(
                companion_id="moss",
                pattern_key="weekend_plans",
                observation_summary="The user mentioned planning the weekend.",
                session_id=f"approved-session-{index}",
                observed_at=now - timedelta(days=day_offset),
                user_approved=True,
            ),
        )
    assert len(database["memory_observations"].documents) == 5
    assert len(database["familiarity_candidates"].documents) == 1


def make_memory_input(scope="companion", companion_id="moss", **overrides):
    values = {
        "scope": scope,
        "companion_id": companion_id,
        "memory_type": "profile",
        "content": "The user likes walks.",
        "source": {
            "type": "explicit_user_statement",
            "companion_id": companion_id or "moss",
            "session_id": "memory-session",
        },
        "user_approved": True,
    }
    values.update(overrides)
    return MemoryCreateInput(**values)


def add_confirmable_candidate(database, user, companion_id="moss", now=None):
    now = now or datetime.now(timezone.utc)
    observations = add_observations(
        database,
        user.user_id,
        companion_id,
        "weekend_walks",
        now,
        [0, 1, 2, 5, 6],
    )
    return evaluate_observation_for_candidate(
        database,
        observations[-1],
        now=now,
    )


def test_confirm_creates_companion_memory_and_is_idempotent(context):
    database, user, _ = context
    candidate = add_confirmable_candidate(database, user)

    confirmed = confirm_candidate(database, user.user_id, candidate.candidate_id)
    repeated = confirm_candidate(database, user.user_id, candidate.candidate_id)

    assert confirmed.candidate.status == FamiliarityCandidateStatus.CONFIRMED
    assert confirmed.memory.memory_type == "familiarity"
    assert confirmed.memory.scope == "companion"
    assert confirmed.memory.companion_id == "moss"
    assert confirmed.memory.created_by_companion_id == "moss"
    assert confirmed.memory.source.familiarity_candidate_id == candidate.candidate_id
    assert repeated.memory.memory_id == confirmed.memory.memory_id
    assert len(database["memories"].documents) == 1


def test_shared_confirmation_requires_explicit_choice(context):
    database, user, _ = context
    candidate = add_confirmable_candidate(database, user)

    confirmed = confirm_candidate(
        database,
        user.user_id,
        candidate.candidate_id,
        share_across_companions=True,
    )

    assert confirmed.memory.scope == "shared"
    assert confirmed.memory.companion_id is None
    assert confirmed.memory.created_by_companion_id == "moss"


def test_rejection_prevents_candidate_from_being_presented_again(context):
    database, user, _ = context
    candidate = add_confirmable_candidate(database, user)

    rejected = reject_candidate(database, user.user_id, candidate.candidate_id)
    latest_observation = {
        key: value
        for key, value in database["memory_observations"].documents[-1].items()
        if key != "_id"
    }
    evaluated_again = evaluate_observation_for_candidate(
        database,
        MemoryObservationDocument.model_validate(latest_observation),
    )

    assert rejected.status == FamiliarityCandidateStatus.REJECTED
    assert evaluated_again.status == FamiliarityCandidateStatus.REJECTED
    assert list_pending_candidates(database, user.user_id) == []
    assert get_pending_candidate_for_chat(database, user.user_id, "moss") is None
    with pytest.raises(FamiliarityCandidateConflict):
        confirm_candidate(database, user.user_id, candidate.candidate_id)


def test_candidates_are_visible_only_to_owner_and_exact_companion(context):
    database, user, other_user = context
    moss_candidate = add_confirmable_candidate(database, user, "moss")
    nova_observations = add_observations(
        database,
        user.user_id,
        "nova",
        "sleep_schedule",
        datetime.now(timezone.utc),
        [0, 1, 2, 5, 6],
    )
    evaluate_observation_for_candidate(database, nova_observations[-1])

    assert get_pending_candidate_for_chat(database, user.user_id, "moss").candidate_id == moss_candidate.candidate_id
    assert len(list_pending_candidates(database, user.user_id, "nova")) == 1
    assert list_pending_candidates(database, other_user.user_id) == []
    assert get_pending_candidate_for_chat(database, other_user.user_id, "moss") is None


def test_memory_correction_preserves_identity_scope_and_updates_confirmation(context):
    database, user, other_user = context
    created = create_memory(
        database,
        user.user_id,
        make_memory_input(scope="shared", companion_id=None),
    )
    corrected = correct_memory(
        database,
        user.user_id,
        created.memory.memory_id,
        MemoryCorrectionInput(content="The user enjoys evening walks.", tags=["walks"]),
    )

    assert corrected.memory_id == created.memory.memory_id
    assert corrected.user_id == user.user_id
    assert corrected.scope == "shared"
    assert corrected.companion_id is None
    assert corrected.content == "The user enjoys evening walks."
    assert corrected.source.type == "user_correction"
    assert corrected.updated_at == corrected.last_confirmed_at
    with pytest.raises(IdentityResourceNotFound):
        correct_memory(
            database,
            other_user.user_id,
            created.memory.memory_id,
            MemoryCorrectionInput(content="unauthorized"),
        )
    with pytest.raises(IdentityResourceNotFound):
        delete_memory(database, other_user.user_id, created.memory.memory_id)


def test_individual_shared_and_companion_deletion_counts_and_cascades(context):
    database, user, _ = context
    shared = create_memory(
        database,
        user.user_id,
        make_memory_input(scope="shared", companion_id=None),
    )
    private = create_memory(database, user.user_id, make_memory_input())
    candidate = add_confirmable_candidate(database, user)

    deletion = delete_memory(database, user.user_id, private.memory.memory_id)
    assert deletion.memories_deleted == 1
    assert deletion.observations_deleted == 5
    assert deletion.candidates_deleted == 1
    assert len(database["memories"].documents) == 1

    assert delete_shared_memories(database, user.user_id).memories_deleted == 1
    assert database["memories"].documents == []
    assert candidate is not None


def test_companion_and_all_memory_deletion_preserve_identity_records(context):
    database, user, _ = context
    create_memory(database, user.user_id, make_memory_input())
    create_memory(
        database,
        user.user_id,
        make_memory_input(scope="companion", companion_id="nova", source={
            "type": "explicit_user_statement",
            "companion_id": "nova",
            "session_id": "session-nova",
        }),
    )
    add_confirmable_candidate(database, user, "moss")
    add_observations(
        database,
        user.user_id,
        "nova",
        "reading",
        datetime.now(timezone.utc),
        [0, 1],
    )

    companion_deletion = delete_companion_memories(
        database,
        user.user_id,
        "moss",
    )
    assert companion_deletion.memories_deleted == 1
    assert companion_deletion.observations_deleted == 5
    assert companion_deletion.candidates_deleted == 1
    assert database["memories"].documents[0]["companion_id"] == "nova"

    add_confirmable_candidate(database, user, "nova")
    full_deletion = delete_all_user_memory(database, user.user_id)
    assert full_deletion.memories_deleted == 1
    assert full_deletion.observations_deleted == 7
    assert full_deletion.candidates_deleted == 1
    assert database["memories"].documents == []
    assert database["memory_observations"].documents == []
    assert database["familiarity_candidates"].documents == []
    assert database["users"].find_one({"user_id": user.user_id}) is not None
    assert database["relationships"].find_one({"user_id": user.user_id}) is not None


def test_memory_management_api_correction_and_deletion_counts(api_client, context):
    database, user, _ = context
    created = create_memory(
        database,
        user.user_id,
        make_memory_input(scope="shared", companion_id=None),
    )
    corrected = api_client.patch(
        f"/users/{user.user_id}/memories/{created.memory.memory_id}",
        json={"content": "The user likes evening walks."},
    )
    assert corrected.status_code == 200
    assert corrected.json()["source"]["type"] == "user_correction"

    deleted = api_client.delete(
        f"/users/{user.user_id}/memories/{created.memory.memory_id}"
    )
    assert deleted.status_code == 200
    assert deleted.json() == {
        "memories_deleted": 1,
        "observations_deleted": 0,
        "candidates_deleted": 0,
    }
    assert "The user likes evening walks" not in deleted.text
    assert api_client.patch(
        f"/users/{user.user_id}/memories/{created.memory.memory_id}",
        json={"content": "missing"},
    ).status_code == 404


def test_candidate_management_api_confirms_and_rejects_explicitly(
    api_client,
    context,
):
    database, user, _ = context
    candidate = add_confirmable_candidate(database, user, "moss")
    listed = api_client.get(f"/users/{user.user_id}/familiarity-candidates")
    assert listed.status_code == 200
    assert [item["candidate_id"] for item in listed.json()] == [candidate.candidate_id]
    companion_list = api_client.get(
        f"/users/{user.user_id}/companions/moss/familiarity-candidates"
    )
    assert companion_list.status_code == 200
    assert len(companion_list.json()) == 1

    confirmed = api_client.post(
        f"/users/{user.user_id}/familiarity-candidates/{candidate.candidate_id}/confirm",
        json={"share_across_companions": True},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["memory"]["scope"] == "shared"
    assert confirmed.json()["candidate"]["status"] == "confirmed"

    rejected_candidate = add_confirmable_candidate(database, user, "nova")
    rejected = api_client.post(
        f"/users/{user.user_id}/familiarity-candidates/{rejected_candidate.candidate_id}/reject"
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"


def test_malformed_candidate_data_returns_generic_unavailable_response(
    api_client,
    context,
):
    database, user, _ = context
    candidate = add_confirmable_candidate(database, user)
    database["familiarity_candidates"].documents[0]["evidence_count"] = 1

    response = api_client.get(
        f"/users/{user.user_id}/familiarity-candidates"
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Stored memory data is invalid"
    assert "evidence_count" not in response.text


def test_memory_database_failure_returns_generic_unavailable_response(
    api_client,
    context,
    monkeypatch,
):
    database, user, _ = context
    monkeypatch.setattr(
        memory_routes,
        "create_memory",
        lambda *_: (_ for _ in ()).throw(
            ServerSelectionTimeoutError("mongodb://secret@example")
        ),
    )

    response = api_client.post(
        f"/users/{user.user_id}/memories",
        json=make_memory_input(scope="shared", companion_id=None).model_dump(
            mode="json"
        ),
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Memory storage is temporarily unavailable."
    assert "secret" not in response.text
    assert database["memories"].documents == []