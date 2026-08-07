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
- Before extraction, report a fast source-entity census, an instance-expanded estimate, and the planned current-view representative count. Keep these totals visible in the desktop task area.
- In Light quality, never expand millions of repeated component entities. Cache bounded real geometry per definition and reuse it through CAD INSERT rotation, mirroring, and scaling; do not invent bounding-polygon proxy blocks.
- In Light quality, distribute a block's traversal budget across child instances by projected footprint. Always reserve enough geometry for large furniture bodies before spending detail on tiny high-poly wheels, screws, tufting, and decorations.
- In Balanced quality, reuse fully visible planar block definitions and apply a 40,000-entity block budget plus a 24,000-entity collection sample limit. Reserve unbounded per-edge occlusion for Precise quality so repeated furniture does not expand into millions of duplicate operations.
- Sample oversized SketchUp `Entities` collections with deterministic evenly spaced indexes rather than their first N members so geometry stored late in imported definitions is not erased.
- In Balanced quality, order root geometry as primitives, complete simple blocks and planar outline/text blocks first. Partition remaining dense roots into a 4x4 screen-space grid and process the grid round-robin by projected footprint; append all chunk results into one payload while sharing the block cache.
- Preserve planar lettering and logos up to a bounded 150,000 recursively expanded entities as complete outline geometry with mesh-seam cleanup.
- Use 100/145/200-second Balanced extraction stages: preserve repeated structural components first, then compact real-geometry outlines, followed by a minimum bounded pass over every remaining uncached dense object. Keep the complete workflow within a five-minute target and never return an empty success merely because the final deadline was reached.
- In a strict section view, use exact full-block edge clipping before the soft deadline. After it, keep fully visible simple blocks complete, discard fully covered blocks by the instance grid, and coarsely clip only partial blocks.
- Reuse already generated block definitions even after a time threshold, including section views when an instance lies wholly on the kept side of the cut plane.
- Before expanding a block, classify it by recursively bounded definition complexity. In Balanced quality, preserve blocks at or below 6,000 expanded entities without collection sampling, mesh cleanup, or a per-block entity budget.
- Repeat that classification at every nested child. A dense wrapper must not spend or sample away a simple child; mark child linework as full fidelity and exempt it from downstream dense-block line caps.
- In the compact deadline stage, keep a hard-bounded protected budget for cheap nested simple children so rails, shelves, signs, and ordinary line groups remain complete without making dense imported meshes unbounded.
- Do not classify drafting complexity from recursive entity count alone. In Balanced quality, promote repeated direct-geometry components up to 12,000 entities and repeated child-only wrappers with at most 8 direct children and 90,000 recursively bounded entities when their projected footprint is meaningful.
- For promoted structural components, traverse the definition once, retain boundaries, silhouettes, curves and meaningful hard seams, apply fine micro-mesh cleanup, and reuse the result as a CAD block. Never reduce a repeated rack wrapper to a late-stage uniform sample merely because its two children contain dense slat geometry.
- Preserve a full-fidelity simple block intact when its projected multi-point visibility probe finds it visible. Discard a fully covered block before traversal, and apply edge-level clipping only to a genuinely partial block.
- Classify full-fidelity simple blocks with fine screen-depth tiles. Never reuse a coarse dense-object visibility tile to discard an adjacent shelf, rail, frame, or other thin structural component.
- Include projected depth in every shared visibility-cache key. For preserved repeated components, use exact unshared instance rays so a table, floor, or neighboring chair cannot supply the cached hit for another instance.
- When a dense wrapper contains repeated preserved chairs, rails, shelves, or other structural children, classify each nested instance independently. Discard only a fully covered child and retain consistent complete geometry for its visible instances outside section views.
- Treat `MaxBlockLines` as a dense-block control. Never apply its line sampling to a block marked `optimizationClass: full`; ordinary groups, sign lettering, and other simple components must retain all merged linework.
- Report live elapsed time in the desktop task area and include total elapsed seconds in desktop and PowerShell results.
- Keep desktop setting explanations fully visible beside or below their controls. In Recent Output, deleting a record must also delete its DXF after confirmation, while clearing the list must never delete files.
- For active section views, start visibility rays immediately behind the cut plane so removed foreground geometry cannot hide valid interior details.
- Keep section intersections unconditionally, but clip ordinary lines and curves to their actually visible intervals.
- Merge collinear fragments and write curves as one CAD circle, arc, spline, or polyline object.
- Create CAD circles and arcs only from SketchUp `ArcCurve` sources. Accept a full, geometrically closed ArcCurve even when an earlier visibility pass left a stale partial flag; never infer circles from unrelated linework.
- Preserve suitable top-level SketchUp components and object-sized groups as CAD blocks.
- Reuse one block definition for repeated top-plan instances and restore each instance with CAD INSERT rotation, mirroring, and scaling; create separate definitions for incompatible projected views.
- Prefer complete block geometry for retained SketchUp components instead of dropping the whole object because a bounding-box visibility sample is occluded.
- Place block references on sanitized `SU-*` layers inherited from their effective SketchUp tags. A nested entity's explicit tag overrides its containing instance tag; Untagged geometry inherits the containing tag. Carry visible SketchUp tag RGB colors into the corresponding CAD layers.
- Preserve BMP Chinese names but replace supplementary-plane Unicode such as emoji in all DXF symbol names and title text because AutoCAD rejects those characters even when ezdxf audit passes.
- Preserve boundary, silhouette, curve, and structural seam linework for furniture. In exceptionally dense imported furniture blocks, discard back-facing and sub-pixel mesh facets before applying a spatial detail budget; never use a blind line cap that can erase the object body.
- Place vegetation blocks on `SU-PLANTS-BLOCKS` so they can be frozen or hidden as a unit.
- Export visible SketchUp face materials as RGB solid CAD hatches while using unpainted foreground faces as non-printing occlusion masks.
- Resolve projected material visibility with per-face depth planes so sloped and crossing surfaces are clipped at their actual depth boundary.
- Draw opaque material regions before transparent regions and order transparent regions from far to near.
- Dissolve and topology-preserving-simplify material boundaries before writing HATCH entities; keep plant HATCH entities on `SU-PLANTS-BLOCKS`.
- For Light and Balanced scenes with hundreds of material faces, compose opaque coverage in bounded front-to-back batches. Keep exact sloped-plane pairwise clipping for Precise quality.

## Drawing Rules

- Add overall projected width and height dimensions in model space.
- Determine orientation from projected geometry: wider drawings use landscape and taller drawings use portrait.
- In automatic mode, choose the smallest fitting standard A-series sheet from A4 through A0 at a practical architectural scale derived from the drawing size.
- Use standard A-series dimensions: A4, A3, A2, A1, or A0. Allow a requested paper size to override automatic selection while preserving automatic orientation, then choose the smallest standard scale that fits that fixed sheet.
- Create a paper-space layout named `<paper>-L` or `<paper>-P` with one binding-margin inner border, a full-width bottom title strip, drawing title, scale, sheet/orientation label, and viewport. Do not draw a duplicate paper-edge frame.
- Use the Windows SimHei TrueType font (`simhei.ttf`) for all paper-frame text so Chinese titles render correctly in AutoCAD.
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
- Before opening a generated file, restore AutoCAD `FILEDIA` and `CMDDIA` to `1` through the running-object table without launching a second CAD instance.
- Bridge failures preserve the Ruby error and backtrace; desktop failures write `SU2CAD_export_failure_*.log` in the selected output folder.
- If desktop material construction fails, a valid audited linework-only DXF is still produced and the downgrade is reported.
