import unittest
from unittest.mock import patch

import build


class TestBuildReleaseName(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
