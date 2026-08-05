from __future__ import annotations

import ctypes
import json
import os
import queue
import threading
import time
import traceback
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from core import (
    APP_VERSION,
    ExportCancelled,
    ExportResult,
    ExportSettings,
    bridge_health,
    cad_is_running,
    export_current_view,
    resource_root,
)


BG = "#F5F7F9"
SURFACE = "#FFFFFF"
SURFACE_ALT = "#F0F3F6"
BORDER = "#E0E5EA"
TEXT = "#20262E"
MUTED = "#6B7682"
PRIMARY = "#16A36A"
PRIMARY_HOVER = "#128158"
SUCCESS = "#23865A"
SUCCESS_SOFT = "#E8F5EE"
WARNING = "#B66A11"
WARNING_SOFT = "#FFF4E5"
ERROR = "#C2414D"
ERROR_SOFT = "#FCEBEC"

QUALITY_LINES = {"轻量": 800, "平衡": 2500, "精细": 6000}
QUALITY_CODES = {"轻量": "light", "平衡": "balanced", "精细": "precise"}
COMPACT_BREAKPOINT = 980
MAX_LOG_LINES = 500
MAX_EVENTS_PER_TICK = 50


def enable_high_dpi() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SU2CAD.Desktop")
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass


def resolve_ui_font(root: tk.Misc) -> str:
    families = set(tkfont.families(root))
    for candidate in (".萍方-简", "萍方-简", "PingFang SC", "苹方-简", "Microsoft YaHei UI"):
        if candidate in families:
            return candidate
    return "TkDefaultFont"


