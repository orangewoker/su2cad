from __future__ import annotations

import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from app.core import ExportResult
from app.sidecar import SidecarServer, configure_standard_streams, load_settings, result_payload, save_settings


class SidecarTests(unittest.TestCase):
    def test_standard_streams_are_forced_to_utf8(self) -> None:
        streams = []
        for _ in range(3):
            stream = mock.Mock()
            streams.append(stream)
        with mock.patch("app.sidecar.sys.stdin", streams[0]), mock.patch(
            "app.sidecar.sys.stdout", streams[1]
        ), mock.patch("app.sidecar.sys.stderr", streams[2]):
            configure_standard_streams()
        for stream in streams:
            stream.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")

    def test_settings_roundtrip_preserves_unicode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            original = {"output_directory": "C:/测试", "paper_size": "A3", "recent": []}
            save_settings(original, path)
            self.assertEqual(load_settings(path), original)

    def test_result_payload_serializes_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dxf = Path(directory) / "view.dxf"
            dxf.write_bytes(b"DXF")
            result = ExportResult(
                dxf=dxf,
                linework_json=Path(directory) / "view.json",
                model_path="model.skp",
                model_title="model",
                paper_size="A3",
                orientation="Landscape",
                layout="A3-L",
                scale="1:100",
                block_references=1,
                plant_block_references=0,
                material_hatches=2,
                material_count=1,
                simplified_blocks=0,
                block_lines_before=10,
                block_lines_after=10,
                audit_errors=0,
                opened_in_cad=False,
            )
            payload = result_payload(result)
            self.assertEqual(payload["dxf"], str(dxf))
            self.assertEqual(payload["file_size"], 3)
            json.dumps(payload, ensure_ascii=False)

    def test_protocol_load_settings_and_status(self) -> None:
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ, {"SU2CAD_SETTINGS_PATH": str(Path(directory) / "settings.json")}
        ), mock.patch("app.sidecar.bridge_health", return_value={"ok": True, "running": True}), mock.patch(
            "app.sidecar.cad_is_running", return_value=True
        ), mock.patch(
            "app.sidecar.sketchup_is_running", return_value=True
        ), mock.patch(
            "app.sidecar.integration_status", return_value={"sketchup": [], "cad": [], "cadPluginInstalled": False, "cadPlugin": {}}
        ):
            save_settings({"paper_size": "AUTO"})
            server = SidecarServer(output)
            server.run(
                io.StringIO(
                    '{"command":"loadSettings","requestId":"a"}\n'
                    '{"command":"status","requestId":"b"}\n'
                    '{"command":"shutdown","requestId":"c"}\n'
                )
            )
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(events[0]["type"], "ready")
        settings_event = next(item for item in events if item["type"] == "settings")
        status_events = [item for item in events if item["type"] == "status"]
        self.assertEqual(settings_event["settings"]["paper_size"], "AUTO")
        self.assertTrue(status_events[0]["cadRunning"])
        self.assertTrue(status_events[0]["sketchupRunning"])
        self.assertGreaterEqual(len(status_events), 2)

    def test_plugin_install_protocol_reports_detected_versions(self) -> None:
        output = io.StringIO()
        result = {
            "status": {"sketchup": [{"version": "2026"}], "cad": [{"version": "2025"}], "cadPluginInstalled": True, "cadPlugin": {}},
            "restartRequired": True,
            "sketchupVersions": ["2026"],
            "cadBundle": "C:/Plugins/SU2CAD.bundle",
        }
        with mock.patch("app.sidecar.install_plugins", return_value=result):
            SidecarServer(output).handle({"command": "installPlugins", "requestId": "plugins"})
        event = json.loads(output.getvalue())
        self.assertEqual(event["type"], "pluginsInstalled")
        self.assertEqual(event["sketchupVersions"], ["2026"])
        self.assertTrue(event["integrations"]["cadPluginInstalled"])

    def test_export_runs_in_background_and_emits_result(self) -> None:
        output = io.StringIO()

        def fake_export(settings, progress, is_cancelled):
            progress(20, "working")
            dxf = settings.output_directory / "done.dxf"
            settings.output_directory.mkdir(parents=True, exist_ok=True)
            dxf.write_bytes(b"ok")
            return ExportResult(
                dxf=dxf,
                linework_json=settings.output_directory / "done.json",
                model_path="model.skp",
                model_title="model",
                paper_size="A3",
                orientation="Landscape",
                layout="A3-L",
                scale="1:100",
                block_references=1,
                plant_block_references=0,
                material_hatches=0,
                material_count=0,
                simplified_blocks=0,
                block_lines_before=1,
                block_lines_after=1,
                audit_errors=0,
                opened_in_cad=False,
            )

        with tempfile.TemporaryDirectory() as directory, mock.patch("app.sidecar.export_current_view", fake_export):
            server = SidecarServer(output)
            server.handle(
                {
                    "command": "export",
                    "requestId": "job",
                    "settings": {"output_directory": directory, "quality": "balanced"},
                }
            )
            deadline = time.time() + 2
            while time.time() < deadline:
                if any(json.loads(line).get("type") == "result" for line in output.getvalue().splitlines()):
                    break
                time.sleep(0.01)
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertIn("progress", [item["type"] for item in events])
        self.assertIn("result", [item["type"] for item in events])


if __name__ == "__main__":
    unittest.main()
