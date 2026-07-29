"""Source-level guards for the high-risk symlink page safety copy."""

import ast
from pathlib import Path
import unittest


PAGE = Path(__file__).resolve().parents[2] / "ui_qt" / "pages" / "symlink_page.py"


class SymlinkPageContractTests(unittest.TestCase):
    def test_page_contains_required_warning_and_all_relative_paths(self):
        source = PAGE.read_text(encoding="utf-8")
        ast.parse(source)
        self.assertIn("首次创建链接前", source)
        self.assertIn("不会移动、复制或删除", source)
        self.assertIn('"models    模型', source)
        self.assertIn('"input    输入图片/文件', source)
        self.assertIn('"output    输出图片/文件', source)
        self.assertIn('"user\\\\default\\\\workflows    工作流', source)

    def test_nonempty_source_is_blocked_before_create(self):
        source = PAGE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        method = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_create_or_repair"
        )
        method_source = ast.get_source_segment(source, method)
        self.assertIn('status.code == "source_nonempty_dir"', method_source)
        self.assertIn("return", method_source)


if __name__ == "__main__":
    unittest.main()