class SU2CADApp:
    def __init__(self, root: ctk.CTk) -> None:
        self.root = root
        self.font_family = resolve_ui_font(root)
        self.root.title(f"SU2CAD {APP_VERSION}")
        icon_path = resource_root() / "assets" / "su2cad.ico"
        if icon_path.exists():
            try:
                self.root.iconbitmap(default=str(icon_path))
            except tk.TclError:
                pass
        self.icon_images: list[tk.PhotoImage] = []
        for icon_size in (16, 20, 24, 32, 40, 48, 64, 128, 256):
            png_path = resource_root() / "assets" / f"su2cad-{icon_size}.png"
            if not png_path.exists():
                continue
            try:
                self.icon_images.append(tk.PhotoImage(file=str(png_path)))
            except tk.TclError:
                continue
        if self.icon_images:
            try:
                # Supplying native-size images prevents Tk/Windows from scaling
                # one large frame down into a blurry taskbar icon.
                self.root.iconphoto(True, *self.icon_images)
            except tk.TclError:
                pass
        self.root.geometry("1180x760")
        self.root.minsize(780, 560)
        self.root.configure(fg_color=BG)

        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.cancel_event = threading.Event()
        self.export_thread: threading.Thread | None = None
        self.status_refreshing = False
        self.sketchup_connected = False
        self.last_result: ExportResult | None = None
        self.recent_row_widgets: list[ctk.CTkBaseClass] = []
        self.log_visible = False
        self.compact_mode = False
        self.compact_settings_visible = False
        self.resize_job: str | None = None
        self.settings_scroll_job: str | None = None
        self.last_progress_value = -1
        self.last_progress_message = ""
        self.last_log_message = ""
        self.log_line_count = 0
        self.export_started_at: float | None = None
        self.switch_buttons: dict[str, ctk.CTkButton] = {}
        self.settings_detail_labels: list[ctk.CTkLabel] = []
        self.settings_path = Path(os.environ.get("APPDATA", str(Path.home()))) / "SU2CAD" / "settings.json"
        self.saved = self._load_settings()
        try:
            saved_max_lines = max(0, int(self.saved.get("max_block_lines", 2500)))
        except (TypeError, ValueError):
            saved_max_lines = 2500

        self.output_var = tk.StringVar(
            value=self.saved.get("output_directory", str(Path.home() / "Desktop" / "SketchUp-CAD"))
        )
        self.paper_var = tk.StringVar(value=self.saved.get("paper_size", "AUTO"))
        self.max_lines_var = tk.StringVar(value=str(saved_max_lines))
        saved_quality = str(self.saved.get("quality", self._quality_for_lines(saved_max_lines)))
        self.quality_var = tk.StringVar(value=saved_quality if saved_quality in QUALITY_LINES else "平衡")
        self.dimensions_var = tk.BooleanVar(value=bool(self.saved.get("dimensions", True)))
        self.occlusion_var = tk.BooleanVar(value=bool(self.saved.get("occlusion", True)))
        self.materials_var = tk.BooleanVar(value=bool(self.saved.get("material_fills", True)))
        self.strict_section_var = tk.BooleanVar(value=bool(self.saved.get("strict_section_occlusion", True)))
        self.open_cad_var = tk.BooleanVar(value=bool(self.saved.get("open_in_cad", True)))
        self.model_var = tk.StringVar(value="正在检测 SketchUp")
        self.phase_var = tk.StringVar(value="等待任务")
        self.elapsed_var = tk.StringVar(value="总耗时 --")
        self.entity_total_var = tk.StringVar(value="模型总实体 -- · 展开估算 -- · 当前视图计划 --")
        self.result_title_var = tk.StringVar(value="准备生成 CAD")
        self.result_detail_var = tk.StringVar(value="连接 SketchUp 后即可导出当前视图")

        self._build_ui()
        self._rebuild_recent()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Configure>", self._on_root_configure, add="+")
        self.root.after(100, self._drain_events)
        self.root.after(200, self._refresh_status)
        self.root.after(0, lambda: self._apply_responsive_layout(self.root.winfo_width()))
        self.root.after(250, self._update_settings_scrollbar_visibility)
        self.root.after(1000, self._tick_elapsed)
        self._log(f"SU2CAD {APP_VERSION} 已启动")

    @staticmethod
    def _quality_for_lines(value: int) -> str:
        return min(QUALITY_LINES, key=lambda name: abs(QUALITY_LINES[name] - value))

    def _font(self, size: int, weight: str = "normal") -> ctk.CTkFont:
        return ctk.CTkFont(family=self.font_family, size=size, weight=weight)

    def _build_ui(self) -> None:
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(1, weight=1)

        self._build_header()
        self._build_content()
        self._build_footer()

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self.root, fg_color=SURFACE, corner_radius=0, height=94)
        self.header = header
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(1, weight=1)
        header.grid_propagate(False)

        brand = ctk.CTkFrame(header, fg_color="transparent")
        brand.grid(row=0, column=0, padx=(28, 24), pady=18, sticky="w")
        ctk.CTkLabel(brand, text="SU2CAD", text_color=TEXT, font=self._font(20, "bold")).pack(anchor="w")
        ctk.CTkLabel(
            brand,
            text="SketchUp 可见视图转轻量 CAD",
            text_color=MUTED,
            font=self._font(11),
        ).pack(anchor="w", pady=(2, 0))

        model = ctk.CTkFrame(header, fg_color="transparent")
        self.model_panel = model
        model.grid(row=0, column=1, padx=12, pady=18, sticky="w")
        ctk.CTkLabel(model, text="当前模型", text_color=MUTED, font=self._font(10)).pack(anchor="w")
        ctk.CTkLabel(
            model,
            textvariable=self.model_var,
            text_color=TEXT,
            font=self._font(13, "bold"),
        ).pack(anchor="w", pady=(4, 0))

        status = ctk.CTkFrame(header, fg_color="transparent")
        self.status_panel = status
        status.grid(row=0, column=2, padx=(12, 28), pady=18, sticky="e")
        self.sketchup_chip = ctk.CTkLabel(
            status,
            text="SketchUp 检测中",
            width=132,
            height=32,
            corner_radius=8,
            fg_color=WARNING_SOFT,
            text_color=WARNING,
            font=self._font(10, "bold"),
        )
        self.sketchup_chip.grid(row=0, column=0, padx=(0, 8))
        self.cad_chip = ctk.CTkLabel(
            status,
            text="CAD 检测中",
            width=116,
            height=32,
            corner_radius=8,
            fg_color=WARNING_SOFT,
            text_color=WARNING,
            font=self._font(10, "bold"),
        )
        self.cad_chip.grid(row=0, column=1, padx=(0, 8))
        self.refresh_button = ctk.CTkButton(
            status,
            text="刷新",
            width=64,
            height=32,
            corner_radius=8,
            fg_color=SURFACE_ALT,
            hover_color=BORDER,
            text_color=TEXT,
            font=self._font(10),
            command=self._refresh_status_now,
        )
        self.refresh_button.grid(row=0, column=2)
        self.compact_settings_button = ctk.CTkButton(
            status,
            text="设置",
            width=72,
            height=32,
            corner_radius=8,
            fg_color=SURFACE_ALT,
            hover_color=BORDER,
            text_color=TEXT,
            font=self._font(10),
            command=self._toggle_compact_settings,
        )
        self.compact_settings_button.grid(row=0, column=3, padx=(8, 0))
        self.compact_settings_button.grid_remove()

    def _build_content(self) -> None:
        content = ctk.CTkFrame(self.root, fg_color=BG, corner_radius=0)
        self.content = content
        content.grid(row=1, column=0, sticky="nsew", padx=24, pady=20)
        content.grid_columnconfigure(0, minsize=360)
        content.grid_columnconfigure(1, weight=1)
        content.grid_rowconfigure(0, weight=1)

        self._build_settings_card(content)
        self._build_workspace_card(content)

    def _build_settings_card(self, parent: ctk.CTkFrame) -> None:
        card = ctk.CTkScrollableFrame(
            parent,
            fg_color=SURFACE,
            corner_radius=8,
            border_width=1,
            border_color=BORDER,
            scrollbar_fg_color=SURFACE,
            scrollbar_button_color="#C7D0D9",
            scrollbar_button_hover_color="#AEB9C4",
        )
        self.settings_card = card
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(card, text="标准图幅", text_color=TEXT, font=self._font(11, "bold"), height=20).grid(
            row=0, column=0, padx=20, pady=(18, 0), sticky="w"
        )
        self.paper_segment = ctk.CTkOptionMenu(
            card,
            values=["AUTO", "A4", "A3", "A2", "A1", "A0"],
            variable=self.paper_var,
            height=34,
            corner_radius=7,
            fg_color=SURFACE_ALT,
            button_color=PRIMARY,
            button_hover_color=PRIMARY_HOVER,
            text_color=TEXT,
            dropdown_fg_color=SURFACE,
            dropdown_hover_color=SURFACE_ALT,
            dropdown_text_color=TEXT,
            font=self._font(10, "bold"),
            dropdown_font=self._font(10),
        )
        self.paper_segment.grid(row=1, column=0, padx=20, pady=(8, 4), sticky="ew")
        paper_detail = ctk.CTkLabel(
            card,
            text="AUTO 会根据图形尺寸和横竖方向自动选择 A0–A4 图幅及比例。",
            text_color=MUTED,
            font=self._font(9),
            height=18,
            anchor="w",
            justify="left",
            wraplength=280,
        )
        paper_detail.grid(row=2, column=0, padx=20, pady=(0, 10), sticky="ew")
        self.settings_detail_labels.append(paper_detail)

        self._add_switch(
            card, 3, "仅输出可见线",
            "开启后过滤被墙体或物体遮挡的背面、内部线；关闭可加快导出。",
            self.occlusion_var,
        )
        self._add_switch(
            card, 4, "材质色块",
            "把 SketchUp 面材质转换为 CAD 实色填充（HATCH）。",
            self.materials_var,
        )
        self._add_switch(
            card, 5, "生成总尺寸",
            "在模型空间自动标注当前图形的总宽和总高。",
            self.dimensions_var,
        )
        self._add_switch(
            card, 6, "完成后打开 CAD",
            "生成完成后自动用 AutoCAD 或天正打开 DXF 文件。",
            self.open_cad_var,
        )

        ctk.CTkLabel(card, text="场景精度", text_color=TEXT, font=self._font(11, "bold"), height=20).grid(
            row=7, column=0, padx=20, pady=(10, 0), sticky="w"
        )
        self.quality_segment = ctk.CTkSegmentedButton(
            card,
            values=list(QUALITY_LINES),
            variable=self.quality_var,
            command=self._set_quality,
            height=36,
            corner_radius=7,
            selected_color=PRIMARY,
            selected_hover_color=PRIMARY_HOVER,
            unselected_color=SURFACE_ALT,
            unselected_hover_color=BORDER,
            text_color=TEXT,
            font=self._font(10),
        )
        self.quality_segment.grid(row=8, column=0, padx=20, pady=(8, 8), sticky="ew")
        quality_detail = ctk.CTkLabel(
            card,
            text="轻量：大模型最快；平衡：速度与细节兼顾；精细：细节最多但耗时最长。",
            text_color=MUTED,
            font=self._font(9),
            height=18,
            anchor="w",
            justify="left",
            wraplength=280,
        )
        quality_detail.grid(row=9, column=0, padx=20, pady=(0, 4), sticky="ew")
        self.settings_detail_labels.append(quality_detail)

        ctk.CTkLabel(
            card,
            text="高级设置",
            text_color=TEXT,
            font=self._font(11, "bold"),
            height=20,
        ).grid(row=10, column=0, padx=20, pady=(8, 0), sticky="w")
        self.advanced_frame = ctk.CTkFrame(card, fg_color=SURFACE_ALT, corner_radius=8)
        self.advanced_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            self.advanced_frame,
            text="块最大线数",
            text_color=TEXT,
            font=self._font(10),
            height=20,
        ).grid(row=0, column=0, padx=10, pady=8, sticky="w")
        ctk.CTkEntry(
            self.advanced_frame,
            textvariable=self.max_lines_var,
            width=96,
            height=28,
            font=self._font(10),
        ).grid(
            row=0, column=1, padx=10, pady=8, sticky="e"
        )
        max_lines_detail = ctk.CTkLabel(
            self.advanced_frame,
            text="限制每个高密度 CAD 块保留的细节线。数值越大越细致、文件越大；0 表示不限制。",
            text_color=MUTED,
            font=self._font(8),
            anchor="w",
            justify="left",
            wraplength=250,
        )
        max_lines_detail.grid(row=1, column=0, columnspan=2, padx=10, pady=(0, 8), sticky="ew")
        self.settings_detail_labels.append(max_lines_detail)
        strict_row = ctk.CTkFrame(self.advanced_frame, fg_color="transparent")
        strict_row.grid(row=2, column=0, columnspan=2, padx=10, pady=(0, 8), sticky="ew")
        strict_row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            strict_row,
            text="剖切面严格遮挡",
            text_color=TEXT,
            font=self._font(9),
            height=28,
        ).grid(row=0, column=0, sticky="w")
        self._create_toggle_button(strict_row, "剖切面严格遮挡", self.strict_section_var).grid(
            row=0, column=1, sticky="e"
        )
        strict_detail = ctk.CTkLabel(
            strict_row,
            text="仅在 SketchUp 启用剖切面时生效；开启会过滤剖切后仍被遮挡的线，但处理更慢。",
            text_color=MUTED,
            font=self._font(8),
            anchor="w",
            justify="left",
            wraplength=250,
        )
        strict_detail.grid(row=1, column=0, columnspan=2, pady=(0, 2), sticky="ew")
        self.settings_detail_labels.append(strict_detail)
        self.advanced_frame.grid(row=11, column=0, padx=20, pady=(6, 12), sticky="ew")

    def _add_switch(self, parent, row: int, title: str, detail: str, variable: tk.BooleanVar) -> None:
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid(row=row, column=0, padx=20, pady=(2, 5), sticky="ew")
        frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            frame,
            text=title,
            text_color=TEXT,
            font=self._font(11, "bold"),
            height=20,
            anchor="w",
            justify="left",
        ).grid(row=0, column=0, sticky="ew")
        detail_label = ctk.CTkLabel(
            frame,
            text=detail,
            text_color=MUTED,
            font=self._font(9),
            height=18,
            anchor="w",
            justify="left",
            wraplength=280,
        )
        detail_label.grid(row=1, column=0, columnspan=2, pady=(2, 0), sticky="ew")
        self.settings_detail_labels.append(detail_label)
        self._create_toggle_button(frame, title, variable).grid(
            row=0, column=1, padx=(12, 0), sticky="e"
        )

    def _create_toggle_button(
        self,
        parent: ctk.CTkFrame,
        name: str,
        variable: tk.BooleanVar,
    ) -> ctk.CTkButton:
        button = ctk.CTkButton(
            parent,
            text="",
            width=68,
            height=30,
            corner_radius=15,
            border_width=1,
            font=self._font(9, "bold"),
        )

        def sync_state(*_args) -> None:
            enabled = bool(variable.get())
            button.configure(
                text="已开启" if enabled else "已关闭",
                fg_color=PRIMARY if enabled else SURFACE_ALT,
                hover_color=PRIMARY_HOVER if enabled else BORDER,
                border_color=PRIMARY if enabled else "#B8C1CA",
                text_color=SURFACE if enabled else MUTED,
            )

        button.configure(command=lambda: variable.set(not variable.get()))
        variable.trace_add("write", sync_state)
        sync_state()
        self.switch_buttons[name] = button
        return button

    def _build_workspace_card(self, parent: ctk.CTkFrame) -> None:
        card = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=8, border_width=1, border_color=BORDER)
        self.workspace_card = card
        card.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(0, weight=1)

        self.tabs = ctk.CTkTabview(
            card,
            fg_color=SURFACE,
            segmented_button_selected_color=PRIMARY,
            segmented_button_selected_hover_color=PRIMARY_HOVER,
            segmented_button_unselected_color=SURFACE_ALT,
            segmented_button_unselected_hover_color=BORDER,
            text_color=TEXT,
        )
        self.tabs.grid(row=0, column=0, sticky="nsew", padx=14, pady=12)
        self.tabs._segmented_button.configure(font=self._font(10))
        task = self.tabs.add("当前任务")
        recent = self.tabs.add("最近输出")
        self._build_task_tab(task)
        self._build_recent_tab(recent)

    def _build_task_tab(self, tab: ctk.CTkFrame) -> None:
        outer_tab = tab
        outer_tab.grid_columnconfigure(0, weight=1)
        outer_tab.grid_rowconfigure(0, weight=1)
        tab = ctk.CTkFrame(
            outer_tab,
            fg_color="transparent",
            corner_radius=0,
        )
        self.task_content = tab
        tab.grid(row=0, column=0, sticky="nsew")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(4, weight=1)

        state = ctk.CTkFrame(tab, fg_color=SURFACE_ALT, corner_radius=8)
        state.grid(row=0, column=0, padx=8, pady=(18, 14), sticky="ew")
        state.grid_columnconfigure(0, weight=1)
        self.state_badge = ctk.CTkLabel(
            state,
            text="就绪",
            width=72,
            height=28,
            corner_radius=7,
            fg_color=SUCCESS_SOFT,
            text_color=SUCCESS,
            font=self._font(10, "bold"),
        )
        self.state_badge.grid(row=0, column=0, padx=18, pady=(16, 4), sticky="w")
        ctk.CTkLabel(
            state,
            textvariable=self.result_title_var,
            text_color=TEXT,
            font=self._font(16, "bold"),
        ).grid(row=1, column=0, padx=18, pady=(4, 2), sticky="w")
        self.result_detail_label = ctk.CTkLabel(
            state,
            textvariable=self.result_detail_var,
            text_color=MUTED,
            font=self._font(10),
            wraplength=600,
            justify="left",
        )
        self.result_detail_label.grid(row=2, column=0, padx=18, pady=(2, 16), sticky="w")

        phase = ctk.CTkFrame(tab, fg_color="transparent")
        phase.grid(row=1, column=0, padx=8, sticky="ew")
        phase.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(phase, text="任务进度", text_color=TEXT, font=self._font(11, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ctk.CTkLabel(phase, textvariable=self.elapsed_var, text_color=PRIMARY, font=self._font(10, "bold")).grid(
            row=0, column=1, padx=(16, 12), sticky="w"
        )
        ctk.CTkLabel(phase, textvariable=self.phase_var, text_color=MUTED, font=self._font(10)).grid(
            row=0, column=2, sticky="e"
        )
        self.entity_total_label = ctk.CTkLabel(
            phase,
            textvariable=self.entity_total_var,
            text_color=MUTED,
            font=self._font(9),
            justify="left",
        )
        self.entity_total_label.grid(row=1, column=0, columnspan=3, pady=(5, 0), sticky="w")
        self.progress = ctk.CTkProgressBar(tab, height=9, corner_radius=5, progress_color=PRIMARY, fg_color=BORDER)
        self.progress.grid(row=2, column=0, padx=8, pady=(8, 18), sticky="ew")
        self.progress.set(0)

        actions = ctk.CTkFrame(tab, fg_color="transparent")
        actions.grid(row=3, column=0, padx=8, sticky="w")
        self.open_file_button = ctk.CTkButton(
            actions,
            text="打开 CAD",
            width=104,
            height=34,
            fg_color=SURFACE_ALT,
            hover_color=BORDER,
            text_color=TEXT,
            font=self._font(10),
            command=self._open_last_result,
            state="disabled",
        )
        self.open_file_button.grid(row=0, column=0, padx=(0, 8))
        ctk.CTkButton(
            actions,
            text="打开目录",
            width=104,
            height=34,
            fg_color=SURFACE_ALT,
            hover_color=BORDER,
            text_color=TEXT,
            font=self._font(10),
            command=self._open_output_folder,
        ).grid(row=0, column=1)

        log_area = ctk.CTkFrame(tab, fg_color="transparent")
        log_area.grid(row=5, column=0, padx=8, pady=(16, 8), sticky="sew")
        log_area.grid_columnconfigure(0, weight=1)
        self.log_button = ctk.CTkButton(
            log_area,
            text="查看详细日志",
            height=30,
            fg_color="transparent",
            hover_color=SURFACE_ALT,
            text_color=MUTED,
            anchor="w",
            font=self._font(10),
            command=self._toggle_log,
        )
        self.log_button.grid(row=0, column=0, sticky="ew")
        self.log_text = ctk.CTkTextbox(
            log_area,
            height=150,
            corner_radius=8,
            border_width=1,
            border_color=BORDER,
            fg_color="#FAFBFC",
            text_color="#435160",
            font=(self.font_family, 9),
            wrap="word",
        )
        self.log_text.grid(row=1, column=0, pady=(6, 0), sticky="ew")
        self.log_text.grid_remove()

    def _build_recent_tab(self, tab: ctk.CTkFrame) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=1)
        header = ctk.CTkFrame(tab, fg_color="transparent")
        header.grid(row=0, column=0, padx=8, pady=(18, 2), sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header,
            text="最近生成的 CAD",
            text_color=TEXT,
            font=self._font(14, "bold"),
        ).grid(row=0, column=0, sticky="w")
        self.clear_recent_button = ctk.CTkButton(
            header,
            text="清空列表",
            width=76,
            height=30,
            corner_radius=7,
            fg_color=SURFACE_ALT,
            hover_color=BORDER,
            text_color=TEXT,
            font=self._font(9),
            command=self._clear_recent_list,
        )
        self.clear_recent_button.grid(row=0, column=1, sticky="e")
        ctk.CTkLabel(
            tab,
            text="“删除”会同时删除磁盘上的 DXF；“清空列表”只移除记录，不删除文件。",
            text_color=MUTED,
            font=self._font(9),
            anchor="w",
        ).grid(row=1, column=0, padx=8, pady=(0, 6), sticky="ew")
        self.recent_frame = ctk.CTkScrollableFrame(tab, fg_color="transparent", corner_radius=0)
        self.recent_frame.grid(row=2, column=0, padx=0, pady=(0, 8), sticky="nsew")
        self.recent_frame.grid_columnconfigure(0, weight=1)

    def _build_footer(self) -> None:
        footer = ctk.CTkFrame(self.root, fg_color=SURFACE, corner_radius=0, height=86)
        self.footer = footer
        footer.grid(row=2, column=0, sticky="ew")
        footer.grid_columnconfigure(1, weight=1)
        footer.grid_propagate(False)

        self.output_label = ctk.CTkLabel(
            footer, text="输出目录", text_color=MUTED, font=self._font(10)
        )
        self.output_label.grid(row=0, column=0, padx=(28, 10), pady=23, sticky="w")
        self.output_entry = ctk.CTkEntry(
            footer,
            textvariable=self.output_var,
            height=40,
            corner_radius=8,
            border_width=1,
            border_color=BORDER,
            fg_color="#FAFBFC",
            font=self._font(10),
        )
        self.output_entry.grid(row=0, column=1, pady=23, sticky="ew")
        self.browse_button = ctk.CTkButton(
            footer,
            text="浏览",
            width=72,
            height=40,
            corner_radius=8,
            fg_color=SURFACE_ALT,
            hover_color=BORDER,
            text_color=TEXT,
            font=self._font(10),
            command=self._browse_output,
        )
        self.browse_button.grid(row=0, column=2, padx=(10, 16), pady=23)
        self.cancel_button = ctk.CTkButton(
            footer,
            text="取消",
            width=78,
            height=42,
            corner_radius=8,
            fg_color=ERROR_SOFT,
            hover_color="#F7D9DC",
            text_color=ERROR,
            font=self._font(10),
            command=self._cancel_export,
        )
        self.cancel_button.grid(row=0, column=3, padx=(0, 10), pady=22)
        self.cancel_button.grid_remove()
        self.export_button = ctk.CTkButton(
            footer,
            text="生成 CAD",
            width=142,
            height=44,
            corner_radius=8,
            fg_color=PRIMARY,
            hover_color=PRIMARY_HOVER,
            text_color="#FFFFFF",
            font=self._font(12, "bold"),
            command=self._start_export,
            state="disabled",
        )
        self.export_button.grid(row=0, column=4, padx=(0, 28), pady=21)

    def _toggle_log(self) -> None:
        self.log_visible = not self.log_visible
        if self.log_visible:
            self.log_text.grid()
            self.log_button.configure(text="收起详细日志")
        else:
            self.log_text.grid_remove()
            self.log_button.configure(text="查看详细日志")

    def _on_root_configure(self, event: tk.Event) -> None:
        if event.widget is not self.root:
            return
        if self.resize_job is not None:
            self.root.after_cancel(self.resize_job)
        width = int(event.width)
        self.resize_job = self.root.after(100, lambda: self._apply_responsive_layout(width))

    def _schedule_settings_scrollbar_update(self) -> None:
        if self.settings_scroll_job is not None:
            self.root.after_cancel(self.settings_scroll_job)
        self.settings_scroll_job = self.root.after(120, self._update_settings_scrollbar_visibility)

    def _update_settings_scrollbar_visibility(self) -> None:
        self.settings_scroll_job = None
        scrollbar = getattr(self.settings_card, "_scrollbar", None)
        canvas = getattr(self.settings_card, "_parent_canvas", None)
        if scrollbar is None or canvas is None:
            return
        viewport_height = int(canvas.winfo_height())
        content_height = int(self.settings_card.winfo_reqheight())
        if viewport_height <= 1:
            return
        if content_height > viewport_height + 4:
            scrollbar.grid()
        else:
            scrollbar.grid_remove()
            canvas.yview_moveto(0.0)

    def _apply_responsive_layout(self, width: int) -> None:
        self.resize_job = None
        compact = width < COMPACT_BREAKPOINT
        available_detail_width = max(260, width - (110 if compact else 520))
        self.result_detail_label.configure(wraplength=available_detail_width)
        self.entity_total_label.configure(wraplength=available_detail_width)
        settings_wrap = min(max(220, width - 100), 620) if compact else 280
        for label in self.settings_detail_labels:
            label.configure(wraplength=settings_wrap)
        self._schedule_settings_scrollbar_update()
        if compact == self.compact_mode:
            return

        self.compact_mode = compact
        if compact:
            self.header.configure(height=82)
            self.model_panel.grid_remove()
            self.cad_chip.grid_remove()
            self.compact_settings_button.grid()
            self.content.grid_configure(padx=14, pady=12)
            self.content.grid_columnconfigure(0, weight=1, minsize=0)
            self.content.grid_columnconfigure(1, weight=0, minsize=0)
            self.workspace_card.grid(row=0, column=0, sticky="nsew", padx=0)
            self.settings_card.grid_remove()
            self.compact_settings_visible = False
            self.compact_settings_button.configure(text="设置")

            self.footer.configure(height=126)
            self.footer.grid_columnconfigure(1, weight=1)
            self.output_label.grid(row=0, column=0, padx=(16, 8), pady=(14, 6), sticky="w")
            self.output_entry.grid(row=0, column=1, pady=(14, 6), sticky="ew")
            self.browse_button.grid(row=0, column=2, padx=(8, 16), pady=(14, 6))
            self.cancel_button.grid_configure(row=1, column=1, padx=(0, 10), pady=(6, 14), sticky="e")
            self.export_button.grid_configure(row=1, column=2, padx=(0, 16), pady=(6, 14), sticky="e")
        else:
            self.header.configure(height=94)
            self.model_panel.grid()
            self.cad_chip.grid()
            self.compact_settings_button.grid_remove()
            self.content.grid_configure(padx=24, pady=20)
            self.content.grid_columnconfigure(0, weight=0, minsize=360)
            self.content.grid_columnconfigure(1, weight=1, minsize=0)
            self.settings_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
            self.workspace_card.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
            self.compact_settings_visible = False

            self.footer.configure(height=86)
            self.output_label.grid(row=0, column=0, padx=(28, 10), pady=23, sticky="w")
            self.output_entry.grid(row=0, column=1, pady=23, sticky="ew")
            self.browse_button.grid(row=0, column=2, padx=(10, 16), pady=23)
            self.cancel_button.grid_configure(row=0, column=3, padx=(0, 10), pady=22, sticky="")
            self.export_button.grid_configure(row=0, column=4, padx=(0, 28), pady=21, sticky="")

        if not (self.export_thread and self.export_thread.is_alive()):
            self.cancel_button.grid_remove()

    def _toggle_compact_settings(self) -> None:
        if not self.compact_mode:
            return
        self.compact_settings_visible = not self.compact_settings_visible
        if self.compact_settings_visible:
            self.workspace_card.grid_remove()
            self.settings_card.grid(row=0, column=0, sticky="nsew", padx=0)
            self.compact_settings_button.configure(text="返回任务")
        else:
            self.settings_card.grid_remove()
            self.workspace_card.grid(row=0, column=0, sticky="nsew", padx=0)
            self.compact_settings_button.configure(text="设置")
        self._schedule_settings_scrollbar_update()

    def _set_quality(self, value: str) -> None:
        self.max_lines_var.set(str(QUALITY_LINES[value]))

    def _load_settings(self) -> dict:
        try:
            return json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _save_settings(self) -> None:
        try:
            max_block_lines = max(0, int(self.max_lines_var.get().strip()))
        except (TypeError, ValueError, tk.TclError):
            max_block_lines = 2500
        data = {
            "output_directory": self.output_var.get(),
            "paper_size": self.paper_var.get(),
            "max_block_lines": max_block_lines,
            "quality": self.quality_var.get(),
            "dimensions": self.dimensions_var.get(),
            "occlusion": self.occlusion_var.get(),
            "material_fills": self.materials_var.get(),
            "strict_section_occlusion": self.strict_section_var.get(),
            "open_in_cad": self.open_cad_var.get(),
            "recent": self.saved.get("recent", [])[:10],
        }
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self.saved = data

    def _browse_output(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.output_var.get(), title="选择 CAD 输出目录")
        if selected:
            self.output_var.set(selected)

    def _collect_settings(self) -> ExportSettings:
        max_lines_text = self.max_lines_var.get().strip()
        if not max_lines_text:
            raise ValueError("请输入块最大线数")
        try:
            max_lines = int(max_lines_text)
        except ValueError as exc:
            raise ValueError("块最大线数必须是整数") from exc
        if max_lines < 0:
            raise ValueError("块最大线数不能小于 0")
        output_text = self.output_var.get().strip()
        if not output_text:
            raise ValueError("请选择输出目录")
        return ExportSettings(
            output_directory=Path(output_text),
            paper_size=self.paper_var.get(),
            max_block_lines=max_lines,
            quality=QUALITY_CODES.get(self.quality_var.get(), "balanced"),
            dimensions=self.dimensions_var.get(),
            occlusion=self.occlusion_var.get(),
            material_fills=self.materials_var.get(),
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
        self.last_progress_value = -1
        self.last_progress_message = ""
        self.progress.set(0)
        self.phase_var.set("正在启动")
        self.entity_total_var.set("模型总实体 -- · 展开估算 -- · 当前视图计划 --")
        self.export_started_at = time.perf_counter()
        self.elapsed_var.set("已用时 00:00")
        self._set_state("处理中", WARNING, WARNING_SOFT)
        self.result_title_var.set("正在读取当前视图")
        self.result_detail_var.set("SketchUp 大模型可能需要数分钟，请保持模型窗口打开")
        self.last_result = None
        self.open_file_button.configure(state="disabled")
        self.export_button.configure(state="disabled")
        if self.compact_mode and self.compact_settings_visible:
            self._toggle_compact_settings()
        self.cancel_button.grid()
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
            details = traceback.format_exc()
            self._write_failure_report(settings, str(exc), details)
            self.events.put(("error", (str(exc), details)))

    def _write_failure_report(self, settings: ExportSettings, message: str, details: str) -> None:
        try:
            output = settings.output_directory.expanduser()
            output.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            report = output / f"SU2CAD_export_failure_{timestamp}.log"
            report.write_text(
                f"SU2CAD {APP_VERSION}\n时间：{datetime.now():%Y-%m-%d %H:%M:%S}\n"
                f"质量：{settings.quality}\n错误：{message}\n\n{details}",
                encoding="utf-8",
            )
        except OSError:
            pass

    def _cancel_export(self) -> None:
        self.cancel_event.set()
        self.cancel_button.configure(state="disabled")
        self.phase_var.set("等待当前阶段结束后取消")
        self._log("已请求取消任务")

    def _set_idle(self) -> None:
        self.cancel_button.grid_remove()
        self.cancel_button.configure(state="normal")
        self.export_button.configure(state="normal" if self.sketchup_connected else "disabled")

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        total = max(0, int(round(seconds)))
        hours, remainder = divmod(total, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"

    def _finish_elapsed(self, seconds: float | None = None) -> float:
        if seconds is None:
            seconds = time.perf_counter() - self.export_started_at if self.export_started_at is not None else 0.0
        self.export_started_at = None
        self.elapsed_var.set(f"总耗时 {self._format_elapsed(seconds)}")
        return seconds

    def _tick_elapsed(self) -> None:
        if self.export_started_at is not None:
            seconds = time.perf_counter() - self.export_started_at
            self.elapsed_var.set(f"已用时 {self._format_elapsed(seconds)}")
        self.root.after(1000, self._tick_elapsed)

    def _set_state(self, text: str, color: str, soft_color: str) -> None:
        self.state_badge.configure(text=text, text_color=color, fg_color=soft_color)

    def _drain_events(self) -> None:
        processed = 0
        try:
            while processed < MAX_EVENTS_PER_TICK:
                event, payload = self.events.get_nowait()
                processed += 1
                if event == "progress":
                    value, message = payload  # type: ignore[misc]
                    numeric_value = int(float(value))
                    text_message = str(message)
                    if numeric_value != self.last_progress_value:
                        self.progress.set(float(numeric_value) / 100.0)
                        self.last_progress_value = numeric_value
                    if text_message != self.last_progress_message:
                        self.phase_var.set(text_message)
                        if text_message.startswith("模型总实体 "):
                            self.entity_total_var.set(text_message)
                        self._log(text_message)
                        self.last_progress_message = text_message
                elif event == "success":
                    result = payload
                    assert isinstance(result, ExportResult)
                    self.last_result = result
                    self._finish_elapsed(result.elapsed_seconds)
                    self._add_recent(result)
                    self._set_idle()
                    self._set_state("已完成", SUCCESS, SUCCESS_SOFT)
                    self.result_title_var.set(f"{result.layout}  {result.scale}")
                    detail = (
                        f"{result.material_count} 种材质，{result.material_hatches} 个色块，"
                        f"{result.block_references} 个块参照，审计错误 {result.audit_errors}，"
                        f"总耗时 {self._format_elapsed(result.elapsed_seconds)}"
                    )
                    fidelity = (
                        f"完整简单对象 {result.full_fidelity_blocks}，"
                        f"优化复杂块 {result.optimized_dense_blocks}，"
                        f"遮挡剔除 {result.occluded_blocks}"
                    )
                    detail = f"{detail} · {fidelity}"
                    self.entity_total_var.set(
                        f"模型总实体 {result.unique_entities:,} · "
                        f"展开估算 {result.expanded_entities:,} · "
                        f"当前视图计划 {result.planned_entities:,}"
                    )
                    if result.warnings:
                        detail = f"{detail} · {result.warnings[0]}"
                        self._log(result.warnings[0])
                    self.result_detail_var.set(detail)
                    self.open_file_button.configure(state="normal")
                    self.tabs.set("当前任务")
                    self._log(f"完成：{result.dxf}")
                elif event == "cancelled":
                    self._finish_elapsed()
                    self.phase_var.set("已取消")
                    self._set_idle()
                    self._set_state("已取消", MUTED, SURFACE_ALT)
                    self.result_title_var.set("任务已取消")
                    self.result_detail_var.set(str(payload))
                    self._log(str(payload))
                elif event == "error":
                    self._finish_elapsed()
                    message, details = payload  # type: ignore[misc]
                    self.phase_var.set("导出失败")
                    self._set_idle()
                    self._set_state("失败", ERROR, ERROR_SOFT)
                    self.result_title_var.set("未能生成 CAD")
                    summary = str(message).splitlines()[0]
                    self.result_detail_var.set(summary[:500])
                    self._log(f"错误：{message}")
                    self._log(str(details))
                    if not self.log_visible:
                        self._toggle_log()
                elif event == "status":
                    health, cad_running = payload  # type: ignore[misc]
                    self.status_refreshing = False
                    self._apply_status(health, cad_running)
        except queue.Empty:
            pass
        self.root.after(20 if processed == MAX_EVENTS_PER_TICK else 100, self._drain_events)

    def _refresh_status(self) -> None:
        if not (self.export_thread and self.export_thread.is_alive()):
            self._refresh_status_now()
        self.root.after(8000, self._refresh_status)

    def _refresh_status_now(self) -> None:
        if self.export_thread and self.export_thread.is_alive():
            return
        if self.status_refreshing:
            return
        self.status_refreshing = True
        threading.Thread(target=self._status_worker, daemon=True).start()

    def _status_worker(self) -> None:
        try:
            health = bridge_health(timeout=2)
        except Exception:
            health = None
        self.events.put(("status", (health, cad_is_running())))

    def _apply_status(self, health: dict | None, cad_running: bool) -> None:
        self.sketchup_connected = bool(health and health.get("ok") and health.get("running"))
        if self.sketchup_connected and health:
            self.sketchup_chip.configure(text="SketchUp 已连接", fg_color=SUCCESS_SOFT, text_color=SUCCESS)
            title = str(health.get("title") or "未命名模型")
            version = str(health.get("sketchup_version") or "")
            self.model_var.set(f"{title}  ·  SketchUp {version}")
        else:
            self.sketchup_chip.configure(text="SketchUp 未连接", fg_color=ERROR_SOFT, text_color=ERROR)
            self.model_var.set("打开 SketchUp 并启动 SU2CAD / Codex Bridge")
        self.cad_chip.configure(
            text="CAD 已运行" if cad_running else "CAD 未运行",
            fg_color=SUCCESS_SOFT if cad_running else WARNING_SOFT,
            text_color=SUCCESS if cad_running else WARNING,
        )
        if not (self.export_thread and self.export_thread.is_alive()):
            self.export_button.configure(state="normal" if self.sketchup_connected else "disabled")

    def _add_recent(self, result: ExportResult) -> None:
        record = {
            "path": str(result.dxf),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "layout": result.layout,
            "size": f"{result.dxf.stat().st_size / 1024 / 1024:.1f} MB",
        }
        recent = [item for item in self.saved.get("recent", []) if item.get("path") != str(result.dxf)]
        self.saved["recent"] = [record, *recent][:10]
        self._save_settings()
        self._rebuild_recent()

    def _rebuild_recent(self) -> None:
        for widget in self.recent_frame.winfo_children():
            widget.destroy()
        recent = self.saved.get("recent", [])
        self.clear_recent_button.configure(state="normal" if recent else "disabled")
        if not recent:
            ctk.CTkLabel(
                self.recent_frame,
                text="还没有输出记录",
                text_color=MUTED,
                font=self._font(11),
            ).grid(row=0, column=0, padx=12, pady=36)
            return
        for index, item in enumerate(recent):
            path = Path(item.get("path", ""))
            row = ctk.CTkFrame(self.recent_frame, fg_color=SURFACE_ALT, corner_radius=8)
            row.grid(row=index, column=0, padx=4, pady=5, sticky="ew")
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                row,
                text=path.name,
                text_color=TEXT,
                font=self._font(10, "bold"),
                anchor="w",
            ).grid(row=0, column=0, padx=14, pady=(10, 2), sticky="ew")
            ctk.CTkLabel(
                row,
                text=f"{item.get('time', '')}  ·  {item.get('layout', '')}  ·  {item.get('size', '')}",
                text_color=MUTED,
                font=self._font(9),
                anchor="w",
            ).grid(row=1, column=0, padx=14, pady=(0, 10), sticky="ew")
            ctk.CTkButton(
                row,
                text="打开",
                width=56,
                height=30,
                fg_color=SURFACE,
                hover_color=BORDER,
                text_color=TEXT,
                font=self._font(10),
                command=lambda selected=path: self._open_path(selected),
            ).grid(row=0, column=1, rowspan=2, padx=(8, 4), pady=10)
            ctk.CTkButton(
                row,
                text="删除",
                width=56,
                height=30,
                fg_color=ERROR_SOFT,
                hover_color="#F7D9DC",
                text_color=ERROR,
                font=self._font(10),
                command=lambda selected=path: self._delete_recent(selected),
            ).grid(row=0, column=2, rowspan=2, padx=(4, 12), pady=10)

    def _delete_recent(self, path: Path) -> None:
        confirmed = messagebox.askyesno(
            "删除输出",
            f"将从列表中移除并永久删除这个 DXF 文件：\n\n{path.name}\n\n是否继续？",
            parent=self.root,
        )
        if not confirmed:
            return
        try:
            if path.exists():
                path.unlink()
        except OSError as exc:
            messagebox.showerror("删除失败", f"无法删除文件：\n{path}\n\n{exc}", parent=self.root)
            return

        self.saved["recent"] = [
            item for item in self.saved.get("recent", [])
            if item.get("path") != str(path)
        ]
        self._save_settings()
        self._rebuild_recent()

    def _clear_recent_list(self) -> None:
        if not self.saved.get("recent", []):
            return
        confirmed = messagebox.askyesno(
            "清空输出列表",
            "只清空最近输出记录，不会删除磁盘上的任何 DXF 文件。\n\n是否继续？",
            parent=self.root,
        )
        if not confirmed:
            return
        self.saved["recent"] = []
        self._save_settings()
        self._rebuild_recent()

    def _open_path(self, path: Path) -> None:
        if not path.exists():
            self._set_state("文件缺失", ERROR, ERROR_SOFT)
            self.result_detail_var.set(f"找不到文件：{path}")
            return
        os.startfile(path)  # type: ignore[attr-defined]

    def _open_last_result(self) -> None:
        if self.last_result:
            self._open_path(self.last_result.dxf)

    def _open_output_folder(self) -> None:
        path = Path(self.output_var.get()).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)  # type: ignore[attr-defined]

    def _log(self, message: str) -> None:
        if message == self.last_log_message:
            return
        self.last_log_message = message
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{timestamp}] {message}\n")
        self.log_line_count += max(1, message.count("\n") + 1)
        overflow = self.log_line_count - MAX_LOG_LINES
        if overflow > 0:
            self.log_text.delete("1.0", f"{overflow + 1}.0")
            self.log_line_count = MAX_LOG_LINES
        self.log_text.see("end")

    def _on_close(self) -> None:
        if self.export_thread and self.export_thread.is_alive():
            if not messagebox.askyesno("退出 SU2CAD", "导出仍在进行，确定退出吗？", parent=self.root):
                return
            self.cancel_event.set()
        try:
            self._save_settings()
        finally:
            self.root.destroy()


def main() -> None:
    enable_high_dpi()
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk(fg_color=BG)
    SU2CADApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
