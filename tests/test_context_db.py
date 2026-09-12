"""Tests for the fact-check context store + embedding sidecar (features 005/007).

``database`` imports only the stdlib, so these run against a real temporary SQLite
file — no mocks, no Discord, no Gemini.
"""
import os
import tempfile
import unittest

import numpy as np

import database
from database import connection


class ContextDBTestCase(unittest.TestCase):
    """Base case: each test gets a fresh temporary database."""

    def setUp(self):
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        # Set on the connection module, not the package: the connection layer reads its own
        # module global, so rebinding the re-exported `database.DB_PATH` would do nothing.
        self._orig_db_path = connection.DB_PATH
        connection.DB_PATH = self._db_path
        database.close_db()  # drop any connection cached for this thread
        database.init_db()

    def tearDown(self):
        database.close_db()
        connection.DB_PATH = self._orig_db_path
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self._db_path + suffix)
            except OSError:
                pass

    # -- fixtures ------------------------------------------------------------

    def _add_context(self, guild_id, channel_id, message_id, content,
                     recorded_at="2026-01-01T00:00:00") -> int:
        """Store one context message and return its message_context.id."""
        database.log_context_message(
            guild_id, channel_id, message_id, 1, "alice", content,
            recorded_at=recorded_at,
        )
        with database.get_conn() as conn:
            return conn.execute(
                "SELECT id FROM message_context WHERE message_id = ?", (message_id,)
            ).fetchone()["id"]

    def _add_embedding(self, row_id, model="gemini-embedding-001", dim=4):
        blob = np.ones(dim, dtype="<f4").tobytes()
        database.upsert_embeddings([(row_id, model, dim, blob)])


class GuildScopingTests(ContextDBTestCase):
    """Semantic hydration must not cross guild boundaries (the bm25 tier never does)."""

    def test_hydration_excludes_rows_from_other_guilds(self):
        mine = self._add_context(111, 10, 1001, "my guild message")
        theirs = self._add_context(222, 20, 2002, "other guild message")

        rows = database.get_context_messages_by_ids([mine, theirs], 111)

        self.assertEqual([r["id"] for r in rows], [mine])

    def test_hydration_returns_all_requested_rows_within_one_guild(self):
        first = self._add_context(111, 10, 1001, "first")
        second = self._add_context(111, 10, 1002, "second")

        rows = database.get_context_messages_by_ids([first, second], 111)

        self.assertEqual(sorted(r["id"] for r in rows), sorted([first, second]))

    def test_hydration_of_an_empty_id_list_is_empty(self):
        self.assertEqual(database.get_context_messages_by_ids([], 111), [])


class PendingModelScopingTests(ContextDBTestCase):
    """Changing model/dim must make already-embedded rows pending again."""

    def test_rows_embedded_under_a_different_model_are_pending_again(self):
        row = self._add_context(111, 10, 1001, "hello world")
        self._add_embedding(row, model="old-model", dim=4)

        pending = database.get_pending_context_rows(10, "new-model", 4)

        self.assertEqual([r["id"] for r in pending], [row])

    def test_rows_embedded_under_a_different_dimension_are_pending_again(self):
        row = self._add_context(111, 10, 1001, "hello world")
        self._add_embedding(row, model="same-model", dim=4)

        pending = database.get_pending_context_rows(10, "same-model", 8)

        self.assertEqual([r["id"] for r in pending], [row])

    def test_rows_embedded_under_the_active_model_are_not_pending(self):
        row = self._add_context(111, 10, 1001, "hello world")
        self._add_embedding(row, model="active", dim=4)

        pending = database.get_pending_context_rows(10, "active", 4)

        self.assertEqual(pending, [])

    def test_never_embedded_rows_are_pending(self):
        row = self._add_context(111, 10, 1001, "hello world")

        pending = database.get_pending_context_rows(10, "active", 4)

        self.assertEqual([r["id"] for r in pending], [row])

    def test_blank_content_rows_are_never_pending(self):
        self._add_context(111, 10, 1001, "   ")

        self.assertEqual(database.get_pending_context_rows(10, "active", 4), [])

    def test_count_embeddings_is_scoped_to_the_active_model(self):
        row = self._add_context(111, 10, 1001, "hello world")
        self._add_embedding(row, model="old-model", dim=4)

        self.assertEqual(database.count_embeddings(111, "new-model", 4), 0)
        self.assertEqual(database.count_embeddings(111, "old-model", 4), 1)

    def test_count_embeddings_is_scoped_to_the_guild(self):
        mine = self._add_context(111, 10, 1001, "mine")
        theirs = self._add_context(222, 20, 2002, "theirs")
        self._add_embedding(mine, model="m", dim=4)
        self._add_embedding(theirs, model="m", dim=4)

        self.assertEqual(database.count_embeddings(111, "m", 4), 1)


