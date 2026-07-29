"""Guards for the sidebar-to-page registration in ``qt_app._setup_ui``."""

import ast
from pathlib import Path


QT_APP = Path(__file__).resolve().parents[2] / "ui_qt" / "qt_app.py"
EXPECTED_PAGE_ENTRIES = [
    ("launch", "page_launch"),
    ("logs", "page_logs"),
    ("plugins", "page_plugins"),
    ("plugin_versions", "page_plugin_versions"),
    ("version", "page_version"),
    ("models", "page_models"),
    ("symlinks", "page_symlinks"),
    ("tasks", "page_tasks"),
    ("settings", "page_settings"),
    ("about", "page_about_me"),
    ("comfyui", "page_about_comfyui"),
    ("about_launcher", "page_about_launcher"),
]


def _assignment(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            return node.value
    raise AssertionError(f"Missing {name!r} assignment in qt_app.py")


def test_navigation_buttons_and_registered_pages_have_one_identical_order():
    """Every sidebar button must select the page carrying the same key."""
    source = QT_APP.read_text(encoding="utf-8")
    tree = ast.parse(source)

    buttons = _assignment(tree, "btns")
    assert isinstance(buttons, ast.Dict)
    button_keys = [key.value for key in buttons.keys if isinstance(key, ast.Constant)]

    entries = _assignment(tree, "page_entries")
    assert isinstance(entries, (ast.Tuple, ast.List))
    actual_entries = []
    for entry in entries.elts:
        assert isinstance(entry, ast.Tuple) and len(entry.elts) == 2
        key, page = entry.elts
        assert isinstance(key, ast.Constant) and isinstance(key.value, str)
        assert isinstance(page, ast.Name)
        actual_entries.append((key.value, page.id))

    assert actual_entries == EXPECTED_PAGE_ENTRIES
    assert button_keys == [key for key, _page in EXPECTED_PAGE_ENTRIES]
    assert "pages = dict(page_entries)" in source
    assert "content.addWidget(wrap_in_scroll(page))" in source
