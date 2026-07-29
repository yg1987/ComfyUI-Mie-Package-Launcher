"""UI for safely managing ComfyUI data directory links."""

from __future__ import annotations

import os
from pathlib import Path

from PyQt5 import QtCore, QtWidgets

from services.symlink_service import RULES, LinkStatus, SymlinkService
from ui_qt.pages.base_page import BasePage
from ui_qt.widgets import InfoCard
from ui_qt.widgets.dialog_helper import DialogHelper


class SymlinkPage(BasePage):
    """Per-environment directory-link management page."""

    WARNING_TEXT = (
        "首次创建链接前，请先自行把原文件夹中的模型、图片和工作流移动到目标目录。\n"
        "本功能不会移动、复制或删除你的文件。若原目录仍含有文件，系统会拒绝创建链接。\n\n"
        "创建成功后，本页会显示“正常 · 目录联接 → 实际目标路径”。\n"
        "Windows 资源管理器可能仍显示为普通文件夹，地址栏也可能保留来源路径；"
        "这是目录联接的正常表现，请以本页状态和“打开目标”为准。\n\n"
        "请检查以下路径（均相对于 ComfyUI 根目录）：\n"
        "models    模型\n"
        "input    输入图片/文件\n"
        "output    输出图片/文件\n"
        "user\\default\\workflows    工作流"
    )

    def __init__(self, app, theme_manager, parent=None):
        super().__init__(theme_manager, parent)
        self.app = app
        self.service = SymlinkService(app)
        self._rows = {}
        self._loading = False
        self._setup_ui()
        self.reload_for_active_environment()

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(25, 25, 25, 25)
        layout.setSpacing(15)

        title = QtWidgets.QLabel("自用-软链接")
        title.setObjectName("SymlinkPageTitle")
        layout.addWidget(title)
        self._title = title

        warning = QtWidgets.QFrame()
        warning.setObjectName("SymlinkSafetyWarning")
        warning_layout = QtWidgets.QHBoxLayout(warning)
        warning_layout.setContentsMargins(18, 16, 18, 16)
        warning_layout.setSpacing(14)
        warning_icon = QtWidgets.QLabel("!")
        warning_icon.setObjectName("SymlinkWarningIcon")
        warning_icon.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignHCenter)
        warning_icon.setFixedWidth(32)
        warning_text = QtWidgets.QLabel(self.WARNING_TEXT)
        warning_text.setObjectName("SymlinkWarningText")
        warning_text.setWordWrap(True)
        warning_text.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        warning_layout.addWidget(warning_icon)
        warning_layout.addWidget(warning_text, 1)
        layout.addWidget(warning)
        self._warning = warning
        self._warning_icon = warning_icon
        self._warning_text = warning_text

        env_row = QtWidgets.QHBoxLayout()
        env_row.setSpacing(10)
        env_caption = QtWidgets.QLabel("当前环境:")
        self.env_label = QtWidgets.QLabel()
        self.env_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        env_row.addWidget(env_caption)
        env_row.addWidget(self.env_label, 1)
        layout.addLayout(env_row)
        self._env_caption = env_caption

        for key, label, relative in RULES:
            card = InfoCard(label, self.theme_manager.styles)
            card_layout = card.layout()
            card_layout.setSpacing(10)

            source_label = QtWidgets.QLabel()
            source_label.setWordWrap(True)
            source_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            card_layout.addWidget(source_label)

            target_row = QtWidgets.QHBoxLayout()
            target_row.setSpacing(8)
            enabled = QtWidgets.QCheckBox("启用")
            enabled.setFixedWidth(58)
            target_edit = QtWidgets.QLineEdit()
            target_edit.setPlaceholderText("为此目录单独选择目标目录")
            browse = QtWidgets.QPushButton("选择目录")
            browse.setToolTip(f"选择{label}的目标目录")
            browse.setFixedWidth(88)
            target_row.addWidget(enabled)
            target_row.addWidget(target_edit, 1)
            target_row.addWidget(browse)
            card_layout.addLayout(target_row)

            status_row = QtWidgets.QHBoxLayout()
            status_row.setSpacing(8)
            status_label = QtWidgets.QLabel("未检查")
            status_label.setObjectName(f"SymlinkStatus_{key}")
            status_label.setMinimumWidth(220)
            status_label.setWordWrap(True)
            open_source = QtWidgets.QPushButton("打开来源")
            open_target = QtWidgets.QPushButton("打开目标")
            create = QtWidgets.QPushButton("创建/修复链接")
            remove = QtWidgets.QPushButton("移除链接")
            for button in (open_source, open_target, create, remove):
                button.setMinimumWidth(86)
            status_row.addWidget(status_label, 1)
            status_row.addWidget(open_source)
            status_row.addWidget(open_target)
            status_row.addWidget(create)
            status_row.addWidget(remove)
            card_layout.addLayout(status_row)

            self._rows[key] = {
                "card": card,
                "source": source_label,
                "enabled": enabled,
                "target": target_edit,
                "browse": browse,
                "status": status_label,
                "open_source": open_source,
                "open_target": open_target,
                "create": create,
                "remove": remove,
                "relative": relative,
            }
            enabled.toggled.connect(lambda _checked, k=key: self._save_rule(k))
            target_edit.editingFinished.connect(lambda k=key: self._save_rule(k))
            browse.clicked.connect(lambda _checked=False, k=key: self._choose_target(k))
            open_source.clicked.connect(lambda _checked=False, k=key: self._open_source(k))
            open_target.clicked.connect(lambda _checked=False, k=key: self._open_target(k))
            create.clicked.connect(lambda _checked=False, k=key: self._create_or_repair(k))
            remove.clicked.connect(lambda _checked=False, k=key: self._remove_link(k))
            layout.addWidget(card)

        action_row = QtWidgets.QHBoxLayout()
        self.check_all_button = QtWidgets.QPushButton("检查全部")
        self.repair_all_button = QtWidgets.QPushButton("创建/修复已启用项")
        self.check_all_button.clicked.connect(self._check_all)
        self.repair_all_button.clicked.connect(self._repair_all)
        action_row.addStretch(1)
        action_row.addWidget(self.check_all_button)
        action_row.addWidget(self.repair_all_button)
        layout.addLayout(action_row)
        layout.addStretch(1)
        self.update_theme()

    def reload_for_active_environment(self):
        self._loading = True
        try:
            self.service = SymlinkService(self.app)
            self.env_label.setText(
                f"{self.service.active_env_name()}    {self.service.comfy_root()}"
            )
            for key, _label, relative in RULES:
                row = self._rows[key]
                config = self.service.rule_config(key)
                row["enabled"].setChecked(config["enabled"])
                row["target"].setText(config["target_path"])
                row["source"].setText(
                    f"来源: {self.service.source_path(key)}    (相对路径: {relative})"
                )
        finally:
            self._loading = False
        self.refresh_statuses()

    def _save_rule(self, key: str):
        if self._loading:
            return
        row = self._rows[key]
        try:
            self.service.set_rule(
                key,
                enabled=row["enabled"].isChecked(),
                target_path=row["target"].text(),
            )
        except Exception as exc:
            DialogHelper.show_error(self, "保存失败", str(exc))
        self._refresh_one(key)

    def _choose_target(self, key: str):
        row = self._rows[key]
        initial = row["target"].text().strip() or str(self.service.comfy_root())
        selected = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "选择目标目录",
            initial,
            QtWidgets.QFileDialog.ShowDirsOnly,
        )
        if selected:
            row["target"].setText(selected)
            self._save_rule(key)

    def _is_running(self) -> bool:
        manager = getattr(self.app, "process_manager", None)
        try:
            if manager is not None and hasattr(manager, "is_running_fast"):
                return bool(manager.is_running_fast())
        except Exception:
            pass
        return False

    def _require_stopped(self) -> bool:
        if not self._is_running():
            return True
        DialogHelper.show_warning(self, "请先停止 ComfyUI", "目录链接操作前必须先停止 ComfyUI。")
        return False

    def _create_or_repair(self, key: str):
        if not self._require_stopped():
            return
        self._save_rule(key)
        status = self.service.inspect(key)
        if status.code == "disabled":
            DialogHelper.show_warning(self, "尚未启用", "请先勾选此项的“启用”。")
            return
        create_target = False
        allow_empty_source = False
        if status.code == "target_missing" and status.target is not None:
            if self.service.is_directory_link(status.source):
                DialogHelper.show_warning(
                    self,
                    "目标目录不存在",
                    f"当前链接的目标目录不存在，请先恢复目标目录：\n{status.target}",
                )
                return
            create_target = DialogHelper.show_confirmation(
                self,
                "创建目标目录",
                f"目标目录不存在，是否创建一个空目录？\n\n{status.target}",
                yes_text="创建空目录",
                no_text="取消",
            )
            if not create_target:
                return
        if status.code == "source_nonempty_dir":
            DialogHelper.show_warning(
                self,
                "原目录仍含有文件",
                "已拒绝创建链接。请先手动把原目录中的所有文件移动到目标目录：\n\n"
                f"原目录：{status.source}\n目标目录：{status.target}",
                min_width=620,
            )
            return
        if status.code == "source_empty_dir":
            allow_empty_source = DialogHelper.show_confirmation(
                self,
                "替换空目录",
                f"来源是空目录。确认移除这个空目录并创建目录链接吗？\n\n{status.source}",
                yes_text="创建链接",
                no_text="取消",
            )
            if not allow_empty_source:
                return
        try:
            result = self.service.create_link(
                key,
                create_target=create_target,
                allow_empty_source=allow_empty_source,
            )
            DialogHelper.show_info(
                self,
                "链接创建成功",
                f"{result.source}\n\n已指向\n\n{result.target}",
                min_width=600,
            )
        except Exception as exc:
            DialogHelper.show_error(self, "创建失败", str(exc))
        self._refresh_one(key)

    def _remove_link(self, key: str):
        if not self._require_stopped():
            return
        status = self.service.inspect(key)
        if not self.service.is_directory_link(status.source):
            DialogHelper.show_warning(self, "无法移除", "来源不是可识别的目录链接，已拒绝删除。")
            return
        confirmed = DialogHelper.show_confirmation(
            self,
            "移除目录链接",
            "只会移除链接本身，目标目录和其中的数据会完整保留。\n\n"
            f"链接：{status.source}\n目标：{self.service.resolved_target(status.source)}",
            yes_text="移除链接",
            no_text="取消",
            destructive=True,
        )
        if not confirmed:
            return
        try:
            self.service.remove_link(key)
            DialogHelper.show_info(self, "已移除", "目录链接已移除，目标目录中的数据未被删除。")
        except Exception as exc:
            DialogHelper.show_error(self, "移除失败", str(exc))
        self._refresh_one(key)

    def _repair_all(self):
        if not self._require_stopped():
            return
        for key in self._rows:
            self._save_rule(key)
        statuses = self.service.ensure_active_links(repair=True)
        self.refresh_statuses()
        attention = self.service.format_attention(statuses)
        if attention:
            DialogHelper.show_warning(
                self,
                "部分项目需要手动处理",
                attention,
                min_width=650,
            )
        else:
            DialogHelper.show_info(self, "检查完成", "所有已启用的目录链接均正常。")

    def _check_all(self):
        """Refresh every status and always explain the result to the user."""
        self.refresh_statuses()
        try:
            statuses = self.service.inspect_all()
        except Exception as exc:
            DialogHelper.show_error(self, "检查失败", f"无法检查目录链接：\n{exc}")
            return

        enabled_statuses = [status for status in statuses if status.code != "disabled"]
        if not enabled_statuses:
            DialogHelper.show_info(
                self,
                "检查完成",
                "当前没有启用的目录链接。勾选“启用”并设置目标目录后，再创建或检查链接。",
            )
            return

        attention = self.service.format_attention(enabled_statuses)
        if attention:
            normal_count = sum(status.code == "link_ok" for status in enabled_statuses)
            DialogHelper.show_warning(
                self,
                "检查完成：发现需要处理的项目",
                f"已检查 {len(enabled_statuses)} 项，其中 {normal_count} 项正常。\n\n{attention}",
                min_width=650,
            )
            return

        DialogHelper.show_info(
            self,
            "检查完成",
            f"已检查 {len(enabled_statuses)} 项已启用的目录链接，全部正常。",
        )

    def refresh_statuses(self):
        for key in self._rows:
            self._refresh_one(key)

    def _refresh_one(self, key: str):
        try:
            status = self.service.inspect(key)
        except Exception as exc:
            label, _relative = next((label, relative) for k, label, relative in RULES if k == key)
            status = LinkStatus(key, label, "error", self.service.source_path(key), None, str(exc))
        row = self._rows[key]
        status_text = self._status_text(status)
        row["status"].setText(status_text)
        row["status"].setToolTip(status_text)
        row["remove"].setEnabled(self.service.is_directory_link(status.source))
        row["open_target"].setEnabled(status.target is not None and status.target.is_dir())
        self._style_status(row["status"], status.code)

    def _status_text(self, status: LinkStatus) -> str:
        if status.code != "link_ok" or status.target is None:
            return status.message
        link_type = self.service.directory_link_type(status.source)
        type_label = "目录联接" if link_type == "junction" else "目录符号链接"
        return f"正常 · {type_label} → {status.target}"

    def _style_status(self, label, code: str):
        colors = self.theme_manager.colors
        # ThemeColors does not provide semantic success/info values.  Keep these
        # explicit so status text never falls back to the default white color.
        if colors.dark:
            palette = {
                "success": "#34D399",
                "info": "#60A5FA",
                "pending": "#FCD34D",
                "warning": "#F59E0B",
                "error": "#F87171",
            }
        else:
            palette = {
                "success": "#15803D",
                "info": "#2563EB",
                "pending": "#A16207",
                "warning": "#D97706",
                "error": "#DC2626",
            }
        if code == "link_ok":
            color = palette["success"]
        elif code == "disabled":
            color = colors.get("label_muted")
        elif code in {"source_missing", "source_empty_dir"}:
            color = palette["info"]
        elif code == "unconfigured":
            color = palette["pending"]
        elif code in {
            "source_nonempty_dir",
            "link_wrong_target",
            "target_missing",
            "path_overlap",
        }:
            color = palette["warning"]
        else:
            color = palette["error"]
        label.setStyleSheet(
            f"color: {color}; font: bold 9pt 'Microsoft YaHei UI'; background: transparent;"
        )

    def _open_source(self, key: str):
        source = self.service.source_path(key)
        if source.exists():
            os.startfile(str(source))
            return
        parent = source.parent
        if parent.exists():
            os.startfile(str(parent))
            return
        DialogHelper.show_warning(self, "目录不存在", str(source))

    def _open_target(self, key: str):
        target = self.service.inspect(key).target
        if target is not None and target.is_dir():
            os.startfile(str(target))
            return
        DialogHelper.show_warning(self, "目标目录不存在", str(target or "尚未设置"))

    def update_theme(self, theme_styles=None):
        super().update_theme(theme_styles)
        colors = self.theme_manager.colors
        styles = self.theme_manager.styles
        if not hasattr(self, "_title"):
            return
        self._title.setStyleSheet(
            f"font: bold 16pt 'Microsoft YaHei UI'; color: {colors.get('label')}; margin-bottom: 5px;"
        )
        self._env_caption.setStyleSheet(f"color: {colors.get('label_muted')};")
        self.env_label.setStyleSheet(f"color: {colors.get('label')}; font-weight: bold;")
        if colors.dark:
            warning_bg = "#3B2A12"
            warning_text = "#FDE68A"
            warning_border = "#F59E0B"
        else:
            warning_bg = "#FFF7ED"
            warning_text = "#7C2D12"
            warning_border = "#D97706"
        self._warning.setStyleSheet(
            f"QFrame#SymlinkSafetyWarning {{ background: {warning_bg}; border: 2px solid {warning_border}; "
            "border-radius: 6px; }"
        )
        self._warning_icon.setStyleSheet(
            f"color: {warning_border}; font: bold 22pt 'Microsoft YaHei UI'; background: transparent;"
        )
        self._warning_text.setStyleSheet(
            f"color: {warning_text}; font: bold 10pt 'Microsoft YaHei UI'; background: transparent;"
        )
        for row in self._rows.values():
            row["target"].setStyleSheet(styles.input_style())
            row["browse"].setStyleSheet(styles.secondary_button_style())
            row["open_source"].setStyleSheet(styles.secondary_button_style())
            row["open_target"].setStyleSheet(styles.secondary_button_style())
            row["create"].setStyleSheet(styles.primary_button_style())
            row["remove"].setStyleSheet(styles.secondary_button_style())
        self.check_all_button.setStyleSheet(styles.secondary_button_style())
        self.repair_all_button.setStyleSheet(styles.primary_button_style())
        self.refresh_statuses()
