import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pymongo.errors import ConfigurationError, ServerSelectionTimeoutError

import database
from main import health


def run_lifespan(app, during_lifespan=None):
    async def run():
        async with database.lifespan(app):
            if during_lifespan is not None:
                during_lifespan()

    asyncio.run(run())


def make_app():
    return SimpleNamespace(state=SimpleNamespace())


def test_initializes_mongodb_and_closes_client(monkeypatch):
    app = make_app()
    client = MagicMock()
    database_handle = object()
    client.__getitem__.return_value = database_handle
    client_factory = MagicMock(return_value=client)
    monkeypatch.setattr(database, "MongoClient", client_factory)
    monkeypatch.setenv("MONGODB_URI", "mongodb://example.test:27017")
    monkeypatch.setenv("MONGODB_DATABASE", "test_mirrorling")
    observed_state = {}

    def verify_started_state():
        observed_state["client"] = app.state.mongo_client
        observed_state["database"] = app.state.database
        observed_state["connected"] = app.state.mongodb_connected

    run_lifespan(app, verify_started_state)

    client_factory.assert_called_once_with(
        "mongodb://example.test:27017",
        serverSelectionTimeoutMS=database.SERVER_SELECTION_TIMEOUT_MS,
    )
    client.admin.command.assert_called_once_with("ping")
    assert observed_state == {
        "client": client,
        "database": database_handle,
        "connected": True,
    }
    client.close.assert_called_once_with()
    assert app.state.mongo_client is None
    assert app.state.database is None
    assert app.state.mongodb_connected is False


def test_unavailable_mongodb_starts_degraded_and_closes_client(
    monkeypatch,
    caplog,
):
    app = make_app()
    client = MagicMock()
    client.admin.command.side_effect = ServerSelectionTimeoutError("timeout")
    monkeypatch.setattr(database, "MongoClient", MagicMock(return_value=client))

    with caplog.at_level(logging.WARNING, logger="database"):
        run_lifespan(app)

    assert "starting in degraded mode" in caplog.text
    client.close.assert_called_once_with()
    assert app.state.mongo_client is None
    assert app.state.database is None
    assert app.state.mongodb_connected is False


def test_invalid_mongodb_configuration_starts_degraded(monkeypatch, caplog):
    app = make_app()
    client_factory = MagicMock(
        side_effect=ConfigurationError("invalid MongoDB URI")
    )
    monkeypatch.setattr(database, "MongoClient", client_factory)

    with caplog.at_level(logging.WARNING, logger="database"):
        run_lifespan(app)

    client_factory.assert_called_once_with(
        database.DEFAULT_MONGODB_URI,
        serverSelectionTimeoutMS=database.SERVER_SELECTION_TIMEOUT_MS,
    )
    assert "starting in degraded mode" in caplog.text
    assert app.state.mongo_client is None
    assert app.state.database is None
    assert app.state.mongodb_connected is False


def test_missing_mongodb_settings_use_local_defaults(monkeypatch):
    client_factory = MagicMock()
    monkeypatch.setattr(database, "MongoClient", client_factory)
    monkeypatch.delenv("MONGODB_URI", raising=False)
    monkeypatch.delenv("MONGODB_DATABASE", raising=False)

    database.create_mongo_client()

    client_factory.assert_called_once_with(
        database.DEFAULT_MONGODB_URI,
        serverSelectionTimeoutMS=database.SERVER_SELECTION_TIMEOUT_MS,
    )
    assert database.get_mongodb_database_name() == "mirrorling"


def test_client_closes_when_application_shutdown_raises(monkeypatch):
    app = make_app()
    client = MagicMock()
    monkeypatch.setattr(database, "MongoClient", MagicMock(return_value=client))

    def raise_during_lifespan():
        raise RuntimeError("application shutdown")

    with pytest.raises(RuntimeError, match="application shutdown"):
        run_lifespan(app, raise_during_lifespan)

    client.close.assert_called_once_with()


def test_memory_store_initialization_failure_keeps_app_available(
    monkeypatch,
    caplog,
):
    app = make_app()
    client = MagicMock()
    monkeypatch.setattr(database, "MongoClient", MagicMock(return_value=client))
    monkeypatch.setattr(
        database,
        "initialize_memory_store",
        MagicMock(side_effect=RuntimeError("index creation failed")),
    )
    observed_state = {}

    def verify_degraded_state():
        observed_state["mongodb_connected"] = app.state.mongodb_connected
        observed_state["memory_store_initialized"] = (
            app.state.memory_store_initialized
        )

    with caplog.at_level(logging.ERROR, logger="database"):
        run_lifespan(app, verify_degraded_state)

    assert observed_state == {
        "mongodb_connected": True,
        "memory_store_initialized": False,
    }
    assert "memory-store initialization failed" in caplog.text
    client.close.assert_called_once_with()


def test_health_reports_mongodb_disconnect_and_recovery():
    client = MagicMock()
    client.admin.command.side_effect = [
        {"ok": 1},
        ServerSelectionTimeoutError("temporarily unavailable"),
        {"ok": 1},
    ]
    app = SimpleNamespace(
        state=SimpleNamespace(
            mongo_client=client,
            mongodb_connected=False,
        )
    )
    request = SimpleNamespace(app=app)

    assert health(request) == {
        "application": "running",
        "mongodb": "connected",
    }
    assert app.state.mongodb_connected is True

    assert health(request) == {
        "application": "running",
        "mongodb": "unavailable",
    }
    assert app.state.mongodb_connected is False

    assert health(request) == {
        "application": "running",
        "mongodb": "connected",
    }
    assert app.state.mongodb_connected is True
    assert client.admin.command.call_count == 3


def test_health_reports_unavailable_without_a_mongo_client():
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                mongo_client=None,
                mongodb_connected=False,
            )
        )
    )

    assert health(request) == {
        "application": "running",
        "mongodb": "unavailable",
    }


def test_health_rejects_malformed_ping_responses():
    client = MagicMock()
    client.admin.command.side_effect = [None, {}, {"ok": 0}]
    app = SimpleNamespace(
        state=SimpleNamespace(
            mongo_client=client,
            mongodb_connected=True,
        )
    )
    request = SimpleNamespace(app=app)

    for _ in range(3):
        assert health(request) == {
            "application": "running",
            "mongodb": "unavailable",
        }
        assert app.state.mongodb_connected is False