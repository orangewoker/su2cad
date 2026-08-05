# SU2CAD

将 Windows 桌面版 SketchUp 当前视图转换为轻量、可编辑、真尺寸的 AutoCAD DXF 平面线稿。

SU2CAD 通过本地 SketchUp Ruby Bridge 读取模型几何，不调用 SketchUp 原生 DWG/DXF 导出器。它会按当前相机方向进行正交投影、处理遮挡和剖切、保留组件块，并自动生成适合图形方向与大小的 A 系列图框。

## 主要功能

- 当前视图转 1:1 毫米模型空间 DXF
- 透视视图自动转为同方向正交投影
- 按 SketchUp 当前窗口范围裁剪，窗口外对象不进入 CAD
- 过滤隐藏标签、隐藏实体和非轮廓柔化网格边
- 根据视线遮挡裁剪普通线段
- 支持活动剖切面与真实截交线
- 共线片段合并，圆弧、圆、样条和多段线保持为 CAD 曲线
- SketchUp 组件和合适尺寸的组保留为 CAD 块
- 植物块统一放入 `SU-PLANTS-BLOCKS` 图层
- 识别当前视图中的 SketchUp 材质颜色并生成 RGB 实色 HATCH
- 无材质前景面参与遮挡，避免后方色块穿透
- 斜面按投影深度平面裁剪，透明材质按远到近顺序叠放
- 高密度植物、雕塑和装饰块自动轻量化
- 大场景先按组件包围盒和当前视口剔除，窗口外几何不再进入逐实体计算
- 遮挡判断按屏幕像素自适应采样并复用深度缓存，缩远视图不再产生海量射线
- 自动添加总宽、总高尺寸
- 自动判断横版或竖版，并从 A4、A3、A2、A1、A0 中选择图幅
- 自动创建双边框、底部标题栏、比例、单位和纸空间视口
- 生成后执行 DXF 审计，并可自动在 AutoCAD 或天正中打开

## 工作流程

```mermaid
flowchart LR
    A["SketchUp 当前视图"] --> B["Ruby Bridge 提取几何"]
    B --> C["遮挡、剖切与轮廓处理"]
    C --> D["材质识别与深度裁剪"]
    D --> E["组件块与曲线重建"]
    E --> F["线稿及 HATCH 轻量化"]
    F --> G["自动图幅与标题栏"]
    G --> H["DXF 审计"]
    H --> I["AutoCAD / 天正"]
```

## 环境要求

- Windows 10 或 Windows 11
- PowerShell 7
- SketchUp 2026 Desktop
- Python 3.10 或更高版本
- AutoCAD 2025，或基于 AutoCAD 2025 的天正 T30
- 已安装并启动本地 Codex SketchUp Bridge

Bridge 默认位置：

```text
%APPDATA%\SketchUp\SketchUp 2026\SketchUp\Plugins\codex_sketchup_bridge\main.rb
```

本地服务健康检查地址：

```text
http://127.0.0.1:8765/health
```

## 安装

将仓库克隆到 Codex 技能目录：

```powershell
git clone https://github.com/orangewoker/su2cad.git "$env:USERPROFILE\.codex\skills\su2cad"
```

安装 Python 依赖：

```powershell
python -m pip install -r "$env:USERPROFILE\.codex\skills\su2cad\requirements.txt"
```

重新打开 Codex 任务后，可通过 `$su2cad` 显式调用。

## 使用

1. 在 SketchUp 中打开模型并调整到需要导出的视图。
2. 确保 SketchUp Bridge 已启动。
3. 保持 AutoCAD 或天正处于打开状态。
4. 在 Codex 中要求使用 `$su2cad` 导出当前视图。

也可以直接运行：

```powershell
pwsh -NoProfile -File "$env:USERPROFILE\.codex\skills\su2cad\scripts\export_and_open.ps1"
```

## 桌面应用

`dev` 分支包含不需要启动 Codex 的 Windows 桌面应用。它直接连接本机 SketchUp Bridge，并在窗口中完成参数设置、进度显示、DXF 生成和 CAD 打开。SketchUp 端仍需安装并启动本地 Bridge 插件。

