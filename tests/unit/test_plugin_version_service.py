import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from services.plugin_version_service import DependencyPlan, DependencyState, PluginInstallPreview, PluginRecord, PluginVersionService, PluginState, UpdateAvailability


class TestPluginVersionServiceScanLocal(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.portable_root = Path(self.temp_dir.name)
        self.custom_nodes = self.portable_root / "ComfyUI" / "custom_nodes"
        self.custom_nodes.mkdir(parents=True)
        self.app = SimpleNamespace(config={"paths": {"comfyui_root": str(self.portable_root)}}, git_path="git")
        self.service = PluginVersionService(self.app)

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def _git(path: Path, *args: str):
        return subprocess.run(
            ["git", "-C", str(path), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    def _create_repo(self, name: str) -> Path:
        repo = self.custom_nodes / name
        repo.mkdir()
        self._git(repo, "init")
        self._git(repo, "config", "user.email", "tests@example.invalid")
        self._git(repo, "config", "user.name", "Plugin Service Tests")
        (repo / "README.md").write_text("plugin", encoding="utf-8")
        self._git(repo, "add", "README.md")
        self._git(repo, "commit", "-m", "initial")
        return repo

    def test_missing_custom_nodes_returns_empty_list(self):
        missing_root = self.portable_root / "other"
        app = SimpleNamespace(config={"paths": {"comfyui_root": str(missing_root)}}, git_path="git")
        self.assertEqual(PluginVersionService(app).scan_local(), [])

    def test_scans_non_git_directories_and_skips_launcher_staging(self):
        (self.custom_nodes / "plain-plugin").mkdir()
        (self.custom_nodes / ".launcher-staging").mkdir()

        records = self.service.scan_local()

        self.assertEqual([record.name for record in records], ["plain-plugin"])
        self.assertEqual(records[0].state, PluginState.NON_GIT)

    def test_scans_clean_git_plugin_without_remote(self):
        repo = self._create_repo("git-plugin")

        records = self.service.scan_local()

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.path, repo.resolve())
        self.assertEqual(record.state, PluginState.NO_REMOTE)
        self.assertIsNotNone(record.head)
        self.assertRegex(record.local_commit_at or "", r"^\d{4}-\d{2}-\d{2}$")
        self.assertFalse(record.can_check)

    def test_detects_local_changes_before_remote_state(self):
        repo = self._create_repo("dirty-plugin")
        (repo / "new-file.txt").write_text("untracked", encoding="utf-8")

        record = self.service.scan_local()[0]

        self.assertEqual(record.state, PluginState.LOCAL_CHANGES)
        self.assertEqual(record.untracked_count, 1)

    def test_rejects_nested_repository_candidate(self):
        self._git(self.custom_nodes, "init")
        self._git(self.custom_nodes, "config", "user.email", "tests@example.invalid")
        self._git(self.custom_nodes, "config", "user.name", "Plugin Service Tests")
        child = self.custom_nodes / "nested-plugin"
        child.mkdir()
        (child / "README.md").write_text("nested", encoding="utf-8")
        self._git(self.custom_nodes, "add", "nested-plugin/README.md")
        self._git(self.custom_nodes, "commit", "-m", "parent repository")

        records = self.service.scan_local()
        self.assertEqual([record.name for record in records], ["nested-plugin"])
        record = records[0]

        self.assertEqual(record.state, PluginState.NESTED_OR_EXTERNAL_REPO)

    def test_sanitizes_remote_credentials_and_query_string(self):
        self.assertEqual(
            PluginVersionService.sanitize_remote_url("https://token@github.com/owner/repo.git?token=secret#frag"),
            "https://github.com/owner/repo.git",
        )
        self.assertEqual(
            PluginVersionService.sanitize_remote_url("git@github.com:owner/repo.git"),
            "github.com:owner/repo.git",
        )

    def test_validates_https_clone_url_and_reserves_staging_path(self):
        target = self.service.validate_install_url(" https://github.com/owner/my-plugin.git ")

        self.assertEqual(target.name, "my-plugin")
        self.assertEqual(target.target_path, (self.custom_nodes / "my-plugin").resolve())
        self.assertEqual(target.staging_path.parent, (self.custom_nodes / ".launcher-staging").resolve())
        with self.assertRaises(ValueError):
            self.service.validate_install_url("https://token@github.com/owner/private.git")
        with self.assertRaises(ValueError):
            self.service.validate_install_url("https://github.com/owner/project")

    def test_clone_to_staging_uses_no_recurse_submodules(self):
        target = self.service.validate_install_url("https://github.com/owner/my-plugin.git")
        completed = subprocess.CompletedProcess([], 0, "", "")

        with patch.object(self.service, "_run_git_raw", return_value=completed) as run_git:
            result = self.service.clone_to_staging(target)

        self.assertEqual(result.outcome, "success")
        run_git.assert_called_once_with(
            "clone", "--no-recurse-submodules", target.source_url, str(target.staging_path), timeout=120
        )

    def test_git_raw_retries_invalid_windows_handle_with_explicit_streams(self):
        invalid_handle = OSError(6, "The handle is invalid")
        fallback = subprocess.CompletedProcess([], 0, "", "")

        with patch("services.plugin_version_service.run_hidden", side_effect=invalid_handle), \
             patch("services.plugin_version_service.subprocess.run", return_value=fallback) as run:
            result = self.service._run_git_raw("clone", "https://example.invalid/plugin.git", timeout=12)

        self.assertIs(result, fallback)
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["git", "clone", "https://example.invalid/plugin.git"])
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertIs(kwargs["stdout"], subprocess.PIPE)
        self.assertIs(kwargs["stderr"], subprocess.PIPE)
        self.assertEqual(kwargs["timeout"], 12)

    def test_execute_git_never_inherits_gui_standard_handles(self):
        process = Mock()
        process.communicate.return_value = ("ok", "")
        process.returncode = 0

        with patch("services.plugin_version_service.subprocess.Popen", return_value=process) as popen:
            result = self.service._execute_git(["git", "status"], timeout=10)

        self.assertEqual(result.returncode, 0)
        args, kwargs = popen.call_args
        self.assertEqual(args[0], ["git", "status"])
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertIs(kwargs["stdout"], subprocess.PIPE)
        self.assertIs(kwargs["stderr"], subprocess.PIPE)

    def test_uninstall_removes_only_a_direct_plugin_directory(self):
        plugin = self.custom_nodes / "remove-me"
        plugin.mkdir()
        (plugin / "file.txt").write_text("remove", encoding="utf-8")
        record = PluginRecord("remove-me", plugin, PluginState.NON_GIT)

        result = self.service.uninstall_one(record)

        self.assertEqual(result.outcome, "success")
        self.assertFalse(plugin.exists())

    def test_check_one_marks_clean_behind_plugin_as_update_available(self):
        repo = self._create_repo("check-plugin")
        record = PluginRecord("check-plugin", repo.resolve(), PluginState.LOCAL_ONLY, head="old", branch="main", upstream="origin/main", remote_name="origin", can_check=True)
        refreshed = PluginRecord("check-plugin", repo.resolve(), PluginState.LOCAL_ONLY, head="old", branch="main", upstream="origin/main", remote_name="origin", can_check=True)
        responses = [
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, "0\t1\n", ""),
            subprocess.CompletedProcess([], 0, "new-head\n", ""),
            subprocess.CompletedProcess([], 0, "2026-07-28T10:20:30+08:00\n", ""),
        ]
        with patch.object(self.service, "_scan_candidate", return_value=refreshed), \
             patch.object(self.service, "_run_git", side_effect=responses):
            result = self.service.check_one(record)

        self.assertEqual(result.state, PluginState.UPDATE_AVAILABLE)
        self.assertEqual(result.target_head, "new-head")
        self.assertEqual(result.update_availability, UpdateAvailability.AVAILABLE)
        self.assertEqual(result.remote_commit_at, "2026-07-28")

    def test_update_many_prepares_dependencies_before_fast_forward_merge(self):
        repo = self._create_repo("update-plugin")
        record = PluginRecord("update-plugin", repo.resolve(), PluginState.UPDATE_AVAILABLE, head="old", target_head="new", can_update=True)
        plan = DependencyPlan(repo.resolve(), "new", DependencyState.SATISFIED, (), ())
        preflight = SimpleNamespace(plans={repo.resolve(): plan})
        prepared = SimpleNamespace(forward_lock=Path("forward.lock"))
        dependencies = SimpleNamespace(
            build_global_index=lambda *args: object(),
            preflight_many=lambda *args: preflight,
            prepare_wheels=lambda *args: prepared,
            install_prepared=lambda *args: SimpleNamespace(success=True, additions=(), changes=(), message="ok"),
            cleanup=lambda *args: None,
        )
        with patch.object(self.service, "scan_local", return_value=[record]), \
             patch.object(self.service, "_dependencies", return_value=dependencies), \
             patch.object(self.service, "_run_git", return_value=subprocess.CompletedProcess([], 0, "", "")) as run_git:
            results = self.service.update_many([record])

        self.assertEqual(results[-1].outcome, "success")
        run_git.assert_called_once_with(repo.resolve(), "merge", "--ff-only", "new")

    def test_expired_install_preview_is_rejected_and_staging_is_cleaned(self):
        staging = self.custom_nodes / ".launcher-staging" / "expired"
        staging.mkdir(parents=True)
        target = self.custom_nodes / "installed-plugin"
        plan = DependencyPlan(staging, "head", DependencyState.SATISFIED, (), ())
        preview = PluginInstallPreview("https://github.com/owner/plugin.git", staging, target, "head", plan, datetime.now() - timedelta(seconds=1))

        result = self.service.install_from_preview(preview)

        self.assertEqual(result.outcome, "failed")
        self.assertFalse(staging.exists())


if __name__ == "__main__":
    unittest.main()
