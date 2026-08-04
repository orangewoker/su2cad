from __future__ import annotations

import ctypes
import json
import os
import queue
import threading
import traceback
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from core import (
    APP_VERSION,
    ExportCancelled,
    ExportResult,
    ExportSettings,
    bridge_health,
    cad_is_running,
    export_current_view,
)


BG = "#f4f6f8"
SURFACE = "#ffffff"
TEXT = "#17202a"
MUTED = "#68737d"
BORDER = "#d8dee4"
PRIMARY = "#1368ce"
PRIMARY_ACTIVE = "#0f56ab"
SUCCESS = "#16834b"
DANGER = "#c23b3b"
LOG_BG = "#17212b"
LOG_TEXT = "#d9e2ea"


def enable_high_dpi() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class SU2CADApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"SU2CAD {APP_VERSION}")
        self.root.geometry("1080x720")
        self.root.minsize(940, 640)
        self.root.configure(bg=BG)

        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.cancel_event = threading.Event()
        self.export_thread: threading.Thread | None = None
        self.status_refreshing = False
        self.last_result: ExportResult | None = None
        self.settings_path = Path(os.environ.get("APPDATA", str(Path.home()))) / "SU2CAD" / "settings.json"
        self.saved = self._load_settings()

        self.output_var = tk.StringVar(
            value=self.saved.get("output_directory", str(Path.home() / "Desktop" / "SketchUp-CAD"))
        )
        self.paper_var = tk.StringVar(value=self.saved.get("paper_size", "AUTO"))
        self.max_lines_var = tk.IntVar(value=int(self.saved.get("max_block_lines", 2500)))
        self.dimensions_var = tk.BooleanVar(value=bool(self.saved.get("dimensions", True)))
        self.occlusion_var = tk.BooleanVar(value=bool(self.saved.get("occlusion", True)))
        self.strict_section_var = tk.BooleanVar(value=bool(self.saved.get("strict_section_occlusion", True)))
        self.open_cad_var = tk.BooleanVar(value=bool(self.saved.get("open_in_cad", True)))
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_text_var = tk.StringVar(value="就绪")
        self.model_var = tk.StringVar(value="等待连接 SketchUp")

        self._configure_styles()
        self._build_ui()
        self._restore_recent()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._drain_events)
        self.root.after(200, self._refresh_status)
        self._log(f"SU2CAD {APP_VERSION} 已启动")

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Surface.TFrame", background=SURFACE)
        style.configure("TLabel", background=BG, foreground=TEXT, font=("Microsoft YaHei UI", 10))
        style.configure("Surface.TLabel", background=SURFACE, foreground=TEXT, font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Microsoft YaHei UI", 20, "bold"))
        style.configure("Subtitle.TLabel", background=BG, foreground=MUTED, font=("Microsoft YaHei UI", 9))
        style.configure("Section.TLabel", background=SURFACE, foreground=TEXT, font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Muted.TLabel", background=SURFACE, foreground=MUTED, font=("Microsoft YaHei UI", 9))
        style.configure("Connected.TLabel", background=SURFACE, foreground=SUCCESS, font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Disconnected.TLabel", background=SURFACE, foreground=DANGER, font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Primary.TButton", font=("Microsoft YaHei UI", 10, "bold"), padding=(18, 10), foreground="#ffffff", background=PRIMARY)
        style.map("Primary.TButton", background=[("active", PRIMARY_ACTIVE), ("disabled", "#9bb9db")])
        style.configure("TButton", font=("Microsoft YaHei UI", 9), padding=(12, 7))
        style.configure("TCheckbutton", background=SURFACE, foreground=TEXT, font=("Microsoft YaHei UI", 9))
        style.configure("TCombobox", padding=5)
        style.configure("TEntry", padding=6)
        style.configure("Horizontal.TProgressbar", background=PRIMARY, troughcolor="#dce4ec", thickness=8)
        style.configure("Treeview", rowheight=28, font=("Microsoft YaHei UI", 9), background=SURFACE, fieldbackground=SURFACE)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=(28, 22, 28, 20))
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=0, minsize=360)
        container.columnconfigure(1, weight=1)
        container.rowconfigure(2, weight=1)

        title = ttk.Label(container, text="SU2CAD", style="Title.TLabel")
        title.grid(row=0, column=0, sticky="w")
        subtitle = ttk.Label(container, text="SketchUp 当前视图转轻量 CAD", style="Subtitle.TLabel")
        subtitle.grid(row=1, column=0, sticky="w", pady=(0, 16))

        status = ttk.Frame(container, style="Surface.TFrame", padding=(18, 12))
        status.grid(row=0, column=1, rowspan=2, sticky="nsew", padx=(24, 0), pady=(0, 16))
        status.columnconfigure(2, weight=1)
        ttk.Label(status, text="SketchUp", style="Surface.TLabel").grid(row=0, column=0, sticky="w")
        self.sketchup_status = ttk.Label(status, text="检测中", style="Disconnected.TLabel")
        self.sketchup_status.grid(row=0, column=1, sticky="w", padx=(10, 24))
        self.cad_status = ttk.Label(status, text="CAD 检测中", style="Disconnected.TLabel")
        self.cad_status.grid(row=0, column=3, sticky="e")
        ttk.Label(status, textvariable=self.model_var, style="Muted.TLabel").grid(
            row=1, column=0, columnspan=4, sticky="ew", pady=(6, 0)
        )

        settings = ttk.Frame(container, style="Surface.TFrame", padding=18)
        settings.grid(row=2, column=0, sticky="nsew")
        settings.columnconfigure(0, weight=1)
        ttk.Label(settings, text="导出设置", style="Section.TLabel").grid(row=0, column=0, sticky="w")

        ttk.Label(settings, text="输出目录", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(16, 5))
        output_row = ttk.Frame(settings, style="Surface.TFrame")
        output_row.grid(row=2, column=0, sticky="ew")
        output_row.columnconfigure(0, weight=1)
        self.output_entry = ttk.Entry(output_row, textvariable=self.output_var)
        self.output_entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(output_row, text="浏览", command=self._browse_output).grid(row=0, column=1, padx=(8, 0))

        options = ttk.Frame(settings, style="Surface.TFrame")
        options.grid(row=3, column=0, sticky="ew", pady=(16, 0))
        options.columnconfigure(0, weight=1)
        options.columnconfigure(1, weight=1)
        ttk.Label(options, text="图幅", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(options, text="块最大线数", style="Muted.TLabel").grid(row=0, column=1, sticky="w", padx=(12, 0))
        self.paper_combo = ttk.Combobox(
            options,
            textvariable=self.paper_var,
            values=("AUTO", "A4", "A3", "A2", "A1", "A0"),
            state="readonly",
            width=12,
        )
        self.paper_combo.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        self.max_lines_spin = ttk.Spinbox(options, from_=0, to=1000000, increment=500, textvariable=self.max_lines_var, width=12)
        self.max_lines_spin.grid(row=1, column=1, sticky="ew", padx=(12, 0), pady=(5, 0))

        checks = ttk.Frame(settings, style="Surface.TFrame")
        checks.grid(row=4, column=0, sticky="ew", pady=(16, 0))
        self.dimension_check = ttk.Checkbutton(checks, text="生成总尺寸", variable=self.dimensions_var)
        self.dimension_check.grid(row=0, column=0, sticky="w")
        self.occlusion_check = ttk.Checkbutton(checks, text="遮挡判断", variable=self.occlusion_var)
        self.occlusion_check.grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.section_check = ttk.Checkbutton(checks, text="严格剖切可见性", variable=self.strict_section_var)
        self.section_check.grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.open_cad_check = ttk.Checkbutton(checks, text="完成后打开 CAD", variable=self.open_cad_var)
        self.open_cad_check.grid(row=3, column=0, sticky="w", pady=(8, 0))

        ttk.Separator(settings).grid(row=5, column=0, sticky="ew", pady=18)
        self.export_button = ttk.Button(settings, text="生成 CAD", style="Primary.TButton", command=self._start_export)
        self.export_button.grid(row=6, column=0, sticky="ew")
        self.cancel_button = ttk.Button(settings, text="取消", command=self._cancel_export, state="disabled")
        self.cancel_button.grid(row=7, column=0, sticky="ew", pady=(8, 0))

        right = ttk.Frame(container)
        right.grid(row=2, column=1, sticky="nsew", padx=(24, 0))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(3, weight=1)

        progress_header = ttk.Frame(right)
        progress_header.grid(row=0, column=0, sticky="ew")
        progress_header.columnconfigure(0, weight=1)
        ttk.Label(progress_header, text="任务进度", font=("Microsoft YaHei UI", 11, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(progress_header, textvariable=self.progress_text_var, style="Subtitle.TLabel").grid(row=0, column=1, sticky="e")
        ttk.Progressbar(right, variable=self.progress_var, maximum=100).grid(row=1, column=0, sticky="ew", pady=(8, 14))

        recent_header = ttk.Frame(right)
        recent_header.grid(row=2, column=0, sticky="ew")
        recent_header.columnconfigure(0, weight=1)
        ttk.Label(recent_header, text="最近输出", font=("Microsoft YaHei UI", 11, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Button(recent_header, text="打开文件", command=self._open_selected).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(recent_header, text="打开目录", command=self._open_output_folder).grid(row=0, column=2, padx=(8, 0))

        self.recent_tree = ttk.Treeview(right, columns=("time", "file", "layout", "size"), show="headings", height=6)
        self.recent_tree.heading("time", text="时间")
        self.recent_tree.heading("file", text="文件")
        self.recent_tree.heading("layout", text="图幅")
        self.recent_tree.heading("size", text="大小")
        self.recent_tree.column("time", width=120, anchor="w", stretch=False)
        self.recent_tree.column("file", width=300, anchor="w")
        self.recent_tree.column("layout", width=80, anchor="center", stretch=False)
        self.recent_tree.column("size", width=80, anchor="e", stretch=False)
        self.recent_tree.grid(row=3, column=0, sticky="nsew", pady=(8, 14))
        self.recent_tree.bind("<Double-1>", lambda _event: self._open_selected())

        ttk.Label(right, text="运行日志", font=("Microsoft YaHei UI", 11, "bold")).grid(row=4, column=0, sticky="w")
        self.log_text = tk.Text(
            right,
            height=9,
            bg=LOG_BG,
            fg=LOG_TEXT,
            insertbackground=LOG_TEXT,
            relief="flat",
            padx=12,
            pady=10,
            font=("Cascadia Mono", 9),
            wrap="word",
            state="disabled",
        )
        self.log_text.grid(row=5, column=0, sticky="ew", pady=(8, 0))

    def _load_settings(self) -> dict:
        try:
            return json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _save_settings(self) -> None:
        data = {
            "output_directory": self.output_var.get(),
            "paper_size": self.paper_var.get(),
            "max_block_lines": self.max_lines_var.get(),
            "dimensions": self.dimensions_var.get(),
            "occlusion": self.occlusion_var.get(),
            "strict_section_occlusion": self.strict_section_var.get(),
            "open_in_cad": self.open_cad_var.get(),
            "recent": self.saved.get("recent", [])[:10],
        }
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self.saved = data

    def _restore_recent(self) -> None:
        for item in self.saved.get("recent", []):
            path = Path(item.get("path", ""))
            self.recent_tree.insert(
                "",
                "end",
                iid=str(path),
                values=(item.get("time", ""), path.name, item.get("layout", ""), item.get("size", "")),
            )

    def _add_recent(self, result: ExportResult) -> None:
        size = f"{result.dxf.stat().st_size / 1024 / 1024:.1f} MB"
        record = {
            "path": str(result.dxf),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "layout": result.layout,
            "size": size,
        }
        recent = [item for item in self.saved.get("recent", []) if item.get("path") != str(result.dxf)]
        self.saved["recent"] = [record, *recent][:10]
        for item in self.recent_tree.get_children():
            self.recent_tree.delete(item)
        self._restore_recent()
        self._save_settings()

    def _browse_output(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.output_var.get(), title="选择 CAD 输出目录")
        if selected:
            self.output_var.set(selected)

    def _collect_settings(self) -> ExportSettings:
        max_lines = int(self.max_lines_var.get())
        if max_lines < 0:
            raise ValueError("块最大线数不能小于 0")
        output = Path(self.output_var.get().strip())
        if not str(output):
            raise ValueError("请选择输出目录")
        return ExportSettings(
            output_directory=output,
            paper_size=self.paper_var.get(),
            max_block_lines=max_lines,
            dimensions=self.dimensions_var.get(),
            occlusion=self.occlusion_var.get(),
            strict_section_occlusion=self.strict_section_var.get(),
            open_in_cad=self.open_cad_var.get(),
        )

    def _start_export(self) -> None:
        if self.export_thread and self.export_thread.is_alive():
            return
        try:
            settings = self._collect_settings()
        except (ValueError, tk.TclError) as exc:
            messagebox.showerror("设置错误", str(exc), parent=self.root)
            return
        self._save_settings()
        self.cancel_event.clear()
        self.progress_var.set(0)
        self.progress_text_var.set("正在启动")
        self.export_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self._log("开始导出当前 SketchUp 视图")
        self.export_thread = threading.Thread(target=self._export_worker, args=(settings,), daemon=True)
        self.export_thread.start()

    def _export_worker(self, settings: ExportSettings) -> None:
        try:
            result = export_current_view(
                settings,
                lambda value, message: self.events.put(("progress", (value, message))),
                self.cancel_event.is_set,
            )
            self.events.put(("success", result))
        except ExportCancelled as exc:
            self.events.put(("cancelled", str(exc)))
        except Exception as exc:
            self.events.put(("error", (str(exc), traceback.format_exc())))

    def _cancel_export(self) -> None:
        self.cancel_event.set()
        self.cancel_button.configure(state="disabled")
        self.progress_text_var.set("等待当前阶段结束后取消")
        self._log("已请求取消任务")

    def _set_idle(self) -> None:
        self.export_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")

    def _drain_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "progress":
                    value, message = payload  # type: ignore[misc]
                    self.progress_var.set(value)
                    self.progress_text_var.set(message)
                    self._log(str(message))
                elif event == "success":
                    result = payload
                    assert isinstance(result, ExportResult)
                    self.last_result = result
                    self._add_recent(result)
                    self._set_idle()
                    reduction = 0
                    if result.block_lines_before:
                        reduction = round((1 - result.block_lines_after / result.block_lines_before) * 100)
                    self._log(
                        f"完成：{result.layout} {result.scale}，块线减少 {reduction}% ，审计错误 {result.audit_errors}"
                    )
                    messagebox.showinfo(
                        "导出完成",
                        f"文件：{result.dxf.name}\n图幅：{result.layout}  {result.scale}\n审计错误：{result.audit_errors}",
                        parent=self.root,
                    )
                elif event == "cancelled":
                    self.progress_text_var.set("已取消")
                    self._set_idle()
                    self._log(str(payload))
                elif event == "error":
                    message, details = payload  # type: ignore[misc]
                    self.progress_text_var.set("导出失败")
                    self._set_idle()
                    self._log(f"错误：{message}")
                    self._log(str(details))
                    messagebox.showerror("导出失败", str(message), parent=self.root)
                elif event == "status":
                    health, cad_running = payload  # type: ignore[misc]
                    self.status_refreshing = False
                    self._apply_status(health, cad_running)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_events)

    def _refresh_status(self) -> None:
        if not self.status_refreshing:
            self.status_refreshing = True
            threading.Thread(target=self._status_worker, daemon=True).start()
        self.root.after(4000, self._refresh_status)

    def _status_worker(self) -> None:
        try:
            health = bridge_health(timeout=2)
        except Exception:
            health = None
        self.events.put(("status", (health, cad_is_running())))

    def _apply_status(self, health: dict | None, cad_running: bool) -> None:
        connected = bool(health and health.get("ok") and health.get("running"))
        self.sketchup_status.configure(
            text="已连接" if connected else "未连接",
            style="Connected.TLabel" if connected else "Disconnected.TLabel",
        )
        self.cad_status.configure(
            text="CAD 已运行" if cad_running else "CAD 未运行",
            style="Connected.TLabel" if cad_running else "Disconnected.TLabel",
        )
        if connected and health:
            title = str(health.get("title") or "未命名模型")
            version = str(health.get("sketchup_version") or "")
            self.model_var.set(f"{title}  |  SketchUp {version}")
        else:
            self.model_var.set("打开 SketchUp 并启动 SU2CAD / Codex Bridge")

    def _selected_path(self) -> Path | None:
        selected = self.recent_tree.selection()
        if selected:
            return Path(selected[0])
        if self.last_result:
            return self.last_result.dxf
        return None

    def _open_selected(self) -> None:
        path = self._selected_path()
        if not path or not path.exists():
            messagebox.showinfo("最近输出", "请选择一个仍然存在的输出文件", parent=self.root)
            return
        os.startfile(path)  # type: ignore[attr-defined]

    def _open_output_folder(self) -> None:
        path = Path(self.output_var.get()).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)  # type: ignore[attr-defined]

    def _log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{timestamp}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _on_close(self) -> None:
        if self.export_thread and self.export_thread.is_alive():
            if not messagebox.askyesno("退出 SU2CAD", "导出仍在进行，确定退出吗？", parent=self.root):
                return
            self.cancel_event.set()
        self._save_settings()
        self.root.destroy()


def main() -> None:
    enable_high_dpi()
    root = tk.Tk()
    SU2CADApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
