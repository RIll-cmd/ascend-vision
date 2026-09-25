import sqlite3

import pytest

from assistant.memory import MemoryStore


def test_only_approved_memory_is_retrieved_across_restart(tmp_path):
    path = tmp_path / "assistant_memory.db"
    store = MemoryStore(path)
    proposal_id = store.propose("I prefer green tea")

    assert store.search("green tea") == []
    assert store.pending()[0]["text"] == "I prefer green tea"

    store.approve(proposal_id)

    assert MemoryStore(path).search("green tea")[0]["text"] == "I prefer green tea"
    assert store.pending() == []


def test_irrelevant_memory_is_excluded_from_search(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    store.approve(store.propose("I prefer green tea"))
    store.approve(store.propose("My favorite sport is tennis"))

    assert [row["text"] for row in store.search("green tea")] == ["I prefer green tea"]


def test_reject_and_delete_remove_text_from_all_memory_queries(tmp_path):
    path = tmp_path / "memory.db"
    store = MemoryStore(path)
    rejected = store.propose("I like mango")
    assert store.reject(rejected)
    assert store.pending() == []

    approved = store.approve(store.propose("I prefer green tea"))
    assert store.delete(approved["id"])

    assert store.active() == []
    assert store.search("green tea") == []
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM memories").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM proposals").fetchone()[0] == 0


def test_edit_replaces_active_memory_and_search_index(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    memory = store.approve(store.propose("I prefer green tea"))

    edited = store.edit(memory["id"], "I prefer black coffee")

    assert edited["text"] == "I prefer black coffee"
    assert store.search("green tea") == []
    assert store.search("black coffee")[0]["id"] == memory["id"]


def test_proposals_expire_and_can_be_discarded_at_runtime_start(tmp_path):
    path = tmp_path / "memory.db"
    store = MemoryStore(path)
    expired = store.propose("I like peaches")
    with sqlite3.connect(path) as db:
        db.execute("UPDATE proposals SET expires_at='2020-01-01T00:00:00+00:00' WHERE id=?", (expired,))
    assert store.pending() == []

    store.propose("I like oranges")
    store.discard_proposals()

    assert MemoryStore(path).pending() == []


def test_enabled_setting_survives_restart_without_deleting_facts(tmp_path):
    path = tmp_path / "memory.db"
    store = MemoryStore(path)
    store.approve(store.propose("I prefer tea"))

    store.set_enabled(False)

    reopened = MemoryStore(path)
    assert not reopened.enabled()
    assert reopened.active()[0]["text"] == "I prefer tea"
    reopened.set_enabled(True)
    assert store.enabled()


@pytest.mark.parametrize("text", [
    "my password is p@ssw0rd",
    "my api key is sk-very-secret",
    "my credit card number is 4111 1111 1111 1111",
    "my bank account number is 123456789",
    "I have diabetes",
    "my home address is 123 Main Street",
])
def test_sensitive_memory_candidate_is_rejected_before_disk_write(tmp_path, text):
    path = tmp_path / "memory.db"
    store = MemoryStore(path)

    with pytest.raises(ValueError):
        store.propose(text)

    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM proposals").fetchone()[0] == 0


def test_memory_size_limit_rejects_oversized_candidate(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    with pytest.raises(ValueError):
        store.propose("x" * 501)
