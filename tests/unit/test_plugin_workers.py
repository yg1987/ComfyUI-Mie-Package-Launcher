import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

try:
    from PyQt5 import QtCore
    from core.plugin_workers import PluginTaskController, PluginTaskWorker
    from ui_qt.pages.plugin_page import PluginPage
except ModuleNotFoundError:  # local non-GUI test interpreter
    QtCore = None
    PluginTaskController = None
    PluginTaskWorker = None
    PluginPage = None


@unittest.skipIf(QtCore is None, "PyQt5 is unavailable in this interpreter")
class TestPluginWorkers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])

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
        page._render_records.assert_called_once_with()
        page.controller.start_scan.assert_not_called()


if __name__ == "__main__":
    unittest.main()
