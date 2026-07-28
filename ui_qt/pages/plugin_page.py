from PyQt5 import QtCore, QtGui, QtWidgets

from core.plugin_workers import PluginTaskController
from services.plugin_version_service import (
    DependencyState,
    PluginState,
    PluginVersionService,
    UpdateAvailability,
)
from ui_qt.widgets.inputs import NoWheelComboBox
from .base_page import BasePage


_PLUGIN_STATE_LABELS = {
    PluginState.LOCAL_ONLY: "尚未检查更新",
    PluginState.UP_TO_DATE: "已是最新",
    PluginState.UPDATE_AVAILABLE: "发现新版本",
    PluginState.LOCAL_CHANGES: "检测到本地修改",
    PluginState.LOCAL_AHEAD: "本地版本更新",
    PluginState.DIVERGED: "本地与远端有差异",
    PluginState.DETACHED_HEAD: "未关联分支",
    PluginState.NO_UPSTREAM: "未设置上游分支",
    PluginState.NO_REMOTE: "未设置远程仓库",
    PluginState.NON_GIT: "非 Git 插件",
    PluginState.UNBORN_HEAD: "仓库尚未提交",
    PluginState.REPOSITORY_ERROR: "仓库异常",
    PluginState.OUTSIDE_CUSTOM_NODES: "插件目录异常",
    PluginState.NESTED_OR_EXTERNAL_REPO: "插件目录异常",
    PluginState.SUBMODULES_UNSUPPORTED: "含子模块，无法管理更新",
    PluginState.CHECK_FAILED: "检查更新失败",
    PluginState.UPDATE_FAILED: "更新失败",
    PluginState.REMOVED: "已卸载",
    PluginState.CANCELLED: "已取消",
}

_UPDATE_LABELS = {
    UpdateAvailability.UNKNOWN: "未检查",
    UpdateAvailability.CHECKING: "正在检查",
    UpdateAvailability.UP_TO_DATE: "已是最新",
    UpdateAvailability.AVAILABLE: "可更新",
    UpdateAvailability.NOT_CHECKABLE: "无法检查",
    UpdateAvailability.CHECK_FAILED: "检查失败",
}

_DEPENDENCY_BLOCKED_STATES = {
    DependencyState.INCOMPATIBLE_CONSTRAINTS_BLOCKED,
    DependencyState.DOWNGRADE_BLOCKED,
    DependencyState.GLOBAL_INDEX_INCOMPLETE,
    DependencyState.PROTECTED_PACKAGE_BLOCKED,
    DependencyState.NON_STANDARD_SOURCE_BLOCKED,
    DependencyState.SOURCE_BUILD_BLOCKED,
    DependencyState.CUSTOM_SCRIPT_BLOCKED,
    DependencyState.PREFLIGHT_FAILED,
    DependencyState.INSTALL_FAILED,
}

