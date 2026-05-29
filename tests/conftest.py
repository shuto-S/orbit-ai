from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.config.loader import load_proactive_config, load_profile
from app.memory.store import MemoryStore
from app.session.manager import SessionManager
from tests.helpers.fakes import FakeResponseAgent


@pytest.fixture
def mvp_context() -> tuple[MemoryStore, SessionManager]:
    with tempfile.TemporaryDirectory() as tempdir:
        db_path = Path(tempdir) / "test.sqlite3"
        store = MemoryStore(db_path)
        manager = SessionManager(
            load_profile(),
            load_proactive_config(),
            store,
            response_agent=FakeResponseAgent(),  # type: ignore[arg-type]
        )
        yield store, manager