class LoadEmbeddingsTests(ContextDBTestCase):
    def test_only_vectors_for_the_active_model_and_dim_are_loaded(self):
        active = self._add_context(111, 10, 1001, "active")
        stale = self._add_context(111, 10, 1002, "stale")
        self._add_embedding(active, model="active", dim=4)
        self._add_embedding(stale, model="stale", dim=4)

        rows = database.load_all_embeddings("active", 4)

        self.assertEqual([r["message_context_id"] for r in rows], [active])

    def test_upsert_is_idempotent_on_the_primary_key(self):
        row = self._add_context(111, 10, 1001, "hello")
        self._add_embedding(row, model="m", dim=4)
        self._add_embedding(row, model="m", dim=4)

        self.assertEqual(database.count_embeddings(111, "m", 4), 1)


class PruneTests(ContextDBTestCase):
    """Behaviour contract for prune_message_context (harness for the SQL rewrite)."""

    def test_per_channel_cap_keeps_only_the_newest_rows(self):
        for i in range(5):
            self._add_context(111, 10, 1000 + i, f"msg {i}",
                              recorded_at=f"2026-01-0{i + 1}T00:00:00")

        deleted = database.prune_message_context(0, 2)

        with database.get_conn() as conn:
            kept = [r["message_id"] for r in conn.execute(
                "SELECT message_id FROM message_context ORDER BY message_id"
            ).fetchall()]
        self.assertEqual(deleted, 3)
        self.assertEqual(kept, [1003, 1004])

    def test_per_channel_cap_is_applied_per_channel(self):
        for i in range(3):
            self._add_context(111, 10, 1000 + i, f"a {i}",
                              recorded_at=f"2026-01-0{i + 1}T00:00:00")
        for i in range(3):
            self._add_context(111, 20, 2000 + i, f"b {i}",
                              recorded_at=f"2026-01-0{i + 1}T00:00:00")

        database.prune_message_context(0, 1)

        with database.get_conn() as conn:
            kept = sorted(r["message_id"] for r in conn.execute(
                "SELECT message_id FROM message_context"
            ).fetchall())
        self.assertEqual(kept, [1002, 2002])

    def test_zero_cap_and_zero_retention_delete_nothing(self):
        self._add_context(111, 10, 1001, "keep me")

        self.assertEqual(database.prune_message_context(0, 0), 0)
        self.assertEqual(database.count_message_context(111), 1)

    def test_retention_deletes_rows_older_than_the_cutoff(self):
        self._add_context(111, 10, 1001, "ancient", recorded_at="2000-01-01T00:00:00")
        self._add_context(111, 10, 1002, "fresh", recorded_at="2999-01-01T00:00:00")

        database.prune_message_context(30, 0)

        with database.get_conn() as conn:
            kept = [r["message_id"] for r in conn.execute(
                "SELECT message_id FROM message_context"
            ).fetchall()]
        self.assertEqual(kept, [1002])

    def test_pruning_a_row_also_drops_its_embedding(self):
        row = self._add_context(111, 10, 1001, "ancient", recorded_at="2000-01-01T00:00:00")
        self._add_embedding(row, model="m", dim=4)

        database.prune_message_context(30, 0)

        self.assertEqual(database.count_embeddings(111, "m", 4), 0)


if __name__ == "__main__":
    unittest.main()
