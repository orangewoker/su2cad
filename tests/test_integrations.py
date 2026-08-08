from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.integrations import (
    discover_cad_installations,
    discover_sketchup_versions,
    install_plugins,
)


ROOT = Path(__file__).resolve().parents[1]


class IntegrationTests(unittest.TestCase):
    def test_discovers_multiple_sketchup_and_cad_versions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            program_files = root / "Program Files"
            appdata = root / "Roaming"
            sketchup_exe = program_files / "SketchUp" / "SketchUp 2024" / "SketchUp" / "SketchUp.exe"
            sketchup_exe.parent.mkdir(parents=True)
            sketchup_exe.write_bytes(b"")
            (appdata / "SketchUp" / "SketchUp 2023" / "SketchUp" / "Plugins").mkdir(parents=True)
            acad_exe = program_files / "Autodesk" / "AutoCAD 2025" / "acad.exe"
            acad_exe.parent.mkdir(parents=True)
            acad_exe.write_bytes(b"")

            sketchup = discover_sketchup_versions(appdata=appdata, program_files=[program_files])
            cad = discover_cad_installations(program_files=[program_files])

            self.assertEqual([item["version"] for item in sketchup], ["2024", "2023"])
            self.assertEqual(cad[0]["version"], "2025")

    def test_installer_copies_plugins_to_every_detected_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            program_files = root / "Program Files"
            appdata = root / "Roaming"
            for version in ("2024", "2026"):
                executable = program_files / "SketchUp" / f"SketchUp {version}" / "SketchUp" / "SketchUp.exe"
                executable.parent.mkdir(parents=True, exist_ok=True)
                executable.write_bytes(b"")

            result = install_plugins(
                appdata=appdata,
                program_files=[program_files],
                plugin_root=ROOT / "plugins",
            )

            self.assertEqual(result["sketchupVersions"], ["2026", "2024"])
            for version in ("2024", "2026"):
                plugin_dir = appdata / "SketchUp" / f"SketchUp {version}" / "SketchUp" / "Plugins"
                self.assertTrue((plugin_dir / "su2cad_bridge.rb").is_file())
                self.assertTrue((plugin_dir / "su2cad_bridge" / "main.rb").is_file())
            self.assertTrue((appdata / "Autodesk" / "ApplicationPlugins" / "SU2CAD.bundle" / "PackageContents.xml").is_file())
            self.assertTrue(result["status"]["cadPluginInstalled"])


if __name__ == "__main__":
    unittest.main()
