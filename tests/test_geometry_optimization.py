from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_dxf import (  # noqa: E402
    Segment,
    merge_collinear,
    segment_length,
    simplify_dense_furniture_segments,
)


class GeometryOptimizationTests(unittest.TestCase):
    def test_dense_furniture_keeps_boundaries_and_removes_micro_mesh(self) -> None:
        boundaries = [
            Segment((0.0, 0.0), (1000.0, 0.0), "SU-FURNITURE", "boundary"),
            Segment((1000.0, 0.0), (1000.0, 1000.0), "SU-FURNITURE", "boundary"),
            Segment((1000.0, 1000.0), (0.0, 1000.0), "SU-FURNITURE", "silhouette"),
            Segment((0.0, 1000.0), (0.0, 0.0), "SU-FURNITURE", "boundary"),
        ]
        micro_mesh = [
            Segment(
                ((index % 100) * 10.0, (index // 100) * 10.0),
                ((index % 100) * 10.0 + 0.5, (index // 100) * 10.0 + 0.5),
                "SU-FURNITURE",
                "hard",
            )
            for index in range(2000)
        ]
        structural = [
            Segment((0.0, float(index * 5)), (1000.0, float(index * 5)), "SU-FURNITURE", "hard")
            for index in range(100)
        ]

        output, changed = simplify_dense_furniture_segments(
            boundaries + micro_mesh + structural,
            800,
        )

        self.assertTrue(changed)
        self.assertTrue(set(boundaries).issubset(output))
        self.assertFalse(any(segment_length(segment) < 2.0 for segment in output))
        self.assertLessEqual(len(output), 1600)

    def test_merge_does_not_merge_boundary_with_mesh_detail(self) -> None:
        merged = merge_collinear(
            [
                Segment((0.0, 0.0), (10.0, 0.0), "SU-A", "boundary"),
                Segment((10.0, 0.0), (20.0, 0.0), "SU-A", "hard"),
            ]
        )
        self.assertEqual(len(merged), 2)
        self.assertEqual({segment.role for segment in merged}, {"boundary", "hard"})


if __name__ == "__main__":
    unittest.main()
