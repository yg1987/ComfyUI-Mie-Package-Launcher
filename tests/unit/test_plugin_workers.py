import unittest
from types import SimpleNamespace

try:
    from PyQt5 import QtCore
    from core.plugin_workers import PluginTaskController, PluginTaskWorker
except ModuleNotFoundError:  # local non-GUI test interpreter
    QtCore = None
    PluginTaskController = None
    PluginTaskWorker = None


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


if __name__ == "__main__":
    unittest.main()
