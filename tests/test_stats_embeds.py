"""Tests for the stats presentation helpers.

These were private functions inside cogs/stats.py and unreachable without importing the cog
(and therefore discord + Gemini). Extracting them to utils/ is what makes this file possible.
"""
import unittest

from utils import stats_embeds as se


class FormatDurationTests(unittest.TestCase):
    def test_zero_and_negative_render_as_a_dash(self):
        self.assertEqual(se.format_duration(0), "—")
        self.assertEqual(se.format_duration(-5), "—")

    def test_under_an_hour_renders_minutes(self):
        self.assertEqual(se.format_duration(45), "45m")

    def test_a_whole_number_of_hours_omits_minutes(self):
        self.assertEqual(se.format_duration(120), "2h")

    def test_hours_and_minutes_render_together(self):
        self.assertEqual(se.format_duration(90), "1h 30m")

    def test_seconds_are_converted_to_whole_minutes(self):
        self.assertEqual(se.format_seconds(3600), "1h")
        self.assertEqual(se.format_seconds(59), "—")


class TrendIndicatorTests(unittest.TestCase):
    def test_growth_from_zero_is_reported_as_new(self):
        self.assertIn("new", se.trend_indicator(10, 0))

    def test_no_activity_either_side_is_flat(self):
        self.assertIn("0%", se.trend_indicator(0, 0))

    def test_an_increase_reports_the_percentage(self):
        self.assertIn("50.0%", se.trend_indicator(150, 100))

    def test_a_decrease_reports_an_absolute_percentage(self):
        result = se.trend_indicator(50, 100)
        self.assertIn("50.0%", result)
        self.assertNotIn("-", result)

    def test_colour_follows_the_direction_of_travel(self):
        self.assertEqual(se.embed_color_for_trend(2, 1), se.COLOR_POSITIVE)
        self.assertEqual(se.embed_color_for_trend(1, 2), se.COLOR_NEGATIVE)
        self.assertEqual(se.embed_color_for_trend(1, 1), se.COLOR_NEUTRAL)


class SparklineTests(unittest.TestCase):
    def test_no_values_render_as_an_empty_string(self):
        self.assertEqual(se.sparkline([]), "")

    def test_one_bar_is_emitted_per_value(self):
        self.assertEqual(len(se.sparkline([1, 2, 3, 4])), 4)

    def test_only_the_most_recent_values_are_shown(self):
        self.assertEqual(len(se.sparkline(list(range(100)), width=10)), 10)

    def test_a_flat_series_does_not_divide_by_zero(self):
        self.assertEqual(len(se.sparkline([5, 5, 5])), 3)


class HeatmapTests(unittest.TestCase):
    def test_no_data_renders_an_empty_bar(self):
        self.assertEqual(se.build_heatmap_bar([], width=24), "░" * 24)

    def test_the_bar_is_the_requested_width(self):
        data = [{"hour": h, "count": h} for h in range(24)]
        self.assertEqual(len(se.build_heatmap_bar(data, width=24)), 24)

    def test_the_busiest_hour_gets_the_tallest_block(self):
        data = [{"hour": 3, "count": 100}, {"hour": 4, "count": 1}]
        self.assertEqual(se.build_heatmap_bar(data, width=24)[3], "█")

    def test_the_offset_rotates_the_bar(self):
        data = [{"hour": 0, "count": 100}]
        self.assertEqual(se.build_heatmap_bar(data, width=24, offset=5)[5], "█")

    def test_out_of_range_hours_are_ignored(self):
        self.assertEqual(se.build_heatmap_bar([{"hour": 99, "count": 5}], width=24), "░" * 24)


class BarChartTests(unittest.TestCase):
    def test_no_items_render_a_no_data_block(self):
        self.assertIn("No data", se.build_bar_chart([]))

    def test_each_item_gets_its_own_line(self):
        chart = se.build_bar_chart([("a", 10), ("b", 5)])

        self.assertEqual(len([ln for ln in chart.splitlines() if "█" in ln or "░" in ln]), 2)

    def test_percentages_are_shown_when_requested(self):
        self.assertIn("%", se.build_bar_chart([("a", 1), ("b", 1)], show_pct=True))

    def test_all_zero_values_do_not_divide_by_zero(self):
        self.assertIn("a", se.build_bar_chart([("a", 0), ("b", 0)]))


class HealthScoreTests(unittest.TestCase):
    def test_an_empty_server_scores_low(self):
        score = se.composite_health_score({})

        self.assertLessEqual(score, 40)

    def test_a_thriving_server_approaches_the_maximum(self):
        score = se.composite_health_score({
            "dau_mau": 1.0, "reaction_per_msg": 0.5, "churn_rate": 0.0,
            "voice_rate": 1.0, "net_growth": 10,
        })

        self.assertEqual(score, 100)

    def test_the_score_never_exceeds_one_hundred(self):
        score = se.composite_health_score({
            "dau_mau": 99, "reaction_per_msg": 99, "churn_rate": -99,
            "voice_rate": 99, "net_growth": 9999,
        })

        self.assertLessEqual(score, 100)

    def test_labels_span_the_whole_range(self):
        labels = [se.health_label(s) for s in (0, 30, 50, 70, 100)]

        self.assertEqual(labels, ["Critical", "Needs Attention", "Average", "Healthy", "Thriving"])


class GiniTests(unittest.TestCase):
    def test_no_values_are_perfectly_equal(self):
        self.assertEqual(se.gini_coefficient([]), 0.0)

    def test_all_zero_values_are_perfectly_equal(self):
        self.assertEqual(se.gini_coefficient([0, 0, 0]), 0.0)

    def test_an_even_distribution_scores_near_zero(self):
        self.assertAlmostEqual(se.gini_coefficient([10, 10, 10, 10]), 0.0, places=6)

    def test_concentration_in_one_user_scores_higher_than_an_even_split(self):
        self.assertGreater(se.gini_coefficient([0, 0, 0, 100]),
                           se.gini_coefficient([25, 25, 25, 25]))


class LabelTests(unittest.TestCase):
    def test_a_split_with_no_data_is_not_available(self):
        self.assertEqual(se.weekday_weekend_label(0, 0), "N/A")

    def test_an_even_split_reads_fifty_fifty(self):
        self.assertEqual(se.weekday_weekend_label(50, 50), "50% / 50%")

    def test_sqlite_weekday_zero_is_sunday(self):
        self.assertEqual(se.day_name(0), "Sun")

    def test_weekday_numbers_wrap(self):
        self.assertEqual(se.day_name(7), "Sun")


class EasternTimeTests(unittest.TestCase):
    def test_the_offset_is_one_of_the_two_eastern_offsets(self):
        offset, abbr = se.et_offset()

        self.assertIn(offset, (-4, -5))
        self.assertIn(abbr, ("EDT", "EST"))

    def test_an_hour_renders_as_a_labelled_clock_time(self):
        rendered = se.utc_hour_to_et(12)

        self.assertRegex(rendered, r"^\d{2}:00 E[DS]T$")

    def test_every_utc_hour_converts(self):
        for hour in range(24):
            self.assertRegex(se.utc_hour_to_et(hour), r"^\d{2}:00 E[DS]T$")


if __name__ == "__main__":
    unittest.main()
