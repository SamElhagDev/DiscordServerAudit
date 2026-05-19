import logging
import os
import yaml

logger = logging.getLogger(__name__)
_config = None


def load_config(path: str = "config.yaml") -> dict:
    global _config
    if _config is None:
        if os.path.exists(path):
            with open(path, "r") as f:
                _config = yaml.safe_load(f) or {}
            logger.info("Config loaded from %r (%d top-level keys)", path, len(_config))
        else:
            _config = {}
            logger.warning("Config file %r not found — using defaults and env vars only", path)

        _env_overrides = {
            "bot.prefix":       os.environ.get("DiscordServerAudit_PREFIX"),
            "admin_role":       os.environ.get("DiscordServerAudit_ADMIN_ROLE"),
            "log_channel_id":   int(os.environ["DiscordServerAudit_LOG_CHANNEL_ID"]) if os.environ.get("DiscordServerAudit_LOG_CHANNEL_ID") else None,
            "audit_channel_id": int(os.environ["DiscordServerAudit_AUDIT_CHANNEL_ID"]) if os.environ.get("DiscordServerAudit_AUDIT_CHANNEL_ID") else None,
            "gemini_key":       os.environ.get("DiscordServerAudit_GEMINI_KEY"),
            "factcheck.emoji":  os.environ.get("DiscordServerAudit_FACTCHECK_EMOJI"),
        }

        applied = []
        for dotkey, value in _env_overrides.items():
            if value is not None:
                _set_nested(_config, dotkey.split("."), value)
                # Mask secret values in log output
                display = "***" if "key" in dotkey or "token" in dotkey else repr(value)
                applied.append(f"{dotkey}={display}")

        if applied:
            logger.info("Env overrides applied: %s", ", ".join(applied))
        else:
            logger.debug("No environment variable overrides applied")

    return _config


def _set_nested(d: dict, keys: list, value):
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


def get(key: str, default=None):
    """Dot-notation getter. e.g. get('intervals.security_audit')"""
    cfg = load_config()
    keys = key.split(".")
    val = cfg
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k, default)
        else:
            return default
    return val
