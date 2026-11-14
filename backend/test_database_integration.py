import os
from datetime import datetime, timedelta, timezone

import pytest
from pymongo import MongoClient
from fastapi.testclient import TestClient

from identity_service import (
    DuplicateRelationship,
    IdentityResourceNotFound,
    create_custom_companion,
    create_relationship,
    create_user,
    get_companion,
    get_user,
    update_first_meeting_status,
)
from memory_models import FirstMeetingStatus
from memory_store import initialize_memory_store
from memory_service import (
    MemoryCorrectionInput,
    MemoryCreateInput,
    MemoryWriteStatus,
    ObservationCreateInput,
    correct_memory,
    create_memory,
    delete_all_user_memory,
    delete_memory,
    record_observation,
)
from identity_service import update_relationship_memory_mode, update_user_memory_mode
from memory_models import MemoryMode
from memory_retrieval import retrieve_relevant_memories
from familiarity_service import (
    confirm_candidate,
    get_pending_candidate_for_chat,
    list_pending_candidates,
)
from main import app


@pytest.mark.skipif(
    os.getenv("RUN_MONGODB_INTEGRATION") != "1",
    reason="Set RUN_MONGODB_INTEGRATION=1 to ping a local MongoDB server.",
)
def test_local_mongodb_accepts_ping():
    client = MongoClient(
        os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27017"),
        serverSelectionTimeoutMS=5000,
    )
    try:
        assert client.admin.command("ping")["ok"] == 1.0
    finally:
        client.close()


@pytest.mark.skipif(
    os.getenv("RUN_MONGODB_INTEGRATION") != "1",
    reason="Set RUN_MONGODB_INTEGRATION=1 to test FastAPI startup and health.",
)
def test_fastapi_startup_and_health_with_local_mongodb():
    with TestClient(app) as test_client:
        response = test_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "application": "running",
        "mongodb": "connected",
    }


@pytest.mark.skipif(
    os.getenv("RUN_MONGODB_INTEGRATION") != "1",
    reason="Set RUN_MONGODB_INTEGRATION=1 to initialize a local MongoDB server.",
)
def test_local_mongodb_memory_store_initialization_is_idempotent():
    client = MongoClient(
        os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27017"),
        serverSelectionTimeoutMS=5000,
    )
    try:
        database = client[os.getenv("MONGODB_DATABASE", "mirrorling")]
        initialize_memory_store(database)
        initialize_memory_store(database)

        assert {
            "users",
            "companions",
            "relationships",
            "memories",
            "memory_observations",
            "familiarity_candidates",
        }.issubset(database.list_collection_names())
        assert database["companions"].count_documents(
            {"companion_id": {"$in": ["moss", "nova", "echo", "zen"]}}
        ) == 4
        assert "relationships_user_companion_unique" in database[
            "relationships"
        ].index_information()
    finally:
        client.close()


@pytest.mark.skipif(
    os.getenv("RUN_MONGODB_INTEGRATION") != "1",
    reason="Set RUN_MONGODB_INTEGRATION=1 to test local MongoDB identity storage.",
)
def test_local_mongodb_persists_identity_and_relationships():
    client = MongoClient(
        os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27017"),
        serverSelectionTimeoutMS=5000,
    )
    database = client[os.getenv("MONGODB_DATABASE", "mirrorling")]
    user_ids = []
    try:
        initialize_memory_store(database)
        user = create_user(database)
        user_ids.append(user.user_id)
        other_user = create_user(database)
        user_ids.append(other_user.user_id)
        assert get_user(database, user.user_id).user_id == user.user_id

        custom = create_custom_companion(
            database,
            user.user_id,
            "Integration companion",
            "Curious and thoughtful.",
        )
        assert get_companion(
            database,
            user.user_id,
            custom.companion_id,
        ).owner_user_id == user.user_id
        with pytest.raises(IdentityResourceNotFound):
            get_companion(database, other_user.user_id, custom.companion_id)

        relationship = create_relationship(database, user.user_id, "moss")
        with pytest.raises(DuplicateRelationship):
            create_relationship(database, user.user_id, "moss")

        started = update_first_meeting_status(
            database,
            user.user_id,
            "moss",
            FirstMeetingStatus.IN_PROGRESS,
        )
        completed = update_first_meeting_status(
            database,
            user.user_id,
            "moss",
            FirstMeetingStatus.COMPLETED,
        )
        assert completed.first_meeting_started_at == started.first_meeting_started_at
        assert completed.first_meeting_completed_at is not None
        assert relationship.memory_mode_override is None
    finally:
        database["relationships"].delete_many(
            {"user_id": {"$in": user_ids}}
        )
        database["companions"].delete_many(
            {"owner_user_id": {"$in": user_ids}}
        )
        database["users"].delete_many({"user_id": {"$in": user_ids}})
        client.close()


