import logging
import os
from contextlib import asynccontextmanager

from pymongo import MongoClient
from pymongo.errors import PyMongoError

from memory_store import initialize_memory_store


logger = logging.getLogger(__name__)

DEFAULT_MONGODB_URI = "mongodb://127.0.0.1:27017"
DEFAULT_MONGODB_DATABASE = "mirrorling"
SERVER_SELECTION_TIMEOUT_MS = 5000


def create_mongo_client() -> MongoClient:
    return MongoClient(
        os.getenv("MONGODB_URI", DEFAULT_MONGODB_URI),
        serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS,
    )


def get_mongodb_database_name() -> str:
    return os.getenv("MONGODB_DATABASE", DEFAULT_MONGODB_DATABASE)


@asynccontextmanager
async def lifespan(app):
    client = None
    app.state.mongo_client = None
    app.state.database = None
    app.state.mongodb_connected = False
    app.state.memory_store_initialized = False

    try:
        client = create_mongo_client()
        client.admin.command("ping")
        app.state.mongo_client = client
        app.state.database = client[get_mongodb_database_name()]
        app.state.mongodb_connected = True
        logger.info("Connected to MongoDB.")
        try:
            initialize_memory_store(app.state.database)
            app.state.memory_store_initialized = True
        except Exception:
            logger.exception(
                "MongoDB memory-store initialization failed; "
                "continuing with degraded functionality."
            )
    except PyMongoError:
        logger.warning(
            "MongoDB is unavailable; starting in degraded mode."
        )
        if client is not None:
            client.close()
            client = None

    try:
        yield
    finally:
        if client is not None:
            client.close()
        app.state.mongo_client = None
        app.state.database = None
        app.state.mongodb_connected = False
        app.state.memory_store_initialized = False