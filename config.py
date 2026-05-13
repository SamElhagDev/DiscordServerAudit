import yaml
import os

_config = None

def load_config(path: str = "config.yaml") -> dict:
    global _config
    if _config is None:
        if not os.path.exists(path):
            raise FileNotFoundError(f"Config file not found at: {path}")
        with open(path, "r") as f:
            _config = yaml.safe_load(f)

        # Allow machine/CI environment variables to override config.yaml values.
        # GitHub Actions secrets set via setx take precedence at runtime
        # without requiring config.yaml to be edited per environment.
        _env_overrides = {
            "bot.token":        os.environ.get("TOKEN"),
            "admin_role":       os.environ.get("ADMIN_ROLE"),
            "log_channel_id":   int(os.environ["LOG_CHANNEL_ID"]) if os.environ.get("LOG_CHANNEL_ID") else None,
            "audit_channel_id": int(os.environ["AUDIT_CHANNEL_ID"]) if os.environ.get("AUDIT_CHANNEL_ID") else None,
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
