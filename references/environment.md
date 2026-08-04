# Local Environment

## Required Applications

- SketchUp 2026 desktop for Windows.
- AutoCAD 2025 or Tianzheng T30 on AutoCAD 2025.
- Python 3.10 or later available as `python`.

## SketchUp Bridge

The exporter expects:

```text
%APPDATA%\SketchUp\SketchUp 2026\SketchUp\Plugins\codex_sketchup_bridge\main.rb
http://127.0.0.1:8765/health
```

The PowerShell runner reads the local token from `main.rb`; never duplicate the token in prompts or output.

If health is unavailable:

1. Keep SketchUp open.
2. Open `Extensions > Codex Bridge > Start`.
3. Retry the health endpoint.

## Output

Default directory:

```text
%USERPROFILE%\Desktop\SketchUp-CAD
```

Each run creates a timestamped linework JSON and DXF. The DXF contains 1:1 model-space geometry, overall dimensions, and an A3 paper-space layout.

