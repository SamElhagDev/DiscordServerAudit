import yaml
import os

_config = None

def load_config(path: str = "config.yaml") -> dict:
    global _config
    if _config is None:
        if os.path.exists(path):
            with open(path, "r") as f:
                _config = yaml.safe_load(f) or {}
        else:
            _config = {}

        # Allow machine/CI environment variables to override config.yaml values.
        # GitHub Actions secrets set via setx take precedence at runtime
        # without requiring config.yaml to be edited per environment.
        _env_overrides = {
            "bot.prefix":       os.environ.get("DiscordServerAudit_PREFIX"),
            "admin_role":       os.environ.get("DiscordServerAudit_ADMIN_ROLE"),
            "log_channel_id":   int(os.environ["DiscordServerAudit_LOG_CHANNEL_ID"]) if os.environ.get("DiscordServerAudit_LOG_CHANNEL_ID") else None,
            "audit_channel_id": int(os.environ["DiscordServerAudit_AUDIT_CHANNEL_ID"]) if os.environ.get("DiscordServerAudit_AUDIT_CHANNEL_ID") else None,
            "gemini_key":       os.environ.get("DiscordServerAudit_GEMINI_KEY"),
        }

        for dotkey, value in _env_overrides.items():
            if value is not None:
                _set_nested(_config, dotkey.split("."), value)

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
