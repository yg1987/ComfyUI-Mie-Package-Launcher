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
    PluginState.CHECK_FAILED,
    PluginState.UPDATE_FAILED,
}

_ATTENTION_STATES = {
    PluginState.LOCAL_CHANGES,
    PluginState.LOCAL_AHEAD,
    PluginState.DIVERGED,
    PluginState.DETACHED_HEAD,
    PluginState.NO_UPSTREAM,
    PluginState.NO_REMOTE,
    PluginState.NON_GIT,
}


_STATE_GUIDANCE = {
    PluginState.LOCAL_CHANGES: "请先提交、暂存或还原本地修改，再重新检查更新。",
    PluginState.LOCAL_AHEAD: "本地提交尚未推送到远端。请确认是否需要推送或保留本地版本。",
    PluginState.DIVERGED: "本地与远端各有提交。请先处理 Git 合并或变基冲突，再更新。",
    PluginState.DETACHED_HEAD: "当前不在分支上。请切换到要跟踪的分支并设置上游分支。",
    PluginState.NO_UPSTREAM: "请为当前分支设置上游分支，然后重新检查更新。",
    PluginState.NO_REMOTE: "仓库没有远程地址。请重新克隆插件，或配置 origin 和上游分支。",
    PluginState.NON_GIT: "这不是 Git 仓库，无法检查或自动更新。请手动维护该插件。",
    PluginState.UNBORN_HEAD: "仓库尚无首次提交。请检查插件目录或重新安装插件。",
    PluginState.REPOSITORY_ERROR: "仓库状态异常。请查看完整错误信息，并修复仓库或重新安装插件。",
    PluginState.OUTSIDE_CUSTOM_NODES: "插件目录不在 custom_nodes 中，不能由此页面管理更新。",
    PluginState.NESTED_OR_EXTERNAL_REPO: "检测到嵌套或外部仓库。请确认目录结构后再处理。",
    PluginState.SUBMODULES_UNSUPPORTED: "插件包含子模块，当前不能安全地自动更新。请按插件说明手动更新。",
    PluginState.CHECK_FAILED: "检查远端更新失败。请查看完整错误信息并确认网络和 Git 配置。",
    PluginState.UPDATE_FAILED: "更新失败。请查看完整错误信息；修复后可重新检查或更新。",
    PluginState.CANCELLED: "操作已取消；如仍需更新，请重新执行操作。",
}


