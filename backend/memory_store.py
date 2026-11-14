from pymongo import ASCENDING, DESCENDING

from memory_models import CompanionDocument, CompanionKind


BUILT_IN_COMPANIONS = (
    {
        "companion_id": "moss",
        "name": "Moss",
        "personality_description": (
            "Warm, relaxed, curious, approachable and playful."
        ),
    },
    {
        "companion_id": "nova",
        "name": "Nova",
        "personality_description": (
            "Gentle, empathetic, thoughtful and patient."
        ),
    },
    {
        "companion_id": "echo",
        "name": "Echo",
        "personality_description": (
            "Energetic, confident, disciplined and motivating."
        ),
    },
    {
        "companion_id": "zen",
        "name": "Zen",
        "personality_description": (
            "Exceptionally calm, grounded, patient and reflective."
        ),
    },
)


def initialize_memory_store(database) -> None:
    database["users"].create_index(
        [("user_id", ASCENDING)],
        name="users_user_id_unique",
        unique=True,
    )

    companions = database["companions"]
    companions.create_index(
        [("companion_id", ASCENDING)],
        name="companions_companion_id_unique",
        unique=True,
    )
    companions.create_index(
        [("owner_user_id", ASCENDING)],
        name="companions_custom_owner_lookup",
        partialFilterExpression={"kind": CompanionKind.CUSTOM.value},
    )

    relationships = database["relationships"]
    relationships.create_index(
        [("user_id", ASCENDING), ("companion_id", ASCENDING)],
        name="relationships_user_companion_unique",
        unique=True,
    )
    relationships.create_index(
        [("user_id", ASCENDING)],
        name="relationships_user_lookup",
    )

    memories = database["memories"]
    memories.create_index(
        [("memory_id", ASCENDING)],
        name="memories_memory_id_unique",
        unique=True,
    )
    memories.create_index(
        [("user_id", ASCENDING), ("scope", ASCENDING)],
        name="memories_shared_user_scope_lookup",
    )
    memories.create_index(
        [
            ("user_id", ASCENDING),
            ("scope", ASCENDING),
            ("companion_id", ASCENDING),
        ],
        name="memories_companion_scope_lookup",
    )
    memories.create_index(
        [("user_id", ASCENDING), ("memory_type", ASCENDING)],
        name="memories_user_type_lookup",
    )
    memories.create_index(
        [("updated_at", DESCENDING)],
        name="memories_updated_at_lookup",
    )
    memories.create_index(
        [("content", "text"), ("tags", "text")],
        name="memories_content_tags_text",
    )
    memories.create_index(
        [("source.familiarity_candidate_id", ASCENDING)],
        name="memories_familiarity_candidate_unique",
        unique=True,
        partialFilterExpression={
            "source.familiarity_candidate_id": {"$type": "string"}
        },
    )

    observations = database["memory_observations"]
    observations.create_index(
        [("observation_id", ASCENDING)],
        name="memory_observations_observation_id_unique",
        unique=True,
    )
    observations.create_index(
        [
            ("user_id", ASCENDING),
            ("companion_id", ASCENDING),
            ("pattern_key", ASCENDING),
            ("observed_at", ASCENDING),
        ],
        name="memory_observations_pattern_history_lookup",
    )

    candidates = database["familiarity_candidates"]
    candidates.create_index(
        [("candidate_id", ASCENDING)],
        name="familiarity_candidates_id_unique",
        unique=True,
    )
    candidates.create_index(
        [
            ("user_id", ASCENDING),
            ("companion_id", ASCENDING),
            ("pattern_key", ASCENDING),
        ],
        name="familiarity_candidates_user_companion_pattern_unique",
        unique=True,
    )
    candidates.create_index(
        [
            ("user_id", ASCENDING),
            ("companion_id", ASCENDING),
            ("status", ASCENDING),
            ("created_at", DESCENDING),
        ],
        name="familiarity_candidates_pending_lookup",
    )

    for companion_data in BUILT_IN_COMPANIONS:
        companion = CompanionDocument(
            **companion_data,
            owner_user_id=None,
            kind=CompanionKind.BUILT_IN,
        )
        companions.update_one(
            {"companion_id": companion.companion_id},
            {"$setOnInsert": companion.model_dump(mode="python")},
            upsert=True,
        )