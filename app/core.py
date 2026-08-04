from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable


APP_NAME = "SU2CAD"
APP_VERSION = "0.2.0"
BRIDGE_URL = "http://127.0.0.1:8765"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def resource_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[1]


ROOT = resource_root()
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from build_dxf import build as build_dxf  # noqa: E402


class ExportCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class ExportSettings:
    output_directory: Path
    paper_size: str = "AUTO"
    max_block_lines: int = 2500
    dimensions: bool = True
    occlusion: bool = True
    material_fills: bool = True
    strict_section_occlusion: bool = True
    open_in_cad: bool = True


@dataclass(frozen=True)
class ExportResult:
    dxf: Path
    linework_json: Path
    model_path: str
    model_title: str
    paper_size: str
    orientation: str
    layout: str
    scale: str
    block_references: int
    plant_block_references: int
    material_hatches: int
    material_count: int
    simplified_blocks: int
    block_lines_before: int
    block_lines_after: int
    audit_errors: int
    opened_in_cad: bool


ProgressCallback = Callable[[int, str], None]
CancelCallback = Callable[[], bool]


def _request_json(
    path: str,
    *,
    method: str = "GET",
    body: dict | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 5,
) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request_headers = {"Content-Type": "application/json"}
    request_headers.update(headers or {})
    request = urllib.request.Request(
        f"{BRIDGE_URL}{path}",
        data=data,
        headers=request_headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise RuntimeError(f"无法连接 SketchUp Bridge：{reason}") from exc


def bridge_health(timeout: int = 3) -> dict:
    return _request_json("/health", timeout=timeout)


def _version_key(path: Path) -> tuple[int, ...]:
    match = re.search(r"SketchUp\s+(\d+(?:\.\d+)*)", str(path))
    return tuple(int(part) for part in match.group(1).split(".")) if match else (0,)


def find_bridge_main() -> Path:
    appdata = Path(os.environ.get("APPDATA", ""))
    base = appdata / "SketchUp"
    candidates: list[Path] = []
    for bridge_name in ("su2cad_bridge", "codex_sketchup_bridge"):
        candidates.extend(base.glob(f"SketchUp */SketchUp/Plugins/{bridge_name}/main.rb"))
    if not candidates:
        raise FileNotFoundError("未找到 SketchUp Bridge 插件 main.rb")
    return max(candidates, key=_version_key)


def read_bridge_token(bridge_main: Path) -> str:
    content = bridge_main.read_text(encoding="utf-8")
    match = re.search(r"TOKEN\s*=\s*'([^']+)'", content)
    if not match:
        raise RuntimeError("无法读取 SketchUp Bridge 本地令牌")
    return match.group(1)


def cad_is_running() -> bool:
    if os.name != "nt":
        return False
    completed = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq acad.exe", "/NH"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        creationflags=CREATE_NO_WINDOW,
        check=False,
    )
    return "acad.exe" in completed.stdout.casefold()


def open_in_cad(path: Path) -> bool:
    launcher = Path(r"C:\Program Files\Common Files\Autodesk Shared\AcShellEx\AcLauncher.exe")
    acad = Path(r"C:\Program Files\Autodesk\AutoCAD 2025\acad.exe")
    if launcher.exists():
        subprocess.Popen(
            [str(launcher), "/O", str(path)],
            creationflags=CREATE_NO_WINDOW,
            close_fds=True,
        )
        return True
    if acad.exists():
        subprocess.Popen([str(acad), str(path)], close_fds=True)
        return True
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
        return True
    return False


