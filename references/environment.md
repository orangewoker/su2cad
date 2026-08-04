# Local Environment

## Required Applications

- SketchUp 2026 desktop for Windows.
- AutoCAD 2025 or Tianzheng T30 on AutoCAD 2025.
- Python 3.10 or later available as `python`.

## SketchUp Bridge

The exporter accepts either bridge plug-in name:

```text
%APPDATA%\SketchUp\SketchUp 2026\SketchUp\Plugins\codex_sketchup_bridge\main.rb
%APPDATA%\SketchUp\SketchUp 2026\SketchUp\Plugins\su2cad_bridge\main.rb
http://127.0.0.1:8765/health
```

The PowerShell runner reads the local token from `main.rb`; never duplicate the token in prompts or output.

If health is unavailable:

1. Keep SketchUp open.
2. Open `Extensions > Codex Bridge > Start`.
3. Retry the health endpoint. The standalone desktop app does not require Codex to be open.

## Output

Default directory:

```text
%USERPROFILE%\Desktop\SketchUp-CAD
```

Each run creates a timestamped linework JSON and DXF. The DXF contains 1:1 model-space geometry, RGB material HATCH entities, overall dimensions, and an automatically sized A-series paper-space layout.
