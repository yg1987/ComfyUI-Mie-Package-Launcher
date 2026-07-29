import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import subprocess

from services.symlink_service import SymlinkService


class _ConfigService:
    def __init__(self, config):
        self.config = config

    def set(self, key, value):
        self.config[key] = value

    def save(self, _data=None):
        return self.config

    def get_config(self):
        return self.config.copy()


class _Services:
    def __init__(self, config):
        self.config = _ConfigService(config)


class _App:
    def __init__(self, root):
        self.config = {
            "environments": [
                {
                    "id": "env_one",
                    "name": "测试环境",
                    "comfyui_root": str(root),
                    "python_path": "python.exe",
                }
            ],
            "active_env_id": "env_one",
        }
        self.services = _Services(self.config)
        self.logger = None

    def get_active_paths(self):
        return self.config["environments"][0]


class SymlinkServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        (self.base / "ComfyUI").mkdir()
        self.app = _App(self.base)
        self.service = SymlinkService(self.app)

    def tearDown(self):
        self.temp.cleanup()

    def configure(self, key="models", target=None, enabled=True):
        target = target or (self.base / "shared" / key)
        self.service.set_rule(key, enabled=enabled, target_path=str(target))
        return Path(target)

    def test_rules_are_stored_per_environment(self):
        target = self.configure()
        stored = self.app.config["symlink_manager"]["environments"]["env_one"]
        self.assertEqual(stored["models"]["target_path"], str(target))
        self.assertTrue(stored["models"]["enabled"])

    def test_nonempty_source_is_never_removed(self):
        target = self.configure()
        target.mkdir(parents=True)
        source = self.service.source_path("models")
        source.mkdir()
        marker = source / "important-model.safetensors"
        marker.write_bytes(b"model")

        with self.assertRaisesRegex(ValueError, "仍含文件"):
            self.service.create_link("models", allow_empty_source=True)

        self.assertTrue(marker.exists())
        self.assertEqual(marker.read_bytes(), b"model")
        self.assertFalse(self.service.is_directory_link(source))

    def test_missing_source_can_be_repaired(self):
        target = self.configure()
        target.mkdir(parents=True)

        statuses = self.service.ensure_active_links(repair=True)

        self.assertEqual(statuses[0].code, "link_ok")
        self.assertTrue(self.service.is_directory_link(self.service.source_path("models")))

    def test_empty_source_can_be_safely_repaired_by_lifecycle_check(self):
        target = self.configure()
        target.mkdir(parents=True)
        source = self.service.source_path("models")
        source.mkdir()

        statuses = self.service.ensure_active_links(repair=True)

        self.assertEqual(statuses[0].code, "link_ok")
        self.assertTrue(self.service.is_directory_link(source))

    def test_empty_source_requires_explicit_permission(self):
        target = self.configure()
        target.mkdir(parents=True)
        source = self.service.source_path("models")
        source.mkdir()

        with self.assertRaisesRegex(ValueError, "需要确认"):
            self.service.create_link("models")
        self.assertTrue(source.is_dir())
        self.assertFalse(self.service.is_directory_link(source))

    def test_remove_link_preserves_target_data(self):
        target = self.configure()
        target.mkdir(parents=True)
        marker = target / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        self.service.create_link("models")

        self.service.remove_link("models")

        self.assertTrue(marker.exists())
        self.assertFalse(os.path.lexists(self.service.source_path("models")))

    def test_workflow_source_uses_documented_relative_path(self):
        expected = self.base / "ComfyUI" / "user" / "default" / "workflows"
        self.assertEqual(self.service.source_path("workflows"), expected)

    def test_rules_for_different_environments_are_isolated(self):
        first_target = self.configure()
        self.app.config["environments"].append(
            {
                "id": "env_two",
                "name": "第二环境",
                "comfyui_root": str(self.base / "other"),
                "python_path": "python.exe",
            }
        )
        self.app.config["active_env_id"] = "env_two"

        self.assertEqual(self.service.rule_config("models")["target_path"], "")
        second_target = self.base / "second-models"
        self.service.set_rule("models", enabled=True, target_path=str(second_target))
        self.app.config["active_env_id"] = "env_one"
        self.assertEqual(self.service.rule_config("models")["target_path"], str(first_target))

    def test_overlapping_source_and_target_are_rejected(self):
        source = self.service.source_path("models")
        self.service.set_rule("models", enabled=True, target_path=str(source.parent))

        status = self.service.inspect("models")

        self.assertEqual(status.code, "path_overlap")
        with self.assertRaisesRegex(ValueError, "互为父子"):
            self.service.create_link("models")

    def test_empty_source_is_restored_when_link_creation_fails(self):
        target = self.configure()
        target.mkdir(parents=True)
        source = self.service.source_path("models")
        source.mkdir()
        self.app.logger = MagicMock()

        with patch.object(
            self.service, "_create_directory_link", side_effect=OSError("expected failure")
        ):
            with self.assertRaisesRegex(OSError, "expected failure"):
                self.service.create_link("models", allow_empty_source=True)

        self.assertTrue(source.is_dir())
        self.assertFalse(self.service.is_directory_link(source))
        failure_log = " ".join(
            str(call) for call in self.app.logger.exception.call_args_list
        )
        self.assertIn("目录链接创建失败", failure_log)
        self.assertIn("source_restored", failure_log)

    @unittest.skipUnless(os.name == "nt", "Windows subprocess behavior")
    def test_windows_junction_uses_hidden_runner_with_devnull_stdin(self):
        target = self.base / "shared" / "models"
        target.mkdir(parents=True)
        source = self.service.source_path("models")
        result = MagicMock(returncode=0, stdout="", stderr="")

        with patch("services.symlink_service.run_hidden", return_value=result) as runner:
            self.service._create_directory_link(source, target)

        call = runner.call_args
        self.assertEqual(call.args[0][3], "mklink")
        self.assertIs(call.kwargs["stdin"], subprocess.DEVNULL)
        self.assertTrue(call.kwargs["capture_output"])

    def test_successful_create_writes_source_and_target_to_log(self):
        target = self.configure()
        target.mkdir(parents=True)
        self.app.logger = MagicMock()

        self.service.create_link("models")

        info_log = " ".join(str(call) for call in self.app.logger.info.call_args_list)
        self.assertIn("目录链接创建请求", info_log)
        self.assertIn(self.service.source_path("models").as_posix(), info_log)
        self.assertIn(target.as_posix(), info_log)

    def test_created_link_reports_its_real_link_type(self):
        target = self.configure()
        target.mkdir(parents=True)

        self.service.create_link("models")

        expected = "junction" if os.name == "nt" else "symlink"
        self.assertEqual(
            self.service.directory_link_type(self.service.source_path("models")),
            expected,
        )


if __name__ == "__main__":
    unittest.main()