def _safe_file_name(value: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return name[:100] or "sketchup-view"


def _ruby_literal(value: str) -> str:
    return value.replace("\\", "/").replace("'", "\\'")


def _check_cancelled(is_cancelled: CancelCallback) -> None:
    if is_cancelled():
        raise ExportCancelled("任务已取消")


def export_current_view(
    settings: ExportSettings,
    progress: ProgressCallback,
    is_cancelled: CancelCallback,
) -> ExportResult:
    progress(3, "正在连接 SketchUp")
    health = bridge_health()
    if not health.get("ok") or not health.get("running"):
        raise RuntimeError("SketchUp Bridge 未运行")
    _check_cancelled(is_cancelled)

    model_title = str(health.get("title") or "SketchUp View")
    model_path = str(health.get("model_path") or "")
    output_directory = settings.output_directory.expanduser().resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_title = _safe_file_name(Path(model_title).stem)
    json_path = output_directory / f"{safe_title}_current_view_{timestamp}.json"
    provisional_dxf = output_directory / f"{safe_title}_current_view_{timestamp}.dxf"

    bridge_main = find_bridge_main()
    token = read_bridge_token(bridge_main)
    ruby_exporter = SCRIPTS_DIR / "export_current_view.rb"
    if not ruby_exporter.exists():
        raise FileNotFoundError(f"缺少导出器：{ruby_exporter}")

    ruby_code = (
        f"load('{_ruby_literal(str(ruby_exporter))}'); "
        "result = SketchupCurrentViewCad.export("
        f"'{_ruby_literal(str(json_path))}', "
        f"occlusion: {'true' if settings.occlusion else 'false'}, "
        f"strict_section_occlusion: {'true' if settings.strict_section_occlusion else 'false'}, "
        f"materials: {'true' if settings.material_fills else 'false'}"
        "); puts JSON.generate(result); result"
    )
    request_body = {
        "command": "run_ruby",
        "args": {"code": ruby_code, "file": str(ruby_exporter)},
        "timeout_ms": 600_000,
    }

    progress(12, "正在提取当前视图几何")
    response = _request_json(
        "/command",
        method="POST",
        body=request_body,
        headers={"X-Codex-SketchUp-Token": token},
        timeout=620,
    )
    if not response.get("ok"):
        raise RuntimeError(f"SketchUp 几何提取失败：{response.get('error', '未知错误')}")
    if not json_path.exists() or json_path.stat().st_size == 0:
        raise RuntimeError("SketchUp 未生成有效线稿 JSON")
    _check_cancelled(is_cancelled)

    progress(62, "正在计算材质色块并生成 DXF")
    builder_result = build_dxf(
        json_path,
        provisional_dxf,
        dimensions=settings.dimensions,
        max_block_lines=settings.max_block_lines,
        requested_paper=settings.paper_size,
    )
    _check_cancelled(is_cancelled)

    orientation = str(builder_result["paperOrientation"])
    orientation_code = "L" if orientation == "Landscape" else "P"
    final_dxf = output_directory / (
        f"{safe_title}_current_view_{builder_result['paperSize']}-{orientation_code}_{timestamp}.dxf"
    )
    if final_dxf.exists():
        final_dxf.unlink()
    provisional_dxf.replace(final_dxf)

    opened = False
    if settings.open_in_cad:
        progress(94, "正在打开 AutoCAD / 天正")
        opened = open_in_cad(final_dxf)

    progress(100, "导出完成")
    return ExportResult(
        dxf=final_dxf,
        linework_json=json_path,
        model_path=model_path,
        model_title=model_title,
        paper_size=str(builder_result["paperSize"]),
        orientation=orientation,
        layout=str(builder_result["layout"]),
        scale=str(builder_result["scale"]),
        block_references=int(builder_result["blockReferences"]),
        plant_block_references=int(builder_result["plantBlockReferences"]),
        material_hatches=int(builder_result["materialHatches"]),
        material_count=int(builder_result["materialCount"]),
        simplified_blocks=int(builder_result["simplifiedBlocks"]),
        block_lines_before=int(builder_result["blockLinesBeforeOptimization"]),
        block_lines_after=int(builder_result["blockLinesAfterOptimization"]),
        audit_errors=int(builder_result["auditErrors"]),
        opened_in_cad=opened,
    )