class PluginPage(BasePage):
    """Independent custom-node plugin management page."""

    def __init__(self, app, theme_manager, parent=None):
        self.app = app
        super().__init__(theme_manager, parent)
        service = getattr(getattr(app, "services", None), "plugin_versions", None) or PluginVersionService(app)
        self.controller = PluginTaskController(service, self)
        self.records = []
        self._visible_records = []
        self._operation_buttons = []
        self._active_operation = None
        self.page = 0
        self.page_size = 20
        self._setup_ui()
        self.controller.finished.connect(self._on_finished)
        self.controller.failed.connect(self._on_failed)
        self.controller.busy_changed.connect(self._set_busy)
        self.controller.install_preview_ready.connect(self._on_preview)
        self.controller.progress.connect(self._on_progress)

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel("插件版本管理"))
        layout.addWidget(QtWidgets.QLabel("管理 ComfyUI/custom_nodes 中的 GitHub 插件；更新前请停止 ComfyUI。"))
        install_row = QtWidgets.QHBoxLayout()
        self.url_input = QtWidgets.QLineEdit()
        self.url_input.setPlaceholderText("https://github.com/owner/repo.git")
        self.install_button = QtWidgets.QPushButton("安装插件")
        self.install_button.clicked.connect(self._prepare_install)
        install_row.addWidget(self.url_input)
        install_row.addWidget(self.install_button)
        layout.addLayout(install_row)
        actions = QtWidgets.QHBoxLayout()
        self.refresh_button = QtWidgets.QPushButton("刷新")
        self.update_all_button = QtWidgets.QPushButton("全部更新")
        self.refresh_button.clicked.connect(self._start_refresh)
        self.update_all_button.clicked.connect(self._update_all)
        actions.addWidget(self.refresh_button)
        actions.addWidget(self.update_all_button)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("搜索插件或远程地址")
        self.search.textChanged.connect(lambda: self._reset_page())
        self.status_filter = NoWheelComboBox(self.theme_manager.styles)
        self.status_filter.addItem("全部", "all")
        self.status_filter.addItem("可更新", "updatable")
        self.status_filter.addItem("异常 / 需处理", "attention")
        self.status_filter.currentIndexChanged.connect(lambda: self._reset_page())
        actions.addWidget(self.search)
        actions.addWidget(self.status_filter)
        actions.addStretch()
        layout.addLayout(actions)
        self.feedback_label = QtWidgets.QLabel()
        self.feedback_label.setWordWrap(True)
        self.feedback_label.setVisible(False)
        layout.addWidget(self.feedback_label)
        self.table = QtWidgets.QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["插件", "是否启用", "状态", "更新状态", "本地日期", "远端日期", "来源", "操作"]
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(7, QtWidgets.QHeaderView.Fixed)
        self.table.setColumnWidth(7, 164)
        self.table.setWordWrap(True)
        self.table.setTextElideMode(QtCore.Qt.ElideNone)
        self.table.cellClicked.connect(self._on_table_cell_clicked)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._apply_widget_styles()
        self.table_container = QtWidgets.QWidget(self)
        self._table_stack = QtWidgets.QStackedLayout(self.table_container)
        self._table_stack.setContentsMargins(0, 0, 0, 0)
        self._table_stack.addWidget(self.table)
        self.empty_state = QtWidgets.QWidget(self.table_container)
        empty_layout = QtWidgets.QVBoxLayout(self.empty_state)
        empty_layout.setAlignment(QtCore.Qt.AlignCenter)
        self.empty_state_label = QtWidgets.QLabel("没有符合当前筛选条件的插件。")
        self.empty_state_label.setAlignment(QtCore.Qt.AlignCenter)
        self.empty_state_label.setStyleSheet("font: 11pt 'Microsoft YaHei UI';")
        self.empty_state_hint = QtWidgets.QLabel("请调整搜索关键字或状态筛选后重试。")
        self.empty_state_hint.setAlignment(QtCore.Qt.AlignCenter)
        self.empty_state_hint.setStyleSheet(
            f"color: {self.theme_manager.colors.get('label_dim', '#6B7280')}; font: 9pt 'Microsoft YaHei UI';"
        )
        empty_layout.addWidget(self.empty_state_label)
        empty_layout.addWidget(self.empty_state_hint)
        self._table_stack.addWidget(self.empty_state)
        layout.addWidget(self.table_container)
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
            self._start_scan()
        self.table.setFocus(QtCore.Qt.OtherFocusReason)

    def _update_all(self):
        eligible = [record for record in self.records if record.can_update]
        if not eligible:
            unchecked = [record for record in self.records if self._is_unchecked(record)]
            if unchecked:
                listing = "\n".join(
                    f"- {record.name}（{record.remote_url_display or '本地插件'}）"
                    for record in unchecked
                )
                content = (
                    "尚未完成插件更新检查，未执行任何更新操作。\n\n"
                    f"待检查插件：{len(unchecked)} 个\n{listing}\n\n"
                    "请先点击“刷新”，检查远端更新和依赖预检；检查完成后，页面会显示可更新数量。"
                )
                self._set_feedback("请先刷新检查插件更新。", level="warning")
                self._show_copyable_details("请先检查更新", content)
                return
            attention = [record for record in self.records if self._needs_attention(record)]
            lines = [
                "当前没有可安全更新的插件。",
                f"已检查插件：{len(self.records)} 个",
                f"需处理插件：{len(attention)} 个",
            ]
            if attention:
                lines.extend(("", "需处理插件："))
                for record in attention:
                    lines.extend(("", self._status_details_text(record)))
            else:
                lines.append("所有已检查的插件均已是最新版本。")
            self._set_feedback("没有可更新的插件。")
            self._show_copyable_details("没有可更新的插件", "\n".join(lines))
            return
        listing = "\n".join(f"- {record.name}（{record.remote_url_display or '本地插件'}）" for record in eligible)
        content = (
            f"将更新以下 {len(eligible)} 个依赖预检通过的插件：\n\n{listing}\n\n"
            "更新前请停止 ComfyUI。更新会先处理已确认安全的依赖，再更新插件代码。"
        )
        if self._confirm_with_details("确认批量更新", content, "开始更新"):
            self._active_operation = "update"
            self._set_feedback(f"正在更新 {len(eligible)} 个插件…")
            self.controller.start_update(eligible)

    def _start_scan(self):
        self._active_operation = "scan"
        self._set_feedback("正在检查本地插件列表…")
        self.controller.start_scan()

    def _start_refresh(self):
        self._active_operation = "refresh"
        self._set_feedback("正在检查插件更新…")
        self.controller.start_refresh()

    def _prepare_install(self):
        source_url = self.url_input.text().strip()
        if not source_url:
            self._set_feedback("请输入完整的 Git 仓库地址。", level="error")
            self._show_copyable_details("无法安装插件", "请先输入完整的 Git 仓库地址，例如：\nhttps://github.com/owner/repository.git")
            return
        self._active_operation = "prepare_install"
        self._set_feedback("正在校验插件地址并预检依赖…")
        self.controller.start_prepare_install(source_url)

    def _start_update_one(self, record):
        if not record.can_update:
            if record.update_availability == UpdateAvailability.UP_TO_DATE:
                self._set_feedback(f"{record.name} 已是最新版本，无需更新。")
            elif self._is_unchecked(record):
                self._set_feedback(f"{record.name} 尚未完成更新检查，请先点击“刷新”。", level="warning")
            else:
                self._set_feedback(f"{record.name} 当前无法安全更新，请先查看状态说明。", level="warning")
            return
        self._active_operation = "update"
        self._set_feedback(f"正在更新插件：{record.name}…")
        self.controller.start_update([record])

    def _reset_page(self):
        self.page = 0
        self._render_records()

    def _change_page(self, delta):
        self.page += delta
        self._render_records()

    def _on_preview(self, preview):
        plan = preview.dependency_plan
        lines = [
            f"来源：{preview.source_url_display}",
            f"目标目录：{preview.target_path}",
            f"目标提交：{preview.target_head}",
            f"依赖预检状态：{plan.state.value}",
        ]
        if plan.requirements:
            lines.extend(("", "检测到的依赖：", *plan.requirements))
        if plan.additions:
            lines.extend(("", "将新增的依赖：", *plan.additions))
        if plan.resolved_changes:
            lines.extend(("", "将调整的依赖：", *plan.resolved_changes))
        if plan.conflicts:
            lines.extend(("", "依赖冲突：", *plan.conflicts))
        if plan.reason:
            lines.extend(("", "预检说明：", plan.reason))
        lines.extend(("", "确认后将安装插件并处理预检通过的依赖。"))
        if self._confirm_with_details("确认安装插件", "\n".join(lines), "安装插件"):
            self._active_operation = "install"
            self._set_feedback(f"正在安装插件：{preview.target_path.name}…")
            self.controller.start_install(preview)
        else:
            self._active_operation = None

    def _on_finished(self, results):
        # An empty scan is a valid completed listing, not a reason to start a
        # second worker while the previous one is still unwinding.
        if not results or (
            hasattr(results[0], "state")
            and not hasattr(results[0], "operation")
        ):
            self.records = list(results)
            self._render_records(focus_table=True)
            if getattr(self, "_active_operation", None) in {"scan", "refresh"}:
                self._set_feedback(f"检查完成：共发现 {len(self.records)} 个插件，可更新 {sum(record.can_update for record in self.records)} 个。")
            if hasattr(self, "_active_operation"):
                self._active_operation = None
        else:
            operation = self._active_operation or getattr(results[0], "operation", "操作")
            self._show_operation_results(operation, results)
            if operation == "update":
                # 更新后重新检查远端与依赖预检，避免表格保留旧的“可更新”状态。
                self._start_refresh()
            else:
                self._start_scan()

    def _on_failed(self, message):
        self._active_operation = None
        self._set_feedback("操作失败；请查看完整错误信息。", level="error")
        self._show_copyable_details("插件管理操作失败", message)

    def _set_busy(self, busy):
        for button in (self.install_button, self.refresh_button, self.update_all_button):
            button.setEnabled(not busy)
        for button in self._operation_buttons:
            button.setEnabled(not busy)
        self.progress.setVisible(busy)
        if busy:
            self.progress.setRange(0, 0)

    def _on_progress(self, result, current, total):
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(current)
        name = getattr(result, "plugin_name", "插件")
        outcome = getattr(result, "outcome", "完成")
        outcome_text = {
            "success": "成功",
            "skipped": "已跳过",
            "failed": "失败",
            "cancelled": "已取消",
        }.get(outcome, outcome)
        self._set_feedback(f"正在处理 {current}/{total}：{name}（{outcome_text}）")

    def _render_records(self, focus_table=False):
        query = self.search.text().casefold()
        status_filter = self.status_filter.currentData()
        records = [
            record for record in self.records
            if self._matches_status_filter(record, status_filter)
            and (
                not query
                or query in record.name.casefold()
                or query in (record.remote_url_display or "").casefold()
            )
        ]
        pages = max(1, (len(records) + self.page_size - 1) // self.page_size)
        self.page = max(0, min(self.page, pages - 1))
        visible = records[self.page * self.page_size:(self.page + 1) * self.page_size]
        self._visible_records = visible
        self._operation_buttons = []
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
                self._format_source_display(record.remote_url_display),
            ]
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                if column == 1:
                    item.setForeground(QtGui.QColor("#22C55E" if enabled else self.theme_manager.colors.get("label_dim")))
                elif column == 2:
                    item.setForeground(QtGui.QColor(status_color))
                elif column == 3:
                    item.setForeground(QtGui.QColor(update_color))
                if column in (2, 3):
                    color = QtGui.QColor(status_color if column == 2 else update_color)
                    background = QtGui.QColor(color)
                    background.setAlpha(72)
                    item.setText(f"● {value}")
                    item.setForeground(color)
                    item.setBackground(background)
                    item.setTextAlignment(QtCore.Qt.AlignCenter)
                    item.setToolTip("点击查看完整状态说明")
                elif column == 6 and record.remote_url_display:
                    item.setToolTip("点击复制完整 Git 地址")
                    font = item.font()
                    font.setUnderline(True)
                    item.setFont(font)
                    item.setForeground(QtGui.QColor(self.theme_manager.colors.get("accent", "#6366F1")))
                self.table.setItem(row, column, item)
            action = QtWidgets.QWidget()
            actions = QtWidgets.QHBoxLayout(action)
            actions.setContentsMargins(4, 4, 4, 4)
            actions.setSpacing(6)
            button = QtWidgets.QPushButton("更新")
            button.clicked.connect(lambda _=False, item=record: self._start_update_one(item))
            button.setMinimumSize(64, 28)
            button.setEnabled(not self.controller.is_busy())
            actions.addWidget(button)
            self._operation_buttons.append(button)
            remove = QtWidgets.QPushButton("卸载")
            remove.clicked.connect(lambda _=False, item=record: self._confirm_uninstall(item))
            remove.setMinimumSize(64, 28)
            remove.setEnabled(not self.controller.is_busy())
            actions.addWidget(remove)
            self._operation_buttons.append(remove)
            self.table.setCellWidget(row, 7, action)
            self.table.setRowHeight(row, 38)
        self.table.resizeRowsToContents()
        self._table_stack.setCurrentWidget(self.empty_state if self.records and not records else self.table)
        self.update_all_button.setText(f"全部更新（{sum(record.can_update for record in self.records)}）")
        self.page_label.setText(f"第 {self.page + 1} / {pages} 页，共 {len(records)} 个插件")
        self.previous_page.setEnabled(self.page > 0)
        self.next_page.setEnabled(self.page + 1 < pages)
        if focus_table:
            self.table.setFocus(QtCore.Qt.OtherFocusReason)

    @staticmethod
    def _is_enabled(record):
        return not record.path.name.endswith(".disabled")

    @staticmethod
    def _format_source_display(source_url):
        if not source_url:
            return "本地插件"
        scheme_end = source_url.find("://")
        start = scheme_end + 3 if scheme_end >= 0 else 0
        path_start = source_url.find("/", start)
        if path_start >= 0:
            return f"{source_url[:path_start + 1]}\n{source_url[path_start + 1:]}"
        return source_url

    def _matches_status_filter(self, record, status_filter):
        if status_filter in (None, "", "all"):
            return True
        if status_filter == "updatable":
            return record.can_update
        if status_filter == "attention":
            return self._needs_attention(record)
        return True

    def _needs_attention(self, record):
        enabled = self._is_enabled(record)
        if not enabled:
            return True
        if record.dependency_state in _DEPENDENCY_BLOCKED_STATES:
            return True
        if record.state in {
            PluginState.UP_TO_DATE,
            PluginState.UPDATE_AVAILABLE,
        }:
            return record.update_availability not in {
                UpdateAvailability.UP_TO_DATE,
                UpdateAvailability.AVAILABLE,
            }
        return record.state != PluginState.UP_TO_DATE

    @staticmethod
    def _is_unchecked(record):
        return record.state == PluginState.LOCAL_ONLY or record.update_availability in {
            UpdateAvailability.UNKNOWN,
            UpdateAvailability.CHECKING,
        }

    def _usable_status(self, record, enabled):
        if not enabled:
            return "已禁用", self.theme_manager.colors.get("label_dim", "#6B7280")
        if record.state == PluginState.LOCAL_ONLY or record.update_availability in {
            UpdateAvailability.UNKNOWN,
            UpdateAvailability.CHECKING,
        }:
            return "未检查", self.theme_manager.colors.get("label_muted", "#9CA3AF")
        if record.dependency_state in _DEPENDENCY_BLOCKED_STATES:
            return "需处理", self.theme_manager.colors.get("warning", "#F59E0B")
        if record.state in _UNUSABLE_STATES:
            return "异常", self.theme_manager.colors.get("error", "#EF4444")
        if record.state in _ATTENTION_STATES:
            return "需处理", self.theme_manager.colors.get("warning", "#F59E0B")
        return "正常", "#22C55E"

    def _update_status(self, record):
        if record.update_availability in _UPDATE_LABELS:
            label = _UPDATE_LABELS[record.update_availability]
            if record.update_availability == UpdateAvailability.AVAILABLE:
                if not record.can_update:
                    return "需处理", self.theme_manager.colors.get("warning", "#F59E0B")
                return label, self.theme_manager.colors.get("accent", "#6366F1")
            if record.update_availability == UpdateAvailability.UP_TO_DATE:
                return label, "#22C55E"
            if record.update_availability == UpdateAvailability.CHECK_FAILED:
                return label, self.theme_manager.colors.get("error", "#EF4444")
            if record.update_availability == UpdateAvailability.NOT_CHECKABLE:
                return label, self.theme_manager.colors.get("warning", "#F59E0B")
            return label, self.theme_manager.colors.get("label_muted", "#9CA3AF")
        return _PLUGIN_STATE_LABELS.get(record.state, "未检查"), self.theme_manager.colors.get("label_muted", "#9CA3AF")

    def _on_table_cell_clicked(self, row, column):
        if row < 0 or row >= len(self._visible_records):
            return
        record = self._visible_records[row]
        if column in (2, 3):
            self._show_status_details(record)
        elif column == 6 and record.remote_url_display:
            QtWidgets.QApplication.clipboard().setText(record.remote_url_display)
            QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), "地址已复制", self.table)
            self._set_feedback("地址已复制", level="success")

    def _show_status_details(self, record):
        self._show_copyable_details("插件状态详情", self._status_details_text(record))

    def _status_details_text(self, record):
        enabled = self._is_enabled(record)
        status_text, _ = self._usable_status(record, enabled)
        update_text, _ = self._update_status(record)
        details = [
            f"插件：{record.name}",
            f"目录：{record.path}",
            f"状态：{status_text}",
            f"更新状态：{update_text}",
            f"来源：{record.remote_url_display or '本地插件'}",
            f"分支：{record.branch or '无分支'}",
            f"本地提交：{record.head or '未知'}",
            f"本地日期：{record.local_commit_at or '未知'}",
            f"远端日期：{record.remote_commit_at or '未知'}",
            f"依赖预检：{record.dependency_state.value}",
        ]
        if record.upstream:
            details.append(f"上游分支：{record.upstream}")
        if record.ahead is not None or record.behind is not None:
            details.append(f"提交差异：领先 {record.ahead or 0}，落后 {record.behind or 0}")
        if record.dirty_count or record.untracked_count:
            details.append(f"本地修改：已修改 {record.dirty_count} 个，未跟踪 {record.untracked_count} 个")
        if record.reason:
            details.extend(("", "详细原因：", record.reason))
        if record.error_code:
            details.append(f"错误码：{record.error_code}")
        if record.dependency_reason:
            details.extend(("", "依赖说明：", record.dependency_reason))
        if record.dependency_additions:
            details.extend(("", "将新增的依赖：", *record.dependency_additions))
        if record.dependency_changes:
            details.extend(("", "将调整的依赖：", *record.dependency_changes))
        if record.dependency_strict_constraints:
            details.extend(("", "严格约束：", *record.dependency_strict_constraints))
        if record.dependency_conflicts:
            details.extend(("", "依赖冲突：", *record.dependency_conflicts))
        guidance = _STATE_GUIDANCE.get(record.state)
        if record.dependency_state in _DEPENDENCY_BLOCKED_STATES:
            guidance = "依赖预检未通过。请先按“依赖冲突”中的完整内容处理，再重新检查更新。"
        if guidance:
            details.extend(("", "处理建议：", guidance))
        return "\n".join(details)

    def _show_copyable_details(self, title, content):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setModal(True)
        dialog.resize(760, 520)
        layout = QtWidgets.QVBoxLayout(dialog)
        text = QtWidgets.QPlainTextEdit(dialog)
        text.setReadOnly(True)
        text.setPlainText(content)
        text.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
        layout.addWidget(text)
        buttons = QtWidgets.QHBoxLayout()
        copy_button = QtWidgets.QPushButton("复制全部信息")
        def copy_all():
            QtWidgets.QApplication.clipboard().setText(content)
            copy_button.setText("已复制")
            self._set_feedback("完整信息已复制。")
        copy_button.clicked.connect(copy_all)
        close_button = QtWidgets.QPushButton("关闭")
        close_button.clicked.connect(dialog.accept)
        buttons.addStretch()
        buttons.addWidget(copy_button)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        dialog.exec_()

    def _confirm_with_details(self, title, content, confirm_text, destructive=False):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setModal(True)
        dialog.resize(760, 500)
        layout = QtWidgets.QVBoxLayout(dialog)
        text = QtWidgets.QPlainTextEdit(dialog)
        text.setReadOnly(True)
        text.setPlainText(content)
        text.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
        layout.addWidget(text)
        buttons = QtWidgets.QHBoxLayout()
        copy_button = QtWidgets.QPushButton("复制全部信息")
        def copy_all():
            QtWidgets.QApplication.clipboard().setText(content)
            copy_button.setText("已复制")
            self._set_feedback("完整信息已复制。")
        copy_button.clicked.connect(copy_all)
        cancel_button = QtWidgets.QPushButton("取消")
        confirm_button = QtWidgets.QPushButton(confirm_text)
        if destructive:
            confirm_button.setStyleSheet("color: #EF4444;")
        cancel_button.clicked.connect(dialog.reject)
        confirm_button.clicked.connect(dialog.accept)
        buttons.addStretch()
        buttons.addWidget(copy_button)
        buttons.addWidget(cancel_button)
        buttons.addWidget(confirm_button)
        layout.addLayout(buttons)
        return dialog.exec_() == QtWidgets.QDialog.Accepted

    def _show_operation_results(self, operation, results):
        operation_labels = {
            "update": "更新结果",
            "install": "安装结果",
            "uninstall": "卸载结果",
        }
        outcome_labels = {
            "success": "成功",
            "skipped": "已跳过",
            "failed": "失败",
            "cancelled": "已取消",
        }
        lines = []
        outcome_counts = {name: 0 for name in outcome_labels}
        for result in results:
            outcome = getattr(result, "outcome", "failed")
            outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
            lines.extend((
                f"插件：{getattr(result, 'plugin_name', '未知插件')}",
                f"结果：{outcome_labels.get(outcome, outcome)}",
                f"状态：{_PLUGIN_STATE_LABELS.get(getattr(result, 'state', None), getattr(result, 'state', '未知'))}",
            ))
            if getattr(result, "previous_head", None) or getattr(result, "current_head", None):
                lines.append(f"提交：{getattr(result, 'previous_head', None) or '未知'} → {getattr(result, 'current_head', None) or '未知'}")
            if getattr(result, "dependencies_added", None):
                lines.extend(("新增依赖：", *result.dependencies_added))
            if getattr(result, "dependencies_changed", None):
                lines.extend(("调整依赖：", *result.dependencies_changed))
            if getattr(result, "message", ""):
                lines.extend(("完整说明：", result.message))
            lines.append("")
        summary = "，".join(
            f"{outcome_labels.get(outcome, outcome)} {count} 项"
            for outcome, count in outcome_counts.items() if count
        ) or "无返回结果"
        level = "error" if outcome_counts.get("failed") else (
            "warning" if outcome_counts.get("skipped") or outcome_counts.get("cancelled") else "success"
        )
        self._set_feedback(f"{operation_labels.get(operation, '操作')}已完成：{summary}。", level=level)
        self._show_copyable_details(operation_labels.get(operation, "操作结果"), "\n".join(lines).rstrip())

    def _confirm_uninstall(self, record):
        content = (
            f"将永久删除以下插件目录：\n{record.path}\n\n"
            "Python 依赖不会删除。此操作不可撤销；如需保留插件文件，请先手动备份。"
        )
        if self._confirm_with_details("确认卸载插件", content, "卸载插件", destructive=True):
            self._active_operation = "uninstall"
            self._set_feedback(f"正在卸载插件：{record.name}…")
            self.controller.start_uninstall(record)

    def _set_feedback(self, message, level="info"):
        if not hasattr(self, "feedback_label"):
            return
        colors = self.theme_manager.colors
        color = {
            "info": colors.get("accent", "#6366F1"),
            "success": "#22C55E",
            "warning": colors.get("warning", "#F59E0B"),
            "error": colors.get("error", "#EF4444"),
        }.get(level, colors.get("label", "#E5E7EB"))
        self.feedback_label.setStyleSheet(f"color: {color}; font: 9pt 'Microsoft YaHei UI';")
        self.feedback_label.setText(message)
        self.feedback_label.setVisible(True)
