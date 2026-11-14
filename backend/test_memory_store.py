from collections import defaultdict

from memory_store import BUILT_IN_COMPANIONS, initialize_memory_store


class FakeCollection:
    def __init__(self):
        self.indexes = []
        self.updates = []
        self.documents = {}

    def create_index(self, keys, **options):
        self.indexes.append((keys, options))
        return options["name"]

    def update_one(self, query, update, upsert=False):
        self.updates.append((query, update, upsert))
        key = query["companion_id"]
        if upsert and key not in self.documents:
            self.documents[key] = dict(update["$setOnInsert"])


class FakeDatabase:
    def __init__(self):
        self.collections = defaultdict(FakeCollection)

    def __getitem__(self, collection_name):
        return self.collections[collection_name]


def test_initialization_creates_all_collections_and_required_indexes():
    database = FakeDatabase()
    initialize_memory_store(database)

    assert set(database.collections) == {
        "users",
        "companions",
        "relationships",
        "memories",
        "memory_observations",
        "familiarity_candidates",
    }

    index_names = {
        collection_name: {
            options["name"]
            for _, options in collection.indexes
        }
        for collection_name, collection in database.collections.items()
    }
    assert index_names["users"] == {"users_user_id_unique"}
    assert index_names["companions"] == {
        "companions_companion_id_unique",
        "companions_custom_owner_lookup",
    }
    assert index_names["relationships"] == {
        "relationships_user_companion_unique",
        "relationships_user_lookup",
    }
    assert index_names["memories"] == {
        "memories_memory_id_unique",
        "memories_shared_user_scope_lookup",
        "memories_companion_scope_lookup",
        "memories_user_type_lookup",
        "memories_updated_at_lookup",
        "memories_content_tags_text",
        "memories_familiarity_candidate_unique",
    }
    assert index_names["memory_observations"] == {
        "memory_observations_observation_id_unique",
        "memory_observations_pattern_history_lookup",
    }
    assert index_names["familiarity_candidates"] == {
        "familiarity_candidates_id_unique",
        "familiarity_candidates_user_companion_pattern_unique",
        "familiarity_candidates_pending_lookup",
    }

    relationship_indexes = database.collections["relationships"].indexes
    unique_relationship_index = next(
        options
        for keys, options in relationship_indexes
        if keys == [("user_id", 1), ("companion_id", 1)]
    )
    assert unique_relationship_index["unique"] is True

    assert all(
        "expireAfterSeconds" not in options
        for collection in database.collections.values()
        for _, options in collection.indexes
    )


def test_builtin_seeding_is_idempotent_and_preserves_customizations():
    database = FakeDatabase()
    companions = database["companions"]

    initialize_memory_store(database)
    assert set(companions.documents) == {"moss", "nova", "echo", "zen"}
    assert len(BUILT_IN_COMPANIONS) == 4
    assert companions.documents["moss"]["personality_description"]

    companions.documents["moss"]["name"] = "Customized Moss"
    initialize_memory_store(database)

    assert companions.documents["moss"]["name"] == "Customized Moss"
    assert len(companions.updates) == 8
    assert all(update == {"$setOnInsert": update["$setOnInsert"]} for _, update, _ in companions.updates)
    assert all(upsert for _, _, upsert in companions.updates)