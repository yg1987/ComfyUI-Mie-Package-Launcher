import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from services.plugin_version_service import (
    DependencyState,
    PluginRecord,
    PluginState,
    PluginVersionService,
    UpdateAvailability,
)


class TestPluginStatusCache(unittest.TestCase):
    def _app(self, base, env_id="env_one"):
        return SimpleNamespace(
            config={"active_env_id": env_id},
            config_manager=SimpleNamespace(config_file=base / "launcher" / "config.json"),
            get_active_paths=lambda: {"comfyui_root": str(base)},
        )

    def test_startup_uses_cache_without_running_git(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "ComfyUI" / "custom_nodes"
            (root / "cached-plugin").mkdir(parents=True)
            (root / "new-plugin.disabled").mkdir()
            app = self._app(base)
            service = PluginVersionService(app)
            cached = PluginRecord(
                "cached-plugin", root / "cached-plugin", PluginState.UPDATE_AVAILABLE,
                update_availability=UpdateAvailability.AVAILABLE,
                dependency_state=DependencyState.SAFE_TO_INSTALL,
                local_commit_at="2026-07-20", remote_commit_at="2026-07-28",
                remote_url_display="https://github.com/example/cached-plugin.git",
                can_check=True, can_update=True,
            )
            service._status_cache.save("env_one", root, [cached])
            service._run_git = Mock(side_effect=AssertionError("startup must not call Git"))

            records = service.load_cached_records()

            self.assertEqual([record.name for record in records], ["cached-plugin", "new-plugin.disabled"])
            self.assertEqual(records[0].update_availability, UpdateAvailability.AVAILABLE)
            self.assertTrue(records[0].can_update)
            self.assertEqual(records[1].state, PluginState.LOCAL_ONLY)
            self.assertIsNotNone(service.last_cached_at)
            service._run_git.assert_not_called()

    def test_cache_is_not_reused_for_another_environment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "ComfyUI" / "custom_nodes"
            (root / "example-plugin").mkdir(parents=True)
            first = PluginVersionService(self._app(base, "env_one"))
            first._status_cache.save(
                "env_one", root,
                [PluginRecord("example-plugin", root / "example-plugin", PluginState.UP_TO_DATE)],
            )
            second = PluginVersionService(self._app(base, "env_two"))

            records = second.load_cached_records()

            self.assertEqual(records[0].state, PluginState.LOCAL_ONLY)
            self.assertIsNone(second.last_cached_at)
