import unittest

from utils import rules_embeds as re_
from utils.rules_store import Citation, RulesAnswer


class RulesEmbedsTests(unittest.TestCase):
    def test_found_answer_has_citation_fields(self):
        e = re_.build_answer_embed("Catan", "Robber?", RulesAnswer("Move it on a 7.", [Citation("Almanac", "p.4 ...")], True))
        self.assertEqual(e.title, "📖 Catan")
        self.assertEqual(e.fields[0].name, "Source 1: Almanac")
        self.assertEqual(e.fields[0].value, "p.4 ...")
        self.assertEqual(e.author.name, "Q: Robber?")

    def test_not_found_answer_has_no_fields(self):
        e = re_.build_answer_embed("Catan", "Q", RulesAnswer("", [], False))
        self.assertIn("don't cover that", e.description)
        self.assertEqual(len(e.fields), 0)

    def test_long_answer_truncated_to_4096(self):
        long_cite = Citation("T" * 400, "s" * 2000)
        e = re_.build_answer_embed("G", "Q" * 500, RulesAnswer("x" * 9000, [long_cite], True))
        self.assertLessEqual(len(e.description), 4096)
        self.assertLessEqual(len(e.fields[0].name), 256)
        self.assertLessEqual(len(e.fields[0].value), 1024)
        self.assertLessEqual(len(e.author.name), 256)

    def test_total_embed_size_within_6000(self):
        cites = [Citation("T" * 200, "s" * 1024) for _ in range(3)]
        e = re_.build_answer_embed("G" * 200, "Q" * 300, RulesAnswer("x" * 4096, cites, True))
        self.assertLessEqual(len(e), 6000)
        self.assertGreaterEqual(len(e.fields), 1)

    def test_total_size_with_huge_everything(self):
        cites = [Citation("T" * 300, "s" * 2000) for _ in range(30)]
        e = re_.build_answer_embed("G" * 400, "Q" * 500, RulesAnswer("x" * 9000, cites, True))
        self.assertLessEqual(len(e), 6000)

    def test_citation_fields_capped_at_25(self):
        cites = [Citation(f"t{i}", "s") for i in range(40)]
        e = re_.build_answer_embed("G", "Q", RulesAnswer("a", cites, True))
        self.assertEqual(len(e.fields), 25)

    def test_empty_games_embed_points_to_addgame(self):
        self.assertIn("/gamerules add", re_.build_games_embed([]).description)

    def test_games_embed_lists_games(self):
        e = re_.build_games_embed([{"name": "Catan", "source_count": 2}])
        self.assertIn("**Catan** — 2 rulebook(s)", e.description)

    def test_sources_embed_formats_size(self):
        e = re_.build_sources_embed("Catan", [{"id": 7, "display_name": "a.pdf", "size_bytes": 3_145_728, "origin": "upload"}])
        self.assertIn("`#7` a.pdf — 3.0 MB (upload)", e.description)

    def test_empty_sources_embed(self):
        self.assertIn("/gamerules addbook", re_.build_sources_embed("Catan", []).description)
