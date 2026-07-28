"""Local discovery and domain types for ComfyUI custom-node plugins.

This module deliberately does not alter the existing ComfyUI update service.
Network checks, dependency planning, and mutating operations are added in later
layers; ``scan_local`` only inspects direct children of ``custom_nodes``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
import os
from pathlib import Path, PurePath
import re
import shutil
import stat
import subprocess
from typing import Literal, Optional, Sequence, Tuple
from urllib.parse import urlsplit, urlunsplit
import uuid

from utils.common import run_hidden


class PluginState(str, Enum):
    LOCAL_ONLY = "local_only"
    UP_TO_DATE = "up_to_date"
    UPDATE_AVAILABLE = "update_available"
    LOCAL_CHANGES = "local_changes"
    LOCAL_AHEAD = "local_ahead"
    DIVERGED = "diverged"
    DETACHED_HEAD = "detached_head"
    NO_UPSTREAM = "no_upstream"
    NO_REMOTE = "no_remote"
    NON_GIT = "non_git"
    UNBORN_HEAD = "unborn_head"
    REPOSITORY_ERROR = "repository_error"
    OUTSIDE_CUSTOM_NODES = "outside_custom_nodes"
    NESTED_OR_EXTERNAL_REPO = "nested_or_external_repo"
    SUBMODULES_UNSUPPORTED = "submodules_unsupported"
    CHECK_FAILED = "check_failed"
    UPDATE_FAILED = "update_failed"
    REMOVED = "removed"
    CANCELLED = "cancelled"


class UpdateAvailability(str, Enum):
    UNKNOWN = "unknown"
    CHECKING = "checking"
    UP_TO_DATE = "up_to_date"
    AVAILABLE = "available"
    NOT_CHECKABLE = "not_checkable"
    CHECK_FAILED = "check_failed"


class DependencyState(str, Enum):
    UNKNOWN = "unknown"
    CHECKING = "checking"
    NOT_CHANGED = "not_changed"
    SATISFIED = "satisfied"
    SAFE_TO_INSTALL = "safe_to_install"
    COMPATIBLE_UPGRADE = "compatible_upgrade"
    INCOMPATIBLE_CONSTRAINTS_BLOCKED = "incompatible_constraints_blocked"
    DOWNGRADE_BLOCKED = "downgrade_blocked"
    GLOBAL_INDEX_INCOMPLETE = "global_index_incomplete"
    PROTECTED_PACKAGE_BLOCKED = "protected_package_blocked"
    NON_STANDARD_SOURCE_BLOCKED = "non_standard_source_blocked"
    SOURCE_BUILD_BLOCKED = "source_build_blocked"
    CUSTOM_SCRIPT_BLOCKED = "custom_script_blocked"
    PREFLIGHT_FAILED = "preflight_failed"
    INSTALLING = "installing"
    INSTALL_FAILED = "install_failed"
    READY = "ready"


@dataclass
class PluginRecord:
    name: str
    path: Path
    state: PluginState
    update_availability: UpdateAvailability = UpdateAvailability.UNKNOWN
    dependency_state: DependencyState = DependencyState.UNKNOWN
    head: Optional[str] = None
    local_commit_at: Optional[str] = None
    remote_commit_at: Optional[str] = None
    branch: Optional[str] = None
    upstream: Optional[str] = None
    remote_name: Optional[str] = None
    remote_url_display: Optional[str] = None
    dirty_count: int = 0
    untracked_count: int = 0
    ahead: Optional[int] = None
    behind: Optional[int] = None
    checked_at: Optional[datetime] = None
    check_generation: int = 0
    target_head: Optional[str] = None
    dependency_additions: Tuple[str, ...] = ()
    dependency_changes: Tuple[str, ...] = ()
    dependency_strict_constraints: Tuple[str, ...] = ()
    dependency_conflicts: Tuple[str, ...] = ()
    dependency_reason: str = ""
    reason: str = ""
    error_code: Optional[str] = None
    can_check: bool = False
    can_update: bool = False


@dataclass(frozen=True)
class DependencyPlan:
    plugin_path: Path
    target_head: str
    state: DependencyState
    requirements: Tuple[str, ...]
    additions: Tuple[str, ...]
    resolved_changes: Tuple[str, ...] = ()
    protected_conflicts: Tuple[str, ...] = ()
    strict_constraints: Tuple[str, ...] = ()
    conflicting_plugins: Tuple[str, ...] = ()
    conflicts: Tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class PluginInstallPreview:
    source_url_display: str
    staging_path: Path
    target_path: Path
    target_head: str
    dependency_plan: DependencyPlan
    expires_at: datetime


@dataclass
class PluginOperationResult:
    plugin_name: str
    operation: Literal["scan", "check", "update", "install", "uninstall"]
    outcome: Literal["success", "skipped", "failed", "cancelled"]
    state: PluginState
    previous_head: Optional[str] = None
    current_head: Optional[str] = None
    dependencies_added: Tuple[str, ...] = ()
    dependencies_changed: Tuple[str, ...] = ()
    message: str = ""


@dataclass(frozen=True)
class PluginInstallTarget:
    source_url: str
    source_url_display: str
    name: str
    target_path: Path
    staging_path: Path


class PluginVersionService:
    """Discovers and classifies direct custom-node directories locally."""

    _NON_REPOSITORY_MARKERS = (
        "not a git repository",
        "not a repository",
    )

    def __init__(self, app):
        self.app = app
        self._dependency_service = None
        self._install_previews = {}
        self._active_processes = set()

    def cancel_active_processes(self):
        for process in tuple(self._active_processes):
            try:
                process.terminate()
            except Exception:
                pass
        if self._dependency_service is not None:
            self._dependency_service.cancel_active_processes()

    def custom_nodes_root(self) -> Path:
        try:
            portable_root = Path(
                self.app.config.get("paths", {}).get("comfyui_root") or "."
            ).resolve()
        except Exception:
            portable_root = Path(".").resolve()
        return portable_root / "ComfyUI" / "custom_nodes"

    def scan_local(self) -> list[PluginRecord]:
        """Return every direct plugin directory without making network calls."""
        root = self.custom_nodes_root()
        if not root.is_dir():
            return []

        root_resolved = root.resolve()
        records: list[PluginRecord] = []
        try:
            candidates = sorted(root.iterdir(), key=lambda item: item.name.casefold())
        except OSError:
            return []

        for candidate in candidates:
            if candidate.name == ".git" or candidate.name.startswith(".launcher-") or not candidate.is_dir():
                continue
            records.append(self._scan_candidate(candidate, root_resolved))
        return records

    def validate_install_url(self, source_url: str) -> PluginInstallTarget:
        value = (source_url or "").strip()
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or not parsed.path.endswith(".git")
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("仅支持不含凭据的 HTTPS .git 仓库地址")
        name = PurePath(parsed.path).name[:-4]
        if not self._is_valid_plugin_name(name):
            raise ValueError("仓库名称不能作为 Windows 目录名")
        root = self.custom_nodes_root()
        if not root.is_dir():
            raise ValueError("未找到 custom_nodes 目录")
        target_path = root / name
        if target_path.exists():
            raise ValueError(f"目标目录已存在：{target_path}")
        staging_path = root / ".launcher-staging" / uuid.uuid4().hex
        return PluginInstallTarget(value, self.sanitize_remote_url(value) or value, name, target_path, staging_path)

    def clone_to_staging(self, target: PluginInstallTarget) -> PluginOperationResult:
        if self._comfyui_is_running():
            return PluginOperationResult(target.name, "install", "skipped", PluginState.CANCELLED, message="ComfyUI 正在运行")
        if target.target_path.exists():
            return PluginOperationResult(target.name, "install", "failed", PluginState.UPDATE_FAILED, message="目标目录已存在")
        try:
            target.staging_path.parent.mkdir(parents=True, exist_ok=True)
            result = self._run_git_raw("clone", "--no-recurse-submodules", target.source_url, str(target.staging_path), timeout=120)
            if result.returncode != 0:
                self.cleanup_staging(target.staging_path)
                return PluginOperationResult(target.name, "install", "failed", PluginState.UPDATE_FAILED, message=self._git_message(result))
            return PluginOperationResult(target.name, "install", "success", PluginState.LOCAL_ONLY, message="已克隆到临时目录")
        except OSError as exc:
            self.cleanup_staging(target.staging_path)
            return PluginOperationResult(target.name, "install", "failed", PluginState.UPDATE_FAILED, message=str(exc))

    def prepare_install(self, source_url: str) -> PluginInstallPreview:
        target = self.validate_install_url(source_url)
        clone = self.clone_to_staging(target)
        if clone.outcome != "success":
            raise ValueError(clone.message)
        candidate = self._scan_candidate(target.staging_path, target.staging_path.parent.resolve())
        if candidate.state in {PluginState.SUBMODULES_UNSUPPORTED, PluginState.REPOSITORY_ERROR, PluginState.NON_GIT, PluginState.UNBORN_HEAD}:
            self.cleanup_staging(target.staging_path)
            raise ValueError(candidate.reason or "临时 clone 无法作为插件安装")
        candidate.target_head = candidate.head
        dependency = self._dependencies()
        index = dependency.build_global_index(self.scan_local(), {candidate.path: candidate.head}, [candidate])
        preflight = dependency.preflight_many(index, [candidate])
        plan = preflight.plans[candidate.path.resolve()]
        if plan.state not in {
            DependencyState.NOT_CHANGED, DependencyState.SATISFIED,
            DependencyState.SAFE_TO_INSTALL, DependencyState.COMPATIBLE_UPGRADE,
        }:
            dependency.cleanup(preflight)
            self.cleanup_staging(target.staging_path)
            raise ValueError(plan.reason or "插件依赖未通过预检")
        preview = PluginInstallPreview(
            target.source_url_display, target.staging_path, target.target_path,
            candidate.head or "", plan, datetime.now() + timedelta(minutes=10),
        )
        self._install_previews[preview.staging_path.resolve()] = (target, candidate, preflight)
        return preview

    def install_from_preview(self, preview: PluginInstallPreview) -> PluginOperationResult:
        key = preview.staging_path.resolve()
        saved = self._install_previews.pop(key, None)
        if saved is None or datetime.now() > preview.expires_at:
            self.cleanup_staging(preview.staging_path)
            return PluginOperationResult(preview.target_path.name, "install", "failed", PluginState.UPDATE_FAILED, message="安装预览已过期")
        target, candidate, preflight = saved
        dependency = self._dependencies()
        try:
            if self._comfyui_is_running() or preview.target_path.exists() or not preview.staging_path.is_dir():
                return PluginOperationResult(preview.target_path.name, "install", "skipped", PluginState.CANCELLED, message="安装条件已变化")
            head = self._run_git(preview.staging_path, "rev-parse", "HEAD")
            if head.returncode != 0 or head.stdout.strip() != preview.target_head:
                return PluginOperationResult(preview.target_path.name, "install", "failed", PluginState.UPDATE_FAILED, message="临时仓库提交已变化")
            prepared = dependency.prepare_wheels(preflight)
            if not hasattr(prepared, "forward_lock"):
                return PluginOperationResult(preview.target_path.name, "install", "failed", PluginState.UPDATE_FAILED, message=prepared.message)
            installed = dependency.install_prepared(prepared)
            if not installed.success:
                return PluginOperationResult(preview.target_path.name, "install", "failed", PluginState.UPDATE_FAILED, message=installed.message)
            try:
                preview.staging_path.rename(preview.target_path)
            except OSError as exc:
                dependency.rollback(prepared)
                return PluginOperationResult(preview.target_path.name, "install", "failed", PluginState.UPDATE_FAILED, message=f"插件目录落位失败：{exc}")
            return PluginOperationResult(preview.target_path.name, "install", "success", PluginState.UP_TO_DATE, None, preview.target_head, installed.additions, installed.changes, "插件与依赖已安装")
        finally:
            dependency.cleanup(preflight)
            if preview.staging_path.exists():
                self.cleanup_staging(preview.staging_path)

    def check_one(self, record: PluginRecord) -> PluginRecord:
        """Fetch one configured upstream and classify its ahead/behind state."""
        if not record.can_check or not record.remote_name:
            return record
        fetched = self._run_git(record.path, "fetch", "--prune", record.remote_name)
        if fetched.returncode != 0:
            record.state = PluginState.CHECK_FAILED
            record.update_availability = UpdateAvailability.CHECK_FAILED
            record.can_update = False
            record.reason = self._git_message(fetched)
            record.error_code = "FETCH_FAILED"
            return record
        refreshed = self._scan_candidate(record.path, self.custom_nodes_root().resolve())
        if not refreshed.can_check or not refreshed.upstream:
            return refreshed
        counts = self._run_git(refreshed.path, "rev-list", "--left-right", "--count", f"HEAD...{refreshed.upstream}")
        try:
            ahead, behind = (int(value) for value in counts.stdout.split())
            if counts.returncode != 0 or ahead < 0 or behind < 0:
                raise ValueError
        except ValueError:
            refreshed.state = PluginState.CHECK_FAILED
            refreshed.update_availability = UpdateAvailability.CHECK_FAILED
            refreshed.reason = "无法解析 Git ahead/behind 状态"
            refreshed.error_code = "REV_LIST_INVALID"
            return refreshed
        refreshed.ahead, refreshed.behind = ahead, behind
        refreshed.checked_at = datetime.now()
        if behind:
            refreshed.update_availability = UpdateAvailability.AVAILABLE
        else:
            refreshed.update_availability = UpdateAvailability.UP_TO_DATE
        if ahead and behind:
            refreshed.state = PluginState.DIVERGED
        elif ahead:
            refreshed.state = PluginState.LOCAL_AHEAD
        elif behind:
            target = self._run_git(refreshed.path, "rev-parse", refreshed.upstream)
            if target.returncode != 0:
                refreshed.state = PluginState.CHECK_FAILED
                refreshed.update_availability = UpdateAvailability.CHECK_FAILED
                refreshed.reason = self._git_message(target)
                return refreshed
            refreshed.state = PluginState.UPDATE_AVAILABLE
            refreshed.target_head = target.stdout.strip()
        else:
            refreshed.state = PluginState.UP_TO_DATE
        # ``upstream`` has just been fetched, so its commit date is a useful,
        # verifiable version indicator for users.  Keep it separate from the
        # local HEAD date instead of relying on directory modification times.
        remote_ref = refreshed.target_head or refreshed.upstream
        if remote_ref:
            refreshed.remote_commit_at = self._commit_date(refreshed.path, remote_ref)
        refreshed.can_check = True
        refreshed.can_update = False
        return refreshed

    def refresh_all(self) -> list[PluginRecord]:
        records = [self.check_one(record) if record.can_check else record for record in self.scan_local()]
        candidates = [record for record in records if record.state == PluginState.UPDATE_AVAILABLE and record.target_head]
        if not candidates:
            return records
        dependency = self._dependencies()
        index = dependency.build_global_index(records, {record.path: record.target_head for record in candidates})
        preflight = dependency.preflight_many(index, candidates)
        for record in candidates:
            plan = preflight.plans.get(record.path.resolve())
            if plan is None:
                continue
            self._apply_dependency_plan(record, plan)
        dependency.cleanup(preflight)
        return records

    def update_one(self, record: PluginRecord) -> PluginOperationResult:
        return self.update_many([record])[0]

    def update_many(self, records: Sequence[PluginRecord]) -> list[PluginOperationResult]:
        eligible = [record for record in records if record.can_update and record.target_head]
        results = [
            PluginOperationResult(record.name, "update", "skipped", record.state, record.head, record.head, message="未通过更新预检")
            for record in records if record not in eligible
        ]
        if self._comfyui_is_running():
            return results + [
                PluginOperationResult(record.name, "update", "skipped", record.state, record.head, record.head, message="ComfyUI 正在运行")
                for record in eligible
            ]
        if not eligible:
            return results
        dependency = self._dependencies()
        all_records = self.scan_local()
        index = dependency.build_global_index(all_records, {record.path: record.target_head for record in eligible})
        preflight = dependency.preflight_many(index, eligible)
        safe = [
            record for record in eligible
            if preflight.plans[record.path.resolve()].state in {
                DependencyState.NOT_CHANGED, DependencyState.SATISFIED,
                DependencyState.SAFE_TO_INSTALL, DependencyState.COMPATIBLE_UPGRADE,
            }
        ]
        if len(safe) != len(eligible):
            for record in eligible:
                plan = preflight.plans[record.path.resolve()]
                if record not in safe:
                    self._apply_dependency_plan(record, plan)
                    results.append(PluginOperationResult(record.name, "update", "skipped", record.state, record.head, record.head, message=plan.reason))
            # A conflicting candidate must not keep safe candidates blocked by
            # its constraints. Re-resolve exactly the remaining batch.
            dependency.cleanup(preflight)
            index = dependency.build_global_index(all_records, {record.path: record.target_head for record in safe})
            preflight = dependency.preflight_many(index, safe)
            final_safe = [
                record for record in safe
                if preflight.plans[record.path.resolve()].state in {
                    DependencyState.NOT_CHANGED, DependencyState.SATISFIED,
                    DependencyState.SAFE_TO_INSTALL, DependencyState.COMPATIBLE_UPGRADE,
                }
            ]
            for record in safe:
                if record not in final_safe:
                    plan = preflight.plans[record.path.resolve()]
                    self._apply_dependency_plan(record, plan)
                    results.append(PluginOperationResult(record.name, "update", "skipped", record.state, record.head, record.head, message=plan.reason))
            safe = final_safe
        if not safe:
            dependency.cleanup(preflight)
            return results
        prepared = dependency.prepare_wheels(preflight)
        if not hasattr(prepared, "forward_lock"):
            dependency.cleanup(preflight)
            return results + [PluginOperationResult(record.name, "update", "failed", PluginState.UPDATE_FAILED, record.head, record.head, message=prepared.message) for record in safe]
        installed = dependency.install_prepared(prepared)
        if not installed.success:
            dependency.cleanup(preflight)
            return results + [PluginOperationResult(record.name, "update", "failed", PluginState.UPDATE_FAILED, record.head, record.head, message=installed.message) for record in safe]
        for record in safe:
            old_head = record.head
            merge = self._run_git(record.path, "merge", "--ff-only", record.target_head)
            if merge.returncode != 0:
                record.state = PluginState.UPDATE_FAILED
                record.reason = self._git_message(merge)
                results.append(PluginOperationResult(record.name, "update", "failed", record.state, old_head, old_head, message=record.reason))
                continue
            record.head = record.target_head
            record.state = PluginState.UP_TO_DATE
            record.update_availability = UpdateAvailability.UP_TO_DATE
            record.can_update = False
            results.append(PluginOperationResult(record.name, "update", "success", record.state, old_head, record.head, installed.additions, installed.changes, "插件与依赖已更新"))
        dependency.cleanup(preflight)
        return results

    def uninstall_one(self, record: PluginRecord) -> PluginOperationResult:
        if self._comfyui_is_running():
            return PluginOperationResult(record.name, "uninstall", "skipped", record.state, message="ComfyUI 正在运行")
        try:
            path = self._validated_direct_plugin_path(record.path)
        except ValueError as exc:
            return PluginOperationResult(record.name, "uninstall", "failed", record.state, message=str(exc))
        try:
            shutil.rmtree(path)
        except OSError as exc:
            return PluginOperationResult(record.name, "uninstall", "failed", record.state, message=f"插件目录未完全删除：{exc}")
        return PluginOperationResult(record.name, "uninstall", "success", PluginState.REMOVED, message="插件目录已删除")

    @staticmethod
    def cleanup_staging(staging_path: Path) -> None:
        shutil.rmtree(staging_path, ignore_errors=True)

    def _scan_candidate(self, candidate: Path, root_resolved: Path) -> PluginRecord:
        try:
            candidate_resolved = candidate.resolve()
        except OSError as exc:
            return self._record(
                candidate,
                PluginState.REPOSITORY_ERROR,
                reason="无法解析插件目录",
                error_code="PATH_RESOLVE_FAILED",
                detail=str(exc),
            )

        if candidate_resolved.parent != root_resolved:
            return self._record(
                candidate,
                PluginState.OUTSIDE_CUSTOM_NODES,
                reason="插件目录不在 custom_nodes 的直接子目录内",
                error_code="OUTSIDE_ROOT",
            )

        inside = self._run_git(candidate_resolved, "rev-parse", "--is-inside-work-tree")
        if inside.returncode != 0 or inside.stdout.strip().lower() != "true":
            if self._is_non_repository(inside):
                return self._record(candidate_resolved, PluginState.NON_GIT)
            return self._git_error(candidate_resolved, inside, "NOT_A_WORKTREE")

        top_level = self._run_git(candidate_resolved, "rev-parse", "--show-toplevel")
        if top_level.returncode != 0:
            return self._git_error(candidate_resolved, top_level, "TOPLEVEL_READ_FAILED")
        try:
            if Path(top_level.stdout.strip()).resolve() != candidate_resolved:
                return self._record(
                    candidate_resolved,
                    PluginState.NESTED_OR_EXTERNAL_REPO,
                    reason="Git 工作树顶层不是插件目录",
                    error_code="TOPLEVEL_MISMATCH",
                )
        except OSError:
            return self._record(
                candidate_resolved,
                PluginState.NESTED_OR_EXTERNAL_REPO,
                reason="无法验证 Git 工作树顶层",
                error_code="TOPLEVEL_MISMATCH",
            )

        return self._scan_git_repository(candidate_resolved)

    def _scan_git_repository(self, path: Path) -> PluginRecord:
        status = self._run_git(path, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        if status.returncode != 0:
            return self._git_error(path, status, "STATUS_FAILED")
        dirty_count, untracked_count = self._status_counts(status.stdout)

        head_result = self._run_git(path, "rev-parse", "--verify", "HEAD")
        if head_result.returncode != 0:
            return self._record(
                path,
                PluginState.UNBORN_HEAD,
                dirty_count=dirty_count,
                untracked_count=untracked_count,
                reason="仓库尚无首个提交",
                error_code="UNBORN_HEAD",
            )
        head = head_result.stdout.strip()
        local_commit_at = self._commit_date(path, head)

        if self._has_submodules(path):
            return self._record(
                path,
                PluginState.SUBMODULES_UNSUPPORTED,
                head=head,
                dirty_count=dirty_count,
                untracked_count=untracked_count,
                reason="插件包含 Git submodule",
                error_code="SUBMODULES_UNSUPPORTED",
            )

        branch_result = self._run_git(path, "branch", "--show-current")
        if branch_result.returncode != 0:
            return self._git_error(path, branch_result, "BRANCH_READ_FAILED", head=head)
        branch = branch_result.stdout.strip() or None

        remotes_result = self._run_git(path, "remote")
        if remotes_result.returncode != 0:
            return self._git_error(path, remotes_result, "REMOTE_READ_FAILED", head=head, branch=branch)
        remotes = tuple(item.strip() for item in remotes_result.stdout.splitlines() if item.strip())

        upstream = None
        remote_name = None
        remote_url_display = None
        if branch:
            upstream_result = self._run_git(
                path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"
            )
            if upstream_result.returncode == 0:
                upstream = upstream_result.stdout.strip() or None
                if upstream and "/" in upstream:
                    remote_name = upstream.split("/", 1)[0]
        if remote_name is None and "origin" in remotes:
            remote_name = "origin"

        if remote_name:
            url_result = self._run_git(path, "remote", "get-url", remote_name)
            if url_result.returncode == 0:
                remote_url_display = self.sanitize_remote_url(url_result.stdout.strip())

        common = {
            "head": head,
            "local_commit_at": local_commit_at,
            "branch": branch,
            "upstream": upstream,
            "remote_name": remote_name,
            "remote_url_display": remote_url_display,
            "dirty_count": dirty_count,
            "untracked_count": untracked_count,
        }
        if dirty_count or untracked_count:
            return self._record(
                path,
                PluginState.LOCAL_CHANGES,
                reason="检测到本地修改，自动更新已禁用",
                error_code="DIRTY_WORKTREE",
                can_check=bool(upstream and remote_name),
                **common,
            )
        if not branch:
            return self._record(
                path,
                PluginState.DETACHED_HEAD,
                reason="插件处于 detached HEAD 状态",
                error_code="DETACHED_HEAD",
                **common,
            )
        if not remotes:
            return self._record(
                path,
                PluginState.NO_REMOTE,
                reason="当前仓库没有远程仓库",
                error_code="NO_REMOTE",
                **common,
            )
        if not upstream or not remote_name:
            return self._record(
                path,
                PluginState.NO_UPSTREAM,
                reason="当前分支没有远程跟踪分支",
                error_code="NO_UPSTREAM",
                **common,
            )
        return self._record(path, PluginState.LOCAL_ONLY, can_check=True, **common)

    def _commit_date(self, path: Path, ref: str) -> Optional[str]:
        """Return a stable, human-readable commit date for a local Git ref."""
        result = self._run_git(path, "show", "-s", "--format=%cI", ref)
        if result.returncode != 0:
            return None
        value = result.stdout.strip()
        return value[:10] if value else None

    def _has_submodules(self, path: Path) -> bool:
        gitmodules = self._run_git(path, "cat-file", "-e", "HEAD:.gitmodules")
        if gitmodules.returncode == 0:
            return True
        status = self._run_git(path, "submodule", "status", "--recursive")
        return status.returncode == 0 and bool(status.stdout.strip())

    def _run_git(self, path: Path, *args: str) -> subprocess.CompletedProcess:
        git_path = getattr(self.app, "git_path", None)
        if not git_path:
            services = getattr(self.app, "services", None)
            git_service = getattr(services, "git", None) if services is not None else None
            if git_service is not None:
                try:
                    git_path, _ = git_service.resolve_git()
                except Exception:
                    git_path = None
        git_path = git_path or "git"
        command = [str(git_path), "-C", str(path), *args]
        result = self._execute_git(command, 60 if args and args[0] == "fetch" else 10)
        if result.returncode != 0 and "dubious ownership" in (result.stderr or "").lower():
            services = getattr(self.app, "services", None)
            git_service = getattr(services, "git", None) if services is not None else None
            if git_service is not None:
                try:
                    git_service.fix_unsafe_repo(str(path))
                    result = run_hidden(
                        command,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=10,
                    )
                except Exception:
                    pass
        return result

    def _execute_git(self, command, timeout):
        try:
            # This path is used after cloning to inspect the temporary Git
            # repository.  A GUI launch can expose an invalid inherited stdin
            # handle (WinError 6), so never let Popen inherit any standard
            # handles from the launcher process.
            kwargs = {
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
            }
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                kwargs["startupinfo"] = startupinfo
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            process = subprocess.Popen(command, **kwargs)
            self._active_processes.add(process)
            try:
                stdout, stderr = process.communicate(timeout=timeout)
                return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                process.terminate()
                stdout, stderr = process.communicate()
                return subprocess.CompletedProcess(command, 1, stdout, stderr or "Git 命令超时")
            finally:
                self._active_processes.discard(process)
        except Exception as exc:
            return subprocess.CompletedProcess(command, 1, "", str(exc))

    def _run_git_raw(self, *args: str, timeout: int) -> subprocess.CompletedProcess:
        git_path = getattr(self.app, "git_path", None) or "git"
        command = [str(git_path), *args]
        try:
            return run_hidden(
                command, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
            )
        except OSError as exc:
            # Some Windows GUI sessions expose an invalid inherited standard
            # handle even though the common hidden-process helper normally
            # guards stdin.  Retry Git with every standard handle explicitly
            # redirected so installing a plugin remains usable.
            if getattr(exc, "winerror", None) != 6 and exc.errno != 6:
                raise
            kwargs = {
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "timeout": timeout,
            }
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            return subprocess.run(command, **kwargs)

    def _dependencies(self):
        if self._dependency_service is None:
            from services.plugin_dependency_service import PluginDependencyService
            self._dependency_service = PluginDependencyService(self.app)
        return self._dependency_service

    @staticmethod
    def _apply_dependency_plan(record: PluginRecord, plan: DependencyPlan) -> None:
        record.dependency_state = plan.state
        record.dependency_additions = plan.additions
        record.dependency_changes = plan.resolved_changes
        record.dependency_strict_constraints = plan.strict_constraints
        record.dependency_conflicts = plan.conflicts
        record.dependency_reason = plan.reason
        record.can_update = plan.state in {
            DependencyState.NOT_CHANGED, DependencyState.SATISFIED,
            DependencyState.SAFE_TO_INSTALL, DependencyState.COMPATIBLE_UPGRADE,
        }

    def _validated_direct_plugin_path(self, candidate: Path) -> Path:
        root = self.custom_nodes_root().resolve()
        raw = Path(candidate)
        if not raw.exists() or not raw.is_dir() or raw.is_symlink() or self._is_reparse_point(raw):
            raise ValueError("插件目录无效或是链接目录")
        resolved = raw.resolve()
        if resolved.parent != root:
            raise ValueError("只能删除 custom_nodes 的直接子目录")
        return resolved

    def _comfyui_is_running(self) -> bool:
        checker = getattr(self.app, "_is_comfyui_running", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                return True
        return False

    @staticmethod
    def _is_valid_plugin_name(name: str) -> bool:
        reserved = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
        return bool(name and name not in {".", ".."} and name.casefold() not in reserved and not re.search(r'[<>:"/\\|?*]', name) and not name.endswith((".", " ")))

    @staticmethod
    def _is_reparse_point(path: Path) -> bool:
        attributes = getattr(os.stat(path, follow_symlinks=False), "st_file_attributes", 0)
        return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))

    @staticmethod
    def _git_message(result: subprocess.CompletedProcess) -> str:
        return ((result.stderr or result.stdout or "Git 操作失败").strip())[:500]

    @staticmethod
    def sanitize_remote_url(remote_url: str) -> Optional[str]:
        value = (remote_url or "").strip()
        if not value:
            return None
        if "://" in value:
            parsed = urlsplit(value)
            host = parsed.hostname or ""
            if parsed.port:
                host = f"{host}:{parsed.port}"
            return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
        if "@" in value and ":" in value:
            return value.split("@", 1)[1]
        return value

    @staticmethod
    def _status_counts(output: str) -> tuple[int, int]:
        dirty_count = 0
        untracked_count = 0
        for entry in (output or "").split("\0"):
            if not entry:
                continue
            if entry.startswith("??"):
                untracked_count += 1
            else:
                dirty_count += 1
        return dirty_count, untracked_count

    def _git_error(self, path: Path, result: subprocess.CompletedProcess, error_code: str, **fields) -> PluginRecord:
        detail = (result.stderr or result.stdout or "Git 命令失败").strip()
        return self._record(
            path,
            PluginState.REPOSITORY_ERROR,
            reason="无法读取 Git 仓库状态",
            error_code=error_code,
            detail=detail,
            **fields,
        )

    @classmethod
    def _is_non_repository(cls, result: subprocess.CompletedProcess) -> bool:
        text = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
        return any(marker in text for marker in cls._NON_REPOSITORY_MARKERS)

    @staticmethod
    def _record(path: Path, state: PluginState, detail: str = "", **fields) -> PluginRecord:
        reason = fields.pop("reason", "")
        if detail:
            reason = f"{reason}：{detail}" if reason else detail
        return PluginRecord(name=path.name, path=path, state=state, reason=reason, **fields)
