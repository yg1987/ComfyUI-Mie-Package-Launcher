from PyQt5 import QtCore, QtWidgets

from core.plugin_workers import PluginTaskController
from services.plugin_version_service import PluginVersionService, PluginState
from .base_page import BasePage


class PluginPage(BasePage):
    """Independent custom-node plugin management page."""

    def __init__(self, app, theme_manager, parent=None):
        self.app = app
        super().__init__(theme_manager, parent)
        service = getattr(getattr(app, "services", None), "plugin_versions", None) or PluginVersionService(app)
        self.controller = PluginTaskController(service, self)
        self.records = []
        self.page = 0
        self.page_size = 20
        self._setup_ui()
        self.controller.finished.connect(self._on_finished)
        self.controller.failed.connect(self._on_failed)
        self.controller.busy_changed.connect(self._set_busy)
        self.controller.install_preview_ready.connect(self._on_preview)

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel("插件版本管理"))
        layout.addWidget(QtWidgets.QLabel("管理 ComfyUI/custom_nodes 中的 GitHub 插件；更新前请停止 ComfyUI。"))
        install_row = QtWidgets.QHBoxLayout()
        self.url_input = QtWidgets.QLineEdit()
        self.url_input.setPlaceholderText("https://github.com/owner/repo.git")
        self.install_button = QtWidgets.QPushButton("安装插件")
        self.install_button.clicked.connect(lambda: self.controller.start_prepare_install(self.url_input.text()))
        install_row.addWidget(self.url_input)
        install_row.addWidget(self.install_button)
        layout.addLayout(install_row)
        actions = QtWidgets.QHBoxLayout()
        self.refresh_button = QtWidgets.QPushButton("刷新")
        self.update_all_button = QtWidgets.QPushButton("全部更新")
        self.refresh_button.clicked.connect(self.controller.start_refresh)
        self.update_all_button.clicked.connect(self._update_all)
        actions.addWidget(self.refresh_button)
        actions.addWidget(self.update_all_button)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("搜索插件或远程地址")
        self.search.textChanged.connect(lambda: self._reset_page())
        self.status_filter = QtWidgets.QComboBox()
        self.status_filter.addItem("全部", "")
        for state in PluginState:
            self.status_filter.addItem(state.value, state.value)
        self.status_filter.currentIndexChanged.connect(lambda: self._reset_page())
        actions.addWidget(self.search)
        actions.addWidget(self.status_filter)
        actions.addStretch()
        layout.addLayout(actions)
        self.table = QtWidgets.QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["插件", "分支/提交", "远程", "代码状态", "依赖状态", "原因", "操作"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        layout.addWidget(self.table)
        pages = QtWidgets.QHBoxLayout()
        self.previous_page = QtWidgets.QPushButton("上一页")
        self.next_page = QtWidgets.QPushButton("下一页")
        self.page_label = QtWidgets.QLabel()
        self.previous_page.clicked.connect(lambda: self._change_page(-1))
        self.next_page.clicked.connect(lambda: self._change_page(1))
        pages.addWidget(self.previous_page); pages.addWidget(self.next_page); pages.addWidget(self.page_label); pages.addStretch()
        layout.addLayout(pages)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

    def showEvent(self, event):
        super().showEvent(event)
        if not self.records and not self.controller.is_busy():
            self.controller.start_scan()

    def _update_all(self):
        eligible = [record for record in self.records if record.can_update]
        if eligible:
            if QtWidgets.QMessageBox.question(self, "确认批量更新", f"将更新 {len(eligible)} 个依赖预检通过的插件。是否继续？") == QtWidgets.QMessageBox.Yes:
                self.controller.start_update(eligible)

    def _reset_page(self):
        self.page = 0
        self._render_records()

    def _change_page(self, delta):
        self.page += delta
        self._render_records()

    def _on_preview(self, preview):
        answer = QtWidgets.QMessageBox.question(self, "确认安装", f"安装 {preview.target_path.name} 并处理其依赖？")
        if answer == QtWidgets.QMessageBox.Yes:
            self.controller.start_install(preview)

    def _on_finished(self, results):
        if results and hasattr(results[0], "state") and not hasattr(results[0], "operation"):
            self.records = results
            self._render_records()
        elif not self.controller.is_busy():
            self.controller.start_scan()

    def _on_failed(self, message):
        QtWidgets.QMessageBox.warning(self, "插件管理", message)

    def _set_busy(self, busy):
        for button in (self.install_button, self.refresh_button, self.update_all_button):
            button.setEnabled(not busy)
        self.progress.setVisible(busy)
        if busy:
            self.progress.setRange(0, 0)

    def _render_records(self):
        query = self.search.text().casefold()
        state = self.status_filter.currentData()
        records = [record for record in self.records if (not state or record.state.value == state) and (not query or query in record.name.casefold() or query in (record.remote_url_display or "").casefold())]
        pages = max(1, (len(records) + self.page_size - 1) // self.page_size)
        self.page = max(0, min(self.page, pages - 1))
        visible = records[self.page * self.page_size:(self.page + 1) * self.page_size]
        self.table.setRowCount(len(visible))
        for row, record in enumerate(visible):
            values = [record.name, f"{record.branch or 'detached'} · {(record.head or '')[:12]}", record.remote_url_display or "—", record.state.value, record.dependency_state.value, record.dependency_reason or record.reason]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QtWidgets.QTableWidgetItem(value))
            action = QtWidgets.QWidget(); actions = QtWidgets.QHBoxLayout(action); actions.setContentsMargins(0, 0, 0, 0)
            if record.can_update:
                button = QtWidgets.QPushButton("更新")
                button.clicked.connect(lambda _=False, item=record: self.controller.start_update([item]))
                actions.addWidget(button)
            elif record.dependency_conflicts or record.dependency_reason:
                button = QtWidgets.QPushButton("原因")
                button.clicked.connect(lambda _=False, item=record: QtWidgets.QMessageBox.information(self, "依赖原因", "\n".join(item.dependency_conflicts) or item.dependency_reason))
                actions.addWidget(button)
            remove = QtWidgets.QPushButton("卸载")
            remove.clicked.connect(lambda _=False, item=record: self._confirm_uninstall(item))
            actions.addWidget(remove)
            self.table.setCellWidget(row, 6, action)
        self.update_all_button.setText(f"全部更新（{sum(record.can_update for record in self.records)}）")
        self.page_label.setText(f"第 {self.page + 1} / {pages} 页，共 {len(records)} 个插件")
        self.previous_page.setEnabled(self.page > 0)
        self.next_page.setEnabled(self.page + 1 < pages)

    def _confirm_uninstall(self, record):
        if QtWidgets.QMessageBox.question(self, "确认卸载", f"永久删除插件目录：{record.path}\nPython 依赖不会删除。是否继续？") == QtWidgets.QMessageBox.Yes:
            self.controller.start_uninstall(record)
