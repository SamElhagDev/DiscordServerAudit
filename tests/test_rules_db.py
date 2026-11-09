"""Tests for game rules persistence (games + sources) against a temp SQLite file."""
import os
import tempfile
import unittest

import database
from database import connection


class RulesDBTestCase(unittest.TestCase):
    def setUp(self):
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._orig_db_path = connection.DB_PATH
        connection.DB_PATH = self._db_path
        database.close_db()
        database.init_db()

    def tearDown(self):
        database.close_db()
        connection.DB_PATH = self._orig_db_path
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self._db_path + suffix)
            except OSError:
                pass

    def _src(self, gid, name="r.pdf"):
        return database.add_source(gid, name, "upload", name, "fileSearchStores/a/documents/d", 10, 99)

    def test_create_and_get_game(self):
        gid = database.create_game(1, "Catan", "fileSearchStores/a", 99)
        self.assertEqual(database.get_game(1, "Catan")["id"], gid)

    def test_lookup_is_case_and_whitespace_insensitive(self):
        database.create_game(1, "Twilight  Imperium", "fileSearchStores/a", 99)
        row = database.get_game(1, "  twilight imperium ")
        self.assertIsNotNone(row)
        self.assertEqual(row["name"], "Twilight Imperium")

    def test_duplicate_name_raises(self):
        database.create_game(1, "Catan", "fileSearchStores/a", 99)
        with self.assertRaises(database.GameExistsError):
            database.create_game(1, "CATAN", "fileSearchStores/b", 99)

    def test_games_are_scoped_to_guild(self):
        database.create_game(1, "Catan", "fileSearchStores/a", 99)
        self.assertIsNone(database.get_game(2, "Catan"))
        self.assertEqual(database.list_games(2), [])
        database.create_game(2, "Catan", "fileSearchStores/b", 99)

    def test_list_games_counts_sources(self):
        gid = database.create_game(1, "Catan", "fileSearchStores/a", 99)
        self._src(gid)
        self._src(gid, "b.pdf")
        rows = database.list_games(1)
        self.assertEqual(rows[0]["source_count"], 2)

    def test_search_game_names_substring(self):
        for n in ("Catan", "Carcassonne", "Root"):
            database.create_game(1, n, "fileSearchStores/" + n, 99)
        self.assertEqual(database.search_game_names(1, "ca"), ["Carcassonne", "Catan"])
        self.assertEqual(len(database.search_game_names(1, "", limit=2)), 2)

    def test_get_source_rejects_other_games_source(self):
        a = database.create_game(1, "A", "fileSearchStores/a", 99)
        b = database.create_game(1, "B", "fileSearchStores/b", 99)
        sid = self._src(a)
        self.assertIsNotNone(database.get_source(a, sid))
        self.assertIsNone(database.get_source(b, sid))

    def test_delete_source(self):
        gid = database.create_game(1, "A", "fileSearchStores/a", 99)
        sid = self._src(gid)
        self.assertTrue(database.delete_source(sid))
        self.assertFalse(database.delete_source(sid))

    def test_delete_game_removes_sources(self):
        gid = database.create_game(1, "A", "fileSearchStores/a", 99)
        self._src(gid)
        self._src(gid, "b.pdf")
        self.assertEqual(database.delete_game(gid), 2)
        self.assertEqual(database.list_sources(gid), [])
        self.assertIsNone(database.get_game(1, "A"))


if __name__ == "__main__":
    unittest.main()
