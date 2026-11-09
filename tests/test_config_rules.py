import os
import unittest

import config


class RulesConfigTests(unittest.TestCase):
    def setUp(self):
        self._saved = config._config
        config._config = None
        os.environ.pop("DiscordServerAudit_USER_ROLE", None)

    def tearDown(self):
        config._config = self._saved
        os.environ.pop("DiscordServerAudit_USER_ROLE", None)

    def test_rules_defaults_present_in_yaml(self):
        self.assertEqual(config.get("user_role"), "Rancher")
        self.assertEqual(config.get("rules.max_source_bytes"), 52428800)
        self.assertEqual(config.get("rules.max_citations"), 3)
        self.assertIn("pro", config.get("rules.model"))
        self.assertEqual(config.get("rules.answer_timeout_seconds"), 90)
        self.assertTrue(config.get("rules.enabled"))

    def test_env_overrides_user_role(self):
        os.environ["DiscordServerAudit_USER_ROLE"] = "Wrangler"
        self.assertEqual(config.get("user_role"), "Wrangler")

    def test_empty_env_counts_as_unset(self):
        os.environ["DiscordServerAudit_USER_ROLE"] = ""
        self.assertEqual(config.get("user_role"), "Rancher")
