import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
import subprocess
from unittest.mock import patch

from services.plugin_dependency_service import (
    DependencyPreflight,
    DependencyInstallResult,
    GlobalDependencyIndex,
    PluginDependencyService,
)
from services.plugin_service import DependencyState, PluginRecord, PluginState


class TestPluginDependencyService(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.service = PluginDependencyService(SimpleNamespace(git_path="git"))

    def tearDown(self):
        self.temp_dir.cleanup()

    def _plugin(self, name: str, requirements: str) -> PluginRecord:
        path = self.root / name
        path.mkdir()
        (path / "requirements.txt").write_text(requirements, encoding="utf-8")
        return PluginRecord(name=name, path=path, state=PluginState.LOCAL_ONLY)

    def test_satisfied_requirement_keeps_existing_version(self):
        candidate = self._plugin("new-plugin", "a>=1\n")
        index = self.service.build_global_index([candidate], {})

        plan = self.service.plans_from_report(index, [candidate], {"a": "3"}, {"install": []})[candidate.path.resolve()]

        self.assertEqual(plan.state, DependencyState.SATISFIED)
        self.assertEqual(plan.resolved_changes, ())

    def test_downgrade_is_blocked_and_names_the_candidate(self):
        existing = self._plugin("existing-plugin", "a>=3\n")
        candidate = self._plugin("new-plugin", "a==1\n")
        index = self.service.build_global_index([existing, candidate], {})
        report = {"install": [{"metadata": {"name": "a", "version": "1"}}]}

        plan = self.service.plans_from_report(index, [candidate], {"a": "3"}, report)[candidate.path.resolve()]

        self.assertEqual(plan.state, DependencyState.DOWNGRADE_BLOCKED)
        self.assertTrue(any("new-plugin" in item and "当前 3，计划 1" in item for item in plan.conflicts))

    def test_upgrade_conflicting_with_existing_plugin_is_blocked_per_candidate(self):
        existing = self._plugin("existing-plugin", "a<4\n")
        candidate = self._plugin("new-plugin", "a>=4\n")
        index = self.service.build_global_index([existing, candidate], {})
        report = {"install": [{"metadata": {"name": "a", "version": "4"}}]}

        plan = self.service.plans_from_report(index, [candidate], {"a": "3"}, report)[candidate.path.resolve()]

        self.assertEqual(plan.state, DependencyState.INCOMPATIBLE_CONSTRAINTS_BLOCKED)
        self.assertIn("existing-plugin", " ".join(plan.conflicts))

    def test_requirements_include_stays_inside_plugin_directory(self):
        candidate = self._plugin("plugin", "-r nested/requirements.txt\n")
        nested = candidate.path / "nested"
        nested.mkdir()
        (nested / "requirements.txt").write_text("b>=1\n", encoding="utf-8")

        index = self.service.build_global_index([candidate], {})

        declaration = index.declaration_for(candidate.path)
        self.assertTrue(declaration.is_complete)
        self.assertEqual([str(item.requirement) for item in declaration.requirements], ["b>=1"])

    def test_protected_package_change_is_blocked(self):
        candidate = self._plugin("new-plugin", "torch>=3\n")
        index = self.service.build_global_index([candidate], {})
        report = {"install": [{"metadata": {"name": "torch", "version": "3"}}]}

        plan = self.service.plans_from_report(index, [candidate], {"torch": "2"}, report)[candidate.path.resolve()]

        self.assertEqual(plan.state, DependencyState.PROTECTED_PACKAGE_BLOCKED)

    def test_preflight_uses_pip_dry_run_report_without_installing(self):
        candidate = self._plugin("new-plugin", "a>=1\n")
        index = self.service.build_global_index([candidate], {})
        commands = []

        def fake_run(command, timeout):
            commands.append(command)
            if "--report" in command:
                Path(command[command.index("--report") + 1]).write_text('{"install": []}', encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch.object(self.service, "_installed_versions", return_value={"a": "3"}), \
             patch.object(self.service, "_pip_check", return_value=()), \
             patch.object(self.service, "_python_exec", return_value="python"), \
             patch.object(self.service, "_run", side_effect=fake_run):
            preflight = self.service.preflight_many(index, [candidate])
        try:
            self.assertEqual(preflight.plans[candidate.path.resolve()].state, DependencyState.SATISFIED)
            self.assertTrue(any("--dry-run" in command and "--report" in command for command in commands))
        finally:
            self.service.cleanup(preflight)

    def test_failed_offline_install_restores_the_previously_changed_package(self):
        candidate = self._plugin("new-plugin", "a>=4\n")
        index = self.service.build_global_index([candidate], {})
        report = {"install": [{"metadata": {"name": "a", "version": "4"}}]}
        plans = self.service.plans_from_report(index, [candidate], {"a": "3"}, report)
        work_dir = self.root / "work"
        work_dir.mkdir()
        preflight = DependencyPreflight(index, plans, {"a": "3"}, {"a": "4"}, (), report, work_dir)

        def fake_download(wheelhouse, specification):
            wheel = wheelhouse / (specification.replace("==", "-") + ".whl")
            wheel.write_bytes(specification.encode("utf-8"))
            return wheel

        with patch.object(self.service, "_download_wheel", side_effect=fake_download):
            batch = self.service.prepare_wheels(preflight)
        self.assertNotIsInstance(batch, DependencyInstallResult)
        calls = []

        def fake_run(command, timeout):
            calls.append(command)
            return subprocess.CompletedProcess(command, 1 if len(calls) == 1 else 0, "", "install failed")

        with patch.object(self.service, "_python_exec", return_value="python"), \
             patch.object(self.service, "_run", side_effect=fake_run), \
             patch.object(self.service, "_validation_has_no_new_errors", return_value=True):
            result = self.service.install_prepared(batch)
        self.assertFalse(result.success)
        self.assertTrue(result.rolled_back)
        self.assertGreaterEqual(len(calls), 2)
        self.service.cleanup(preflight)


if __name__ == "__main__":
    unittest.main()
