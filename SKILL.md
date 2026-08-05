---
name: su2cad
description: Convert the active Windows SketchUp viewport into lightweight AutoCAD linework and RGB material hatches without SketchUp's DWG/DXF exporter. Use when Codex must remove occluded or off-screen geometry, preserve components as CAD blocks, fit curves, retain real millimeter dimensions, add overall dimensions and an automatically sized/oriented A-series paper-space layout, and open the generated DXF in AutoCAD or Tianzheng CAD.
---

# SU2CAD

Generate a true-size DXF from the open SketchUp model and current camera direction. Use the local SketchUp Ruby bridge for geometry access and `ezdxf` for deterministic CAD construction.

## Workflow

1. Confirm SketchUp and AutoCAD are open. Do not save or alter the SketchUp model.
2. Check `http://127.0.0.1:8765/health`. If unavailable, read [references/environment.md](references/environment.md).
3. Run:

```powershell
pwsh -NoProfile -File "$env:USERPROFILE\.codex\skills\su2cad\scripts\export_and_open.ps1"
```

4. Read the JSON summary printed by the script. Report the DXF path, entity counts, selected paper size/orientation/scale, and audit result.
5. Verify AutoCAD opened the generated DXF and that the reported layout (for example `A4-P` or `A3-L`) exists.

## Projection Rules

- Always output model-space geometry at 1:1 millimeters.
- For a perspective source camera, keep its direction and up vector but use orthographic projection. State this behavior because perspective geometry cannot carry consistent true dimensions.
- Clip projected lines, curves, faces, and blocks to the active SketchUp viewport; do not export geometry outside the current window.
- Do not invoke SketchUp's native DWG/DXF exporter.
- Keep hidden tags, hidden entities, and non-profile softened mesh edges out of linework.
- For softened or smoothed geometry, restore only view-direction silhouette edges; do not emit triangulation seams.
- When an active SketchUp section plane exists, clip geometry to its kept side and generate true face-plane intersection lines on `SU-SUCAD-SECTION`.
- Enable ray-test occlusion by default. Use `-DisableOcclusion` only for diagnosis or unusually large models.
- Retry ray-test exceptions once. Conservatively keep geometry for a small number of isolated failures and report them; fail the export if failures exceed the bounded tolerance.
- Cull component and group bounds against the active viewport and section plane before traversing their definitions.
- Sample line visibility in screen space according to the selected quality profile, cap samples per segment, and reuse nearest-hit depth by screen tile.
- In the desktop app, run SketchUp extraction as short resumable steps so progress and cancellation remain responsive and one long HTTP request cannot time out the whole scene.
- In Light quality, never expand millions of repeated component entities. Cache bounded entity samples per definition, use compact range-preserving proxy blocks for dense repeated components, and cap each entity collection at 4000 inspected representatives.
- Report live elapsed time in the desktop task area and include total elapsed seconds in desktop and PowerShell results.
- For active section views, start visibility rays immediately behind the cut plane so removed foreground geometry cannot hide valid interior details.
- Keep section intersections unconditionally, but clip ordinary lines and curves to their actually visible intervals.
- Merge collinear fragments and write curves as one CAD circle, arc, spline, or polyline object.
- Create CAD circles and arcs only from SketchUp `ArcCurve` sources. Determine closure from SketchUp edge topology, never from coincident projected endpoints alone.
- Preserve suitable top-level SketchUp components and object-sized groups as CAD blocks.
- Reuse one block definition when normalized projected geometry, size, and orientation match; create separate definitions for different views or dimensions.
- Prefer complete block geometry for retained SketchUp components instead of dropping the whole object because a bounding-box visibility sample is occluded.
- Place block references on sanitized `SU-BLOCK_*` layers derived from SketchUp component or group names.
- Limit dense mesh-derived block linework to spatially distributed representative lines while preserving block extents and insert coordinates.
- Place vegetation blocks on `SU-PLANTS-BLOCKS` so they can be frozen or hidden as a unit.
- Export visible SketchUp face materials as RGB solid CAD hatches while using unpainted foreground faces as non-printing occlusion masks.
- Resolve projected material visibility with per-face depth planes so sloped and crossing surfaces are clipped at their actual depth boundary.
- Draw opaque material regions before transparent regions and order transparent regions from far to near.
- Dissolve and topology-preserving-simplify material boundaries before writing HATCH entities; keep plant HATCH entities on `SU-PLANTS-BLOCKS`.

## Drawing Rules

- Add overall projected width and height dimensions in model space.
- Determine orientation from projected geometry: wider drawings use landscape and taller drawings use portrait.
- In automatic mode, choose the smallest fitting standard A-series sheet from A4 through A0 at a practical architectural scale derived from the drawing size.
- Use standard A-series dimensions: A4, A3, A2, A1, or A0. Allow a requested paper size to override automatic selection while preserving automatic orientation.
- Create a paper-space layout named `<paper>-L` or `<paper>-P` with an outer edge, binding-margin inner border, full-width bottom title strip, drawing title, scale, sheet/orientation label, and viewport.
- Keep the viewport above the title strip and include overall dimensions inside the printable area.
- Keep source SketchUp tags on sanitized `SU-*` CAD layers.
- Treat the generated DXF as a new artifact. Never overwrite the source `.skp` or an existing `.dwg`.

## Options

- `-OutputDirectory <path>`: choose output location.
- `-NoOpen`: build and audit without launching AutoCAD.
- `-NoDimensions`: omit overall dimensions.
- `-NoMaterials`: omit SketchUp material color fills while retaining visible linework.
- `-DisableOcclusion`: skip SketchUp ray testing for faster diagnostic output.
- `-IncludeHiddenSectionEdges`: diagnostic full-edge section output; expect clutter.
- `-MaxBlockLines <count>`: cap representative lines in each dense block; default `2500`, use `0` to disable optimization.
- `-Quality <Light|Balanced|Precise>`: control screen-space occlusion sampling, visibility-boundary refinement, minimum projected material area, and large-scene detail. Default `Balanced`.
- `-PaperSize <AUTO|A0|A1|A2|A3|A4>`: select the sheet; default `AUTO`. Orientation is always derived from drawing proportions.

## Validation

Require all of the following before declaring success:

- SketchUp bridge response is successful.
- Linework JSON and DXF exist and are non-empty.
- `ezdxf.readfile()` succeeds.
- DXF audit reports zero errors.
- At least one line or curve exists.
- When materials are enabled, HATCH entities preserve RGB/alpha, use valid boundary paths, and remain behind visible linework.
- The reported layout exists, uses the reported standard paper dimensions, and its orientation matches the projected geometry.
- The paper-space frame, full-width title strip, scale text, and viewport exist.
- AutoCAD title changes to the generated DXF when opening was requested.
- Bridge failures preserve the Ruby error and backtrace; desktop failures write `SU2CAD_export_failure_*.log` in the selected output folder.
- If desktop material construction fails, a valid audited linework-only DXF is still produced and the downgrade is reported.
