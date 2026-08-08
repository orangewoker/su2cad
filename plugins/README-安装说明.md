# SU2CAD 连接插件 0.8.1

SU2CAD 桌面端内已提供“插件 → 一键安装 / 修复”，会自动识别本机已安装的 SketchUp 和 AutoCAD 版本。本文件夹也可独立分享和安装。

## 最简单的安装方式

1. 解压整个 `SU2CAD-Plugins-0.8.1.zip`。
2. 双击 `Install-SU2CAD-Plugins.cmd`。
3. 关闭后重新打开 SketchUp 和 AutoCAD / 天正。

脚本会扫描 `Program Files` 和当前 Windows 用户的 SketchUp 配置，为每个已检测版本安装 Bridge；AutoCAD Helper 会安装到 Autodesk 通用 `ApplicationPlugins` 目录，无需绑定某一年份。

## 单独安装 SketchUp RBZ

1. 在 SketchUp 打开“扩展程序管理器”。
2. 选择“安装扩展程序”。
3. 选择 `SU2CAD-SketchUp-Bridge-0.8.1.rbz`。
4. 重启 SketchUp，菜单中会出现 `SU2CAD Bridge`。

## 单独安装 AutoCAD Bundle

1. 解压 `SU2CAD-AutoCAD-Helper-0.8.1.zip`。
2. 把完整的 `SU2CAD.bundle` 复制到 `%APPDATA%\Autodesk\ApplicationPlugins\`。
3. 重启 AutoCAD / 天正。

可用命令：`SU2CADSTATUS`、`SU2CADFIXDIALOGS`、`SU2CADOPEN`。

## 版本兼容

- SketchUp：根据实际安装目录动态识别，不锁定 2025/2026。
- AutoCAD：Bundle 支持 Win64 AutoCAD R22.0–R26.0，桌面端也会扫描安装目录、PATH 和 Windows 注册表。
- 同时安装多个 SketchUp 时，插件会安装到每个已检测版本的当前用户目录。导出时建议只打开一个 SketchUp 实例。
