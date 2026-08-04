from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "scripts"))

import core  # noqa: E402
from build_dxf import select_paper_and_scale  # noqa: E402


class CoreTests(unittest.TestCase):
    def test_safe_file_name_preserves_chinese(self) -> None:
        self.assertEqual(core._safe_file_name('包头商场<>:"/\\|?*'), "包头商场_________")

    def test_ruby_literal_normalizes_windows_path(self) -> None:
        self.assertEqual(core._ruby_literal(r"C:\Temp\it's.skp"), "C:/Temp/it\\'s.skp")

    def test_portrait_auto_paper(self) -> None:
        result = select_paper_and_scale(13_621.8, 40_915.7, "AUTO", True)
        self.assertEqual(result["orientation"], "Portrait")
        self.assertEqual(result["name"], "A4")
        self.assertEqual(result["scale"], 200)

    def test_landscape_auto_paper(self) -> None:
        result = select_paper_and_scale(40_915.7, 13_621.8, "AUTO", True)
        self.assertEqual(result["orientation"], "Landscape")
        self.assertEqual(result["name"], "A4")
        self.assertEqual(result["scale"], 200)

    def test_bridge_installation_is_detectable(self) -> None:
        self.assertTrue(core.find_bridge_main().is_file())


if __name__ == "__main__":
    unittest.main()