_UNUSABLE_STATES = {
    PluginState.REPOSITORY_ERROR,
    PluginState.OUTSIDE_CUSTOM_NODES,
    PluginState.NESTED_OR_EXTERNAL_REPO,
    PluginState.UNBORN_HEAD,
    PluginState.SUBMODULES_UNSUPPORTED,
    PluginState.UPDATE_FAILED,
}


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
        self.status_filter = NoWheelComboBox(self.theme_manager.styles)
        self.status_filter.addItem("全部", "")
        for state in PluginState:
            self.status_filter.addItem(_PLUGIN_STATE_LABELS[state], state.value)
        self.status_filter.currentIndexChanged.connect(lambda: self._reset_page())
        actions.addWidget(self.search)
        actions.addWidget(self.status_filter)
        actions.addStretch()
        layout.addLayout(actions)
        self.table = QtWidgets.QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            ["插件", "是否启用", "状态", "更新状态", "本地日期", "远端日期", "版本", "来源", "操作"]
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(7, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(8, QtWidgets.QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._apply_widget_styles()
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

    def _apply_widget_styles(self):
        """Use explicit colors because popup views do not inherit page QSS."""
        colors = self.theme_manager.colors
        self.status_filter.update_theme(self.theme_manager.styles)
        self.table.setStyleSheet(f"""
            QTableWidget {{
                background-color: {colors.get('table_bg')};
                color: {colors.get('table_text')};
                alternate-background-color: {colors.get('table_alt_bg')};
                border: 1px solid {colors.get('table_border')};
                gridline-color: {colors.get('table_border')};
                selection-background-color: {colors.get('table_selected_bg')};
                selection-color: {colors.get('table_selected_text')};
                font: 10pt "Microsoft YaHei UI";
            }}
            QTableWidget::item {{
                color: {colors.get('table_text')};
                padding: 6px;
                border-bottom: 1px solid {colors.get('table_border')};
            }}
            QTableWidget::item:selected {{
                color: {colors.get('table_selected_text')};
                background-color: {colors.get('table_selected_bg')};
            }}
            QHeaderView::section {{
                background-color: {colors.get('table_header_bg')};
                color: {colors.get('table_header_text')};
                border: 0;
                border-bottom: 1px solid {colors.get('table_header_border')};
                padding: 7px 6px;
                font: bold 10pt "Microsoft YaHei UI";
            }}
            QScrollBar:vertical {{ background: transparent; width: 8px; }}
            QScrollBar::handle:vertical {{
                background-color: {colors.get('scroll_handle')}; border-radius: 4px; min-height: 30px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

    def _on_theme_changed(self, theme_styles):
        super()._on_theme_changed(theme_styles)
        if hasattr(self, "status_filter"):
            self._apply_widget_styles()

    def showEvent(self, event):
        super().showEvent(event)
        if not self.records and not self.controller.is_busy():
            self.controller.start_scan()
        self.table.setFocus(QtCore.Qt.OtherFocusReason)

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
        # An empty scan is a valid completed listing, not a reason to start a
        # second worker while the previous one is still unwinding.
        if not results or (
            hasattr(results[0], "state")
            and not hasattr(results[0], "operation")
        ):
            self.records = list(results)
            self._render_records(focus_table=True)
        else:
            self.controller.start_scan()

    def _on_failed(self, message):
        QtWidgets.QMessageBox.warning(self, "插件管理", message)

    def _set_busy(self, busy):
        for button in (self.install_button, self.refresh_button, self.update_all_button):
            button.setEnabled(not busy)
        self.progress.setVisible(busy)
        if busy:
            self.progress.setRange(0, 0)

    def _render_records(self, focus_table=False):
        query = self.search.text().casefold()
        state = self.status_filter.currentData()
        records = [record for record in self.records if (not state or record.state.value == state) and (not query or query in record.name.casefold() or query in (record.remote_url_display or "").casefold())]
        pages = max(1, (len(records) + self.page_size - 1) // self.page_size)
        self.page = max(0, min(self.page, pages - 1))
        visible = records[self.page * self.page_size:(self.page + 1) * self.page_size]
        self.table.setRowCount(len(visible))
        for row, record in enumerate(visible):
            enabled = self._is_enabled(record)
            status_text, status_color = self._usable_status(record, enabled)
            update_text, update_color = self._update_status(record)
            values = [
                record.name,
                "已启用" if enabled else "已禁用",
                status_text,
                update_text,
                record.local_commit_at or "—",
                record.remote_commit_at or "—",
                f"{record.branch or '无分支'} · {(record.head or '—')[:12]}",
                record.remote_url_display or "本地插件",
            ]
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                if column == 1:
                    item.setForeground(QtGui.QColor("#22C55E" if enabled else self.theme_manager.colors.get("label_dim")))
                elif column == 2:
                    item.setForeground(QtGui.QColor(status_color))
                elif column == 3:
                    item.setForeground(QtGui.QColor(update_color))
                if column == 7 and record.remote_url_display:
                    item.setToolTip(record.remote_url_display)
                self.table.setItem(row, column, item)
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
            self.table.setCellWidget(row, 8, action)
        self.update_all_button.setText(f"全部更新（{sum(record.can_update for record in self.records)}）")
        self.page_label.setText(f"第 {self.page + 1} / {pages} 页，共 {len(records)} 个插件")
        self.previous_page.setEnabled(self.page > 0)
        self.next_page.setEnabled(self.page + 1 < pages)
        if focus_table:
            self.table.setFocus(QtCore.Qt.OtherFocusReason)

    @staticmethod
    def _is_enabled(record):
        return not record.path.name.endswith(".disabled")

    def _usable_status(self, record, enabled):
        if not enabled:
            return "已禁用", self.theme_manager.colors.get("label_dim", "#6B7280")
        if record.dependency_state in _DEPENDENCY_BLOCKED_STATES:
            return "需处理（依赖）", self.theme_manager.colors.get("warning", "#F59E0B")
        if record.state in _UNUSABLE_STATES:
            return "需处理", self.theme_manager.colors.get("error", "#EF4444")
        if record.state == PluginState.LOCAL_CHANGES:
            return "可用（有本地修改）", self.theme_manager.colors.get("warning", "#F59E0B")
        if record.state in {
            PluginState.NO_REMOTE,
            PluginState.NO_UPSTREAM,
            PluginState.DETACHED_HEAD,
            PluginState.NON_GIT,
        }:
            return "可用（无法检查更新）", self.theme_manager.colors.get("warning", "#F59E0B")
        return "可用", "#22C55E"

    def _update_status(self, record):
        if record.update_availability in _UPDATE_LABELS:
            label = _UPDATE_LABELS[record.update_availability]
            if record.update_availability == UpdateAvailability.AVAILABLE:
                if not record.can_update:
                    return "有新版本（需处理）", self.theme_manager.colors.get("warning", "#F59E0B")
                return label, self.theme_manager.colors.get("accent", "#6366F1")
            if record.update_availability == UpdateAvailability.UP_TO_DATE:
                return label, "#22C55E"
            if record.update_availability == UpdateAvailability.CHECK_FAILED:
                return label, self.theme_manager.colors.get("error", "#EF4444")
            if record.update_availability == UpdateAvailability.NOT_CHECKABLE:
                return label, self.theme_manager.colors.get("warning", "#F59E0B")
            return label, self.theme_manager.colors.get("label_muted", "#9CA3AF")
        return _PLUGIN_STATE_LABELS.get(record.state, "未检查"), self.theme_manager.colors.get("label_muted", "#9CA3AF")

    def _confirm_uninstall(self, record):
        if QtWidgets.QMessageBox.question(self, "确认卸载", f"永久删除插件目录：{record.path}\nPython 依赖不会删除。是否继续？") == QtWidgets.QMessageBox.Yes:
            self.controller.start_uninstall(record)
