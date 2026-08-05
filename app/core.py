from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable


APP_NAME = "SU2CAD"
APP_VERSION = "0.6.0"
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


class BridgeRequestError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, payload: dict | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}


@dataclass(frozen=True)
class ExportSettings:
    output_directory: Path
    paper_size: str = "AUTO"
    max_block_lines: int = 2500
    quality: str = "balanced"
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
    warnings: tuple[str, ...] = ()
    elapsed_seconds: float = 0.0
    full_fidelity_blocks: int = 0
    optimized_dense_blocks: int = 0
    occluded_blocks: int = 0
    unique_entities: int = 0
    expanded_entities: int = 0
    planned_entities: int = 0
    time_budget_skipped: int = 0
    time_budget_omitted: int = 0


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
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}
        error = str(payload.get("error") or raw.strip() or f"HTTP {exc.code} {exc.reason}")
        backtrace = payload.get("backtrace")
        if isinstance(backtrace, list) and backtrace:
            error = f"{error}\n" + "\n".join(str(line) for line in backtrace[:20])
        stdout = str(payload.get("stdout") or "").strip()
        stderr = str(payload.get("stderr") or "").strip()
        if stdout:
            error = f"{error}\nSketchUp 输出：{stdout[-2000:]}"
        if stderr:
            error = f"{error}\nSketchUp 错误输出：{stderr[-2000:]}"
        raise BridgeRequestError(
            f"SketchUp Bridge 返回 HTTP {exc.code}：{error}",
            status_code=exc.code,
            payload=payload,
        ) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise BridgeRequestError(f"无法连接 SketchUp Bridge：{reason}") from exc


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


def _parse_stdout_json(response: dict) -> dict:
    stdout = str(response.get("stdout") or "")
    for line in reversed(stdout.splitlines()):
        candidate = line.strip()
        if not candidate:
            continue
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise RuntimeError("SketchUp Bridge 未返回有效的任务状态")


def _run_ruby_json(code: str, file: Path, token: str, *, timeout: int = 60) -> dict:
    response = _request_json(
        "/command",
        method="POST",
        body={
            "command": "run_ruby",
            "args": {"code": code, "file": str(file)},
            "timeout_ms": max(1, timeout - 2) * 1000,
        },
        headers={"X-Codex-SketchUp-Token": token},
        timeout=timeout,
    )
    if not response.get("ok"):
        raise RuntimeError(f"SketchUp 命令失败：{response.get('error', '未知错误')}")
    return _parse_stdout_json(response)


def _abort_bridge_export(session_id: str, ruby_exporter: Path, token: str) -> None:
    ruby_code = (
        f"result = SketchupCurrentViewCad.cancel_export('{_ruby_literal(session_id)}'); "
        "puts JSON.generate(result); result"
    )
    try:
        _run_ruby_json(ruby_code, ruby_exporter, token, timeout=15)
    except Exception:
        pass


def _extract_geometry_chunked(
    json_path: Path,
    ruby_exporter: Path,
    token: str,
    settings: ExportSettings,
    progress: ProgressCallback,
    is_cancelled: CancelCallback,
    workload: dict | None = None,
) -> dict:
    quality = settings.quality if settings.quality in {"light", "balanced", "precise"} else "balanced"
    extraction_started = time.perf_counter()
    start_code = (
        f"load('{_ruby_literal(str(ruby_exporter))}'); "
        "result = SketchupCurrentViewCad.start_export("
        f"'{_ruby_literal(str(json_path))}', "
        f"occlusion: {'true' if settings.occlusion else 'false'}, "
        f"strict_section_occlusion: {'true' if settings.strict_section_occlusion else 'false'}, "
        f"materials: {'true' if settings.material_fills else 'false'}, "
        f"quality: '{quality}'"
        "); puts JSON.generate(result); result"
    )
    started = _run_ruby_json(start_code, ruby_exporter, token, timeout=30)
    session_id = str(started.get("sessionId") or "")
    if not session_id:
        raise RuntimeError("SketchUp 未创建有效的导出会话")

    try:
        last_reported = -1
        while True:
            if is_cancelled():
                _abort_bridge_export(session_id, ruby_exporter, token)
                raise ExportCancelled("任务已取消")
            step_code = (
                f"result = SketchupCurrentViewCad.step_export('{_ruby_literal(session_id)}', budget_ms: 250); "
                "puts JSON.generate(result); result"
            )
            step = _run_ruby_json(step_code, ruby_exporter, token, timeout=30)
            processed = max(0, int(step.get("processedEntities") or 0))
            if processed != last_reported:
                planned = max(0, int((workload or {}).get("plannedEntities") or 0))
                if planned:
                    ratio = min(processed / planned, 0.99)
                    entity_progress = 12 + int(46 * ratio)
                    if quality in {"light", "balanced"}:
                        target_seconds = 110.0 if quality == "balanced" else 85.0
                        time_ratio = min((time.perf_counter() - extraction_started) / target_seconds, 0.98)
                        entity_progress = max(entity_progress, 12 + int(46 * time_ratio))
                    estimated = min(58, entity_progress)
                    count_label = f"{processed:,} / {planned:,}"
                else:
                    estimated = min(58, 12 + int(46 * processed / (processed + 12_000)))
                    count_label = f"{processed:,}"
                entity_label = "代表实体" if quality in {"light", "balanced"} else "实体"
                progress(estimated, f"正在提取当前视图几何 · 已计算 {count_label} 个{entity_label}")
                last_reported = processed
            if step.get("done"):
                result = step.get("result")
                return result if isinstance(result, dict) else {}
    except Exception:
        _abort_bridge_export(session_id, ruby_exporter, token)
        raise