下载 GitHub Release 中的 `SU2CAD-0.4.1-windows-x64.zip`，完整解压后运行：

```text
SU2CAD\SU2CAD.exe
```

不要只复制单独的 EXE，`_internal` 目录包含 Python、Tk、NumPy 和 DXF 引擎运行时。

桌面功能包括：

- SketchUp Bridge 与 AutoCAD/天正连接状态
- 当前模型名称和 SketchUp 版本
- 输出目录与最近生成文件
- 自动或固定 A0-A4 图幅
- 仅输出可见线、材质色块、总尺寸和自动打开 CAD 开关
- 所有开关改为带“已开启 / 已关闭”文字的高对比状态按钮，缩放窗口时也能直接辨认
- 轻量、平衡、精细三档场景精度，同时控制遮挡采样、材质细节和块线数
- 响应式高 DPI 布局；窄窗口使用“设置 / 返回任务”切换，任务与设置都可滚动
- SketchUp 几何按时间片分批提取，显示真实已处理实体数并支持快速取消
- 轻量模式对每个超大实体集合设置硬上限，缓存代表实体列表，并把高密度重复组件转换为十二边形/矩形代理块；不会再逐个展开数百万片叶片
- 任务栏实时显示已用时间，完成、取消或失败后保留总处理耗时
- 导出期间暂停 Bridge 状态轮询，进度与日志更新采用节流，避免界面卡顿
- Bridge HTTP 500 会显示真实 Ruby 错误和调用栈，并在输出目录保存失败报告
- 单个射线异常会自动重试并保守保留对应几何；只有连续异常超过限额才终止任务
- 材质构建失败时自动降级生成完整纯线稿 DXF
- `%APPDATA%\SU2CAD\settings.json` 设置持久化

源码启动：

```powershell
python .\app\su2cad_app.py
```

构建 Windows 应用：

```powershell
pwsh -NoProfile -File .\build_app.ps1
```

构建结果：

```text
dist\SU2CAD\SU2CAD.exe
```

桌面应用会将设置和最近输出记录保存在：

```text
%APPDATA%\SU2CAD\settings.json
```

默认输出目录：

```text
%USERPROFILE%\Desktop\SketchUp-CAD
```

## 命令参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `-OutputDirectory <path>` | 桌面 `SketchUp-CAD` | 指定输出目录 |
| `-NoOpen` | 关闭 | 只生成和审计，不打开 CAD |
| `-NoDimensions` | 关闭 | 不生成总尺寸 |
| `-NoMaterials` | 关闭 | 不生成 SketchUp 材质色块 |
| `-DisableOcclusion` | 关闭 | 跳过射线遮挡判断，用于诊断或超大模型 |
| `-IncludeHiddenSectionEdges` | 关闭 | 输出完整剖切边，可能产生杂线 |
| `-MaxBlockLines <count>` | `2500` | 每个高密度块保留的代表线数量，`0` 表示不优化 |
| `-Quality <Light\|Balanced\|Precise>` | `Balanced` | 控制屏幕空间遮挡采样、材质最小投影面积和可见性边界精度 |
| `-PaperSize <AUTO\|A0\|A1\|A2\|A3\|A4>` | `AUTO` | 自动或固定标准图幅，方向仍由图形比例决定 |

示例：生成固定 A3 图幅但不自动打开 CAD：

```powershell
pwsh -NoProfile -File "$env:USERPROFILE\.codex\skills\su2cad\scripts\export_and_open.ps1" `
  -PaperSize A3 `
  -NoOpen
```

## 自动图框

- 宽度大于或等于高度时生成横版布局，例如 `A3-L`。
- 高度大于宽度时生成竖版布局，例如 `A4-P`。
- `AUTO` 会结合图形尺寸和常用建筑比例，在 A4 至 A0 中选择最小可容纳图形与尺寸标注的图幅。
- 图框包含外边界、装订边、内边框、贯通式底部标题栏、项目名、比例、图幅方向与毫米单位。
- DXF 只保留 `Model` 和正式纸空间布局，不保留空白 `Layout1`。

