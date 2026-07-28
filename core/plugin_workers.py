"""Qt background task wrapper for the independent plugin-management service."""

from __future__ import annotations

import threading
from typing import Sequence

from PyQt5 import QtCore

from services.plugin_version_service import PluginOperationResult, PluginRecord


class PluginTaskWorker(QtCore.QThread):
    progress = QtCore.pyqtSignal(object, int, int)
    finished_results = QtCore.pyqtSignal(object)
    install_preview_ready = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str)

    def __init__(self, service, task: str, payload=None, parent=None):
        super().__init__(parent)
        self.service = service
        self.task = task
        self.payload = payload
        self.cancel_event = threading.Event()
        self.results = None

    def _emit_results(self, results):
        self.results = results
        self.finished_results.emit(results)

    def request_cancel(self):
        self.cancel_event.set()
        try:
            self.service.cancel_active_processes()
        except Exception:
            pass

    def run(self):
        try:
            if self.cancel_event.is_set():
                self._emit_results([])
                return
            if self.task == "scan":
                records = self.service.scan_local()
                self._emit_results(records)
            elif self.task == "refresh":
                records = self.service.refresh_all()
                self._emit_results(records)
            elif self.task == "update":
                records: Sequence[PluginRecord] = self.payload or ()
                results = self.service.update_many(records)
                for index, result in enumerate(results, start=1):
                    self.progress.emit(result, index, len(results))
                self._emit_results(results)
            elif self.task == "prepare_install":
                preview = self.service.prepare_install(self.payload)
                self.install_preview_ready.emit(preview)
            elif self.task == "install":
                result = self.service.install_from_preview(self.payload)
                self.progress.emit(result, 1, 1)
                self._emit_results([result])
            elif self.task == "uninstall":
                result = self.service.uninstall_one(self.payload)
                self.progress.emit(result, 1, 1)
                self._emit_results([result])
            else:
                raise ValueError(f"未知插件任务：{self.task}")
        except Exception as exc:
            self.failed.emit(str(exc))


class PluginTaskController(QtCore.QObject):
    progress = QtCore.pyqtSignal(object, int, int)
    finished = QtCore.pyqtSignal(object)
    install_preview_ready = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str)
    busy_changed = QtCore.pyqtSignal(bool)

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self._worker = None

    def is_busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def request_cancel(self):
        if self._worker is not None:
            self._worker.request_cancel()

    def start_scan(self):
        self._start("scan")

    def start_refresh(self):
        self._start("refresh")

    def start_update(self, records):
        self._start("update", records)

    def start_prepare_install(self, source_url: str):
        self._start("prepare_install", source_url)

    def start_install(self, preview):
        self._start("install", preview)

    def start_uninstall(self, record):
        self._start("uninstall", record)

    def _start(self, task, payload=None):
        if self.is_busy():
            raise RuntimeError("插件任务正在执行")
        worker = PluginTaskWorker(self.service, task, payload, self)
        self._worker = worker
        worker.progress.connect(self.progress)
        # A result handler may immediately start another task.  Delay its
        # delivery until this QThread has finished and its reference is clear,
        # otherwise old-task cleanup can delete the new running worker.
        worker.install_preview_ready.connect(self.install_preview_ready)
        worker.failed.connect(self.failed)
        worker.finished.connect(self._clear_worker)
        self.busy_changed.emit(True)
        worker.start()

    def _clear_worker(self):
        worker = self._worker
        results = worker.results if worker is not None else None
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        self.busy_changed.emit(False)
        if results is not None:
            self.finished.emit(results)
