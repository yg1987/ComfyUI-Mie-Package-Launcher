import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

try:
    from PyQt5 import QtCore, QtWidgets
    from core.plugin_workers import PluginTaskController, PluginTaskWorker
    from services.plugin_version_service import PluginRecord, PluginState, UpdateAvailability
    from ui_qt.pages.plugin_page import PluginPage
    from ui_qt.theme_manager import ThemeManager
except ModuleNotFoundError:  # local non-GUI test interpreter
    QtCore = None
    QtWidgets = None
    PluginTaskController = None
    PluginTaskWorker = None
    PluginPage = None
    ThemeManager = None
    PluginRecord = None
    PluginState = None
    UpdateAvailability = None


@unittest.skipIf(QtCore is None, "PyQt5 is unavailable in this interpreter")
class TestPluginWorkers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_worker_is_qthread(self):
        worker = PluginTaskWorker(SimpleNamespace(scan_local=lambda: []), "scan")
        self.assertIsInstance(worker, QtCore.QThread)

    def test_controller_refuses_second_task_while_first_is_running(self):
        service = SimpleNamespace(scan_local=lambda: [])
        controller = PluginTaskController(service)
        controller._worker = SimpleNamespace(isRunning=lambda: True, request_cancel=lambda: None)

        with self.assertRaises(RuntimeError):
            controller.start_scan()

    def test_worker_scan_emits_records(self):
        observed = []
        worker = PluginTaskWorker(SimpleNamespace(scan_local=lambda: ["record"]), "scan")
        worker.finished_results.connect(observed.append)

        worker.run()

        self.assertEqual(observed, [["record"]])

    def test_results_are_emitted_only_after_worker_cleanup(self):
        """A result handler must be able to start another task safely."""
        controller = PluginTaskController(SimpleNamespace(scan_local=lambda: []))
        worker_at_result = []
        completed = []
        controller.finished.connect(
            lambda results: (
                worker_at_result.append(controller._worker),
                completed.append(results),
            )
        )

        controller.start_scan()
        deadline = time.monotonic() + 2
        while not completed and time.monotonic() < deadline:
            self.qt_app.processEvents()
            QtCore.QThread.msleep(5)

        self.assertEqual(completed, [[]])
        self.assertEqual(worker_at_result, [None])
        self.assertFalse(controller.is_busy())

    def test_empty_scan_results_render_an_empty_list_without_rescanning(self):
        page = SimpleNamespace(
            controller=SimpleNamespace(start_scan=Mock()),
            records=["stale"],
            _render_records=Mock(),
        )

        PluginPage._on_finished(page, [])

        self.assertEqual(page.records, [])
        page._render_records.assert_called_once_with(focus_table=True)
        page.controller.start_scan.assert_not_called()

    def test_refresh_moves_focus_to_table_without_stealing_search_focus(self):
        """Use a real Qt widget tree to guard the focus behavior users see."""
        service = SimpleNamespace(refresh_all=lambda: [])
        app_context = SimpleNamespace(
            services=SimpleNamespace(plugin_versions=service)
        )
        page = PluginPage(app_context, ThemeManager())
        # A truthy list suppresses the automatic first scan in showEvent; the
        # test itself is about the focus transition caused by clicking refresh.
        page.records = [object()]
        page.resize(900, 600)
        page.show()
        self.qt_app.processEvents()
        try:
            page.search.setFocus()
            self.qt_app.processEvents()
            self.assertIs(self.qt_app.focusWidget(), page.search)

            page.refresh_button.click()
            deadline = time.monotonic() + 2
            while page.controller.is_busy() and time.monotonic() < deadline:
                self.qt_app.processEvents()
                QtCore.QThread.msleep(5)

            self.qt_app.processEvents()
            self.assertFalse(page.controller.is_busy())
            self.assertIs(self.qt_app.focusWidget(), page.table)

            # Filtering should leave a user's active search box alone.
            page.search.setFocus()
            page.search.setText("plugin")
            self.qt_app.processEvents()
            self.assertIs(self.qt_app.focusWidget(), page.search)
        finally:
            page.close()

    def test_plugin_management_table_uses_readable_labels_dates_and_popup_colors(self):
        service = SimpleNamespace(refresh_all=lambda: [])
        app_context = SimpleNamespace(services=SimpleNamespace(plugin_versions=service))
        page = PluginPage(app_context, ThemeManager())
        page.records = [PluginRecord(
            "example-plugin", Path("example-plugin"), PluginState.UPDATE_AVAILABLE,
            update_availability=UpdateAvailability.AVAILABLE,
            local_commit_at="2026-07-20", remote_commit_at="2026-07-28",
            head="1234567890abcdef", branch="main",
            remote_url_display="https://github.com/example/example-plugin.git",
            can_update=True,
        )]
        try:
            page._render_records()

            self.assertEqual(page.table.columnCount(), 9)
            self.assertEqual(
                [page.table.horizontalHeaderItem(i).text() for i in range(9)],
                ["插件", "是否启用", "状态", "更新状态", "本地日期", "远端日期", "版本", "来源", "操作"],
            )
            self.assertEqual(page.table.item(0, 1).text(), "已启用")
            self.assertEqual(page.table.item(0, 2).text(), "可用")
            self.assertEqual(page.table.item(0, 3).text(), "可更新")
            self.assertEqual(page.table.item(0, 4).text(), "2026-07-20")
            self.assertEqual(page.table.item(0, 5).text(), "2026-07-28")
            self.assertIn("QAbstractItemView", page.status_filter.styleSheet())
            self.assertIn("color: #E5E7EB", page.status_filter.styleSheet())
            self.assertIn("QTableWidget::item", page.table.styleSheet())
            self.assertIn("color: #E5E7EB", page.table.styleSheet())
        finally:
            page.close()


if __name__ == "__main__":
    unittest.main()
