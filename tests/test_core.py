from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "scripts"))

import core  # noqa: E402
import ezdxf  # noqa: E402
from build_dxf import (  # noqa: E402
    FRAME_TEXT_FONT,
    FRAME_TEXT_STYLE,
    add_paper_layout,
    clean_dxf_text,
    clean_layer,
    select_paper_and_scale,
)


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

    def test_fixed_paper_uses_smallest_fitting_scale(self) -> None:
        result = select_paper_and_scale(34_730.0, 59_897.0, "A3", True)
        self.assertEqual(result["orientation"], "Portrait")
        self.assertEqual(result["name"], "A3")
        self.assertEqual(result["scale"], 200)

    def test_dxf_names_preserve_chinese_but_remove_emoji(self) -> None:
        self.assertEqual(clean_dxf_text("夏至🌿材质"), "夏至_材质")
        self.assertEqual(clean_layer("MATERIAL_夏至🌿"), "SU-MATERIAL_夏至_")

    def test_paper_layout_has_one_inner_frame_and_windows_heiti_text(self) -> None:
        doc = ezdxf.new("R2018", setup=True)
        config = select_paper_and_scale(20_000.0, 10_000.0, "A3", True)
        layout_name = add_paper_layout(
            doc,
            (0.0, 0.0, 20_000.0, 10_000.0),
            config,
            "联洋项目",
        )
        paper = doc.layouts.get(layout_name)
        frames = list(paper.query("LWPOLYLINE[layer=='SUCAD-FRAME']"))
        self.assertEqual(len(frames), 1)
        self.assertEqual(doc.styles.get(FRAME_TEXT_STYLE).dxf.font, FRAME_TEXT_FONT)
        self.assertTrue(all(text.dxf.style == FRAME_TEXT_STYLE for text in paper.query("TEXT")))

    def test_bridge_installation_is_detectable(self) -> None:
        self.assertTrue(core.find_bridge_main().is_file())

    def test_desktop_icon_exists(self) -> None:
        self.assertTrue((core.resource_root() / "assets" / "su2cad.ico").is_file())
        for size in (16, 20, 24, 32, 40, 48, 64, 128, 256):
            self.assertTrue((core.resource_root() / "assets" / f"su2cad-{size}.png").is_file())

    def test_http_500_preserves_bridge_error_details(self) -> None:
        payload = {
            "ok": False,
            "error": "NoMethodError: broken face",
            "backtrace": ["export_current_view.rb:123", "export_current_view.rb:56"],
        }
        error = urllib.error.HTTPError(
            "http://127.0.0.1:8765/command",
            500,
            "Internal Server Error",
            {},
            io.BytesIO(json.dumps(payload).encode("utf-8")),
        )
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(core.BridgeRequestError) as raised:
                core._request_json("/command")
        self.assertIn("NoMethodError: broken face", str(raised.exception))
        self.assertIn("export_current_view.rb:123", str(raised.exception))

    def test_chunked_extraction_reports_progress_and_quality(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "view.json"
            settings = core.ExportSettings(
                output_directory=Path(temporary),
                quality="light",
                open_in_cad=False,
            )
            responses = [
                {"ok": True, "sessionId": "session-1"},
                {"ok": True, "done": False, "processedEntities": 1500},
                {"ok": True, "done": True, "processedEntities": 3000},
            ]
            progress_events: list[tuple[int, str]] = []
            with patch("core._run_ruby_json", side_effect=responses) as run:
                core._extract_geometry_chunked(
                    output,
                    Path(temporary) / "export_current_view.rb",
                    "token",
                    settings,
                    lambda value, message: progress_events.append((value, message)),
                    lambda: False,
                )
            self.assertEqual(run.call_count, 3)
            self.assertIn("quality: 'light'", run.call_args_list[0].args[0])
            self.assertIn("3,000", progress_events[-1][1])


if __name__ == "__main__":
    unittest.main()
