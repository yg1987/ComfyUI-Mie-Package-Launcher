"""Persistent, per-environment cache for the self-managed plugin page.

The cache deliberately lives beside ``launcher/config.json`` but is separate
from user configuration.  It contains only derived Git/update information and
is safe to discard at any time.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from config.manager import atomic_write_json


class PluginStatusCache:
    SCHEMA_VERSION = 1

    def __init__(self, app):
        self.app = app

    def load(self, environment_id: str, custom_nodes_root: Path) -> tuple[dict[str, dict], str | None]:
        """Return cached rows only when they belong to this exact environment."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("schema_version") != self.SCHEMA_VERSION:
                return {}, None
            entry = (data.get("environments") or {}).get(environment_id) or {}
            if entry.get("custom_nodes_root") != str(custom_nodes_root.resolve()):
                return {}, None
            plugins = entry.get("plugins") or {}
            return (
                {name: value for name, value in plugins.items() if isinstance(name, str) and isinstance(value, dict)},
                entry.get("checked_at") if isinstance(entry.get("checked_at"), str) else None,
            )
        except (OSError, ValueError, TypeError):
            return {}, None

    def save(self, environment_id: str, custom_nodes_root: Path, records: Iterable[Any]) -> None:
        """Atomically replace this environment's derived plugin results."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("schema_version") != self.SCHEMA_VERSION:
                data = {"schema_version": self.SCHEMA_VERSION, "environments": {}}
        except (OSError, ValueError, TypeError):
            data = {"schema_version": self.SCHEMA_VERSION, "environments": {}}

        environments = data.setdefault("environments", {})
        environments[environment_id] = {
            "custom_nodes_root": str(custom_nodes_root.resolve()),
            "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "plugins": {record.name: self._record_payload(record) for record in records},
        }
        atomic_write_json(self.path, data)

    @property
    def path(self) -> Path:
        manager = getattr(self.app, "config_manager", None)
        config_file = getattr(manager, "config_file", None)
        if config_file:
            return Path(config_file).with_name("plugin_status_cache.json")
        return Path.cwd() / "launcher" / "plugin_status_cache.json"

    @staticmethod
    def _record_payload(record: Any) -> dict[str, Any]:
        def value(name: str, default=None):
            item = getattr(record, name, default)
            return getattr(item, "value", item)

        checked_at = value("checked_at")
        return {
            "state": value("state"),
            "update_availability": value("update_availability"),
            "dependency_state": value("dependency_state"),
            "head": value("head"),
            "local_commit_at": value("local_commit_at"),
            "remote_commit_at": value("remote_commit_at"),
            "branch": value("branch"),
            "upstream": value("upstream"),
            "remote_name": value("remote_name"),
            "remote_url_display": value("remote_url_display"),
            "dirty_count": value("dirty_count", 0),
            "untracked_count": value("untracked_count", 0),
            "ahead": value("ahead"),
            "behind": value("behind"),
            "checked_at": checked_at.isoformat() if isinstance(checked_at, datetime) else None,
            "target_head": value("target_head"),
            "dependency_additions": list(value("dependency_additions", ()) or ()),
            "dependency_changes": list(value("dependency_changes", ()) or ()),
            "dependency_strict_constraints": list(value("dependency_strict_constraints", ()) or ()),
            "dependency_conflicts": list(value("dependency_conflicts", ()) or ()),
            "dependency_reason": value("dependency_reason", ""),
            "reason": value("reason", ""),
            "error_code": value("error_code"),
            "can_check": bool(value("can_check", False)),
            "can_update": bool(value("can_update", False)),
        }
