"""Guards that launch and every core update path invoke symlink checks."""

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


def _method_source(path: Path, method_name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    method = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == method_name
    )
    return ast.get_source_segment(source, method)


class SymlinkLifecycleHookTests(unittest.TestCase):
    def test_process_start_repairs_and_blocks_on_attention(self):
        source = _method_source(ROOT / "core" / "process_manager.py", "start_comfyui")
        self.assertIn("ensure_active_links(repair=True)", source)
        self.assertIn("format_attention", source)
        self.assertLess(source.index("ensure_active_links"), source.index("build_launch_params"))

    def test_normal_update_checks_before_git_work(self):
        source = _method_source(ROOT / "services" / "version_service.py", "upgrade_latest")
        self.assertIn("_ensure_directory_links()", source)
        self.assertIn("SYMLINKS_NEED_ATTENTION", source)
        self.assertLess(source.index("_ensure_directory_links()"), source.index("git", source.index("_ensure_directory_links()")))

    def test_commit_switch_checks_links(self):
        source = _method_source(ROOT / "services" / "version_service.py", "upgrade_to_commit")
        self.assertIn("_ensure_directory_links()", source)

    def test_force_update_ui_rechecks_after_completion(self):
        source = _method_source(ROOT / "ui_qt" / "qt_app.py", "_force_update")
        self.assertIn("ensure_active_links(repair=True)", source)
        self.assertIn("强制更新后的软链接需要处理", source)


if __name__ == "__main__":
    unittest.main()
