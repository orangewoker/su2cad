from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_dxf import (  # noqa: E402
    Segment,
    merge_collinear,
    optimize_block_segments,
    segment_length,
    simplify_dense_furniture_segments,
)


class GeometryOptimizationTests(unittest.TestCase):
    def test_balanced_mode_has_bounded_reusable_block_extraction(self) -> None:
        source = (ROOT / "scripts" / "export_current_view.rb").read_text(encoding="utf-8")
        self.assertIn("BALANCED_BLOCK_ENTITY_BUDGET = 40_000", source)
        self.assertIn("BALANCED_ENTITY_SAMPLES_PER_COLLECTION = 24_000", source)
        self.assertIn("bounded_mode = bounded_block_quality?(context[:quality])", source)
        self.assertIn("cacheable = bounded_mode", source)
        self.assertIn("visibility_state = instance_visibility_state", source)
        self.assertIn("visibility_state == :occluded", source)
        self.assertIn("visibility_state == :visible", source)
        self.assertIn("fine: preserved_fidelity", source)
        self.assertIn("fine ? :fine : :coarse", source)

    def test_balanced_simple_blocks_are_complete_and_exactly_occluded(self) -> None:
        source = (ROOT / "scripts" / "export_current_view.rb").read_text(encoding="utf-8")
        self.assertIn("BALANCED_FULL_FIDELITY_ENTITY_THRESHOLD = 6_000", source)
        self.assertIn("full_fidelity = full_fidelity_block?", source)
        self.assertIn(
            "preserved_fidelity = full_fidelity || structural_priority || outline_priority",
            source,
        )
        self.assertIn("dense_sampling = bounded_mode && !preserved_fidelity", source)
        self.assertIn(
            "block_occlusion = context[:occlusion] && visibility_state == :partial",
            source,
        )
        self.assertIn("occlusion: block_occlusion", source)
        self.assertIn("light_entity_budget: dense_sampling ? { remaining: entity_budget } : nil", source)
        self.assertIn("elsif full_fidelity_block?(entity, context)", source)
        self.assertIn("context[:light_sampling] = false", source)
        self.assertIn("context[:fidelity] = 'full'", source)
        self.assertIn("'mixed'", source)
        self.assertIn("context[:profile][:depth_tile_pixels]", source)
        self.assertNotIn("occlusion: bounded_mode ? false : context[:occlusion]", source)

    def test_balanced_uses_distributed_sampling_priority_and_deadlines(self) -> None:
        source = (ROOT / "scripts" / "export_current_view.rb").read_text(encoding="utf-8")
        self.assertIn("entities[index]", source)
        self.assertIn("prioritize_root_entities", source)
        self.assertIn("outline_fidelity_block?", source)
        self.assertIn("recursive_limit", source)
        self.assertIn("dense_soft_deadline", source)
        self.assertIn("dense_hard_deadline", source)
        self.assertIn("skippedTimeBudget", source)

    def test_balanced_spatial_chunks_and_protected_simple_children(self) -> None:
        source = (ROOT / "scripts" / "export_current_view.rb").read_text(encoding="utf-8")
        self.assertIn("BALANCED_SPATIAL_CHUNK_GRID = 4", source)
        self.assertIn("dense = Hash.new", source)
        self.assertIn("dense_order << item[2]", source)
        self.assertIn("processedSpatialChunks", source)
        self.assertIn("BALANCED_PROTECTED_CHILD_BUDGET = 8_000", source)
        self.assertIn("compact_full_fidelity_child?", source)
        self.assertNotIn("context[:omitted_time_budget] += 1\n        return :emitted", source)

    def test_repeated_visual_structures_are_promoted_before_dense_sampling(self) -> None:
        source = (ROOT / "scripts" / "export_current_view.rb").read_text(encoding="utf-8")
        self.assertIn("BALANCED_STRUCTURAL_DIRECT_THRESHOLD = 12_000", source)
        self.assertIn("BALANCED_STRUCTURAL_WRAPPER_THRESHOLD = 90_000", source)
        self.assertIn("structural_fidelity_block?", source)
        self.assertIn("children_only = instance.definition.entities.all?", source)
        self.assertIn("protected_structural_budget", source)
        self.assertIn("'structural'", source)

    def test_nested_sketchup_tags_override_only_untagged_parent_inheritance(self) -> None:
        source = (ROOT / "scripts" / "export_current_view.rb").read_text(encoding="utf-8")
        self.assertIn("child_tag = effective_tag_name(entity, outer_tag)", source)
        self.assertIn("tag_name(entity) || outer_tag", source)
        self.assertIn("layers: export_layers(model)", source)
        self.assertIn("layer: layer || tag_name(instance) || 'Untagged'", source)

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

    def test_full_fidelity_block_ignores_dense_line_cap(self) -> None:
        segments = [
            Segment((float(index), 0.0), (float(index), 100.0), "SU-SIGN", "hard")
            for index in range(3000)
        ]
        output, changed = optimize_block_segments(
            segments,
            800,
            plant=False,
            optimization_class="full",
        )
        self.assertFalse(changed)
        self.assertEqual(output, segments)

    def test_structural_block_ignores_dense_line_cap(self) -> None:
        segments = [
            Segment((float(index), 0.0), (float(index), 100.0), "SU-RACK", "hard")
            for index in range(3000)
        ]
        output, changed = optimize_block_segments(
            segments,
            800,
            plant=False,
            optimization_class="structural",
        )
        self.assertFalse(changed)
        self.assertEqual(output, segments)

    def test_mixed_block_never_samples_simple_child_lines(self) -> None:
        protected = [
            Segment(
                (float(index), 0.0),
                (float(index), 100.0),
                "SU-SIGN-TEXT",
                "hard",
                "full",
            )
            for index in range(1200)
        ]
        dense = [
            Segment(
                (float(index), 200.0),
                (float(index) + 0.25, 200.25),
                "SU-MESH",
                "hard",
                "dense",
            )
            for index in range(4000)
        ]
        output, changed = optimize_block_segments(
            protected + dense,
            800,
            plant=False,
            optimization_class="mixed",
        )
        self.assertTrue(changed)
        self.assertTrue(set(protected).issubset(output))


if __name__ == "__main__":
    unittest.main()
