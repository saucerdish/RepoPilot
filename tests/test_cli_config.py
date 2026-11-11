import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from repopilot.cli.config import configure_environment


class UserConfigurationTests(unittest.TestCase):
    def test_mode_defaults_and_flag_environment_settings_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / "repopilot"
            settings.mkdir()
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(root)}, clear=True):
                def configured(value=None):
                    args = SimpleNamespace(env_file=None, model=None, base_url=None, mode=value)
                    configure_environment(args, origin=root)
                    return args.mode

                self.assertEqual(configured(), "manual")
                (settings / "config.json").write_text('{"mode": "auto"}')
                self.assertEqual(configured(), "auto")
                os.environ["REPOPILOT_MODE"] = "manual"
                self.assertEqual(configured(), "manual")
                self.assertEqual(configured("auto"), "auto")

    def test_invalid_configured_mode_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / "repopilot"
            settings.mkdir()
            (settings / "config.json").write_text('{"mode": "all"}')
            args = SimpleNamespace(env_file=None, model=None, base_url=None)
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(root)}, clear=True):
                with self.assertRaisesRegex(ValueError, "manual or auto"):
                    configure_environment(args, origin=root)

    def test_bare_command_loads_defaults_and_referenced_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / "repopilot"
            settings.mkdir()
            (settings / "credentials.env").write_text("OPENAI_API_KEY=fixture-key\n")
            (settings / "config.json").write_text(json.dumps({
                "model": "provider/flash", "base_url": "https://provider.example/v1", "env_file": "credentials.env",
            }))
            args = SimpleNamespace(env_file=None, model=None, base_url=None)
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(root)}, clear=True):
                configure_environment(args)
                self.assertEqual(os.environ["OPENAI_API_KEY"], "fixture-key")
                self.assertEqual(os.environ["MODEL_ID"], "provider/flash")
                self.assertEqual(os.environ["OPENAI_BASE_URL"], "https://provider.example/v1")

    def test_flags_environment_and_dotenv_override_user_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "repopilot").mkdir()
            (root / "repopilot" / "config.json").write_text(json.dumps({
                "model": "user-default", "base_url": "https://default.example/v1",
            }))
            env_file = root / "explicit.env"
            env_file.write_text("MODEL_ID=dotenv-model\nOPENAI_BASE_URL=https://dotenv.example/v1\n")
            args = SimpleNamespace(env_file=env_file, model=None, base_url=None)
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(root), "MODEL_ID": "environment-model"}, clear=True):
                configure_environment(args)
                self.assertEqual(os.environ["MODEL_ID"], "environment-model")
                self.assertEqual(os.environ["OPENAI_BASE_URL"], "https://dotenv.example/v1")
                args.model = "flag-model"
                args.base_url = "https://flag.example/v1"
                configure_environment(args)
                self.assertEqual(os.environ["MODEL_ID"], "flag-model")
                self.assertEqual(os.environ["OPENAI_BASE_URL"], "https://flag.example/v1")

    def test_bad_configuration_is_reported_before_loading_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "repopilot").mkdir()
            (root / "repopilot" / "config.json").write_text('{"model": ["invalid"]}')
            args = SimpleNamespace(env_file=None, model=None, base_url=None)
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(root)}, clear=True):
                with self.assertRaisesRegex(ValueError, "nonempty string"):
                    configure_environment(args)