def _preflight_workload(ruby_exporter: Path, token: str, quality: str) -> dict:
    code = (
        f"load('{_ruby_literal(str(ruby_exporter))}'); "
        f"result = SketchupCurrentViewCad.workload_summary(quality: '{quality}'); "
        "puts JSON.generate(result); result"
    )
    return _run_ruby_json(code, ruby_exporter, token, timeout=30)


def _linework_fallback_json(source: Path) -> Path:
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["fills"] = []
    for block in payload.get("blocks", []):
        if isinstance(block, dict):
            block["fills"] = []
    stats = payload.get("stats")
    if isinstance(stats, dict):
        stats["fills"] = 0
        stats["materials"] = False
        stats["materialFallback"] = True
    fallback = source.with_name(f"{source.stem}_linework_only.json")
    temporary = fallback.with_suffix(f"{fallback.suffix}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(fallback)
    return fallback


def export_current_view(
    settings: ExportSettings,
    progress: ProgressCallback,
    is_cancelled: CancelCallback,
) -> ExportResult:
    started_at = time.perf_counter()
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

    progress(6, "正在统计模型实体")
    workload = _preflight_workload(ruby_exporter, token, settings.quality)
    unique_entities = int(workload.get("uniqueEntities") or 0)
    expanded_entities = int(workload.get("expandedEstimate") or 0)
    planned_entities = int(workload.get("plannedEntities") or 0)
    progress(
        10,
        (
            f"模型总实体 {unique_entities:,} · "
            f"展开估算 {expanded_entities:,} · "
            f"当前视图计划 {planned_entities:,}"
        ),
    )
    progress(12, "正在提取当前视图几何")
    extraction_result = _extract_geometry_chunked(
        json_path,
        ruby_exporter,
        token,
        settings,
        progress,
        is_cancelled,
        workload,
    )
    if not json_path.exists() or json_path.stat().st_size == 0:
        raise RuntimeError("SketchUp 未生成有效线稿 JSON")
    _check_cancelled(is_cancelled)

    progress(62, "正在计算材质色块并生成 DXF")
    warnings: list[str] = []
    ray_errors = int(extraction_result.get("rayErrors") or 0)
    if ray_errors:
        warnings.append(f"{ray_errors} 个遮挡采样点异常，已保守保留对应几何")
    time_budget_skipped = int(extraction_result.get("skippedTimeBudget") or 0)
    if time_budget_skipped:
        warnings.append(
            f"为控制平衡模式耗时，已在完整保留简单对象后对 "
            f"{time_budget_skipped} 个高密度对象使用紧凑轮廓"
        )
    time_budget_omitted = int(extraction_result.get("omittedTimeBudget") or 0)
    if time_budget_omitted:
        warnings.append(
            f"达到平衡模式总时限后，已停止处理最后 "
            f"{time_budget_omitted} 个最小高密度对象"
        )
    build_json_path = json_path
    try:
        builder_result = build_dxf(
            build_json_path,
            provisional_dxf,
            dimensions=settings.dimensions,
            max_block_lines=settings.max_block_lines,
            requested_paper=settings.paper_size,
        )
    except Exception as material_error:
        if not settings.material_fills:
            raise
        warnings.insert(0, f"材质色块处理失败，已自动生成纯线稿：{material_error}")
        progress(70, "材质处理失败，正在降级生成完整线稿")
        build_json_path = _linework_fallback_json(json_path)
        builder_result = build_dxf(
            build_json_path,
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
        warnings=tuple(warnings),
        elapsed_seconds=round(time.perf_counter() - started_at, 2),
        full_fidelity_blocks=(
            int(extraction_result.get("fullFidelityBlocks") or 0)
            + int(extraction_result.get("fullFidelityChildren") or 0)
        ),
        optimized_dense_blocks=int(extraction_result.get("optimizedDenseBlocks") or 0),
        occluded_blocks=int(extraction_result.get("occludedBlocks") or 0),
        unique_entities=unique_entities,
        expanded_entities=expanded_entities,
        planned_entities=planned_entities,
        time_budget_skipped=time_budget_skipped,
        time_budget_omitted=time_budget_omitted,
    )
