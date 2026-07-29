import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from PyQt5 import QtWidgets

from ui_qt.pages.symlink_page import SymlinkPage
from services.symlink_service import LinkStatus
from ui_qt.widgets.dialog_helper import DialogHelper
from ui_qt.theme_manager import ThemeManager


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
                    "id": "env_page",
                    "name": "页面测试",
                    "comfyui_root": str(root),
                    "python_path": "python.exe",
                }
            ],
            "active_env_id": "env_page",
        }
        self.services = _Services(self.config)
        self.process_manager = None
        self.logger = None

    def get_active_paths(self):
        return self.config["environments"][0]


class SymlinkPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "ComfyUI").mkdir()
        self.theme = ThemeManager(dark=True)
        self.page = SymlinkPage(_App(self.root), self.theme)

    def tearDown(self):
        self.page.deleteLater()
        self.temp.cleanup()

    def test_warning_is_first_and_lists_all_source_paths(self):
        layout = self.page.layout()
        self.assertIs(layout.itemAt(1).widget(), self.page._warning)
        text = self.page._warning_text.text()
        for path in ("models", "input", "output", "user\\default\\workflows"):
            self.assertIn(path, text)
        self.assertIn("Windows 资源管理器可能仍显示为普通文件夹", text)
        self.assertIn("正常 · 目录联接 → 实际目标路径", text)

    def test_page_exposes_four_independent_target_inputs(self):
        self.assertEqual(set(self.page._rows), {"models", "input", "output", "workflows"})
        inputs = [row["target"] for row in self.page._rows.values()]
        self.assertEqual(len({id(widget) for widget in inputs}), 4)

    def test_theme_switch_keeps_warning_style_valid_shape(self):
        self.page.update_theme()
        self.assertIn("#3B2A12", self.page._warning.styleSheet())
        self.assertIn("#F59E0B", self.page._warning.styleSheet())
        self.assertIn("#FDE68A", self.page._warning_text.styleSheet())

        self.theme.set_theme(False)
        self.page.update_theme()
        style = self.page._warning.styleSheet()
        self.assertEqual(style.count("{"), style.count("}"))
        self.assertIn("#D97706", style)
        self.assertIn("#FFF7ED", style)
        self.assertIn("#7C2D12", self.page._warning_text.styleSheet())

    def test_statuses_use_distinct_semantic_colors(self):
        status = self.page._rows["models"]["status"]

        self.page._style_status(status, "link_ok")
        self.assertIn("#34D399", status.styleSheet())
        self.page._style_status(status, "source_missing")
        self.assertIn("#60A5FA", status.styleSheet())
        self.page._style_status(status, "target_missing")
        self.assertIn("#F59E0B", status.styleSheet())
        self.page._style_status(status, "error")
        self.assertIn("#F87171", status.styleSheet())

    def test_check_all_reports_when_nothing_is_enabled(self):
        with patch.object(DialogHelper, "show_info") as show_info:
            self.page._check_all()

        show_info.assert_called_once()
        self.assertEqual(show_info.call_args.args[1], "检查完成")
        self.assertIn("没有启用", show_info.call_args.args[2])

    def test_healthy_junction_status_names_type_and_target(self):
        source = self.page.service.source_path("models")
        target = self.root / "shared" / "models"
        status = LinkStatus("models", "模型", "link_ok", source, target)

        with patch.object(self.page.service, "inspect", return_value=status), patch.object(
            self.page.service, "directory_link_type", return_value="junction"
        ):
            self.page._refresh_one("models")

        text = self.page._rows["models"]["status"].text()
        self.assertEqual(text, f"正常 · 目录联接 → {target}")
        self.assertEqual(self.page._rows["models"]["status"].toolTip(), text)


if __name__ == "__main__":
    unittest.main()
