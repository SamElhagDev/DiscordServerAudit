import unittest
from types import SimpleNamespace

from utils import rules_embeds as re_
from utils.rules_store import Citation, RulesAnswer


def _book(i, name="a.pdf", size=3_145_728, origin="upload"):
    return {"id": i, "display_name": name, "size_bytes": size, "origin": origin}


class AnswerCardTests(unittest.TestCase):
    def test_card_has_question_title_answer_sources_and_footer(self):
        cites = [Citation("Living Rules", ["q1", "q2"]), Citation("FAQ", ["q3"])]
        e = re_.build_answer_embed("TI4", "Can I retreat?", RulesAnswer("Yes.", cites, True), asked_by="Sam")
        self.assertEqual(e.title, "❓ Can I retreat?")
        self.assertEqual(e.description, "Yes.")
        self.assertEqual([(f.name, f.value) for f in e.fields], [("Sources", "📚 Living Rules · FAQ")])
        self.assertEqual(e.footer.text, "🎲 TI4 · asked by Sam")
        self.assertEqual(e.color, re_.GOLD)

    def test_card_does_not_include_quotes(self):
        e = re_.build_answer_embed("G", "Q", RulesAnswer("A", [Citation("B", ["secret quote"])], True))
        self.assertNotIn("secret quote", str(e.to_dict()))

    def test_not_found_is_grey_without_fields(self):
        e = re_.build_answer_embed("Catan", "Q", RulesAnswer("", [], False))
        self.assertIn("don't cover that", e.description)
        self.assertEqual(len(e.fields), 0)
        self.assertEqual(e.color, re_.GREY)

    def test_huge_answer_fits_discord_limits(self):
        cites = [Citation("T" * 300, ["s" * 2000]) for _ in range(30)]
        e = re_.build_answer_embed("G" * 400, "Q" * 500, RulesAnswer("x" * 9000, cites, True),
                                   asked_by="U" * 100)
        self.assertLessEqual(len(e), 6000)
        self.assertLessEqual(len(e.title), 256)
        self.assertLessEqual(len(e.description), 4096)
        self.assertLessEqual(len(e.fields[0].value), 1024)


class SourcesDetailTests(unittest.TestCase):
    def test_one_field_per_book_with_all_quotes(self):
        cites = [Citation("Living Rules", ["Retreat at the start of a round.", "Defender announces first."]),
                 Citation("FAQ", ["Retreating ships move to an adjacent system."])]
        e = re_.build_sources_detail_embed("TI4", "Retreat?", cites)
        self.assertEqual([f.name for f in e.fields], ["📄 Living Rules", "📄 FAQ"])
        self.assertEqual(e.fields[0].value,
                         "> Retreat at the start of a round.\n> Defender announces first.")
        self.assertEqual(e.author.name, "❓ Retreat?")

    def test_quotes_that_do_not_fit_are_dropped_whole(self):
        e = re_.build_sources_detail_embed("G", "Q", [Citation("Book", ["a" * 600, "b" * 600])])
        self.assertEqual(e.fields[0].value, "> " + "a" * 600)

    def test_single_long_quote_is_shortened(self):
        e = re_.build_sources_detail_embed("G", "Q", [Citation("Book", ["z" * 3000])])
        self.assertLessEqual(len(e.fields[0].value), 1024)
        self.assertTrue(e.fields[0].value.startswith("> zzz"))

    def test_fields_capped_and_total_within_6000(self):
        e = re_.build_sources_detail_embed("G" * 300, "Q" * 500,
                                           [Citation(f"t{i}" * 50, ["s" * 900] * 5) for i in range(40)])
        self.assertLessEqual(len(e.fields), 25)
        self.assertLessEqual(len(e), 6000)

    def test_no_citations(self):
        self.assertIn("No quoted passages", re_.build_sources_detail_embed("G", "Q", []).description)


class ListTests(unittest.TestCase):
    def test_empty_games_points_to_add(self):
        self.assertIn("/gamerules add", re_.build_games_embed([]).description)

    def test_games_listed_with_counts_and_footer(self):
        e = re_.build_games_embed([{"name": "Catan", "source_count": 2}, {"name": "Risk", "source_count": 1}])
        self.assertEqual(e.description, "**Catan** · 2 rulebooks\n**Risk** · 1 rulebook")
        self.assertTrue(e.footer.text.startswith("2 games"))

    def test_books_show_size_origin_and_download_state(self):
        rows = [_book(1, "Base.pdf"), _book(2, "FAQ", 524_288, "url"), _book(3, "Old.pdf")]
        e = re_.build_sources_embed("Catan", rows, {1: "file", 2: "link", 3: "none"})
        self.assertEqual(e.title, "📚 Catan")
        lines = e.description.split("\n")
        self.assertEqual(lines[0], "📄 **Base.pdf**")
        self.assertIn("3.0 MB · upload", lines[1])
        self.assertNotIn("download", lines[1])
        self.assertIn("🔗 **FAQ**", e.description)
        self.assertIn("0.5 MB · link\n", e.description)
        self.assertIn("⚠️ **Old.pdf**", e.description)
        self.assertIn("re-add to download", e.description)
        self.assertIn("3 rulebooks", e.footer.text)

    def test_tiny_file_size(self):
        e = re_.build_sources_embed("G", [_book(1, size=10)], {1: "file"})
        self.assertIn("<0.1 MB", e.description)

    def test_empty_books_points_to_addbook(self):
        self.assertIn("/gamerules addbook", re_.build_sources_embed("Catan", []).description)


class NoticeTests(unittest.TestCase):
    def _result(self, oks):
        items = [SimpleNamespace(label=f"f{i}.pdf", ok=ok, detail="too big") for i, ok in enumerate(oks)]
        return SimpleNamespace(game_name="Catan", items=items, total=3)

    def test_submission_all_ok_is_green(self):
        e = re_.build_submission_embed(self._result([True, True]))
        self.assertEqual(e.color, re_.GREEN)
        self.assertEqual(e.description, "✅ f0.pdf\n✅ f1.pdf")
        self.assertEqual(e.footer.text, "All added · 3 rulebooks in total")

    def test_submission_partial_is_orange_with_reason(self):
        e = re_.build_submission_embed(self._result([True, False]))
        self.assertEqual(e.color, re_.ORANGE)
        self.assertIn("❌ f1.pdf\n too big", e.description)
        self.assertTrue(e.footer.text.startswith("1 added, 1 failed"))

    def test_submission_all_failed_is_red(self):
        self.assertEqual(re_.build_submission_embed(self._result([False])).color, re_.RED)

    def test_submission_long_list_truncated(self):
        e = re_.build_submission_embed(self._result([True] * 400))
        self.assertLessEqual(len(e.description), 4096)
        self.assertIn("more", e.description)

    def test_error_embed_strips_duplicate_cross(self):
        self.assertEqual(re_.error_embed("❌ No game named **X**.").description, "❌ No game named **X**.")
        self.assertEqual(re_.error_embed("Oops").description, "❌ Oops")
        self.assertEqual(re_.error_embed("x").color, re_.RED)


if __name__ == "__main__":
    unittest.main()