## 轻量化策略

复杂植物和装饰模型经俯视投影后可能产生数十万条亚毫米网格线。SU2CAD 对高密度块执行：

1. 共线线段合并。
2. 删除打印比例下不可见的微小线段。
3. 按二维网格均匀保留代表线，避免细节集中在局部。
4. 保持块插入点、外包范围、图层、曲线和真实尺寸不变。
5. 同材质面先合并，再对 HATCH 边界做保拓扑简化和顶点限额。
6. 组件包围盒先与当前视口和剖切面求交，完全不可见的组件不会进入内部遍历。
7. 长线按屏幕像素而不是固定模型长度采样，并对相邻屏幕网格复用最近命中深度。
8. 轻量模式每个实体集合最多读取 4000 个代表实体；重复组件超过 300 个内部实体时直接生成低多边形代理块，同一定义和材质只计算一次。

桌面应用将长时间的 SketchUp 提取拆成约 250 毫秒的时间片。每个时间片结束后，SketchUp 可以处理界面事件，应用也可以更新已计算的代表实体数、总耗时或响应取消，不再依赖单个 10 分钟 HTTP 请求。

轻量模式以“快速得到可用总图”为目标：高密度重复植物和装饰会使用保留范围、图层及主材质的代理块，而不保留每片叶子的轮廓。需要完整细节时使用平衡或精细模式。

当前测试模型在启用遮挡和材质后生成约 `310 KB` 的 DXF，包含 54 个块参照和 24 个材质 HATCH，审计错误为 0。实际大小取决于当前视口、材质数量和模型复杂度。

实际测试中，约 85 MB、39.9 万块内实体的 DXF 可缩减至约 18.5 MB、7.2 万块内实体。具体结果取决于模型复杂度和 `-MaxBlockLines` 设置。

## 输出结构

- `SU-*`：来自 SketchUp 标签的普通图层
- `SU-BLOCK_*`：组件或组的块参照图层
- `SU-PLANTS-BLOCKS`：植物块及其材质 HATCH 专用图层
- `SU-MATERIAL_*`：SketchUp 材质 RGB 实色 HATCH
- `SU-SUCAD-SECTION`：剖切交线
- `SUCAD-DIM`：总尺寸
- `SUCAD-FRAME`：纸空间图框与标题栏
- `SUCAD-VPORT`：不打印的纸空间视口边界

每次运行创建新的 JSON 与 DXF，不覆盖源 `.skp` 或已有 `.dwg`。

## 故障排查

### Bridge 无法连接

保持 SketchUp 打开，并在 SketchUp 中执行：

```text
Extensions > Codex Bridge > Start
```

然后访问 `http://127.0.0.1:8765/health` 确认返回成功。

### DXF 仍然较大

降低高密度块上限：

```powershell
pwsh -NoProfile -File "$env:USERPROFILE\.codex\skills\su2cad\scripts\export_and_open.ps1" `
  -MaxBlockLines 1000
```

超大场景优先选择轻量模式：

```powershell
pwsh -NoProfile -File "$env:USERPROFILE\.codex\skills\su2cad\scripts\export_and_open.ps1" `
  -Quality Light `
  -MaxBlockLines 800
```

### Bridge 返回 HTTP 500

桌面应用会展开 Bridge 返回的 Ruby 错误和前 20 行调用栈，并在输出目录生成 `SU2CAD_export_failure_*.log`。不再只显示没有诊断价值的 `Internal Server Error`。

### 透视视图尺寸与画面不同

DXF 必须具有统一真实比例，因此透视相机会保留方向和向上向量，但转换为正交投影。这是预期行为。

## 目录结构

```text
su2cad/
├── SKILL.md
├── README.md
├── SU2CAD.spec
├── build_app.ps1
├── requirements.txt
├── app/
│   ├── core.py
│   └── su2cad_app.py
├── agents/
│   └── openai.yaml
├── references/
│   └── environment.md
└── scripts/
    ├── export_and_open.ps1
    ├── export_current_view.rb
    └── build_dxf.py
```

## 许可证

[MIT License](LICENSE)
