import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import build


class TestBuildReleaseName(unittest.TestCase):
    def test_generated_enigma_project_uses_current_dist_files_not_template_paths(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist_dir = root / "dist"
            (dist_dir / "assets").mkdir(parents=True)
            input_exe = dist_dir / "ComfyUI_Launcher_Internal.exe"
            input_exe.write_bytes(b"exe")
            (dist_dir / "assets" / "rabbit.png").write_bytes(b"png")
            (dist_dir / "PyQt5").mkdir()
            (dist_dir / "PyQt5" / "QtCore.pyd").write_bytes(b"pyd")
            template = root / "template.evb"
            template.write_text(
                '<?xml version="1.0" encoding="windows-1252"?>\n<>\n'
                '  <Registries><Enabled>False</Enabled></Registries>\n</>\n',
                encoding="utf-8",
            )
            output_project = dist_dir / "enigma-build.evb"

            build.generate_enigma_project(
                str(template), str(dist_dir), str(input_exe),
                str(dist_dir / "ComfyUI_Launcher_Internal_boxed.exe"), str(output_project),
            )

            content = output_project.read_text(encoding="utf-8")
            self.assertIn(str(input_exe), content)
            self.assertIn("rabbit.png", content)
            self.assertIn("QtCore.pyd", content)
            self.assertNotIn(r"F:\ComfyUI-Mie-Package-Launcher", content)

    @patch("build.os.path.isfile", return_value=True)
    @patch("build.os.path.abspath", return_value=r"D:\\CodexTools\\launcher-build\\Scripts\\python.exe")
    def test_explicit_build_python_takes_priority_over_project_venv(self, _abspath, _isfile):
        self.assertEqual(
            build.find_python_exe(r"D:\\CodexTools\\launcher-build\\Scripts\\python.exe"),
            r"D:\\CodexTools\\launcher-build\\Scripts\\python.exe",
        )

    @patch("build.time.strftime", return_value="20260728_141530")
    def test_release_filename_contains_version_and_timestamp(self, _strftime):
        self.assertEqual(
            build.generate_release_filename("v1.2.3", True),
            "ComfyUI启动器_v1.2.3_20260728_141530_test.exe",
        )

    @patch("build.time.strftime", return_value="20260728_141530")
    def test_release_filename_marks_stable_build_without_test_suffix(self, _strftime):
        self.assertEqual(
            build.generate_release_filename("v1.2.3", False),
            "ComfyUI启动器_v1.2.3_20260728_141530.exe",
        )

    @patch("sys.argv", ["build.py"])
    def test_local_build_defaults_to_test_channel(self):
        self.assertTrue(build.parse_args().test)

    @patch("sys.argv", ["build.py", "--release"])
    def test_release_flag_selects_formal_channel(self):
        self.assertFalse(build.parse_args().test)


if __name__ == "__main__":
    unittest.main()
