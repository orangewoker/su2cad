from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ezdxf  # noqa: E402
from shapely.geometry import Polygon  # noqa: E402
from build_dxf import (  # noqa: E402
    MAX_HATCH_VERTICES_PER_GROUP,
    PLANT_BLOCK_LAYER,
    add_curve,
    build,
    enforce_geometry_vertex_budget,
    expanded_fills,
    geometry_vertex_count,
    visible_material_geometry,
)


class MaterialHatchTests(unittest.TestCase):
    @staticmethod
    def base_payload() -> dict:
        return {
            "format": "sketchup-current-view-linework",
            "version": 2,
            "unit": "mm",
            "model": {"title": "Material test"},
            "camera": {"sourcePerspective": False},
            "lines": [
                {"start": [0, 0, 0], "end": [100, 0, 0], "layer": "Outline"},
                {"start": [100, 0, 0], "end": [100, 100, 0], "layer": "Outline"},
            ],
            "curves": [],
            "blocks": [],
            "blockReferences": [],
            "fills": [],
        }

    def test_unpainted_foreground_masks_painted_background(self) -> None:
        payload = self.base_payload()
        payload["fills"] = [
                {
                    "materialName": "Red",
                    "color": [220, 40, 40],
                    "alpha": 1.0,
                    "paint": True,
                    "layer": "MATERIAL_Red",
                    "depth": 0,
                    "loops": [
                        {"outer": True, "points": [[0, 0, 0], [100, 0, 0], [100, 100, 0], [0, 100, 0]]}
                    ],
                },
                {
                    "materialName": None,
                    "color": None,
                    "alpha": 1.0,
                    "paint": False,
                    "layer": "SUCAD-OCCLUDER",
                    "depth": 10,
                    "loops": [
                        {"outer": True, "points": [[25, 25, 10], [75, 25, 10], [75, 75, 10], [25, 75, 10]]}
                    ],
                },
            ]
        with tempfile.TemporaryDirectory() as temporary:
            json_path = Path(temporary) / "input.json"
            dxf_path = Path(temporary) / "output.dxf"
            json_path.write_text(json.dumps(payload), encoding="utf-8")
            result = build(json_path, dxf_path, dimensions=False)
            doc = ezdxf.readfile(dxf_path)
            hatches = list(doc.modelspace().query("HATCH"))

        self.assertEqual(result["auditErrors"], 0)
        self.assertEqual(result["materialHatches"], 1)
        self.assertEqual(result["materialCount"], 1)
        self.assertEqual(len(hatches), 1)
        self.assertEqual(hatches[0].rgb, (220, 40, 40))
        self.assertEqual(len(hatches[0].paths), 2)

    def test_sloped_face_is_clipped_at_depth_crossing(self) -> None:
        payload = self.base_payload()
        payload["fills"] = [
            {
                "materialName": "Red slope",
                "color": [220, 40, 40],
                "alpha": 1.0,
                "paint": True,
                "layer": "MATERIAL_Red",
                "depth": 5,
                "loops": [
                    {"outer": True, "points": [[0, 0, 0], [10, 0, 10], [10, 10, 10], [0, 10, 0]]}
                ],
            },
            {
                "materialName": None,
                "color": None,
                "alpha": 1.0,
                "paint": False,
                "layer": "SUCAD-OCCLUDER",
                "depth": 5,
                "loops": [
                    {"outer": True, "points": [[0, 0, 5], [10, 0, 5], [10, 10, 5], [0, 10, 5]]}
                ],
            },
        ]

        visible, _occluders = visible_material_geometry(expanded_fills(payload))

        self.assertEqual(len(visible), 1)
        self.assertAlmostEqual(visible[0].geometry.area, 55.0, places=4)
        self.assertAlmostEqual(visible[0].geometry.bounds[0], 4.5, places=4)

    def test_transparent_material_is_written_after_opaque_background(self) -> None:
        payload = self.base_payload()
        square = [{"outer": True, "points": [[0, 0, 0], [100, 0, 0], [100, 100, 0], [0, 100, 0]]}]
        glass_square = [
            {"outer": True, "points": [[0, 0, 10], [100, 0, 10], [100, 100, 10], [0, 100, 10]]}
        ]
        payload["fills"] = [
            {
                "materialName": "Floor",
                "color": [180, 160, 120],
                "alpha": 1.0,
                "paint": True,
                "layer": "MATERIAL_Floor",
                "depth": 0,
                "loops": square,
            },
            {
                "materialName": "Glass",
                "color": [120, 190, 220],
                "alpha": 0.45,
                "paint": True,
                "layer": "MATERIAL_Glass",
                "depth": 10,
                "loops": glass_square,
            },
        ]
        with tempfile.TemporaryDirectory() as temporary:
            json_path = Path(temporary) / "input.json"
            dxf_path = Path(temporary) / "output.dxf"
            json_path.write_text(json.dumps(payload), encoding="utf-8")
            build(json_path, dxf_path, dimensions=False)
            hatches = list(ezdxf.readfile(dxf_path).modelspace().query("HATCH"))

        self.assertEqual([hatch.rgb for hatch in hatches], [(180, 160, 120), (120, 190, 220)])
        self.assertAlmostEqual(hatches[-1].transparency, 0.55, places=2)

    def test_plant_material_uses_plant_block_layer(self) -> None:
        payload = self.base_payload()
        payload["blocks"] = [
            {
                "name": "SU_TREE",
                "sourceName": "Tree 01",
                "lines": [],
                "curves": [],
                "fills": [
                    {
                        "materialName": "Leaf",
                        "color": [60, 150, 80],
                        "alpha": 1.0,
                        "paint": True,
                        "layer": "MATERIAL_Leaf",
                        "sourceLayer": "Plants",
                        "depth": 0,
                        "loops": [
                            {"outer": True, "points": [[0, 0, 0], [20, 0, 0], [20, 20, 0], [0, 20, 0]]}
                        ],
                    }
                ],
            }
        ]
        payload["blockReferences"] = [
            {"block": "SU_TREE", "insert": [10, 10], "depth": 0, "layer": "BLOCK_Tree", "sourceName": "Tree 01"}
        ]
        with tempfile.TemporaryDirectory() as temporary:
            json_path = Path(temporary) / "input.json"
            dxf_path = Path(temporary) / "output.dxf"
            json_path.write_text(json.dumps(payload), encoding="utf-8")
            build(json_path, dxf_path, dimensions=False)
            hatches = list(ezdxf.readfile(dxf_path).modelspace().query("HATCH"))

        self.assertEqual(len(hatches), 1)
        self.assertEqual(hatches[0].dxf.layer, PLANT_BLOCK_LAYER)

    def test_partially_occluded_closed_curve_never_becomes_circle(self) -> None:
        doc = ezdxf.new("R2018")
        modelspace = doc.modelspace()
        points = [
            [10.0, 0.0],
            [7.071, 7.071],
            [0.0, 10.0],
            [-7.071, 7.071],
            [-10.0, 0.0],
        ]
        add_curve(
            modelspace,
            {
                "points": points,
                "closed": True,
                "partiallyOccluded": True,
                "curveType": "ArcCurve",
            },
            "SU-ARC",
        )

        self.assertEqual(len(modelspace.query("CIRCLE")), 0)
        self.assertTrue(len(modelspace.query("ARC SPLINE LWPOLYLINE")) >= 1)

    def test_extreme_hatch_boundary_obeys_vertex_budget(self) -> None:
        point_count = MAX_HATCH_VERTICES_PER_GROUP + 10_001
        points = []
        for index in range(point_count):
            angle = (2.0 * math.pi * index) / point_count
            radius = 100_000.0 + (1.0 if index % 2 else 0.0)
            points.append((math.cos(angle) * radius, math.sin(angle) * radius))
        geometry = enforce_geometry_vertex_budget(Polygon(points), 0.0)

        self.assertLessEqual(geometry_vertex_count(geometry, 0.0), MAX_HATCH_VERTICES_PER_GROUP)


if __name__ == "__main__":
    unittest.main()