@pytest.mark.skipif(
    os.getenv("RUN_MONGODB_INTEGRATION") != "1",
    reason="Set RUN_MONGODB_INTEGRATION=1 to test local MongoDB memory writes.",
)
def test_local_mongodb_enforces_memory_permissions_and_records_observations():
    client = MongoClient(
        os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27017"),
        serverSelectionTimeoutMS=5000,
    )
    database = client[os.getenv("MONGODB_DATABASE", "mirrorling")]
    user_ids = []
    try:
        initialize_memory_store(database)
        user = create_user(database)
        user_ids.append(user.user_id)
        create_relationship(database, user.user_id, "moss")

        memory_input = MemoryCreateInput(
            scope="shared",
            memory_type="profile",
            content="The user enjoys morning walks.",
            source={
                "type": "explicit_user_statement",
                "companion_id": "moss",
                "session_id": "integration-session",
            },
        )
        pending = create_memory(database, user.user_id, memory_input)
        assert pending.status == MemoryWriteStatus.APPROVAL_REQUIRED
        assert database["memories"].count_documents(
            {"user_id": user.user_id}
        ) == 0

        update_user_memory_mode(
            database,
            user.user_id,
            MemoryMode.REMEMBER_USEFUL_THINGS,
        )
        created = create_memory(database, user.user_id, memory_input)
        assert created.status == MemoryWriteStatus.CREATED
        assert database["memories"].count_documents(
            {"user_id": user.user_id}
        ) == 1
        retrieved = retrieve_relevant_memories(
            database,
            user.user_id,
            "morning walks",
            "moss",
        )
        assert [memory.memory_id for memory in retrieved] == [
            created.memory.memory_id
        ]

        create_relationship(database, user.user_id, "nova")
        private_input = MemoryCreateInput(
            scope="companion",
            companion_id="nova",
            memory_type="profile",
            content="The user enjoys studying chess openings.",
            source={
                "type": "explicit_user_statement",
                "companion_id": "nova",
                "session_id": "integration-session",
            },
        )
        private_memory = create_memory(database, user.user_id, private_input)
        nova_memories = retrieve_relevant_memories(
            database,
            user.user_id,
            "chess openings",
            "nova",
        )
        moss_memories = retrieve_relevant_memories(
            database,
            user.user_id,
            "chess openings",
            "moss",
        )
        assert [memory.memory_id for memory in nova_memories] == [
            private_memory.memory.memory_id
        ]
        assert all(
            memory.memory_id != private_memory.memory.memory_id
            for memory in moss_memories
        )

        observation_input = ObservationCreateInput(
            companion_id="moss",
            pattern_key="morning_walks",
            observation_summary="The user mentioned a morning walking routine.",
            session_id="integration-session",
        )
        recorded = record_observation(
            database,
            user.user_id,
            observation_input,
        )
        assert recorded.status == MemoryWriteStatus.CREATED

        update_relationship_memory_mode(
            database,
            user.user_id,
            "moss",
            MemoryMode.DONT_REMEMBER,
        )
        disabled = record_observation(
            database,
            user.user_id,
            observation_input,
        )
        assert disabled.status == MemoryWriteStatus.MEMORY_DISABLED
        assert database["memory_observations"].count_documents(
            {"user_id": user.user_id}
        ) == 1
    finally:
        database["memory_observations"].delete_many(
            {"user_id": {"$in": user_ids}}
        )
        database["memories"].delete_many({"user_id": {"$in": user_ids}})
        database["relationships"].delete_many(
            {"user_id": {"$in": user_ids}}
        )
        database["users"].delete_many({"user_id": {"$in": user_ids}})
        client.close()


@pytest.mark.skipif(
    os.getenv("RUN_MONGODB_INTEGRATION") != "1",
    reason="Set RUN_MONGODB_INTEGRATION=1 to test local MongoDB familiarity lifecycle.",
)
def test_local_mongodb_familiarity_confirmation_correction_and_cleanup():
    client = MongoClient(
        os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27017"),
        serverSelectionTimeoutMS=5000,
    )
    database = client[os.getenv("MONGODB_DATABASE", "mirrorling")]
    user_ids = []
    try:
        initialize_memory_store(database)
        user = create_user(database)
        user_ids.append(user.user_id)
        create_relationship(database, user.user_id, "moss")
        update_user_memory_mode(
            database,
            user.user_id,
            MemoryMode.REMEMBER_USEFUL_THINGS,
        )

        now = datetime.now(timezone.utc)
        for index, day_offset in enumerate((0, 1, 2, 5, 6)):
            result = record_observation(
                database,
                user.user_id,
                ObservationCreateInput(
                    companion_id="moss",
                    pattern_key="weekend_walking",
                    observation_summary=(
                        "The user mentioned walking on the weekend."
                    ),
                    session_id=f"familiarity-session-{index}",
                    observed_at=now - timedelta(days=day_offset),
                ),
            )
            assert result.status == MemoryWriteStatus.CREATED

        candidates = list_pending_candidates(database, user.user_id, "moss")
        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.evidence_count == 5
        assert candidate.distinct_day_count == 5
        assert get_pending_candidate_for_chat(
            database,
            user.user_id,
            "moss",
        ).candidate_id == candidate.candidate_id

        confirmed = confirm_candidate(database, user.user_id, candidate.candidate_id)
        assert confirmed.memory.memory_type == "familiarity"
        assert confirmed.memory.scope == "companion"
        assert confirmed.memory.source.familiarity_candidate_id == candidate.candidate_id

        corrected = correct_memory(
            database,
            user.user_id,
            confirmed.memory.memory_id,
            MemoryCorrectionInput(content="The user enjoys weekend walks."),
        )
        assert corrected.source.type == "user_correction"
        deletion = delete_memory(
            database,
            user.user_id,
            corrected.memory_id,
        )
        assert deletion.memories_deleted == 1
        assert deletion.observations_deleted == 5
        assert deletion.candidates_deleted == 1
        assert database["users"].count_documents(
            {"user_id": user.user_id}
        ) == 1
        assert database["relationships"].count_documents(
            {"user_id": user.user_id}
        ) == 1
    finally:
        database["familiarity_candidates"].delete_many(
            {"user_id": {"$in": user_ids}}
        )
        database["memory_observations"].delete_many(
            {"user_id": {"$in": user_ids}}
        )
        database["memories"].delete_many({"user_id": {"$in": user_ids}})
        database["relationships"].delete_many(
            {"user_id": {"$in": user_ids}}
        )
        database["users"].delete_many({"user_id": {"$in": user_ids}})
        client.close()