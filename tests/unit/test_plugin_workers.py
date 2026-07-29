import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

try:
    from PyQt5 import QtCore, QtWidgets
    from core.plugin_workers import PluginTaskController, PluginTaskWorker
    from services.plugin_version_service import DependencyState, PluginRecord, PluginState, UpdateAvailability
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
    DependencyState = None


@unittest.skipIf(QtCore is None, "PyQt5 is unavailable in this interpreter")
class TestPluginWorkers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_worker_is_qthread(self):
        worker = PluginTaskWorker(SimpleNamespace(load_cached_records=lambda: []), "scan")
        self.assertIsInstance(worker, QtCore.QThread)

    def test_controller_refuses_second_task_while_first_is_running(self):
        service = SimpleNamespace(load_cached_records=lambda: [])
        controller = PluginTaskController(service)
        controller._worker = SimpleNamespace(isRunning=lambda: True, request_cancel=lambda: None)

        with self.assertRaises(RuntimeError):
            controller.start_scan()

    def test_worker_scan_emits_records(self):
        observed = []
        worker = PluginTaskWorker(SimpleNamespace(load_cached_records=lambda: ["record"]), "scan")
        worker.finished_results.connect(observed.append)

        worker.run()

        self.assertEqual(observed, [["record"]])

    def test_worker_update_emits_each_result_as_it_is_reported(self):
        records = [SimpleNamespace(name="first"), SimpleNamespace(name="second")]
        first = SimpleNamespace(plugin_name="first", outcome="success")
        second = SimpleNamespace(plugin_name="second", outcome="failed")

        def update_many(_records, on_progress):
            on_progress(first, 1, 2)
            on_progress(second, 2, 2)
            return [first, second]

        worker = PluginTaskWorker(SimpleNamespace(update_many=update_many), "update", records)
        observed = []
        worker.progress.connect(lambda result, current, total: observed.append((result, current, total)))

        worker.run()

        self.assertEqual(observed, [(first, 1, 2), (second, 2, 2)])

    def test_results_are_emitted_only_after_worker_cleanup(self):
        """A result handler must be able to start another task safely."""
        controller = PluginTaskController(SimpleNamespace(load_cached_records=lambda: []))
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

            self.assertEqual(page.table.columnCount(), 8)
            self.assertEqual(
                [page.table.horizontalHeaderItem(i).text() for i in range(8)],
                ["插件", "是否启用", "状态", "更新状态", "本地日期", "远端日期", "来源", "操作"],
            )
            self.assertEqual(page.table.item(0, 1).text(), "已启用")
            self.assertEqual(page.table.item(0, 2).text(), "● 正常")
            self.assertEqual(page.table.item(0, 3).text(), "● 可更新")
            self.assertGreater(page.table.item(0, 2).background().color().alpha(), 0)
            self.assertGreater(page.table.item(0, 3).background().color().alpha(), 0)
            self.assertEqual(page.table.item(0, 4).text(), "2026-07-20")
            self.assertEqual(page.table.item(0, 5).text(), "2026-07-28")
            self.assertEqual(
                page.table.item(0, 6).text().replace("\n", ""),
                "https://github.com/example/example-plugin.git",
            )
            self.assertIn("QAbstractItemView", page.status_filter.styleSheet())
            self.assertIn("color: #E5E7EB", page.status_filter.styleSheet())
            self.assertIn("QTableWidget::item", page.table.styleSheet())
            self.assertIn("color: #E5E7EB", page.table.styleSheet())
        finally:
            page.close()

    def test_copyable_details_hides_help_button_and_keeps_text_readable(self):
        service = SimpleNamespace(refresh_all=lambda: [])
        app_context = SimpleNamespace(services=SimpleNamespace(plugin_versions=service))
        page = PluginPage(app_context, ThemeManager())
        observed = {}

        def inspect_dialog():
            dialog = self.qt_app.activeModalWidget()
            observed["dialog"] = dialog
            observed["has_help_button"] = bool(
                dialog.windowFlags() & QtCore.Qt.WindowContextHelpButtonHint
            )
            text = dialog.findChild(QtWidgets.QPlainTextEdit)
            observed["text"] = text.toPlainText()
            observed["style"] = text.styleSheet()
            dialog.accept()

        try:
            QtCore.QTimer.singleShot(0, inspect_dialog)
            page._show_copyable_details("结果", "")

            self.assertIsNotNone(observed.get("dialog"))
            self.assertFalse(observed["has_help_button"])
            self.assertEqual(observed["text"], "没有可显示的详细信息。")
            self.assertIn("color: #E5E7EB", observed["style"])
        finally:
            page.close()

    def test_status_detail_contains_full_reason_and_processing_guidance(self):
        service = SimpleNamespace(refresh_all=lambda: [])
        app_context = SimpleNamespace(services=SimpleNamespace(plugin_versions=service))
        page = PluginPage(app_context, ThemeManager())
        record = PluginRecord(
            "example-plugin", Path("example-plugin"), PluginState.LOCAL_CHANGES,
            update_availability=UpdateAvailability.NOT_CHECKABLE,
            dependency_state=DependencyState.PREFLIGHT_FAILED,
            reason="检测到未提交的 workflow.py 修改。",
            dependency_reason="requirements.txt 中的依赖需要人工确认。",
            dependency_additions=("numpy==2.0",),
            dependency_changes=("torch: 2.0 → 2.1",),
            dependency_strict_constraints=("comfyui-core<1.0",),
            dependency_conflicts=("torch==2.0 与当前环境不兼容",),
            dirty_count=1,
            untracked_count=2,
        )
        try:
            details = page._status_details_text(record)

            self.assertIn("检测到未提交的 workflow.py 修改。", details)
            self.assertIn("torch==2.0 与当前环境不兼容", details)
            self.assertIn("依赖预检：preflight_failed", details)
            self.assertIn("numpy==2.0", details)
            self.assertIn("torch: 2.0 → 2.1", details)
            self.assertIn("comfyui-core<1.0", details)
            self.assertIn("处理建议：", details)
            self.assertIn("依赖预检未通过", details)
        finally:
            page.close()

    def test_source_click_copies_full_git_url_and_zero_update_has_feedback(self):
        service = SimpleNamespace(refresh_all=lambda: [])
        app_context = SimpleNamespace(services=SimpleNamespace(plugin_versions=service))
        page = PluginPage(app_context, ThemeManager())
        source = "https://github.com/example/example-plugin.git"
        page.records = [PluginRecord(
            "example-plugin", Path("example-plugin"), PluginState.UP_TO_DATE,
            update_availability=UpdateAvailability.UP_TO_DATE,
            remote_url_display=source,
        )]
        try:
            page._render_records()
            page._on_table_cell_clicked(0, 6)
            self.assertEqual(self.qt_app.clipboard().text(), source)
            self.assertEqual(page.feedback_label.text(), "地址已复制")

            page._update_all()
            self.assertEqual(page.feedback_label.text(), "所有已检查的插件均已是最新版本。")
        finally:
            page.close()

    def test_zero_update_before_refresh_explains_that_a_check_is_required(self):
        service = SimpleNamespace(refresh_all=lambda: [])
        app_context = SimpleNamespace(services=SimpleNamespace(plugin_versions=service))
        page = PluginPage(app_context, ThemeManager())
        page.records = [PluginRecord(
            "example-plugin", Path("example-plugin"), PluginState.LOCAL_ONLY,
            update_availability=UpdateAvailability.UNKNOWN,
            remote_url_display="https://github.com/example/example-plugin.git",
        )]
        try:
            page._update_all()

            self.assertEqual(page.feedback_label.text(), "尚有 1 个插件未检查更新；请先点击“刷新”。")
        finally:
            page.close()

    def test_compact_confirmation_does_not_create_a_details_editor(self):
        service = SimpleNamespace(refresh_all=lambda: [])
        app_context = SimpleNamespace(services=SimpleNamespace(plugin_versions=service))
        page = PluginPage(app_context, ThemeManager())
        observed = {}

        def inspect_dialog():
            dialog = self.qt_app.activeModalWidget()
            observed["dialog"] = dialog
            observed["details_editor"] = dialog.findChild(QtWidgets.QPlainTextEdit)
            observed["message"] = dialog.findChild(QtWidgets.QLabel).text()
            dialog.reject()

        try:
            QtCore.QTimer.singleShot(0, inspect_dialog)
            accepted = page._confirm_action(
                "确认卸载插件", "将永久删除插件目录。", "卸载插件", destructive=True
            )

            self.assertFalse(accepted)
            self.assertIsNotNone(observed.get("dialog"))
            self.assertIsNone(observed["details_editor"])
            self.assertEqual(observed["message"], "将永久删除插件目录。")
        finally:
            page.close()

    def test_filter_empty_state_and_operation_feedback(self):
        service = SimpleNamespace(refresh_all=lambda: [])
        app_context = SimpleNamespace(services=SimpleNamespace(plugin_versions=service))
        page = PluginPage(app_context, ThemeManager())
        record = PluginRecord(
            "example-plugin", Path("example-plugin"), PluginState.UP_TO_DATE,
            update_availability=UpdateAvailability.UP_TO_DATE,
        )
        result = SimpleNamespace(
            plugin_name="example-plugin",
            outcome="success",
            state=PluginState.UP_TO_DATE,
            previous_head="before",
            current_head="after",
            dependencies_added=("example==1.0",),
            dependencies_changed=(),
            message="插件与依赖已更新",
        )
        failed_result = SimpleNamespace(
            plugin_name="failed-plugin",
            outcome="failed",
            state=PluginState.UPDATE_FAILED,
            previous_head="before",
            current_head="before",
            dependencies_added=(),
            dependencies_changed=(),
            message="Git fast-forward 失败",
        )
        try:
            page.records = [record]
            page.status_filter.setCurrentIndex(1)  # 可更新
            page._render_records()
            self.assertIs(page._table_stack.currentWidget(), page.empty_state)
            self.assertIn("没有符合", page.empty_state_label.text())

            page._on_progress(result, 1, 2)
            self.assertEqual(page.feedback_label.text(), "正在处理 1/2：example-plugin（成功）")

            page._show_copyable_details = Mock()
            page._show_operation_results("update", [result, failed_result], refreshed=True)
            self.assertEqual(page.feedback_label.text(), "更新结果已完成：成功 1 项，失败 1 项。")
            page._show_copyable_details.assert_called_once()
            details = page._show_copyable_details.call_args.args[1]
            self.assertIn("插件列表与状态已刷新", details)
            self.assertIn("插件与依赖已更新", details)
            self.assertIn("Git fast-forward 失败", details)
        finally:
            page.close()

    def test_single_and_batch_update_show_start_feedback(self):
        service = SimpleNamespace(refresh_all=lambda: [])
        app_context = SimpleNamespace(services=SimpleNamespace(plugin_versions=service))
        page = PluginPage(app_context, ThemeManager())
        record = PluginRecord(
            "example-plugin", Path("example-plugin"), PluginState.UPDATE_AVAILABLE,
            update_availability=UpdateAvailability.AVAILABLE,
            can_update=True,
        )
        try:
            page.controller.start_update = Mock()
            page._start_update_one(record)
            self.assertEqual(page.feedback_label.text(), "正在更新插件：example-plugin…")
            page.controller.start_update.assert_called_once_with([record])

            page.controller.start_update.reset_mock()
            page.records = [record]
            page._confirm_with_details = Mock(return_value=True)
            page._update_all()
            self.assertEqual(page.feedback_label.text(), "正在更新 1 个插件…")
            page._confirm_with_details.assert_called_once()
            self.assertIn("example-plugin", page._confirm_with_details.call_args.args[1])
            page.controller.start_update.assert_called_once_with([record])
        finally:
            page.close()

    def test_update_button_is_always_visible_and_reports_when_current(self):
        service = SimpleNamespace(refresh_all=lambda: [])
        app_context = SimpleNamespace(services=SimpleNamespace(plugin_versions=service))
        page = PluginPage(app_context, ThemeManager())
        record = PluginRecord(
            "example-plugin", Path("example-plugin"), PluginState.UP_TO_DATE,
            update_availability=UpdateAvailability.UP_TO_DATE,
        )
        try:
            page.records = [record]
            page.controller.start_update = Mock()
            page._render_records()

            actions = page.table.cellWidget(0, 7).layout()
            self.assertEqual(actions.itemAt(0).widget().text(), "更新")
            self.assertEqual(actions.itemAt(1).widget().text(), "卸载")

            actions.itemAt(0).widget().click()
            self.assertEqual(page.feedback_label.text(), "example-plugin 已是最新版本，无需更新。")
            page.controller.start_update.assert_not_called()
        finally:
            page.close()

    def test_completed_update_refreshes_remote_status(self):
        service = SimpleNamespace(refresh_all=lambda: [])
        app_context = SimpleNamespace(services=SimpleNamespace(plugin_versions=service))
        page = PluginPage(app_context, ThemeManager())
        result = SimpleNamespace(
            operation="update", plugin_name="example-plugin", outcome="success"
        )
        try:
            page._active_operation = "update"
            page._show_operation_results = Mock()
            page._start_refresh = Mock()
            page._start_scan = Mock()

            page._on_finished([result])

            page._show_operation_results.assert_not_called()
            page._start_refresh.assert_called_once()
            page._start_scan.assert_not_called()

            # 结果框在刷新结果到达后才显示，关闭它不会触发新的后台任务。
            page._active_operation = "refresh"
            page._on_finished([])
            page._show_operation_results.assert_called_once_with(
                "update", [result], refreshed=True
            )
        finally:
            page.close()

    def test_completed_install_refreshes_before_showing_results(self):
        service = SimpleNamespace(refresh_all=lambda: [])
        app_context = SimpleNamespace(services=SimpleNamespace(plugin_versions=service))
        page = PluginPage(app_context, ThemeManager())
        result = SimpleNamespace(
            operation="install", plugin_name="example-plugin", outcome="success"
        )
        try:
            page._active_operation = "install"
            page._show_operation_results = Mock()
            page._start_refresh = Mock()

            page._on_finished([result])

            page._show_operation_results.assert_not_called()
            page._start_refresh.assert_called_once()
        finally:
            page.close()

    def test_busy_state_disables_row_operations(self):
        service = SimpleNamespace(refresh_all=lambda: [])
        app_context = SimpleNamespace(services=SimpleNamespace(plugin_versions=service))
        page = PluginPage(app_context, ThemeManager())
        page.records = [PluginRecord(
            "example-plugin", Path("example-plugin"), PluginState.UPDATE_AVAILABLE,
            update_availability=UpdateAvailability.AVAILABLE,
            can_update=True,
        )]
        try:
            page._render_records()
            self.assertTrue(all(button.isEnabled() for button in page._operation_buttons))
            page._set_busy(True)
            self.assertTrue(all(not button.isEnabled() for button in page._operation_buttons))
            page._set_busy(False)
            self.assertTrue(all(button.isEnabled() for button in page._operation_buttons))
        finally:
            page.close()

    def test_cached_startup_load_hides_progress_and_explains_remote_is_stale(self):
        service = SimpleNamespace(load_cached_records=lambda: [], last_cached_at=None)
        app_context = SimpleNamespace(services=SimpleNamespace(plugin_versions=service))
        page = PluginPage(app_context, ThemeManager())
        record = PluginRecord("example-plugin", Path("example-plugin"), PluginState.LOCAL_ONLY)
        try:
            page._active_operation = "scan"
            page._set_busy(True)
            self.assertFalse(page.progress.isVisible())

            page._on_finished([record])

            self.assertIn("尚未检查远端更新", page.feedback_label.text())
        finally:
            page.close()


if __name__ == "__main__":
    unittest.main()
