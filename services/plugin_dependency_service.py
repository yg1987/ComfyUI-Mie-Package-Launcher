"""Static dependency indexing and preflight classification for plugins."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tempfile
from typing import Iterable, Mapping, Optional, Sequence
import shutil

from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from services.plugin_version_service import DependencyPlan, DependencyState, PluginRecord
from services.update_service import FROZEN_PKGS
from utils.common import run_hidden
from utils import paths as PATHS

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - supported by the bundled runtime
    import tomli as tomllib


class DependencyParseError(ValueError):
    pass


@dataclass(frozen=True)
class IndexedRequirement:
    plugin_name: str
    plugin_path: Path
    source: str
    requirement: Requirement

    @property
    def package_name(self) -> str:
        return canonicalize_name(self.requirement.name)


@dataclass(frozen=True)
class PluginDependencyDeclaration:
    plugin_name: str
    plugin_path: Path
    requirements: tuple[IndexedRequirement, ...]
    incomplete_reason: str = ""

    @property
    def is_complete(self) -> bool:
        return not self.incomplete_reason


@dataclass(frozen=True)
class GlobalDependencyIndex:
    declarations: tuple[PluginDependencyDeclaration, ...]

    @property
    def requirements(self) -> tuple[IndexedRequirement, ...]:
        return tuple(requirement for item in self.declarations for requirement in item.requirements)

    def declaration_for(self, path: Path) -> Optional[PluginDependencyDeclaration]:
        normalized = path.resolve()
        return next((item for item in self.declarations if item.plugin_path == normalized), None)

    @property
    def incomplete_declarations(self) -> tuple[PluginDependencyDeclaration, ...]:
        return tuple(item for item in self.declarations if not item.is_complete)


@dataclass(frozen=True)
class DependencyPreflight:
    index: GlobalDependencyIndex
    plans: Mapping[Path, DependencyPlan]
    current_versions: Mapping[str, str]
    planned_versions: Mapping[str, str]
    baseline_check: tuple[str, ...]
    report: Mapping
    work_dir: Path


@dataclass(frozen=True)
class PreparedDependencyBatch:
    preflight: DependencyPreflight
    wheelhouse: Path
    forward_lock: Path
    previous_lock: Path
    additions: tuple[str, ...]
    changed_packages: tuple[str, ...]


@dataclass(frozen=True)
class DependencyInstallResult:
    success: bool
    rolled_back: bool
    message: str
    additions: tuple[str, ...] = ()
    changes: tuple[str, ...] = ()


class PluginDependencyService:
    """Reads static declarations and maps a resolver report to plugin plans."""

    def __init__(self, app):
        self.app = app
        self._active_processes = set()

    def cancel_active_processes(self):
        for process in tuple(self._active_processes):
            try:
                process.terminate()
            except Exception:
                pass

    def build_global_index(
        self,
        installed_records: Sequence[PluginRecord],
        target_overrides: Mapping[Path, str],
        install_candidates: Sequence[PluginRecord] = (),
    ) -> GlobalDependencyIndex:
        records_by_path: dict[Path, PluginRecord] = {}
        for record in (*installed_records, *install_candidates):
            records_by_path[record.path.resolve()] = record

        declarations = []
        normalized_overrides = {Path(path).resolve(): target for path, target in target_overrides.items()}
        for path, record in sorted(records_by_path.items(), key=lambda item: item[1].name.casefold()):
            target = normalized_overrides.get(path)
            declarations.append(self._read_declaration(record, target))
        return GlobalDependencyIndex(tuple(declarations))

    def plans_from_report(
        self,
        index: GlobalDependencyIndex,
        candidates: Sequence[PluginRecord],
        current_versions: Mapping[str, str],
        report: Mapping,
    ) -> dict[Path, DependencyPlan]:
        """Classify every candidate using pip's non-mutating dry-run report.

        ``report`` is expected to be pip's JSON report. Entries not present in
        its install list retain their current installed version.
        """
        current = {canonicalize_name(name): str(version) for name, version in current_versions.items()}
        planned = dict(current)
        for item in report.get("install", ()):
            metadata = item.get("metadata") or {}
            name = metadata.get("name")
            version = metadata.get("version")
            if name and version:
                planned[canonicalize_name(name)] = str(version)

        changed_packages = {
            name
            for name, version in planned.items()
            if current.get(name) != version
        }
        global_conflicts = self._global_conflicts(index, planned, changed_packages)
        incomplete_names = tuple(item.plugin_name for item in index.incomplete_declarations)
        plans: dict[Path, DependencyPlan] = {}
        for candidate in candidates:
            declaration = index.declaration_for(candidate.path)
            plans[candidate.path.resolve()] = self._build_plan(
                candidate,
                declaration,
                current,
                planned,
                changed_packages,
                global_conflicts,
                incomplete_names,
            )
        return plans

    def _build_plan(
        self,
        candidate: PluginRecord,
        declaration: Optional[PluginDependencyDeclaration],
        current: Mapping[str, str],
        planned: Mapping[str, str],
        changed_packages: set[str],
        global_conflicts: Mapping[str, tuple[IndexedRequirement, ...]],
        incomplete_names: tuple[str, ...],
    ) -> DependencyPlan:
        target_head = candidate.target_head or candidate.head or ""
        if declaration is None:
            return self._plan(candidate, target_head, DependencyState.PREFLIGHT_FAILED, reason="候选插件不在依赖索引中")
        if not declaration.is_complete:
            return self._plan(candidate, target_head, DependencyState.PREFLIGHT_FAILED, reason=declaration.incomplete_reason)

        requirements = tuple(item.requirement for item in declaration.requirements)
        requirement_strings = tuple(str(item) for item in requirements)
        if not requirements:
            return self._plan(candidate, target_head, DependencyState.NOT_CHANGED)

        candidate_packages = {item.package_name for item in declaration.requirements}
        additions: list[str] = []
        changes: list[str] = []
        conflicts: list[str] = []
        protected: list[str] = []
        downgrade = False
        incompatible = False

        for item in declaration.requirements:
            package = item.package_name
            target_version = planned.get(package)
            current_version = current.get(package)
            if target_version is None:
                incompatible = True
                conflicts.append(f"{candidate.name}: {item.requirement} 未出现在解析计划中")
                continue
            if not self._version_satisfies(target_version, item.requirement):
                incompatible = True
                conflicts.append(
                    f"{candidate.name}: {item.requirement}，解析版本为 {target_version}"
                )
            if current_version is None:
                additions.append(f"{package}: 未安装 → {target_version}")
            elif current_version != target_version:
                changes.append(f"{package}: {current_version} → {target_version}")
                comparison = self._compare_versions(target_version, current_version)
                if comparison is not None and comparison < 0:
                    downgrade = True
                    conflicts.append(
                        f"{candidate.name}: {package} 当前 {current_version}，计划 {target_version}，约束 {item.requirement.specifier or '任意版本'}"
                    )
                if package in FROZEN_PKGS:
                    protected.append(f"{package}: {current_version} → {target_version}")

        for package in candidate_packages & changed_packages:
            for conflicting in global_conflicts.get(package, ()):
                if conflicting.plugin_path != candidate.path.resolve():
                    conflicts.append(
                        f"{candidate.name} 与 {conflicting.plugin_name}: {package} 计划 {planned[package]} 不满足 {conflicting.requirement.specifier or '任意版本'}"
                    )
                    incompatible = True

        if protected:
            return self._plan(
                candidate, target_head, DependencyState.PROTECTED_PACKAGE_BLOCKED,
                additions, changes, tuple(protected), tuple(conflicts), "插件要求修改受保护核心依赖",
                requirements=requirement_strings,
            )
        if downgrade:
            return self._plan(
                candidate, target_head, DependencyState.DOWNGRADE_BLOCKED,
                additions, changes, (), tuple(conflicts), "解析计划会降级现有依赖",
                requirements=requirement_strings,
            )
        if incompatible:
            related = tuple(sorted({item.plugin_name for values in global_conflicts.values() for item in values if item.plugin_name != candidate.name}))
            return self._plan(
                candidate, target_head, DependencyState.INCOMPATIBLE_CONSTRAINTS_BLOCKED,
                additions, changes, (), tuple(conflicts), "依赖约束与已安装插件不兼容", related,
                requirements=requirement_strings,
            )
        if changed_packages and incomplete_names:
            return self._plan(
                candidate, target_head, DependencyState.GLOBAL_INDEX_INCOMPLETE,
                additions, changes, (), (), "无法完整验证其他插件兼容性：" + "、".join(incomplete_names),
                requirements=requirement_strings,
            )
        if additions:
            return self._plan(candidate, target_head, DependencyState.SAFE_TO_INSTALL, additions, changes, requirements=requirement_strings)
        if changes:
            return self._plan(candidate, target_head, DependencyState.COMPATIBLE_UPGRADE, additions, changes, requirements=requirement_strings)
        return self._plan(candidate, target_head, DependencyState.SATISFIED, requirements=requirement_strings)

    @staticmethod
    def _plan(
        candidate: PluginRecord,
        target_head: str,
        state: DependencyState,
        additions: Iterable[str] = (),
        changes: Iterable[str] = (),
        protected: Iterable[str] = (),
        conflicts: Iterable[str] = (),
        reason: str = "",
        conflicting_plugins: Iterable[str] = (),
        requirements: Iterable[str] = (),
    ) -> DependencyPlan:
        return DependencyPlan(
            plugin_path=candidate.path.resolve(),
            target_head=target_head,
            state=state,
            requirements=tuple(requirements),
            additions=tuple(additions),
            resolved_changes=tuple(changes),
            protected_conflicts=tuple(protected),
            conflicting_plugins=tuple(conflicting_plugins),
            conflicts=tuple(conflicts),
            reason=reason,
        )

    def preflight_many(
        self,
        index: GlobalDependencyIndex,
        candidates: Sequence[PluginRecord],
    ) -> DependencyPreflight:
        """Run pip's resolver in dry-run mode without changing the environment."""
        work_dir = Path(tempfile.mkdtemp(prefix=".launcher-plugin-deps-"))
        current_versions = self._installed_versions()
        baseline_check = self._pip_check()
        combined = work_dir / "combined.txt"
        combined.write_text(
            "\n".join(str(item.requirement) for item in index.requirements) + "\n",
            encoding="utf-8",
        )
        report_path = work_dir / "plan.json"
        constraints = work_dir / "protected-constraints.txt"
        constraints.write_text(
            "\n".join(
                f"{name}=={current_versions[canonicalize_name(name)]}"
                for name in sorted(FROZEN_PKGS)
                if canonicalize_name(name) in current_versions
            ) + "\n",
            encoding="utf-8",
        )
        if not index.requirements:
            return DependencyPreflight(index, self.plans_from_report(index, candidates, current_versions, {"install": []}), current_versions, dict(current_versions), (), {"install": []}, work_dir)

        command = [
            self._python_exec(), "-m", "pip", "install", "--dry-run", "--report", str(report_path),
            "--upgrade-strategy", "only-if-needed", "-r", str(combined), "-c", str(constraints),
        ]
        index_url = self._index_url()
        if index_url:
            command.extend(["--index-url", index_url])
        result = self._run(command, timeout=120)
        if result.returncode != 0 or not report_path.exists():
            return DependencyPreflight(
                index,
                self._failed_plans(candidates, "依赖 resolver 预检失败：" + self._command_error(result)),
                current_versions,
                dict(current_versions),
                baseline_check,
                {},
                work_dir,
            )
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return DependencyPreflight(
                index,
                self._failed_plans(candidates, "无法读取 pip 预检报告：" + str(exc)),
                current_versions,
                dict(current_versions),
                baseline_check,
                {},
                work_dir,
            )
        plans = self.plans_from_report(index, candidates, current_versions, report)
        planned_versions = self._planned_versions(current_versions, report)
        return DependencyPreflight(index, plans, current_versions, planned_versions, baseline_check, report, work_dir)

    def prepare_wheels(self, preflight: DependencyPreflight) -> PreparedDependencyBatch | DependencyInstallResult:
        """Download exact forward and rollback wheels before mutating Python."""
        blocked = [plan for plan in preflight.plans.values() if plan.state not in {
            DependencyState.NOT_CHANGED, DependencyState.SATISFIED,
            DependencyState.SAFE_TO_INSTALL, DependencyState.COMPATIBLE_UPGRADE,
        }]
        if blocked:
            return DependencyInstallResult(False, False, "存在未通过预检的插件依赖")

        wheelhouse = preflight.work_dir / "wheelhouse"
        wheelhouse.mkdir(exist_ok=True)
        changed = tuple(sorted(
            name for name, version in preflight.planned_versions.items()
            if preflight.current_versions.get(name) != version
        ))
        additions = tuple(name for name in changed if name not in preflight.current_versions)
        forward_rows: list[str] = []
        previous_rows: list[str] = []
        for name in changed:
            target = preflight.planned_versions[name]
            downloaded = self._download_wheel(wheelhouse, f"{name}=={target}")
            if not downloaded:
                return DependencyInstallResult(False, False, f"无法准备 {name}=={target} 的 wheel")
            forward_rows.append(self._lock_row(name, target, downloaded))
            previous = preflight.current_versions.get(name)
            if previous:
                downloaded_previous = self._download_wheel(wheelhouse, f"{name}=={previous}")
                if not downloaded_previous:
                    return DependencyInstallResult(False, False, f"无法准备 {name}=={previous} 的回退 wheel")
                previous_rows.append(self._lock_row(name, previous, downloaded_previous))

        forward_lock = preflight.work_dir / "forward.lock"
        previous_lock = preflight.work_dir / "previous.lock"
        forward_lock.write_text("\n".join(forward_rows) + ("\n" if forward_rows else ""), encoding="utf-8")
        previous_lock.write_text("\n".join(previous_rows) + ("\n" if previous_rows else ""), encoding="utf-8")
        return PreparedDependencyBatch(preflight, wheelhouse, forward_lock, previous_lock, additions, changed)

    def install_prepared(self, batch: PreparedDependencyBatch) -> DependencyInstallResult:
        """Install the prepared lock offline, validate it, and roll back on failure."""
        if not batch.changed_packages:
            return DependencyInstallResult(True, False, "依赖已满足")
        install = self._run([
            self._python_exec(), "-m", "pip", "install", "--no-index", "--no-deps",
            "--find-links", str(batch.wheelhouse), "--require-hashes", "-r", str(batch.forward_lock),
        ], timeout=300)
        if install.returncode == 0 and self._validation_has_no_new_errors(batch.preflight.baseline_check):
            changes = tuple(
                f"{name}: {batch.preflight.current_versions.get(name, '未安装')} → {batch.preflight.planned_versions[name]}"
                for name in batch.changed_packages
            )
            return DependencyInstallResult(True, False, "依赖安装并校验成功", batch.additions, changes)
        reason = self._command_error(install)
        rolled_back = self.rollback(batch)
        return DependencyInstallResult(False, rolled_back, "依赖安装或校验失败：" + reason)

    def rollback(self, batch: PreparedDependencyBatch) -> bool:
        """Restore only packages changed by this batch; never touch unrelated packages."""
        success = True
        if batch.previous_lock.read_text(encoding="utf-8").strip():
            restore = self._run([
                self._python_exec(), "-m", "pip", "install", "--no-index", "--no-deps",
                "--find-links", str(batch.wheelhouse), "--require-hashes", "-r", str(batch.previous_lock),
            ], timeout=300)
            success = success and restore.returncode == 0
        if batch.additions:
            uninstall = self._run([self._python_exec(), "-m", "pip", "uninstall", "-y", *batch.additions], timeout=120)
            success = success and uninstall.returncode == 0
        return success and self._validation_has_no_new_errors(batch.preflight.baseline_check)

    @staticmethod
    def cleanup(preflight: DependencyPreflight) -> None:
        shutil.rmtree(preflight.work_dir, ignore_errors=True)

    def _download_wheel(self, wheelhouse: Path, specification: str) -> Optional[Path]:
        before = set(wheelhouse.iterdir())
        command = [
            self._python_exec(), "-m", "pip", "download", "--only-binary=:all:", "--no-deps",
            "--dest", str(wheelhouse), specification,
        ]
        index_url = self._index_url()
        if index_url:
            command.extend(["--index-url", index_url])
        result = self._run(command, timeout=300)
        if result.returncode != 0:
            return None
        created = [path for path in wheelhouse.iterdir() if path not in before and path.suffix == ".whl"]
        return created[0] if len(created) == 1 else None

    @staticmethod
    def _lock_row(name: str, version: str, wheel: Path) -> str:
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        return f"{name}=={version} --hash=sha256:{digest}"

    def _installed_versions(self) -> dict[str, str]:
        script = (
            "import json, importlib.metadata as m; "
            "print(json.dumps({d.metadata['Name']: d.version for d in m.distributions() if d.metadata.get('Name')}))"
        )
        result = self._run([self._python_exec(), "-c", script], timeout=30)
        if result.returncode != 0:
            return {}
        try:
            return {canonicalize_name(name): str(version) for name, version in json.loads(result.stdout).items()}
        except (TypeError, json.JSONDecodeError):
            return {}

    def _pip_check(self) -> tuple[str, ...]:
        result = self._run([self._python_exec(), "-m", "pip", "check"], timeout=60)
        output = (result.stdout or result.stderr or "").strip()
        if result.returncode == 0 or not output:
            return ()
        return tuple(line.strip() for line in output.splitlines() if line.strip())

    def _validation_has_no_new_errors(self, baseline: tuple[str, ...]) -> bool:
        return set(self._pip_check()).issubset(set(baseline))

    def _python_exec(self) -> str:
        try:
            portable_root = Path(self.app.config.get("paths", {}).get("comfyui_root") or ".").resolve()
            comfy_root = portable_root / "ComfyUI"
            configured = self.app.config.get("paths", {}).get("python_path", "python_embeded/python.exe")
            return str(PATHS.resolve_python_exec(comfy_root, configured))
        except Exception:
            return "python"

    def _index_url(self) -> Optional[str]:
        try:
            mode = self.app.pypi_proxy_mode.get()
            if mode == "custom":
                return (self.app.pypi_proxy_url.get() or "").strip() or None
            if mode == "none":
                return "https://pypi.org/simple/"
        except Exception:
            pass
        return None

    def _run(self, command: Sequence[str], timeout: int) -> subprocess.CompletedProcess:
        command = list(command)
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
            self._active_processes.add(process)
            try:
                stdout, stderr = process.communicate(timeout=timeout)
                return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                process.terminate()
                stdout, stderr = process.communicate()
                return subprocess.CompletedProcess(command, 1, stdout, stderr or "pip 命令超时")
            finally:
                self._active_processes.discard(process)
        except Exception as exc:
            return subprocess.CompletedProcess(command, 1, "", str(exc))

    @staticmethod
    def _planned_versions(current: Mapping[str, str], report: Mapping) -> dict[str, str]:
        planned = dict(current)
        for item in report.get("install", ()):
            metadata = item.get("metadata") or {}
            if metadata.get("name") and metadata.get("version"):
                planned[canonicalize_name(metadata["name"])] = str(metadata["version"])
        return planned

    def _failed_plans(self, candidates: Sequence[PluginRecord], reason: str) -> dict[Path, DependencyPlan]:
        return {
            candidate.path.resolve(): self._plan(
                candidate, candidate.target_head or candidate.head or "", DependencyState.PREFLIGHT_FAILED, reason=reason
            )
            for candidate in candidates
        }

    @staticmethod
    def _command_error(result: subprocess.CompletedProcess) -> str:
        message = (result.stderr or result.stdout or "未知错误").strip().replace("\n", " ")
        return message[:500]

    def _read_declaration(self, record: PluginRecord, target_head: Optional[str]) -> PluginDependencyDeclaration:
        try:
            requirements = list(self._read_requirements(record, target_head))
            requirements.extend(self._read_pyproject_dependencies(record, target_head))
            if self._source_exists(record, target_head, "install.py"):
                return PluginDependencyDeclaration(record.name, record.path.resolve(), tuple(requirements), "包含 install.py，无法静态确认依赖")
            return PluginDependencyDeclaration(record.name, record.path.resolve(), tuple(requirements))
        except DependencyParseError as exc:
            return PluginDependencyDeclaration(record.name, record.path.resolve(), (), str(exc))

    def _read_requirements(self, record: PluginRecord, target_head: Optional[str]) -> tuple[IndexedRequirement, ...]:
        if not self._source_exists(record, target_head, "requirements.txt"):
            return ()
        environment = default_environment()
        output: list[IndexedRequirement] = []
        visited: set[str] = set()

        def read_file(relative: str, depth: int) -> None:
            if depth > 5:
                raise DependencyParseError("requirements 引用层级超过 5 层")
            normalized = self._normalize_relative_path(relative)
            if normalized in visited:
                raise DependencyParseError("requirements 存在循环引用")
            visited.add(normalized)
            text = self._read_source(record, target_head, normalized)
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("-r ") or line.startswith("--requirement "):
                    included = line.split(None, 1)[1]
                    parent = PurePosixPath(normalized).parent
                    read_file(str(parent / included), depth + 1)
                    continue
                if line.startswith("-"):
                    raise DependencyParseError(f"不支持 requirements 选项：{line}")
                try:
                    requirement = Requirement(line)
                except InvalidRequirement as exc:
                    raise DependencyParseError(f"requirements 语法错误 {normalized}: {line}") from exc
                if requirement.marker is None or requirement.marker.evaluate(environment):
                    output.append(IndexedRequirement(record.name, record.path.resolve(), normalized, requirement))

        read_file("requirements.txt", 0)
        return tuple(output)

    def _read_pyproject_dependencies(self, record: PluginRecord, target_head: Optional[str]) -> tuple[IndexedRequirement, ...]:
        if not self._source_exists(record, target_head, "pyproject.toml"):
            return ()
        try:
            data = tomllib.loads(self._read_source(record, target_head, "pyproject.toml"))
        except Exception as exc:
            raise DependencyParseError("pyproject.toml 无法解析") from exc
        project = data.get("project") or {}
        dynamic = project.get("dynamic") or ()
        if "dependencies" in dynamic:
            raise DependencyParseError("pyproject.toml 使用动态 dependencies")
        environment = default_environment()
        indexed = []
        for raw in project.get("dependencies") or ():
            try:
                requirement = Requirement(raw)
            except InvalidRequirement as exc:
                raise DependencyParseError(f"pyproject.toml dependencies 语法错误：{raw}") from exc
            if requirement.marker is None or requirement.marker.evaluate(environment):
                indexed.append(IndexedRequirement(record.name, record.path.resolve(), "pyproject.toml", requirement))
        return tuple(indexed)

    def _source_exists(self, record: PluginRecord, target_head: Optional[str], relative: str) -> bool:
        try:
            self._read_source(record, target_head, relative)
            return True
        except (DependencyParseError, FileNotFoundError):
            return False

    def _read_source(self, record: PluginRecord, target_head: Optional[str], relative: str) -> str:
        normalized = self._normalize_relative_path(relative)
        if target_head and record.head:
            git_path = getattr(self.app, "git_path", None) or "git"
            result = run_hidden(
                [str(git_path), "-C", str(record.path), "show", f"{target_head}:{normalized}"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            )
            if result.returncode != 0:
                raise FileNotFoundError(normalized)
            return result.stdout
        source_path = (record.path / Path(*PurePosixPath(normalized).parts)).resolve()
        if source_path.parent != record.path.resolve() and record.path.resolve() not in source_path.parents:
            raise DependencyParseError("依赖文件路径越界")
        try:
            return source_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise DependencyParseError(f"无法读取依赖文件：{normalized}") from exc

    @staticmethod
    def _normalize_relative_path(value: str) -> str:
        path = PurePosixPath(value.strip())
        if not value or path.is_absolute() or ".." in path.parts:
            raise DependencyParseError("requirements 引用了仓库外路径")
        normalized = str(path)
        if normalized in ("", "."):
            raise DependencyParseError("requirements 引用路径无效")
        return normalized

    @staticmethod
    def _version_satisfies(version: str, requirement: Requirement) -> bool:
        try:
            return Version(version) in requirement.specifier
        except InvalidVersion:
            return False

    @staticmethod
    def _compare_versions(left: str, right: str) -> Optional[int]:
        try:
            return (Version(left) > Version(right)) - (Version(left) < Version(right))
        except InvalidVersion:
            return None

    def _global_conflicts(
        self,
        index: GlobalDependencyIndex,
        planned: Mapping[str, str],
        changed_packages: set[str],
    ) -> dict[str, tuple[IndexedRequirement, ...]]:
        conflicts: dict[str, tuple[IndexedRequirement, ...]] = {}
        for package in changed_packages:
            version = planned.get(package)
            if version is None:
                continue
            failed = tuple(
                item for item in index.requirements
                if item.package_name == package and not self._version_satisfies(version, item.requirement)
            )
            if failed:
                conflicts[package] = failed
        return conflicts
