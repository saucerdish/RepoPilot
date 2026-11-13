"""Load non-secret user defaults and a referenced dotenv file."""

import json
import os
import tempfile
from pathlib import Path


def settings_path():
    root = Path(os.environ["XDG_CONFIG_HOME"]) if os.environ.get("XDG_CONFIG_HOME") else Path.home() / ".config"
    return root / "repopilot" / "config.json"


def configure_environment(args, origin=None):
    from dotenv import load_dotenv

    path = settings_path()
    settings = {}
    if path.is_file():
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise ValueError(f"Cannot read RepoPilot settings: {path}") from exc
        if not isinstance(settings, dict) or any(
            key in settings and (not isinstance(settings[key], str) or not settings[key].strip())
            for key in ("model", "base_url", "env_file", "theme", "mode")
        ):
            raise ValueError(f"RepoPilot settings must contain nonempty string values: {path}")

    if args.env_file:
        load_dotenv(args.env_file, override=False)
    elif settings.get("env_file"):
        env_file = Path(settings["env_file"]).expanduser()
        if not env_file.is_absolute():
            env_file = path.parent / env_file
        if not env_file.is_file():
            raise ValueError(f"Configured credentials file does not exist: {env_file}")
        load_dotenv(env_file, override=False)
    else:
        origin = Path(origin) if origin is not None else Path(__file__).resolve().parents[2]
        for directory in (origin, *origin.parents):
            candidate = directory / ".env"
            if candidate.is_file():
                load_dotenv(candidate, override=False)
                break

    for setting, name in (("model", "MODEL_ID"), ("base_url", "OPENAI_BASE_URL")):
        if settings.get(setting):
            os.environ.setdefault(name, settings[setting])
    if args.model:
        os.environ["MODEL_ID"] = args.model
    if args.base_url:
        os.environ["OPENAI_BASE_URL"] = args.base_url
    args.theme = getattr(args, "theme", None) or os.getenv("REPOPILOT_THEME") or settings.get("theme", "auto")
    if args.theme not in ("auto", "light", "dark"):
        raise ValueError("Theme must be auto, light or dark")
    args.mode = getattr(args, "mode", None) or os.getenv("REPOPILOT_MODE") or settings.get("mode", "manual")
    if args.mode not in ("manual", "auto"):
        raise ValueError("Mode must be manual or auto")


def save_theme(mode):
    if mode not in ("auto", "light", "dark"):
        raise ValueError("Theme must be auto, light or dark")
    path = settings_path()
    settings = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if not isinstance(settings, dict):
        raise ValueError(f"Invalid settings file: {path}")
    settings["theme"] = mode
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, encoding="utf-8", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(settings, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
