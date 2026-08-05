from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import customtkinter as ctk


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from core import ExportResult  # noqa: E402
from su2cad_app import SU2CADApp  # noqa: E402


class AppWorkflowTests(unittest.TestCase):
    def test_generate_button_runs_background_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = ctk.CTk()
            root.withdraw()
            app = SU2CADApp(root)
            app.settings_path = Path(temporary) / "settings.json"
            app.output_var.set(temporary)
            output = Path(temporary) / "result.dxf"
            output.write_bytes(b"DXF")
            linework = Path(temporary) / "result.json"
            linework.write_text("{}", encoding="utf-8")

            result = ExportResult(
                dxf=output,
                linework_json=linework,
                model_path="test.skp",
                model_title="Test",
                paper_size="A4",
                orientation="Landscape",
                layout="A4-L",
                scale="1:100",
                block_references=4,
                plant_block_references=1,
                material_hatches=3,
                material_count=2,
                simplified_blocks=1,
                block_lines_before=100,
                block_lines_after=20,
                audit_errors=0,
                opened_in_cad=False,
                elapsed_seconds=65.2,
                full_fidelity_blocks=12,
                optimized_dense_blocks=3,
                occluded_blocks=5,
                unique_entities=8_143_295,
                expanded_entities=11_417_247,
                planned_entities=4_641_683,
            )

            def fake_export(_settings, progress, _cancelled):
                progress(62, "正在计算材质色块并生成 DXF")
                progress(100, "导出完成")
                return result

            with patch("su2cad_app.export_current_view", side_effect=fake_export):
                app.last_result = result
                app.open_file_button.configure(state="normal")
                app._start_export()
                self.assertIsNone(app.last_result)
                self.assertEqual(app.open_file_button.cget("state"), "disabled")
                assert app.export_thread is not None
                app.export_thread.join(timeout=5)
                app._drain_events()

            self.assertEqual(app.last_result, result)
            self.assertEqual(app.result_title_var.get(), "A4-L  1:100")
            self.assertIn("2 种材质", app.result_detail_var.get())
            self.assertIn("完整简单对象 12", app.result_detail_var.get())
            self.assertIn("遮挡剔除 5", app.result_detail_var.get())
            self.assertIn("总耗时 01:05", app.result_detail_var.get())
            self.assertEqual(app.elapsed_var.get(), "总耗时 01:05")
            self.assertEqual(
                app.entity_total_var.get(),
                "模型总实体 8,143,295 · 展开估算 11,417,247 · 当前视图计划 4,641,683",
            )
            app.max_lines_var.set("")
            app._save_settings()
            saved = app._load_settings()
            self.assertEqual(saved["max_block_lines"], 2500)
            self.assertIsInstance(app.settings_card, ctk.CTkScrollableFrame)
            self.assertIsInstance(app.task_content, ctk.CTkFrame)
            self.assertFalse(hasattr(app, "task_scroll"))
            self.assertIsInstance(app.paper_segment, ctk.CTkOptionMenu)
            self.assertEqual(app.paper_segment.cget("values"), ["AUTO", "A4", "A3", "A2", "A1", "A0"])
            self.assertGreaterEqual(len(app.settings_detail_labels), 5)
            self.assertTrue(all(label.cget("wraplength") >= 250 for label in app.settings_detail_labels))
            self.assertEqual(app.advanced_frame.winfo_manager(), "grid")
            self.assertFalse(hasattr(app, "advanced_button"))
            self.assertEqual(app.export_button.cget("fg_color"), "#16A36A")
            self.assertEqual(app.quality_segment.cget("selected_color"), "#16A36A")
            self.assertEqual(len(app.icon_images), 9)
            app.materials_var.set(True)
            self.assertEqual(app.switch_buttons["材质色块"].cget("text"), "已开启")
            app.materials_var.set(False)
            self.assertEqual(app.switch_buttons["材质色块"].cget("text"), "已关闭")
            self.assertIn(
                app.font_family,
                {".萍方-简", "萍方-简", "PingFang SC", "苹方-简", "Microsoft YaHei UI", "TkDefaultFont"},
            )
            with (
                patch.object(app.settings_card._parent_canvas, "winfo_height", return_value=900),
                patch.object(app.settings_card, "winfo_reqheight", return_value=700),
                patch.object(app.settings_card._scrollbar, "grid_remove") as hide_scrollbar,
            ):
                app._update_settings_scrollbar_visibility()
                hide_scrollbar.assert_called_once()
            app._apply_responsive_layout(820)
            self.assertTrue(app.compact_mode)
            self.assertEqual(app.compact_settings_button.cget("text"), "设置")
            app._toggle_compact_settings()
            self.assertEqual(app.compact_settings_button.cget("text"), "返回任务")
            app._apply_responsive_layout(1180)
            self.assertFalse(app.compact_mode)
            self.assertEqual(app.workspace_card.grid_info()["column"], 1)

            app._add_recent(result)
            self.assertTrue(output.exists())
            with patch("su2cad_app.messagebox.askyesno", return_value=True):
                app._delete_recent(output)
            self.assertFalse(output.exists())
            self.assertFalse(any(item.get("path") == str(output) for item in app.saved.get("recent", [])))

            preserved = Path(temporary) / "preserved.dxf"
            preserved.write_bytes(b"DXF")
            app.saved["recent"] = [
                {"path": str(preserved), "time": "now", "layout": "A4-L", "size": "0.1 MB"}
            ]
            app._save_settings()
            app._rebuild_recent()
            with patch("su2cad_app.messagebox.askyesno", return_value=True):
                app._clear_recent_list()
            self.assertTrue(preserved.exists())
            self.assertEqual(app.saved.get("recent"), [])
            self.assertEqual(app.clear_recent_button.cget("state"), "disabled")
            root.destroy()


if __name__ == "__main__":
    unittest.main()
