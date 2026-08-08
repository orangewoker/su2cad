from __future__ import annotations

import json
import os
import sys
import threading
import traceback
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from app.core import (
    APP_VERSION,
    ExportCancelled,
    ExportResult,
    ExportSettings,
    bridge_health,
    cad_is_running,
    export_current_view,
    open_in_cad,
)
from app.integrations import integration_status, install_plugins


def settings_path() -> Path:
    override = os.environ.get("SU2CAD_SETTINGS_PATH")
    if override:
        return Path(override)
    appdata = Path(os.environ.get("APPDATA") or Path.home())
    return appdata / "SU2CAD" / "settings.json"


def load_settings(path: Path | None = None) -> dict[str, Any]:
    target = path or settings_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_settings(data: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    target = path or settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(f"{target.suffix}.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    return data


def result_payload(result: ExportResult) -> dict[str, Any]:
    payload = asdict(result)
    payload["dxf"] = str(result.dxf)
    payload["linework_json"] = str(result.linework_json)
    payload["warnings"] = list(result.warnings)
    try:
        payload["file_size"] = result.dxf.stat().st_size
    except OSError:
        payload["file_size"] = 0
    return payload


class SidecarServer:
    def __init__(self, output: TextIO | None = None) -> None:
        self.output = output or sys.stdout
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._export_thread: threading.Thread | None = None
        self._active_id: str | None = None

    def emit(self, event_type: str, **payload: Any) -> None:
        message = {"type": event_type, **payload}
        line = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            self.output.write(line + "\n")
            self.output.flush()

    def _status(self, request_id: str | None) -> None:
        try:
            health = bridge_health(timeout=2)
        except Exception as exc:
            health = {"ok": False, "running": False, "error": str(exc)}
        self.emit(
            "status",
            requestId=request_id,
            health=health,
            cadRunning=cad_is_running(),
            exporting=bool(self._export_thread and self._export_thread.is_alive()),
            integrations=integration_status(),
        )

    def _export(self, request_id: str, raw: dict[str, Any]) -> None:
        started = datetime.now()
        settings = ExportSettings(
            output_directory=Path(str(raw.get("output_directory") or "")).expanduser(),
            paper_size=str(raw.get("paper_size") or "AUTO"),
            max_block_lines=max(0, int(raw.get("max_block_lines") or 0)),
            quality=str(raw.get("quality") or "balanced"),
            dimensions=bool(raw.get("dimensions", True)),
            occlusion=bool(raw.get("occlusion", True)),
            material_fills=bool(raw.get("material_fills", True)),
            strict_section_occlusion=bool(raw.get("strict_section_occlusion", True)),
            open_in_cad=bool(raw.get("open_in_cad", True)),
        )
        if not str(settings.output_directory):
            raise ValueError("请选择输出目录")

        try:
            result = export_current_view(
                settings,
                lambda value, message: self.emit(
                    "progress",
                    requestId=request_id,
                    progress=int(value),
                    message=str(message),
                    elapsed=(datetime.now() - started).total_seconds(),
                ),
                self._cancel_event.is_set,
            )
            self.emit("result", requestId=request_id, result=result_payload(result))
        except ExportCancelled as exc:
            self.emit("cancelled", requestId=request_id, message=str(exc))
        except Exception as exc:
            self.emit(
                "error",
                requestId=request_id,
                message=str(exc),
                details=traceback.format_exc(),
            )
        finally:
            with self._state_lock:
                self._active_id = None
                self._export_thread = None
                self._cancel_event.clear()

    def handle(self, message: dict[str, Any]) -> None:
        command = str(message.get("command") or "")
        request_id = str(message.get("requestId") or "") or None
        try:
            if command == "status":
                self._status(request_id)
            elif command == "loadSettings":
                loaded = load_settings()
                loaded.setdefault("output_directory", str(Path.home() / "Desktop" / "SketchUp-CAD"))
                self.emit("settings", requestId=request_id, settings=loaded)
            elif command == "saveSettings":
                raw_settings = message.get("settings")
                if not isinstance(raw_settings, dict):
                    raise ValueError("设置数据格式不正确")
                saved = save_settings(raw_settings)
                self.emit("settingsSaved", requestId=request_id, settings=saved)
            elif command == "export":
                if request_id is None:
                    raise ValueError("导出任务缺少 requestId")
                raw_settings = message.get("settings")
                if not isinstance(raw_settings, dict):
                    raise ValueError("导出设置格式不正确")
                with self._state_lock:
                    if self._export_thread and self._export_thread.is_alive():
                        raise RuntimeError("已有导出任务正在进行")
                    self._cancel_event.clear()
                    self._active_id = request_id
                    self._export_thread = threading.Thread(
                        target=self._export,
                        args=(request_id, raw_settings),
                        name="su2cad-export",
                        daemon=True,
                    )
                    self._export_thread.start()
                self.emit("exportStarted", requestId=request_id)
            elif command == "cancel":
                if self._export_thread and self._export_thread.is_alive():
                    self._cancel_event.set()
                self.emit("cancelRequested", requestId=request_id, activeId=self._active_id)
            elif command == "detectApplications":
                self.emit(
                    "integrations",
                    requestId=request_id,
                    integrations=integration_status(),
                )
            elif command == "installPlugins":
                result = install_plugins()
                self.emit(
                    "pluginsInstalled",
                    requestId=request_id,
                    integrations=result["status"],
                    restartRequired=result["restartRequired"],
                    sketchupVersions=result["sketchupVersions"],
                    cadBundle=result["cadBundle"],
                    message="插件已安装，请重启 SketchUp 和 CAD",
                )
            elif command == "openCad":
                path = Path(str(message.get("path") or ""))
                if not path.exists():
                    raise FileNotFoundError(f"找不到文件：{path}")
                self.emit("opened", requestId=request_id, ok=open_in_cad(path), path=str(path))
            elif command == "openPath":
                path = Path(str(message.get("path") or "")).expanduser()
                if bool(message.get("createDirectory")):
                    path.mkdir(parents=True, exist_ok=True)
                if not path.exists():
                    raise FileNotFoundError(f"找不到路径：{path}")
                os.startfile(path)  # type: ignore[attr-defined]
                self.emit("opened", requestId=request_id, ok=True, path=str(path))
            elif command == "deleteFile":
                path = Path(str(message.get("path") or ""))
                deleted = False
                if path.exists():
                    if path.suffix.lower() != ".dxf":
                        raise ValueError("只允许删除 DXF 输出文件")
                    path.unlink()
                    deleted = True
                self.emit("fileDeleted", requestId=request_id, path=str(path), deleted=deleted)
            elif command == "shutdown":
                self._cancel_event.set()
                self.emit("shutdown", requestId=request_id)
            else:
                raise ValueError(f"未知命令：{command or '<empty>'}")
        except Exception as exc:
            self.emit(
                "error",
                requestId=request_id,
                message=str(exc),
                details=traceback.format_exc(),
            )

    def run(self, input_stream: TextIO | None = None) -> None:
        source = input_stream or sys.stdin
        self.emit("ready", version=APP_VERSION)
        for raw_line in source:
            line = raw_line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError("命令必须是 JSON 对象")
            except Exception as exc:
                self.emit("error", requestId=None, message=str(exc), details=traceback.format_exc())
                continue
            self.handle(message)
            if message.get("command") == "shutdown":
                break


def main() -> int:
    SidecarServer().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
