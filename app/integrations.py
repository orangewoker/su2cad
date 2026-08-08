from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from app.core import discover_cad_executables, resource_root


PLUGIN_ROOT = resource_root() / "plugins"


def _version_key(value: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", value)
    return tuple(int(number) for number in numbers) or (0,)


def _unique_paths(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path).casefold()
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def discover_sketchup_versions(
    *,
    appdata: Path | None = None,
    program_files: list[Path] | None = None,
) -> list[dict[str, Any]]:
    roaming = appdata or Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    roots = program_files or [
        Path(os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles") or r"C:\Program Files"),
        Path(os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"),
    ]
    installs: dict[str, Path | None] = {}
    for root in _unique_paths(roots):
        executables = list(root.glob("SketchUp/SketchUp */SketchUp.exe"))
        executables.extend(root.glob("SketchUp/SketchUp */SketchUp/SketchUp.exe"))
        for executable in _unique_paths(executables):
            release_dir = executable.parent.parent if executable.parent.name.casefold() == "sketchup" else executable.parent
            match = re.search(r"SketchUp\s+(.+)$", release_dir.name, re.IGNORECASE)
            if match:
                installs[match.group(1)] = executable

    profile_root = roaming / "SketchUp"
    if profile_root.exists():
        for profile in profile_root.glob("SketchUp *"):
            if not profile.is_dir():
                continue
            match = re.search(r"SketchUp\s+(.+)$", profile.name, re.IGNORECASE)
            if match:
                installs.setdefault(match.group(1), None)

    discovered: list[dict[str, Any]] = []
    for version, executable in installs.items():
        plugin_dir = profile_root / f"SketchUp {version}" / "SketchUp" / "Plugins"
        su2cad_bridge = plugin_dir / "su2cad_bridge" / "main.rb"
        legacy_bridge = plugin_dir / "codex_sketchup_bridge" / "main.rb"
        discovered.append(
            {
                "version": version,
                "executable": str(executable) if executable is not None else "",
                "pluginDirectory": str(plugin_dir),
                "pluginInstalled": su2cad_bridge.is_file() or legacy_bridge.is_file(),
                "nativePluginInstalled": su2cad_bridge.is_file(),
            }
        )
    return sorted(discovered, key=lambda item: _version_key(str(item["version"])), reverse=True)


def discover_cad_installations(*, program_files: list[Path] | None = None) -> list[dict[str, Any]]:
    roots = program_files or [
        Path(os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles") or r"C:\Program Files"),
        Path(os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"),
    ]
    executables: list[Path] = []
    for root in _unique_paths(roots):
        executables.extend(root.glob("Autodesk/AutoCAD */acad.exe"))
        executables.extend(root.glob("Autodesk/AutoCAD*/*/acad.exe"))
    if program_files is None:
        executables.extend(discover_cad_executables())
    else:
        which_acad = shutil.which("acad.exe")
        if which_acad:
            executables.append(Path(which_acad))

    discovered: list[dict[str, Any]] = []
    for executable in _unique_paths(executables):
        match = re.search(r"AutoCAD\s*([^\\/]*)", str(executable.parent), re.IGNORECASE)
        label = executable.parent.name
        version = (match.group(1).strip() if match else "") or label
        discovered.append({"version": version, "name": label, "executable": str(executable)})
    return sorted(discovered, key=lambda item: _version_key(str(item["version"])), reverse=True)


def cad_plugin_directory(*, appdata: Path | None = None) -> Path:
    roaming = appdata or Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    return roaming / "Autodesk" / "ApplicationPlugins" / "SU2CAD.bundle"


def cad_plugin_status(*, appdata: Path | None = None) -> dict[str, Any]:
    roaming = appdata or Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    status_file = roaming / "SU2CAD" / "cad_plugin_status.json"
    try:
        value = json.loads(status_file.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def integration_status(
    *,
    appdata: Path | None = None,
    program_files: list[Path] | None = None,
) -> dict[str, Any]:
    cad_bundle = cad_plugin_directory(appdata=appdata)
    return {
        "sketchup": discover_sketchup_versions(appdata=appdata, program_files=program_files),
        "cad": discover_cad_installations(program_files=program_files),
        "cadPluginInstalled": (cad_bundle / "PackageContents.xml").is_file(),
        "cadPlugin": cad_plugin_status(appdata=appdata),
    }


def install_plugins(
    *,
    appdata: Path | None = None,
    program_files: list[Path] | None = None,
    plugin_root: Path | None = None,
) -> dict[str, Any]:
    payload_root = plugin_root or PLUGIN_ROOT
    sketchup_source = payload_root / "sketchup"
    cad_source = payload_root / "autocad" / "SU2CAD.bundle"
    if not (sketchup_source / "su2cad_bridge.rb").is_file():
        raise FileNotFoundError(f"SketchUp plugin payload is missing: {sketchup_source}")
    if not (cad_source / "PackageContents.xml").is_file():
        raise FileNotFoundError(f"AutoCAD plugin payload is missing: {cad_source}")

    installed_sketchup: list[str] = []
    for item in discover_sketchup_versions(appdata=appdata, program_files=program_files):
        plugin_dir = Path(str(item["pluginDirectory"]))
        plugin_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sketchup_source / "su2cad_bridge.rb", plugin_dir / "su2cad_bridge.rb")
        target_folder = plugin_dir / "su2cad_bridge"
        if target_folder.exists():
            shutil.rmtree(target_folder)
        shutil.copytree(sketchup_source / "su2cad_bridge", target_folder)
        installed_sketchup.append(str(item["version"]))

    target_bundle = cad_plugin_directory(appdata=appdata)
    target_bundle.parent.mkdir(parents=True, exist_ok=True)
    if target_bundle.exists():
        shutil.rmtree(target_bundle)
    shutil.copytree(cad_source, target_bundle)
    return {
        "ok": True,
        "sketchupVersions": installed_sketchup,
        "cadBundle": str(target_bundle),
        "restartRequired": True,
        "status": integration_status(appdata=appdata, program_files=program_files),
    }
