"""Safe per-environment directory-link management for ComfyUI data folders."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import stat
import subprocess
from typing import Iterable

from utils.common import run_hidden


RULES = (
    ("models", "模型", Path("models")),
    ("input", "输入图片/文件", Path("input")),
    ("output", "输出图片/文件", Path("output")),
    ("workflows", "工作流", Path("user") / "default" / "workflows"),
)
RULE_MAP = {key: (label, relative) for key, label, relative in RULES}

STATUS_MESSAGES = {
    "disabled": "未启用",
    "unconfigured": "未设置目标目录",
    "source_missing": "尚未创建链接",
    "source_empty_dir": "来源是空目录，可安全创建链接",
    "source_nonempty_dir": "来源目录仍含文件，请先手动移动",
    "link_ok": "链接正常",
    "link_wrong_target": "链接指向了其他目录",
    "target_missing": "目标目录不存在",
    "target_not_directory": "目标路径不是目录",
    "path_overlap": "来源目录与目标目录不能相同或互为父子目录",
    "source_not_directory": "来源路径不是目录链接或目录",
    "error": "检查失败",
}

_LINK_TAGS = {
    0xA0000003,  # IO_REPARSE_TAG_MOUNT_POINT (junction)
    0xA000000C,  # IO_REPARSE_TAG_SYMLINK
}


@dataclass(frozen=True)
class LinkStatus:
    key: str
    label: str
    code: str
    source: Path
    target: Path | None
    detail: str = ""

    @property
    def message(self) -> str:
        return self.detail or STATUS_MESSAGES.get(self.code, self.code)

    @property
    def ok(self) -> bool:
        return self.code in {"disabled", "link_ok"}

    @property
    def needs_attention(self) -> bool:
        return self.code not in {"disabled", "link_ok"}

    @property
    def safely_repairable(self) -> bool:
        return self.code in {"source_missing", "source_empty_dir"}


class SymlinkService:
    """Manage configured links without moving or deleting user data."""

    def __init__(self, app):
        self.app = app

    def _config(self) -> dict:
        config = getattr(self.app, "config", None)
        return config if isinstance(config, dict) else {}

    def active_env_id(self) -> str:
        config = self._config()
        envs = config.get("environments")
        active_id = str(config.get("active_env_id") or "").strip()
        if isinstance(envs, list):
            for env in envs:
                if isinstance(env, dict) and env.get("id") == active_id:
                    return active_id
            for env in envs:
                if isinstance(env, dict) and env.get("id"):
                    return str(env["id"])
        return active_id or "env_default"

    def active_env_name(self) -> str:
        env_id = self.active_env_id()
        envs = self._config().get("environments")
        if isinstance(envs, list):
            for env in envs:
                if isinstance(env, dict) and env.get("id") == env_id:
                    return str(env.get("name") or env_id)
        return env_id

    def comfy_root(self) -> Path:
        try:
            paths = self.app.get_active_paths()
        except Exception:
            from config.migrations import resolve_active_paths

            paths = resolve_active_paths(self._config())
        base = Path((paths or {}).get("comfyui_root") or ".").expanduser()
        return Path(os.path.abspath(str(base / "ComfyUI")))

    def source_path(self, key: str) -> Path:
        try:
            _label, relative = RULE_MAP[key]
        except KeyError as exc:
            raise ValueError(f"未知目录规则: {key}") from exc
        return self.comfy_root() / relative

    def rule_config(self, key: str) -> dict:
        if key not in RULE_MAP:
            raise ValueError(f"未知目录规则: {key}")
        manager = self._config().get("symlink_manager")
        env_configs = manager.get("environments") if isinstance(manager, dict) else None
        current = env_configs.get(self.active_env_id()) if isinstance(env_configs, dict) else None
        rule = current.get(key) if isinstance(current, dict) else None
        return {
            "enabled": bool(rule.get("enabled", False)) if isinstance(rule, dict) else False,
            "target_path": str(rule.get("target_path") or "") if isinstance(rule, dict) else "",
        }

    def set_rule(self, key: str, *, enabled: bool, target_path: str) -> None:
        if key not in RULE_MAP:
            raise ValueError(f"未知目录规则: {key}")
        config = self._config()
        manager = config.setdefault("symlink_manager", {})
        env_configs = manager.setdefault("environments", {})
        env_config = env_configs.setdefault(self.active_env_id(), {})
        env_config[key] = {
            "enabled": bool(enabled),
            "target_path": str(target_path or "").strip(),
        }
        self._save_manager(manager)

    def _save_manager(self, manager: dict) -> None:
        services = getattr(self.app, "services", None)
        config_service = getattr(services, "config", None) if services is not None else None
        if config_service is not None:
            config_service.set("symlink_manager", manager)
            config_service.save(None)
            self.app.config = config_service.get_config()
            return
        save = getattr(self.app, "save_config", None)
        if callable(save):
            save()

    @staticmethod
    def _target_path(raw: str) -> Path | None:
        value = str(raw or "").strip().strip('"')
        if not value:
            return None
        return Path(os.path.abspath(os.path.expandvars(os.path.expanduser(value))))

    @staticmethod
    def directory_link_type(path: Path) -> str:
        """Return ``junction``/``symlink`` for recognized directory links."""
        try:
            info = os.lstat(path)
        except (FileNotFoundError, OSError):
            return ""
        reparse_tag = getattr(info, "st_reparse_tag", 0)
        if reparse_tag == 0xA0000003:
            return "junction"
        if stat.S_ISLNK(info.st_mode):
            return "symlink"
        if reparse_tag == 0xA000000C:
            return "symlink"
        return ""

    @staticmethod
    def is_directory_link(path: Path) -> bool:
        return bool(SymlinkService.directory_link_type(path))

    @staticmethod
    def resolved_target(path: Path) -> Path | None:
        if not SymlinkService.is_directory_link(path):
            return None
        try:
            return Path(os.path.realpath(str(path)))
        except OSError:
            return None

    @staticmethod
    def _same_path(left: Path, right: Path) -> bool:
        return os.path.normcase(os.path.realpath(os.path.abspath(str(left)))) == os.path.normcase(
            os.path.realpath(os.path.abspath(str(right)))
        )

    @staticmethod
    def _directory_empty(path: Path) -> bool:
        with os.scandir(path) as entries:
            return next(entries, None) is None

    @staticmethod
    def _paths_overlap(left: Path, right: Path) -> bool:
        try:
            left_value = os.path.normcase(os.path.realpath(os.path.abspath(str(left))))
            right_value = os.path.normcase(os.path.realpath(os.path.abspath(str(right))))
            common = os.path.commonpath((left_value, right_value))
            return common in {left_value, right_value}
        except (OSError, ValueError):
            return False

    def inspect(self, key: str) -> LinkStatus:
        label, _relative = RULE_MAP[key]
        source = self.source_path(key)
        config = self.rule_config(key)
        target = self._target_path(config["target_path"])
        if not config["enabled"]:
            return LinkStatus(key, label, "disabled", source, target)
        if target is None:
            return LinkStatus(key, label, "unconfigured", source, None)
        try:
            if self.is_directory_link(source):
                actual_target = self.resolved_target(source)
                if actual_target is not None and self._same_path(actual_target, target):
                    if target.is_dir():
                        return LinkStatus(key, label, "link_ok", source, target)
                    return LinkStatus(key, label, "target_missing", source, target)
                detail = STATUS_MESSAGES["link_wrong_target"]
                if actual_target is not None:
                    detail = f"链接当前指向: {actual_target}"
                return LinkStatus(key, label, "link_wrong_target", source, target, detail)
            if self._paths_overlap(source, target):
                return LinkStatus(key, label, "path_overlap", source, target)
            if target.exists() and not target.is_dir():
                return LinkStatus(key, label, "target_not_directory", source, target)
            if os.path.lexists(source) and source.is_dir() and not self._directory_empty(source):
                return LinkStatus(key, label, "source_nonempty_dir", source, target)
            if not target.exists():
                return LinkStatus(key, label, "target_missing", source, target)
            if not os.path.lexists(source):
                return LinkStatus(key, label, "source_missing", source, target)
            if not source.is_dir():
                return LinkStatus(key, label, "source_not_directory", source, target)
            return LinkStatus(key, label, "source_empty_dir", source, target)
        except OSError as exc:
            return LinkStatus(key, label, "error", source, target, str(exc))

    def inspect_all(self, *, enabled_only: bool = False) -> list[LinkStatus]:
        statuses = [self.inspect(key) for key, _label, _relative in RULES]
        if enabled_only:
            statuses = [status for status in statuses if status.code != "disabled"]
        return statuses

    def create_link(
        self,
        key: str,
        *,
        create_target: bool = False,
        allow_empty_source: bool = False,
    ) -> LinkStatus:
        status = self.inspect(key)
        target = status.target
        source = status.source
        self._log(
            "info",
            "目录链接创建请求: key=%s status=%s source=%s target=%s",
            key,
            status.code,
            source,
            target,
        )
        if status.code == "link_ok":
            self._log("info", "目录链接无需处理，当前链接正常: %s -> %s", source, target)
            return status
        if target is None:
            self._log("warning", "目录链接创建被拒绝: key=%s 未设置目标目录", key)
            raise ValueError("请先设置目标目录")
        if self._paths_overlap(source, target):
            self._log(
                "warning", "目录链接创建被拒绝，来源和目标重叠: %s <-> %s", source, target
            )
            raise ValueError(STATUS_MESSAGES["path_overlap"])
        if not target.exists():
            if not create_target:
                self._log("warning", "目录链接创建被拒绝，目标目录不存在: %s", target)
                raise ValueError("目标目录不存在")
            try:
                target.mkdir(parents=True, exist_ok=False)
                self._log("info", "目录链接目标目录已创建: %s", target)
            except Exception:
                self._log("exception", "创建目录链接目标目录失败: %s", target)
                raise
        if not target.is_dir():
            self._log("warning", "目录链接创建被拒绝，目标不是目录: %s", target)
            raise ValueError("目标路径不是目录")
        if self.is_directory_link(source):
            self._log("warning", "目录链接创建被拒绝，来源已是其他目录链接: %s", source)
            raise ValueError("来源已经是指向其他位置的目录链接，请先移除原链接")
        restore_source_on_failure = not os.path.lexists(source)
        if os.path.lexists(source):
            if not source.is_dir() or not self._directory_empty(source):
                self._log("warning", "目录链接创建被拒绝，来源目录非空: %s", source)
                raise ValueError("来源目录仍含文件，请先手动移动到目标目录")
            if not allow_empty_source:
                self._log("warning", "目录链接创建等待确认，来源是空目录: %s", source)
                raise ValueError("来源是空目录，需要确认后才能替换为目录链接")
            source.rmdir()
            restore_source_on_failure = True
            self._log("info", "已移除待替换的空来源目录: %s", source)
        try:
            source.parent.mkdir(parents=True, exist_ok=True)
            self._create_directory_link(source, target)
        except Exception as exc:
            source_restored = False
            restore_error = None
            if restore_source_on_failure and not os.path.lexists(source):
                try:
                    source.mkdir(parents=True, exist_ok=True)
                    source_restored = True
                except Exception as restore_exc:
                    restore_error = restore_exc
            self._log(
                "exception",
                "目录链接创建失败: key=%s source=%s target=%s error=%r "
                "source_restored=%s restore_error=%r",
                key,
                source,
                target,
                exc,
                source_restored,
                restore_error,
            )
            raise
        result = self.inspect(key)
        if result.code != "link_ok":
            self._log(
                "error",
                "目录链接创建后校验失败: key=%s status=%s message=%s source=%s target=%s",
                key,
                result.code,
                result.message,
                source,
                target,
            )
            raise OSError(f"目录链接创建后校验失败: {result.message}")
        self._log("info", "目录链接已创建: %s -> %s", source, target)
        return result

    def _create_directory_link(self, source: Path, target: Path) -> None:
        if os.name != "nt":
            try:
                os.symlink(str(target), str(source), target_is_directory=True)
                self._log("info", "目录符号链接创建成功: %s -> %s", source, target)
            except Exception:
                self._log("exception", "目录符号链接创建失败: %s -> %s", source, target)
                raise
            return
        command = [
            os.environ.get("COMSPEC") or shutil.which("cmd.exe") or "cmd.exe",
            "/d",
            "/c",
            "mklink",
            "/J",
            str(source),
            str(target),
        ]
        self._log("info", "开始创建 Windows 目录联接: %s -> %s", source, target)
        try:
            result = run_hidden(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdin=subprocess.DEVNULL,
            )
        except Exception:
            self._log("exception", "执行 mklink 失败: %s -> %s", source, target)
            raise
        if result.returncode == 0:
            self._log("info", "Windows 目录联接创建成功: %s -> %s", source, target)
            return
        junction_detail = (result.stderr or result.stdout or "mklink failed").strip()
        self._log(
            "warning",
            "Windows 目录联接创建失败，将回退目录符号链接: rc=%s source=%s "
            "target=%s detail=%s",
            result.returncode,
            source,
            target,
            junction_detail,
        )
        try:
            os.symlink(str(target), str(source), target_is_directory=True)
            self._log("info", "Windows 目录符号链接回退成功: %s -> %s", source, target)
        except OSError as exc:
            self._log(
                "exception",
                "Windows 目录符号链接回退失败: source=%s target=%s "
                "junction_rc=%s junction_detail=%s symlink_error=%r",
                source,
                target,
                result.returncode,
                junction_detail,
                exc,
            )
            raise OSError(
                "创建目录联接失败，目录符号链接回退也失败。"
                f"目录联接: {junction_detail}; 符号链接: {exc}。"
                "网络路径可能需要 Windows 开发者模式或管理员权限。"
            ) from exc

    def remove_link(self, key: str) -> LinkStatus:
        status = self.inspect(key)
        source = status.source
        if not self.is_directory_link(source):
            raise ValueError("来源不是可识别的目录链接，已拒绝删除")
        os.rmdir(source)
        self._log("info", "目录链接已移除（目标数据保留）: %s", source)
        return self.inspect(key)

    def ensure_active_links(self, *, repair: bool = True) -> list[LinkStatus]:
        results: list[LinkStatus] = []
        for status in self.inspect_all(enabled_only=True):
            if repair and status.safely_repairable and status.target is not None:
                try:
                    status = self.create_link(
                        status.key,
                        allow_empty_source=status.code == "source_empty_dir",
                    )
                except (OSError, ValueError) as exc:
                    status = LinkStatus(
                        status.key,
                        status.label,
                        "error",
                        status.source,
                        status.target,
                        str(exc),
                    )
            results.append(status)
        return results

    @staticmethod
    def format_attention(statuses: Iterable[LinkStatus]) -> str:
        lines = []
        for status in statuses:
            if status.needs_attention:
                lines.append(f"{status.label}: {status.message}\n  {status.source}")
        return "\n\n".join(lines)

    def _log(self, level: str, message: str, *args) -> None:
        logger = getattr(self.app, "logger", None)
        if logger is not None:
            try:
                getattr(logger, level, logger.info)(message, *args)
            except Exception:
                pass
