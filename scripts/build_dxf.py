#!/usr/bin/env python3
"""Build a dimensioned A3 DXF from SketchUp current-view linework JSON."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "vendor"))

import ezdxf  # noqa: E402
import numpy as np  # noqa: E402
from ezdxf import units  # noqa: E402
from ezdxf.enums import TextEntityAlignment  # noqa: E402


STANDARD_SCALES = [1, 2, 5, 10, 20, 25, 50, 75, 100, 125, 150, 200, 250, 500, 750, 1000, 1500, 2000, 2500, 5000]
PAPER_SIZES = {
    "A4": (210.0, 297.0),
    "A3": (297.0, 420.0),
    "A2": (420.0, 594.0),
    "A1": (594.0, 841.0),
    "A0": (841.0, 1189.0),
}
INVALID_LAYER_CHARS = re.compile(r"[<>/\\\":;?*|=`,]")
DEFAULT_MAX_BLOCK_LINES = 2500
PLANT_BLOCK_LAYER = "SU-PLANTS-BLOCKS"
PLANT_TERMS = (
    "plant", "tree", "grass", "garden", "gardem", "folha", "buqu",
    "植物", "绿萝", "花", "树", "草", "灌木", "乔木", "盆栽",
)


@dataclass(frozen=True)
class Segment:
    start: tuple[float, float]
    end: tuple[float, float]
    layer: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_json", type=Path)
    parser.add_argument("output_dxf", type=Path)
    parser.add_argument("--paper", choices=("AUTO", *PAPER_SIZES), default="AUTO")
    parser.add_argument("--no-dimensions", action="store_true")
    parser.add_argument(
        "--max-block-lines",
        type=int,
        default=DEFAULT_MAX_BLOCK_LINES,
        help="Maximum representative LINE entities retained per dense block; 0 disables optimization.",
    )
    return parser.parse_args()


def clean_layer(value: str) -> str:
    name = INVALID_LAYER_CHARS.sub("_", str(value or "Untagged")).strip()
    name = re.sub(r"\s+", "_", name)
    return ("SU-" + name)[:200] if name else "SU-Untagged"


def point2(value: Iterable[float]) -> tuple[float, float]:
    data = list(value)
    return float(data[0]), float(data[1])


def is_plant_name(value: str) -> bool:
    folded = str(value or "").casefold()
    return any(term.casefold() in folded for term in PLANT_TERMS)


def normalize_segment(raw: dict) -> Segment | None:
    start = point2(raw["start"])
    end = point2(raw["end"])
    if math.dist(start, end) < 0.01:
        return None
    return Segment(start, end, clean_layer(raw.get("layer", "Untagged")))


def merge_collinear(segments: list[Segment], angle_tol: float = 1e-5, gap_tol: float = 0.2) -> list[Segment]:
    groups: dict[tuple[str, int, int], list[tuple[float, float, float, float]]] = {}
    for segment in segments:
        x1, y1 = segment.start
        x2, y2 = segment.end
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length < 0.01:
            continue
        ux, uy = dx / length, dy / length
        if uy < 0 or (abs(uy) < 1e-12 and ux < 0):
            ux, uy = -ux, -uy
        angle_key = round(math.atan2(uy, ux) / angle_tol)
        intercept = x1 * uy - y1 * ux
        key = (segment.layer, angle_key, round(intercept / 0.05))
        t1, t2 = x1 * ux + y1 * uy, x2 * ux + y2 * uy
        groups.setdefault(key, []).append((min(t1, t2), max(t1, t2), ux, uy))

    output: list[Segment] = []
    for (layer, _angle, intercept_key), spans in groups.items():
        spans.sort(key=lambda item: item[0])
        merged: list[list[float]] = []
        for start, end, ux, uy in spans:
            if merged and start <= merged[-1][1] + gap_tol:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end, ux, uy])
        intercept = intercept_key * 0.05
        for start, end, ux, uy in merged:
            nx, ny = uy, -ux
            p1 = (ux * start + nx * intercept, uy * start + ny * intercept)
            p2 = (ux * end + nx * intercept, uy * end + ny * intercept)
            output.append(Segment(p1, p2, layer))
    return output


def segment_length(segment: Segment) -> float:
    return math.dist(segment.start, segment.end)


def simplify_dense_segments(segments: list[Segment], max_lines: int) -> tuple[list[Segment], bool]:
    """Retain spatially distributed representative lines from dense mesh-derived blocks."""
    if max_lines <= 0 or len(segments) <= max_lines:
        return segments, False

    xs = [point[0] for segment in segments for point in (segment.start, segment.end)]
    ys = [point[1] for segment in segments for point in (segment.start, segment.end)]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    width, height = xmax - xmin, ymax - ymin
    diagonal = math.hypot(width, height)
    minimum_length = max(0.5, diagonal / 5000.0)
    candidates = [segment for segment in segments if segment_length(segment) >= minimum_length]
    if len(candidates) <= max_lines:
        return candidates, True

    grid_side = max(1, int(math.sqrt(max_lines)))
    cell_width = max(width / grid_side, 1e-9)
    cell_height = max(height / grid_side, 1e-9)
    buckets: dict[tuple[int, int], Segment] = {}
    for segment in candidates:
        midpoint = (
            (segment.start[0] + segment.end[0]) / 2.0,
            (segment.start[1] + segment.end[1]) / 2.0,
        )
        cell = (
            min(grid_side - 1, max(0, int((midpoint[0] - xmin) / cell_width))),
            min(grid_side - 1, max(0, int((midpoint[1] - ymin) / cell_height))),
        )
        previous = buckets.get(cell)
        if previous is None or segment_length(segment) > segment_length(previous):
            buckets[cell] = segment

    selected = set(buckets.values())
    if len(selected) < max_lines:
        for segment in sorted(candidates, key=segment_length, reverse=True):
            selected.add(segment)
            if len(selected) >= max_lines:
                break
    return sorted(selected, key=lambda item: (item.layer, item.start, item.end)), True


def fit_circle(points: list[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    unique = points[:-1] if len(points) > 3 and math.dist(points[0], points[-1]) < 0.01 else points
    if len(unique) < 3:
        return None
    array = np.asarray(unique, dtype=float)
    a = np.column_stack((2.0 * array[:, 0], 2.0 * array[:, 1], np.ones(len(array))))
    b = array[:, 0] ** 2 + array[:, 1] ** 2
    try:
        cx, cy, constant = np.linalg.lstsq(a, b, rcond=None)[0]
    except np.linalg.LinAlgError:
        return None
    radius_sq = constant + cx * cx + cy * cy
    if radius_sq <= 0:
        return None
    radius = math.sqrt(radius_sq)
    residuals = np.abs(np.hypot(array[:, 0] - cx, array[:, 1] - cy) - radius)
    return float(cx), float(cy), radius, float(residuals.max(initial=0.0))


def add_curve(layout, raw: dict, layer: str) -> list[tuple[float, float]]:
    points = [point2(point) for point in raw.get("points", [])]
    if len(points) < 2:
        return []
    closed = bool(raw.get("closed"))
    is_arc_curve = raw.get("curveType") == "ArcCurve"
    unique_count = len(points) - 1 if closed and math.dist(points[0], points[-1]) < 0.01 else len(points)
    circle = fit_circle(points) if is_arc_curve and unique_count >= (8 if closed else 4) else None
    if circle:
        cx, cy, radius, residual = circle
        tolerance = min(0.5, max(0.05, radius * 1e-5))
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)
        full_circle_shape = (
            not closed
            or (
                min(width, height) / max(width, height, 1e-9) >= 0.98
                and abs(width - 2.0 * radius) <= max(0.5, radius * 0.01)
                and abs(height - 2.0 * radius) <= max(0.5, radius * 0.01)
            )
        )
        if residual <= tolerance and full_circle_shape:
            if closed:
                layout.add_circle((cx, cy), radius, dxfattribs={"layer": layer})
            else:
                angles = np.unwrap(np.arctan2(
                    np.asarray([point[1] - cy for point in points]),
                    np.asarray([point[0] - cx for point in points]),
                ))
                start_angle, end_angle = float(angles[0]), float(angles[-1])
                if end_angle < start_angle:
                    start_angle, end_angle = end_angle, start_angle
                layout.add_arc(
                    (cx, cy),
                    radius,
                    math.degrees(start_angle) % 360.0,
                    math.degrees(end_angle) % 360.0,
                    dxfattribs={"layer": layer},
                )
            return points

    if closed:
        vertices = points[:-1] if math.dist(points[0], points[-1]) < 0.01 else points
        layout.add_lwpolyline(vertices, close=True, dxfattribs={"layer": layer})
    elif len(points) >= 4:
        layout.add_spline(fit_points=points, degree=min(3, len(points) - 1), dxfattribs={"layer": layer})
    else:
        layout.add_lwpolyline(points, dxfattribs={"layer": layer})
    return points


def select_scale(width: float, height: float, viewport_size: tuple[float, float]) -> int:
    required = max(width / viewport_size[0], height / viewport_size[1], 1.0)
    for scale in STANDARD_SCALES:
        if scale >= required * 1.10:
            return scale
    return int(math.ceil(required / 500.0) * 500)


def paper_dimensions(name: str, landscape: bool) -> tuple[float, float]:
    short_side, long_side = PAPER_SIZES[name]
    return (long_side, short_side) if landscape else (short_side, long_side)


def paper_viewport_size(paper_size: tuple[float, float]) -> tuple[float, float]:
    width, height = paper_size
    # 20 mm binding margin, 10 mm outer margins, 25 mm full-width title strip.
    return width - 40.0, height - 57.0


def preferred_scale(long_side_mm: float) -> int:
    if long_side_mm <= 12_000:
        return 50
    if long_side_mm <= 25_000:
        return 100
    if long_side_mm <= 50_000:
        return 200
    if long_side_mm <= 100_000:
        return 500
    if long_side_mm <= 200_000:
        return 1000
    return 2000


def scale_fits(
    width: float,
    height: float,
    viewport_size: tuple[float, float],
    scale: int,
    dimensions: bool,
) -> bool:
    dimension_allowance = 15.0 if dimensions else 0.0
    return (
        width / scale + dimension_allowance <= viewport_size[0]
        and height / scale + dimension_allowance <= viewport_size[1]
    )


def select_paper_and_scale(
    width: float,
    height: float,
    requested_paper: str,
    dimensions: bool,
) -> dict:
    landscape = width >= height
    candidates = list(PAPER_SIZES) if requested_paper == "AUTO" else [requested_paper]
    target_scale = preferred_scale(max(width, height))

    for name in candidates:
        size = paper_dimensions(name, landscape)
        viewport = paper_viewport_size(size)
        if scale_fits(width, height, viewport, target_scale, dimensions):
            return {
                "name": name,
                "size": size,
                "viewport": viewport,
                "orientation": "Landscape" if landscape else "Portrait",
                "scale": target_scale,
            }

    name = candidates[-1]
    size = paper_dimensions(name, landscape)
    viewport = paper_viewport_size(size)
    allowance = 15.0 if dimensions else 0.0
    usable = (max(viewport[0] - allowance, 1.0), max(viewport[1] - allowance, 1.0))
    scale = select_scale(width, height, usable)
    return {
        "name": name,
        "size": size,
        "viewport": viewport,
        "orientation": "Landscape" if landscape else "Portrait",
        "scale": scale,
    }


def add_overall_dimensions(msp, bounds: tuple[float, float, float, float], scale: int) -> tuple[float, float, float, float]:
    xmin, ymin, xmax, ymax = bounds
    width, height = xmax - xmin, ymax - ymin
    offset = 10.0 * scale
    text_height = 2.5 * scale
    arrow_size = 2.5 * scale
    override = {
        "dimtxt": text_height,
        "dimasz": arrow_size,
        "dimexe": 1.25 * scale,
        "dimexo": 1.0 * scale,
        "dimgap": 0.75 * scale,
        "dimdec": 0,
        "dimtad": 1,
        "dimlfac": 1.0,
    }
    if width > 1.0:
        dim = msp.add_linear_dim(
            base=((xmin + xmax) / 2.0, ymin - offset),
            p1=(xmin, ymin),
            p2=(xmax, ymin),
            angle=0,
            dimstyle="EZDXF",
            override=override,
            dxfattribs={"layer": "SUCAD-DIM"},
        )
        dim.render()
    if height > 1.0:
        dim = msp.add_linear_dim(
            base=(xmax + offset, (ymin + ymax) / 2.0),
            p1=(xmax, ymin),
            p2=(xmax, ymax),
            angle=90,
            dimstyle="EZDXF",
            override=override,
            dxfattribs={"layer": "SUCAD-DIM"},
        )
        dim.render()
    return xmin, ymin - 1.5 * offset, xmax + 1.5 * offset, ymax


def add_paper_layout(doc, view_bounds: tuple[float, float, float, float], paper_config: dict, title: str) -> str:
    name = str(paper_config["name"])
    orientation = str(paper_config["orientation"])
    layout_name = f"{name}-{'L' if orientation == 'Landscape' else 'P'}"
    if layout_name in doc.layout_names():
        doc.layouts.delete(layout_name)
    paper = doc.layouts.new(layout_name)
    for existing in list(doc.layout_names()):
        if existing not in ("Model", layout_name):
            doc.layouts.delete(existing)
    width, height = paper_config["size"]
    viewport_width, viewport_height = paper_config["viewport"]
    scale = int(paper_config["scale"])
    paper.page_setup(size=(width, height), margins=(10, 10, 10, 20), units="mm", rotation=0, scale=1, name=name)

    inner_left, inner_bottom = 20.0, 10.0
    inner_right, inner_top = width - 10.0, height - 10.0
    title_top = inner_bottom + 25.0
    paper.add_lwpolyline([(0, 0), (width, 0), (width, height), (0, height)], close=True,
                         dxfattribs={"layer": "SUCAD-FRAME"})
    paper.add_lwpolyline(
        [(inner_left, inner_bottom), (inner_right, inner_bottom), (inner_right, inner_top), (inner_left, inner_top)],
        close=True,
        dxfattribs={"layer": "SUCAD-FRAME"},
    )
    paper.add_line((inner_left, title_top), (inner_right, title_top), dxfattribs={"layer": "SUCAD-FRAME"})
    scale_cell = max(inner_left + 60.0, inner_right - 80.0)
    paper_cell = max(scale_cell + 35.0, inner_right - 40.0)
    paper.add_line((scale_cell, inner_bottom), (scale_cell, title_top), dxfattribs={"layer": "SUCAD-FRAME"})
    paper.add_line((paper_cell, inner_bottom), (paper_cell, title_top), dxfattribs={"layer": "SUCAD-FRAME"})
    paper.add_text(title or "SketchUp Current View", dxfattribs={"height": 3.5, "layer": "SUCAD-FRAME"}).set_placement(
        (inner_left + 4.0, inner_bottom + 13.0), align=TextEntityAlignment.MIDDLE_LEFT
    )
    paper.add_text(f"1:{scale}", dxfattribs={"height": 3.5, "layer": "SUCAD-FRAME"}).set_placement(
        (scale_cell + 4.0, inner_bottom + 13.0), align=TextEntityAlignment.MIDDLE_LEFT
    )
    paper.add_text(f"{name}-{'L' if orientation == 'Landscape' else 'P'} | mm", dxfattribs={"height": 3.5, "layer": "SUCAD-FRAME"}).set_placement(
        (paper_cell + 4.0, inner_bottom + 13.0), align=TextEntityAlignment.MIDDLE_LEFT
    )

    xmin, ymin, xmax, ymax = view_bounds
    center = ((xmin + xmax) / 2.0, (ymin + ymax) / 2.0)
    paper.add_viewport(
        center=((inner_left + inner_right) / 2.0, title_top + viewport_height / 2.0),
        size=(viewport_width, viewport_height),
        view_center_point=center,
        view_height=viewport_height * scale,
        status=2,
        dxfattribs={"layer": "SUCAD-VPORT"},
    )
    return layout_name


def build(
    input_path: Path,
    output_path: Path,
    dimensions: bool = True,
    max_block_lines: int = DEFAULT_MAX_BLOCK_LINES,
    requested_paper: str = "AUTO",
) -> dict:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if payload.get("unit") != "mm":
        raise ValueError("Input linework must use millimeters")

    doc = ezdxf.new("R2018", setup=True)
    doc.units = units.MM
    doc.header["$INSUNITS"] = units.MM
    doc.layers.add("SUCAD-DIM", color=1, lineweight=25)
    doc.layers.add("SU-SUCAD-SECTION", color=1, lineweight=70)
    doc.layers.add("SUCAD-FRAME", color=7, lineweight=35)
    doc.layers.add("SUCAD-VPORT", color=8, plot=False)
    doc.layers.add(PLANT_BLOCK_LAYER, color=3, lineweight=18)
    msp = doc.modelspace()

    segments = [segment for segment in (normalize_segment(raw) for raw in payload.get("lines", [])) if segment]
    merged = merge_collinear(segments)
    used_layers = {segment.layer for segment in merged}
    used_layers.update(clean_layer(raw.get("layer", "Untagged")) for raw in payload.get("curves", []))
    for block in payload.get("blocks", []):
        used_layers.update(clean_layer(raw.get("layer", "Untagged")) for raw in block.get("lines", []))
        used_layers.update(clean_layer(raw.get("layer", "Untagged")) for raw in block.get("curves", []))
    used_layers.update(clean_layer(raw.get("layer", "Untagged")) for raw in payload.get("blockReferences", []))
    for name in sorted(used_layers):
        if name not in doc.layers:
            doc.layers.add(name, color=7, lineweight=18)

    extent_points: list[tuple[float, float]] = []
    for segment in merged:
        msp.add_line(segment.start, segment.end, dxfattribs={"layer": segment.layer})
        extent_points.extend((segment.start, segment.end))
    for raw in payload.get("curves", []):
        extent_points.extend(add_curve(msp, raw, clean_layer(raw.get("layer", "Untagged"))))

    block_points: dict[str, list[tuple[float, float]]] = {}
    plant_blocks: set[str] = set()
    block_lines_before = 0
    block_lines_after = 0
    simplified_blocks = 0
    for raw_block in payload.get("blocks", []):
        name = str(raw_block["name"])
        block = doc.blocks.new(name=name)
        points: list[tuple[float, float]] = []
        block_segments = [
            segment for segment in (normalize_segment(raw) for raw in raw_block.get("lines", [])) if segment
        ]
        merged_block_segments = merge_collinear(block_segments)
        block_lines_before += len(merged_block_segments)
        points.extend(point for segment in merged_block_segments for point in (segment.start, segment.end))
        output_block_segments, simplified = simplify_dense_segments(merged_block_segments, max_block_lines)
        block_lines_after += len(output_block_segments)
        simplified_blocks += int(simplified)
        for segment in output_block_segments:
            block.add_line(segment.start, segment.end, dxfattribs={"layer": segment.layer})
        for raw_curve in raw_block.get("curves", []):
            points.extend(add_curve(block, raw_curve, clean_layer(raw_curve.get("layer", "Untagged"))))
        if (
            is_plant_name(raw_block.get("sourceName", ""))
            or any(is_plant_name(raw.get("layer", "")) for raw in raw_block.get("lines", []))
            or any(is_plant_name(raw.get("layer", "")) for raw in raw_block.get("curves", []))
        ):
            plant_blocks.add(name)
        block_points[name] = points

    for reference in payload.get("blockReferences", []):
        name = str(reference["block"])
        insert = point2(reference["insert"])
        source_layer = reference.get("layer", "Untagged")
        layer = (
            PLANT_BLOCK_LAYER
            if name in plant_blocks or is_plant_name(source_layer) or is_plant_name(reference.get("sourceName", ""))
            else clean_layer(source_layer)
        )
        msp.add_blockref(name, insert, dxfattribs={"layer": layer})
        extent_points.extend((point[0] + insert[0], point[1] + insert[1]) for point in block_points.get(name, []))

    if not extent_points:
        raise ValueError("SketchUp current view produced no visible linework")
    xs = [point[0] for point in extent_points]
    ys = [point[1] for point in extent_points]
    geometry_bounds = min(xs), min(ys), max(xs), max(ys)
    paper_config = select_paper_and_scale(
        geometry_bounds[2] - geometry_bounds[0],
        geometry_bounds[3] - geometry_bounds[1],
        requested_paper.upper(),
        dimensions,
    )
    scale = int(paper_config["scale"])
    view_bounds = add_overall_dimensions(msp, geometry_bounds, scale) if dimensions else geometry_bounds
    layout_name = add_paper_layout(doc, view_bounds, paper_config, payload.get("model", {}).get("title", ""))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(output_path)
    loaded = ezdxf.readfile(output_path)
    auditor = loaded.audit()
    if auditor.has_errors:
        raise RuntimeError(f"DXF audit found {len(auditor.errors)} errors")
    return {
        "path": str(output_path),
        "linesBeforeMerge": len(segments),
        "linesAfterMerge": len(merged),
        "curves": len(payload.get("curves", [])),
        "blocks": len(payload.get("blocks", [])),
        "blockReferences": len(payload.get("blockReferences", [])),
        "blockLinesBeforeOptimization": block_lines_before,
        "blockLinesAfterOptimization": block_lines_after,
        "simplifiedBlocks": simplified_blocks,
        "maxBlockLines": max_block_lines,
        "plantBlockReferences": sum(
            1
            for reference in payload.get("blockReferences", [])
            if str(reference.get("block", "")) in plant_blocks
            or is_plant_name(reference.get("layer", ""))
            or is_plant_name(reference.get("sourceName", ""))
        ),
        "scale": f"1:{scale}",
        "paperSize": paper_config["name"],
        "paperOrientation": paper_config["orientation"],
        "layout": layout_name,
        "paperDimensionsMm": [round(value, 2) for value in paper_config["size"]],
        "geometryBoundsMm": [round(value, 2) for value in geometry_bounds],
        "sourcePerspective": bool(payload.get("camera", {}).get("sourcePerspective")),
        "auditErrors": len(auditor.errors),
    }


def main() -> int:
    args = parse_args()
    result = build(
        args.input_json,
        args.output_dxf,
        dimensions=not args.no_dimensions,
        max_block_lines=args.max_block_lines,
        requested_paper=args.paper,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
