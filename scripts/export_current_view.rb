# frozen_string_literal: true

require 'json'
require 'fileutils'
require 'digest'

module SketchupCurrentViewCad
  MM_PER_INCH = 25.4
  VISIBILITY_TOLERANCE_INCH = 2.0 / MM_PER_INCH
  SECTION_TOLERANCE_INCH = 0.5 / MM_PER_INCH
  MIN_FRAGMENT_INCH = 2.0 / MM_PER_INCH
  MIN_FILL_AREA_MM2 = 4.0
  MATERIAL_PREFILTER_AREA_MM2 = 250_000.0
  MAX_TOLERATED_RAY_ERRORS = 20
  LIGHT_BLOCK_ENTITY_BUDGET = 20_000
  LIGHT_ENTITY_SAMPLES_PER_COLLECTION = 12_000
  BALANCED_BLOCK_ENTITY_BUDGET = 40_000
  BALANCED_ENTITY_SAMPLES_PER_COLLECTION = 24_000
  LIGHT_FULL_FIDELITY_ENTITY_THRESHOLD = 1_000
  BALANCED_FULL_FIDELITY_ENTITY_THRESHOLD = 6_000
  BALANCED_STRUCTURAL_DIRECT_THRESHOLD = 12_000
  BALANCED_STRUCTURAL_WRAPPER_THRESHOLD = 90_000
  BALANCED_STRUCTURAL_WRAPPER_CHILDREN = 8
  LIGHT_COMPACT_CHILD_ENTITY_THRESHOLD = 400
  BALANCED_COMPACT_CHILD_ENTITY_THRESHOLD = 1_500
  LIGHT_PROTECTED_CHILD_BUDGET = 2_000
  BALANCED_PROTECTED_CHILD_BUDGET = 8_000
  LIGHT_SPATIAL_CHUNK_GRID = 3
  BALANCED_SPATIAL_CHUNK_GRID = 4
  LIGHT_UNIQUE_COMPONENT_THRESHOLD = 100_000
  EXPORT_SESSIONS = {}
  QUALITY_PROFILES = {
    'light' => {
      sample_pixels: 4.0,
      max_visibility_intervals: 8,
      max_clip_depth: 3,
      depth_tile_pixels: 4.0,
      min_object_pixels: 0.75,
      material_sample_pixels2: 16.0
    },
    'balanced' => {
      sample_pixels: 2.0,
      max_visibility_intervals: 24,
      max_clip_depth: 5,
      depth_tile_pixels: 2.0,
      min_object_pixels: 0.35,
      material_sample_pixels2: 4.0
    },
    'precise' => {
      sample_pixels: 1.0,
      max_visibility_intervals: 96,
      max_clip_depth: 7,
      depth_tile_pixels: 1.0,
      min_object_pixels: 0.0,
      material_sample_pixels2: 1.0
    }
  }.freeze

  class << self
    def export(output_path, occlusion: true, strict_section_occlusion: true, materials: true, quality: 'balanced',
               cooperative: nil)
      model = Sketchup.active_model
      raise 'No active SketchUp model' unless model

      camera = model.active_view.camera
      basis = camera_basis(camera)
      viewport = viewport_bounds(model.active_view, camera)
      profile = quality_profile(quality)
      viewport_width_mm = viewport[:max_x] - viewport[:min_x]
      mm_per_pixel = viewport_width_mm / [model.active_view.vpwidth.to_f, 1.0].max
      section = active_section(model, camera)
      effective_occlusion = section ? strict_section_occlusion : occlusion
      diagonal = [model.bounds.diagonal.to_f, 1000.0].max
      export_started = monotonic_time
      # Leave enough of the three-minute Balanced target for material
      # composition, DXF serialization and audit outside SketchUp.
      dense_soft_seconds = quality.to_s == 'balanced' ? 72.0 : 55.0
      dense_hard_seconds = quality.to_s == 'balanced' ? 105.0 : 80.0
      dense_stop_seconds = quality.to_s == 'balanced' ? 135.0 : 100.0
      context = {
        model: model,
        basis: basis,
        viewport: viewport,
        section: section,
        ray_distance: diagonal * 3.0,
        occlusion: effective_occlusion,
        visibility_cache: {},
        depth_cache: {},
        instance_depth_cache: {},
        processed_curves: {},
        lines: [],
        curves: [],
        fills: [],
        blocks: [],
        block_references: [],
        block_hashes: {},
        light_block_cache: {},
        light_planar_block_cache: {},
        light_entity_collections: {},
        definition_complexity_cache: {},
        block_mode: true,
        root_priority: true,
        emit_materials: materials,
        material_prefilter: true,
        profile: profile,
        quality: quality.to_s,
        mm_per_pixel: [mm_per_pixel, 1.0e-6].max,
        cooperative: cooperative,
        export_started: export_started,
        dense_soft_deadline: quality.to_s == 'precise' ? nil : export_started + dense_soft_seconds,
        dense_hard_deadline: quality.to_s == 'precise' ? nil : export_started + dense_hard_seconds,
        dense_stop_deadline: quality.to_s == 'precise' ? nil : export_started + dense_stop_seconds,
        fidelity: 'full',
        light_sampling: false,
        time_compact: false,
        light_entity_budget: nil,
        skipped_hidden: 0,
        skipped_offscreen: 0,
        skipped_sampled: 0,
        reused_block_references: 0,
        full_fidelity_blocks: 0,
        structural_fidelity_blocks: 0,
        full_fidelity_children: 0,
        optimized_dense_blocks: 0,
        occluded_blocks: 0,
        skipped_time_budget: 0,
        omitted_time_budget: 0,
        skipped_occluded: 0,
        skipped_material_faces: 0,
        material_faces: 0,
        ray_errors: 0,
        section_lines: 0,
        silhouette_edges: 0
      }

      walk_entities(model.entities, Geom::Transformation.new, nil, nil, context)
      if context[:ray_errors] > MAX_TOLERATED_RAY_ERRORS
        raise "SketchUp visibility ray testing failed #{context[:ray_errors]} times"
      end

      payload = {
        format: 'sketchup-current-view-linework',
        version: 2,
        unit: 'mm',
        model: {
          title: model.title.to_s,
          path: model.path.to_s,
          modified: model.modified?
        },
        camera: {
          sourcePerspective: camera.perspective?,
          direction: vector_array(camera.direction),
          up: vector_array(camera.up),
          eyeMm: point_array(camera.eye),
          targetMm: point_array(camera.target),
          projection: 'orthographic',
          viewportMm: [
            (viewport[:max_x] - viewport[:min_x]).round(4),
            (viewport[:max_y] - viewport[:min_y]).round(4)
          ]
        },
        section: section && {
          name: section[:name],
          planeMm: section[:plane].map.with_index { |value, index| index == 3 ? (value * MM_PER_INCH).round(4) : value.round(8) }
        },
        layers: export_layers(model),
        lines: context[:lines],
        curves: context[:curves],
        fills: context[:fills],
        blocks: context[:blocks],
        blockReferences: context[:block_references],
        stats: {
          lineFragments: context[:lines].length,
          curves: context[:curves].length,
          fills: context[:fills].length,
          materialFaces: context[:material_faces],
          skippedMaterialFaces: context[:skipped_material_faces],
          skippedHidden: context[:skipped_hidden],
          skippedOffscreen: context[:skipped_offscreen],
          skippedSampled: context[:skipped_sampled],
          skippedOccluded: context[:skipped_occluded],
          sectionLines: context[:section_lines],
          silhouetteEdges: context[:silhouette_edges],
          blocks: context[:blocks].length,
          blockReferences: context[:block_references].length,
          reusedBlockReferences: context[:reused_block_references],
          fullFidelityBlocks: context[:full_fidelity_blocks],
          structuralFidelityBlocks: context[:structural_fidelity_blocks],
          fullFidelityChildren: context[:full_fidelity_children],
          optimizedDenseBlocks: context[:optimized_dense_blocks],
          occludedBlocks: context[:occluded_blocks],
          skippedTimeBudget: context[:skipped_time_budget],
          omittedTimeBudget: context[:omitted_time_budget],
          spatialChunks: context[:spatial_chunks].to_i,
          processedSpatialChunks: (context[:spatial_chunks_touched] || {}).length,
          extractionSeconds: (monotonic_time - export_started).round(2),
          occlusion: effective_occlusion,
          requestedOcclusion: occlusion,
          strictSectionOcclusion: strict_section_occlusion,
          materials: materials,
          quality: quality.to_s,
          rayErrors: context[:ray_errors]
        }
      }

      FileUtils.mkdir_p(File.dirname(output_path))
      temporary = "#{output_path}.tmp"
      File.write(temporary, JSON.generate(payload))
      File.rename(temporary, output_path)
      payload[:stats].merge(path: output_path, sourcePerspective: camera.perspective?)
    ensure
      File.delete(temporary) if defined?(temporary) && temporary && File.exist?(temporary)
    end

    def start_export(output_path, occlusion: true, strict_section_occlusion: true, materials: true, quality: 'balanced')
      cleanup_export_sessions
      session_id = "#{Process.pid}-#{(Time.now.to_f * 1000).to_i}-#{rand(1_000_000)}"
      cooperative = {
        processed_entities: 0,
        deadline: nil
      }
      fiber = Fiber.new do
        export(
          output_path,
          occlusion: occlusion,
          strict_section_occlusion: strict_section_occlusion,
          materials: materials,
          quality: quality,
          cooperative: cooperative
        )
      end
      EXPORT_SESSIONS[session_id] = {
        fiber: fiber,
        cooperative: cooperative,
        created_at: Time.now
      }
      {
        ok: true,
        sessionId: session_id,
        quality: quality.to_s
      }
    end

    def step_export(session_id, budget_ms: 250)
      session = EXPORT_SESSIONS[session_id.to_s]
      raise "Unknown or expired export session: #{session_id}" unless session

      budget = [[budget_ms.to_f, 50.0].max, 1000.0].min
      control = session[:cooperative]
      control[:deadline] = monotonic_time + (budget / 1000.0)
      yielded_or_result = session[:fiber].resume
      if session[:fiber].alive?
        {
          ok: true,
          done: false,
          processedEntities: control[:processed_entities],
          processedSpatialChunks: control[:processed_spatial_chunks].to_i,
          spatialChunks: control[:spatial_chunks].to_i,
          checkpoint: yielded_or_result
        }
      else
        EXPORT_SESSIONS.delete(session_id.to_s)
        {
          ok: true,
          done: true,
          processedEntities: control[:processed_entities],
          processedSpatialChunks: control[:processed_spatial_chunks].to_i,
          spatialChunks: control[:spatial_chunks].to_i,
          result: yielded_or_result
        }
      end
    rescue StandardError
      EXPORT_SESSIONS.delete(session_id.to_s)
      raise
    end

    def cancel_export(session_id)
      removed = EXPORT_SESSIONS.delete(session_id.to_s)
      {
        ok: true,
        cancelled: !removed.nil?,
        sessionId: session_id.to_s
      }
    end

    private

    def cleanup_export_sessions
      cutoff = Time.now - 3600
      EXPORT_SESSIONS.delete_if { |_session_id, session| session[:created_at] < cutoff }
    end

    def monotonic_time
      Process.clock_gettime(Process::CLOCK_MONOTONIC)
    end

    def quality_profile(quality)
      QUALITY_PROFILES.fetch(quality.to_s, QUALITY_PROFILES['balanced'])
    end

    # Return a fast model census without expanding repeated component geometry.
    # uniqueEntities is exact for source collections; expandedEstimate is a
    # deliberately labelled estimate because nested definitions multiply when
    # their parents are instanced.
    def workload_summary(quality: 'balanced')
      model = Sketchup.active_model
      raise 'No active SketchUp model' unless model

      definitions = model.definitions.to_a.select { |definition| definition.respond_to?(:entities) }
      root_entities = model.entities.length.to_i
      definition_entities = definitions.sum { |definition| definition.entities.length.to_i }
      definition_instances = definitions.sum { |definition| definition.instances.length.to_i }
      expanded_estimate = root_entities + definitions.sum do |definition|
        count = definition.instances.length.to_i
        count.positive? ? definition.entities.length.to_i * count : 0
      end

      camera = model.active_view.camera
      basis = camera_basis(camera)
      viewport = viewport_bounds(model.active_view, camera)
      profile = quality_profile(quality)
      viewport_width_mm = viewport[:max_x] - viewport[:min_x]
      mm_per_pixel = viewport_width_mm / [model.active_view.vpwidth.to_f, 1.0].max
      context = {
        basis: basis,
        viewport: viewport,
        section: active_section(model, camera),
        profile: profile,
        mm_per_pixel: [mm_per_pixel, 1.0e-6].max,
        quality: quality.to_s,
        definition_complexity_cache: {}
      }
      planned_entities = 0
      visible_root_instances = 0
      model.entities.each do |entity|
        if entity.is_a?(Sketchup::Group) || entity.is_a?(Sketchup::ComponentInstance)
          world_transform = entity.transformation
          next unless entity_visible?(entity) && instance_intersects_view?(entity, world_transform, context)

          visible_root_instances += 1
          if quality.to_s == 'precise'
            planned_entities += entity.definition.entities.length.to_i
          elsif full_fidelity_block?(entity, context)
            threshold = quality.to_s == 'balanced' ?
                          BALANCED_FULL_FIDELITY_ENTITY_THRESHOLD :
                          LIGHT_FULL_FIDELITY_ENTITY_THRESHOLD
            planned_entities += definition_complexity(entity.definition, context, threshold)
          elsif structural_fidelity_block?(entity, world_transform, context)
            planned_entities += definition_complexity(
              entity.definition,
              context,
              BALANCED_STRUCTURAL_WRAPPER_THRESHOLD
            )
          elsif outline_fidelity_block?(entity, world_transform, context)
            outline_limit = quality.to_s == 'balanced' ? 150_000 : 60_000
            planned_entities += definition_complexity(entity.definition, context, outline_limit)
          else
            cap = block_entity_budget(quality.to_s)
            planned_entities += definition_complexity(entity.definition, context, cap)
          end
        elsif entity_visible?(entity)
          planned_entities += 1
        end
      end

      {
        uniqueEntities: root_entities + definition_entities,
        rootEntities: root_entities,
        definitionEntities: definition_entities,
        definitionCount: definitions.length,
        definitionInstances: definition_instances,
        expandedEstimate: expanded_estimate,
        visibleRootInstances: visible_root_instances,
        plannedEntities: [planned_entities, 1].max
      }
    end
    public :workload_summary

    def cooperative_checkpoint(context)
      control = context[:cooperative]
      return unless control

      control[:processed_entities] += 1
      deadline = control[:deadline]
      return unless deadline && monotonic_time >= deadline

      Fiber.yield(
        processedEntities: control[:processed_entities],
        processedSpatialChunks: control[:processed_spatial_chunks].to_i,
        spatialChunks: control[:spatial_chunks].to_i
      )
    end

    def walk_entities(entities, transform, outer_tag, outer_material, context)
      source_count = entities.respond_to?(:length) ? entities.length.to_i : 0
      selected_entities = context[:light_sampling] ? light_sampled_entities(entities, transform, context) : entities
      if context[:root_priority]
        # Consume this flag once. Recursive fallback traversal shares the root
        # context and must not repeatedly sort large nested collections.
        context[:root_priority] = false
        selected_entities = prioritize_root_entities(selected_entities, transform, context)
      end
      selected_count = selected_entities.respond_to?(:length) ? selected_entities.length.to_i : source_count
      light_plan = context[:light_entity_budget] ? light_child_budget_plan(selected_entities, transform, context) : nil
      selected_entities.each_with_index do |entity, index|
        budget = context[:light_entity_budget]
        is_instance = entity.is_a?(Sketchup::Group) || entity.is_a?(Sketchup::ComponentInstance)
        if budget && !is_instance
          if budget[:remaining] <= 0
            context[:light_budget_exhausted] = true
            context[:skipped_sampled] += [selected_count - index, 0].max
            break
          end
          budget[:remaining] -= 1
        end
        cooperative_checkpoint(context)
        if context[:root_spatial_map]
          chunk = context[:root_spatial_map][entity.object_id]
          if chunk
            touched = context[:spatial_chunks_touched] ||= {}
            unless touched[chunk]
              touched[chunk] = true
              if context[:cooperative]
                context[:cooperative][:processed_spatial_chunks] = touched.length
              end
            end
          end
        end
        unless entity_visible?(entity)
          context[:skipped_hidden] += 1
          next
        end

        case entity
        when Sketchup::Group, Sketchup::ComponentInstance
          child_tag = effective_tag_name(entity, outer_tag)
          child_material = entity.respond_to?(:material) && entity.material ? entity.material : outer_material
          world_transform = transform * entity.transformation
          unless instance_intersects_view?(entity, world_transform, context)
            context[:skipped_offscreen] += 1
            next
          end
          block_result = if context[:block_mode] && block_candidate?(entity, world_transform, context)
                           emit_instance_block(entity, world_transform, child_tag, child_material, context)
                         else
                           :fallback
          end
          if block_result == :fallback
            if budget && light_plan
              walk_light_child_entities(
                entity,
                world_transform,
                child_tag,
                child_material,
                context,
                light_plan
              )
            else
              walk_entities(entity.definition.entities, world_transform, child_tag, child_material, context)
            end
          end
        when Sketchup::Edge
          emit_edge(entity, entities, transform, outer_tag, context)
        when Sketchup::Face
          emit_face_material(entity, transform, outer_tag, outer_material, context) if context[:emit_materials]
          emit_section_intersections(entity, transform, context) if context[:section]
        end
      end
    end

    # A dense imported component often stores tiny wheels, screws, tufting or
    # decorative meshes before its actual body. A single shared depth-first
    # budget therefore used to stop before the large seat/cabinet/table body.
    # Allocate each child its own projected-area-weighted share and reserve part
    # of the collection budget for direct faces/edges.
    def light_child_budget_plan(entities, transform, context)
      children = entities.select do |entity|
        entity.is_a?(Sketchup::Group) || entity.is_a?(Sketchup::ComponentInstance)
      end
      return nil if children.empty?

      budget = context[:light_entity_budget]
      primitive_count = entities.length - children.length
      primitive_reserve = if primitive_count.positive?
                            [(budget[:remaining] * 0.2).round, primitive_count, 256].max
                          else
                            0
                          end
      primitive_reserve = [primitive_reserve, budget[:remaining] / 2].min
      weights = children.each_with_object({}) do |entity, output|
        output[entity.object_id] = light_child_weight(entity, transform, context)
      end
      {
        remaining_weight: weights.values.sum,
        weights: weights,
        remaining_children: children.length,
        primitive_reserve: primitive_reserve
      }
    end

    def walk_light_child_entities(entity, world_transform, child_tag, child_material, context, plan)
      shared_budget = context[:light_entity_budget]
      structural_mode = structural_fidelity_block?(entity, world_transform, context)
      fidelity_mode = if full_fidelity_block?(entity, context) &&
                         (!context[:time_compact] || compact_full_fidelity_child?(entity, context))
                        :full
                      elsif structural_mode &&
                            (!context[:time_compact] || compact_structural_child?(entity, context))
                        :structural
                      elsif !context[:time_compact] && outline_fidelity_block?(entity, world_transform, context)
                        :outline
                      end
      if fidelity_mode
        context[:full_fidelity_children] += 1
        context[:structural_fidelity_blocks] += 1 if fidelity_mode == :structural
        previous_sampling = context[:light_sampling]
        previous_cleanup = context[:light_mesh_cleanup]
        previous_cleanup_pixels = context[:mesh_cleanup_pixels]
        previous_fidelity = context[:fidelity]
        previous_occlusion = context[:occlusion]
        previous_profile = context[:profile]
        context[:light_entity_budget] = nil
        context[:light_sampling] = false
        context[:light_mesh_cleanup] = fidelity_mode == :outline || fidelity_mode == :structural
        context[:mesh_cleanup_pixels] = 0.2 if fidelity_mode == :structural
        context[:fidelity] = 'full'
        if fidelity_mode == :full
          context[:occlusion] = context[:requested_occlusion] unless context[:requested_occlusion].nil?
          context[:profile] = context[:full_fidelity_profile] if context[:full_fidelity_profile]
        end
        walk_entities(entity.definition.entities, world_transform, child_tag, child_material, context)
        return
      end

      weight = plan[:weights][entity.object_id] || 1.0
      available = [shared_budget[:remaining] - plan[:primitive_reserve], 0].max
      remaining_weight = [plan[:remaining_weight], weight].max
      proportional = (available * weight / remaining_weight).round
      minimum = [256, available].min
      quota = [[proportional, minimum].max, available].min

      plan[:remaining_weight] -= weight
      plan[:remaining_children] -= 1
      if quota <= 0
        context[:light_budget_exhausted] = true
        context[:skipped_sampled] += entity.definition.entities.length.to_i
        return
      end

      child_budget = { remaining: quota }
      context[:light_entity_budget] = child_budget
      walk_entities(entity.definition.entities, world_transform, child_tag, child_material, context)
      used = quota - child_budget[:remaining]
      shared_budget[:remaining] -= used
    ensure
      context[:light_sampling] = previous_sampling unless previous_sampling.nil?
      context[:light_mesh_cleanup] = previous_cleanup unless previous_cleanup.nil?
      context[:mesh_cleanup_pixels] = previous_cleanup_pixels unless previous_cleanup_pixels.nil?
      context[:fidelity] = previous_fidelity unless previous_fidelity.nil?
      context[:occlusion] = previous_occlusion unless previous_occlusion.nil?
      context[:profile] = previous_profile unless previous_profile.nil?
      context[:light_entity_budget] = shared_budget if shared_budget
    end

    # Even after the global deadline, a cheap nested group is more valuable
    # than another uniform sample of upholstery/vegetation mesh. Reserve a
    # small, hard-bounded budget for these structural children so rails, signs,
    # shelves and ordinary line groups remain complete without making a dense
    # furniture definition unbounded again.
    def compact_full_fidelity_child?(entity, context)
      remaining = context[:protected_child_budget].to_i
      return false if remaining <= 0

      threshold = context[:quality] == 'balanced' ?
                    BALANCED_COMPACT_CHILD_ENTITY_THRESHOLD :
                    LIGHT_COMPACT_CHILD_ENTITY_THRESHOLD
      complexity = definition_complexity(entity.definition, context, threshold)
      return false if complexity > threshold || complexity > remaining

      context[:protected_child_budget] = remaining - complexity
      true
    rescue StandardError
      false
    end

    def compact_structural_child?(entity, context)
      remaining = context[:protected_structural_budget].to_i
      return false if remaining <= 0

      complexity = definition_complexity(
        entity.definition,
        context,
        BALANCED_STRUCTURAL_DIRECT_THRESHOLD
      )
      return false if complexity > BALANCED_STRUCTURAL_DIRECT_THRESHOLD || complexity > remaining

      context[:protected_structural_budget] = remaining - complexity
      true
    rescue StandardError
      false
    end

    def light_child_weight(entity, transform, context)
      child_transform = transform * entity.transformation
      width, height = projected_definition_size(entity.definition, child_transform, context[:basis])
      area = [width.abs * height.abs, 1.0].max
      # Square root keeps large bodies ahead without starving smaller meaningful
      # children. Entity count contributes only mildly, preventing a tiny wheel
      # mesh from outranking a full furniture body.
      Math.sqrt(area) * Math.log2([entity.definition.entities.length.to_i, 2].max)
    rescue StandardError
      1.0
    end

    # SketchUp models with dense vegetation can contain the same definition
    # millions of times. Build the light-mode representative list once per
    # Entities collection, then reuse it for every rotated/transformed instance.
    # This avoids repeatedly walking the complete Ruby collection merely to skip
    # most of it.
    def light_sampled_entities(entities, transform, context)
      cache = context[:light_entity_collections] ||= {}
      sample_limit = context[:entity_sample_limit] || LIGHT_ENTITY_SAMPLES_PER_COLLECTION
      key = [entities.object_id, sample_limit]
      cached = cache[key]
      return cached if cached

      count = entities.respond_to?(:length) ? entities.length.to_i : 0
      if count <= sample_limit
        selected = entities.to_a
      else
        # Sampling the first N entities erased complete objects stored later in
        # imported definitions. SketchUp Entities supports indexed access, so
        # take deterministic evenly distributed samples across the collection
        # without paying for a full Ruby traversal.
        step = (count - 1).to_f / [sample_limit - 1, 1].max
        indexes = (0...sample_limit).map { |index| (index * step).round }.uniq
        selected = indexes.filter_map { |index| entities[index] }
        context[:skipped_sampled] += [count - selected.length, 0].max
      end
      # Process large child definitions before small decorative meshes. Preserve
      # source order for primitive geometry and for equal-size children.
      indexed = selected.each_with_index.to_a
      selected = indexed.sort_by do |entity, index|
        if entity.is_a?(Sketchup::Group) || entity.is_a?(Sketchup::ComponentInstance)
          [-light_child_weight(entity, transform, context), index]
        else
          [0.0, index]
        end
      end.map(&:first)
      cache[key] = selected
    end

    # Split the viewport into a small screen-space grid. Preserve root
    # primitives and complete simple/planar objects first, then interleave dense
    # objects from every cell. One difficult furniture/plant cluster can no
    # longer consume the whole deadline before another region is visited. The
    # cell results still append to one payload, so repeated definitions share
    # the same block cache and are stitched into one editable DXF without seams.
    def prioritize_root_entities(entities, transform, context)
      indexed = entities.to_a.each_with_index.to_a
      simple = []
      dense = Hash.new { |hash, key| hash[key] = [] }
      grid = context[:quality] == 'balanced' ? BALANCED_SPATIAL_CHUNK_GRID : LIGHT_SPATIAL_CHUNK_GRID
      viewport = context[:viewport]
      width = [viewport[:max_x] - viewport[:min_x], 1.0].max
      height = [viewport[:max_y] - viewport[:min_y], 1.0].max
      spatial_map = {}

      indexed.each do |entity, index|
        unless entity.is_a?(Sketchup::Group) || entity.is_a?(Sketchup::ComponentInstance)
          simple << [0, 0.0, index, entity]
          next
        end

        world_transform = transform * entity.transformation
        full = full_fidelity_block?(entity, context)
        structural = !full && structural_fidelity_block?(entity, world_transform, context)
        outline = !full && !structural && outline_fidelity_block?(entity, world_transform, context)
        projected = bounds_corners(entity.definition.bounds).map do |point|
          project(point.transform(world_transform), context[:basis])
        end
        xs = projected.map { |point| point[0] }
        ys = projected.map { |point| point[1] }
        footprint = (xs.max - xs.min).abs * (ys.max - ys.min).abs
        if full || structural || outline
          priority = full ? 1 : (structural ? 2 : 3)
          simple << [priority, -footprint, index, entity]
          next
        end

        center_x = (xs.min + xs.max) / 2.0
        center_y = (ys.min + ys.max) / 2.0
        column = (((center_x - viewport[:min_x]) / width) * grid).floor.clamp(0, grid - 1)
        row = (((center_y - viewport[:min_y]) / height) * grid).floor.clamp(0, grid - 1)
        chunk = row * grid + column
        dense[chunk] << [-footprint, index, entity]
        spatial_map[entity.object_id] = chunk
      rescue StandardError
        simple << [4, 0.0, index, entity]
      end

      simple.sort_by! { |priority, footprint, index, _entity| [priority, footprint, index] }
      dense.each_value { |items| items.sort_by! { |footprint, index, _entity| [footprint, index] } }
      dense_order = []
      chunk_ids = dense.keys.sort
      until chunk_ids.empty?
        chunk_ids = chunk_ids.select do |chunk|
          item = dense[chunk].shift
          dense_order << item[2] if item
          !dense[chunk].empty?
        end
      end
      context[:root_spatial_map] = spatial_map
      context[:spatial_chunks] = dense.keys.length
      context[:spatial_chunks_touched] = {}
      if context[:cooperative]
        context[:cooperative][:spatial_chunks] = dense.keys.length
        context[:cooperative][:processed_spatial_chunks] = 0
      end
      simple.map(&:last) + dense_order
    rescue StandardError
      entities
    end

    def block_candidate?(instance, world_transform, context)
      if context[:quality] == 'light'
        entity_count = instance.definition.entities.length.to_i
        instance_count = instance.definition.instances.length
        return true if instance_count > 1
        return entity_count >= LIGHT_UNIQUE_COMPONENT_THRESHOLD
      end
      return true if instance.is_a?(Sketchup::ComponentInstance) && !instance.is_a?(Sketchup::Group)
      return true if instance.definition.instances.length > 1

      width, height = projected_definition_size(instance.definition, world_transform, context[:basis])
      width > 20.0 && height > 20.0 && width < 6000.0 && height < 6000.0
    rescue StandardError
      false
    end

    def emit_instance_block(instance, world_transform, outer_tag, outer_material, context)
      bounded_mode = bounded_block_quality?(context[:quality])
      full_fidelity = full_fidelity_block?(instance, context)
      structural_priority = !full_fidelity && structural_fidelity_block?(instance, world_transform, context)
      outline_priority = !full_fidelity && !structural_priority &&
                         outline_fidelity_block?(instance, world_transform, context)
      preserved_fidelity = full_fidelity || structural_priority || outline_priority
      # A coarse instance-depth tile is safe for dense furniture, but it can
      # erase an adjacent thin shelf or rail when both sample points land in
      # the same large tile. Simple blocks are cheap enough to classify with
      # the normal edge-resolution cache.
      visibility_state = instance_visibility_state(
        instance,
        world_transform,
        context,
        fine: preserved_fidelity
      )
      if visibility_state == :occluded
        context[:skipped_occluded] += 1
        context[:occluded_blocks] += 1
        return :occluded
      end
      context[:full_fidelity_blocks] += 1 if preserved_fidelity
      context[:structural_fidelity_blocks] += 1 if structural_priority
      context[:optimized_dense_blocks] += 1 unless preserved_fidelity
      cacheable = bounded_mode &&
                  instance_fully_on_kept_section_side?(instance, world_transform, context) &&
                  instance_fully_inside_view?(instance, world_transform, context) &&
                  visibility_state == :visible
      cache_key = if cacheable
                    light_block_cache_key(instance, world_transform, outer_tag, outer_material)
                  end
      cached = cache_key && context[:light_block_cache][cache_key]
      if cached
        origin = project(Geom::Point3d.new(0, 0, 0).transform(world_transform), context[:basis])
        insert = [
          (cached[:insert][0] + origin[0] - cached[:origin][0]).round(4),
          (cached[:insert][1] + origin[1] - cached[:origin][1]).round(4)
        ]
        depth = (cached[:depth] + origin[2] - cached[:origin][2]).round(4)
        append_block_reference(context, instance, cached[:block], insert, depth, layer: outer_tag)
        context[:reused_block_references] += 1
        return :emitted
      end
      planar_key = cacheable ? light_planar_block_cache_key(instance, outer_tag, outer_material) : nil
      planar_cached = planar_key && context[:light_planar_block_cache][planar_key]
      if planar_cached
        reference = planar_block_reference(planar_cached, world_transform, context[:basis])
        if reference
          append_block_reference(
            context,
            instance,
            planar_cached[:block],
            reference[:insert],
            reference[:depth],
            layer: outer_tag,
            rotation: reference[:rotation],
            xscale: reference[:xscale],
            yscale: reference[:yscale]
          )
          context[:reused_block_references] += 1
          return :emitted
        end
      end
      dense_sampling = bounded_mode && !preserved_fidelity
      entity_budget = block_entity_budget(context[:quality])
      sample_limit = entity_sample_limit(context[:quality])
      hard_budget_mode = dense_sampling &&
                         (dense_hard_deadline_exceeded?(context) || dense_stop_deadline_exceeded?(context))
      if hard_budget_mode
        # Never erase a whole late furniture/sign component. Emit a compact
        # representative outline after the deadline so every visible root
        # object still has editable CAD geometry.
        context[:skipped_time_budget] += 1
        entity_budget = [entity_budget, context[:quality] == 'balanced' ? 220 : 150].min
        sample_limit = [sample_limit, context[:quality] == 'balanced' ? 180 : 120].min
      elsif dense_sampling && dense_soft_deadline_exceeded?(context)
        entity_budget = [entity_budget, context[:quality] == 'balanced' ? 8_000 : 4_000].min
        sample_limit = [sample_limit, context[:quality] == 'balanced' ? 6_000 : 3_000].min
      end
      mesh_cleanup_pixels = if structural_priority
                              0.2
                            elsif hard_budget_mode
                              context[:quality] == 'balanced' ? 0.8 : 1.2
                            else
                              context[:quality] == 'light' ? 0.75 : 0.4
                            end
      # Once the block-level probe says a simple component is visible, keep
      # that component complete. Re-running every edge through a shared depth
      # tile can erase a plain rail beside a perforated or high-poly screen.
      # Fully covered blocks were already removed above; only genuinely partial
      # blocks need edge-level clipping.
      block_occlusion = context[:occlusion] && visibility_state == :partial
      block_profile = if block_occlusion &&
                         (!preserved_fidelity || dense_soft_deadline_exceeded?(context))
                        dense_occlusion_profile(context[:profile], context[:quality])
                      else
                        context[:profile]
                      end

      block_context = {
        model: context[:model],
        basis: context[:basis],
        viewport: context[:viewport],
        section: context[:section],
        ray_distance: context[:ray_distance],
        # Simple groups are always emitted completely and use normal per-edge
        # visibility clipping. Dense groups keep their bounded geometry budget;
        # only partially covered dense instances use a coarse visibility profile.
        occlusion: block_occlusion,
        requested_occlusion: context[:occlusion],
        visibility_cache: context[:visibility_cache],
        depth_cache: context[:depth_cache],
        instance_depth_cache: context[:instance_depth_cache],
        processed_curves: {},
        lines: [],
        curves: [],
        fills: [],
        blocks: [],
        block_references: [],
        block_hashes: {},
        light_block_cache: context[:light_block_cache],
        light_planar_block_cache: context[:light_planar_block_cache],
        light_entity_collections: context[:light_entity_collections],
        definition_complexity_cache: context[:definition_complexity_cache],
        block_mode: false,
        root_priority: false,
        emit_materials: context[:emit_materials],
        material_prefilter: true,
        profile: block_profile,
        full_fidelity_profile: full_fidelity_occlusion_profile(context[:profile], context[:quality]),
        quality: context[:quality],
        mm_per_pixel: context[:mm_per_pixel],
        cooperative: context[:cooperative],
        export_started: context[:export_started],
        dense_soft_deadline: context[:dense_soft_deadline],
        dense_hard_deadline: context[:dense_hard_deadline],
        dense_stop_deadline: context[:dense_stop_deadline],
        fidelity: preserved_fidelity ? 'full' : 'dense',
        light_sampling: dense_sampling,
        time_compact: hard_budget_mode,
        light_mesh_cleanup: dense_sampling || outline_priority || structural_priority,
        mesh_cleanup_pixels: mesh_cleanup_pixels,
        entity_sample_limit: sample_limit,
        light_entity_budget: dense_sampling ? { remaining: entity_budget } : nil,
        protected_child_budget: context[:quality] == 'balanced' ?
                                  BALANCED_PROTECTED_CHILD_BUDGET :
                                  LIGHT_PROTECTED_CHILD_BUDGET,
        protected_structural_budget: context[:quality] == 'balanced' ? 40_000 : 0,
        light_budget_exhausted: false,
        skipped_hidden: 0,
        skipped_offscreen: 0,
        skipped_sampled: 0,
        reused_block_references: 0,
        full_fidelity_blocks: 0,
        structural_fidelity_blocks: 0,
        full_fidelity_children: 0,
        optimized_dense_blocks: 0,
        occluded_blocks: 0,
        skipped_time_budget: 0,
        omitted_time_budget: 0,
        skipped_occluded: 0,
        skipped_material_faces: 0,
        material_faces: 0,
        ray_errors: 0,
        section_lines: 0,
        silhouette_edges: 0
      }
      walk_entities(instance.definition.entities, world_transform, outer_tag, outer_material, block_context)
      if block_context[:lines].empty? && block_context[:curves].empty? && block_context[:fills].empty?
        context[:skipped_occluded] += block_context[:skipped_occluded]
        context[:skipped_offscreen] += block_context[:skipped_offscreen]
        context[:skipped_sampled] += block_context[:skipped_sampled]
        context[:skipped_material_faces] += block_context[:skipped_material_faces]
        context[:ray_errors] += block_context[:ray_errors]
        return :occluded
      end

      normalized = normalize_block_geometry(block_context[:lines], block_context[:curves], block_context[:fills])
      return :fallback unless normalized

      contains_full_fidelity = block_context[:lines].any? { |line| line[:fidelity] == 'full' }
      optimization_class = if structural_priority
                             'structural'
                           elsif preserved_fidelity
                             'full'
                           elsif contains_full_fidelity
                             'mixed'
                           else
                             'dense'
                           end
      geometry_hash = block_geometry_hash(normalized[:lines], normalized[:curves], normalized[:fills])
      geometry_hash = Digest::SHA1.hexdigest("#{geometry_hash}:#{optimization_class}")
      source_name = instance.name.to_s.empty? ? instance.definition.name.to_s : instance.name.to_s
      block_name = context[:block_hashes][geometry_hash]
      unless block_name
        clean_name = source_name.gsub(/[^0-9A-Za-z_\-]/, '_').gsub(/_+/, '_').sub(/^_+|_+$/, '')
        clean_name = 'OBJECT' if clean_name.empty?
        block_name = "SU_#{clean_name[0, 32]}_#{geometry_hash[0, 8].upcase}"
        context[:blocks] << {
          name: block_name,
          sourceName: source_name,
          definitionId: instance.definition.persistent_id,
          geometryHash: geometry_hash,
          optimizationClass: optimization_class,
           lines: normalized[:lines],
           curves: normalized[:curves],
           fills: normalized[:fills]
        }
        context[:block_hashes][geometry_hash] = block_name
      end

      append_block_reference(
        context, instance, block_name, normalized[:insert], normalized[:depth], layer: outer_tag
      )
      if cache_key
        origin = project(Geom::Point3d.new(0, 0, 0).transform(world_transform), context[:basis])
        context[:light_block_cache][cache_key] = {
          block: block_name,
          insert: normalized[:insert],
          depth: normalized[:depth],
          origin: origin
        }
        context[:light_planar_block_cache][planar_key] ||= {
          block: block_name,
          insert: normalized[:insert],
          depth: normalized[:depth],
          origin: origin,
          linear: projected_planar_linear(world_transform, context[:basis])
        }
      end
      context[:section_lines] += block_context[:section_lines]
      context[:silhouette_edges] += block_context[:silhouette_edges]
      context[:skipped_hidden] += block_context[:skipped_hidden]
      context[:skipped_offscreen] += block_context[:skipped_offscreen]
      context[:skipped_sampled] += block_context[:skipped_sampled]
      context[:skipped_occluded] += block_context[:skipped_occluded]
      context[:skipped_material_faces] += block_context[:skipped_material_faces]
      context[:material_faces] += block_context[:material_faces]
      context[:ray_errors] += block_context[:ray_errors]
      context[:full_fidelity_children] += block_context[:full_fidelity_children]
      context[:structural_fidelity_blocks] += block_context[:structural_fidelity_blocks]
      context[:skipped_time_budget] += block_context[:skipped_time_budget]
      context[:omitted_time_budget] += block_context[:omitted_time_budget]
      :emitted
    rescue StandardError
      :fallback
    end

    def bounded_block_quality?(quality)
      quality == 'light' || quality == 'balanced'
    end

    def dense_soft_deadline_exceeded?(context)
      deadline = context[:dense_soft_deadline]
      deadline && monotonic_time >= deadline
    end

    def dense_hard_deadline_exceeded?(context)
      deadline = context[:dense_hard_deadline]
      deadline && monotonic_time >= deadline
    end

    def dense_stop_deadline_exceeded?(context)
      deadline = context[:dense_stop_deadline]
      deadline && monotonic_time >= deadline
    end

    def full_fidelity_block?(instance, context)
      return true unless bounded_block_quality?(context[:quality])

      threshold = context[:quality] == 'balanced' ?
                    BALANCED_FULL_FIDELITY_ENTITY_THRESHOLD :
                    LIGHT_FULL_FIDELITY_ENTITY_THRESHOLD
      definition_complexity(instance.definition, context, threshold) <= threshold
    rescue StandardError
      false
    end

    # Raw recursive entity count is a poor proxy for drafting complexity. A
    # repeated chair can contain thousands of triangulated faces, and a rack
    # wrapper can contain only two children while those children expand to tens
    # of thousands of entities. Promote these bounded, visibly significant,
    # repeated assemblies so their structural linework is extracted once and
    # reused as a CAD block instead of being reduced to a few sampled fragments.
    def structural_fidelity_block?(instance, world_transform, context)
      return false unless context[:quality] == 'balanced'
      instance_count = instance.definition.instances.length
      return false if instance_count < 2

      width, height = projected_definition_size(instance.definition, world_transform, context[:basis])
      width_pixels = width.abs / context[:mm_per_pixel]
      height_pixels = height.abs / context[:mm_per_pixel]
      return false if [width_pixels, height_pixels].max < 4.0
      return false if width_pixels * height_pixels < 12.0

      direct = instance.definition.entities.length.to_i
      if direct <= BALANCED_STRUCTURAL_DIRECT_THRESHOLD
        recursive = definition_complexity(
          instance.definition,
          context,
          BALANCED_STRUCTURAL_DIRECT_THRESHOLD
        )
        return true if recursive <= BALANCED_STRUCTURAL_DIRECT_THRESHOLD
      end

      return false if direct > BALANCED_STRUCTURAL_WRAPPER_CHILDREN
      return false if instance_count < 3
      children_only = instance.definition.entities.all? do |entity|
        entity.is_a?(Sketchup::Group) || entity.is_a?(Sketchup::ComponentInstance)
      end
      return false unless children_only

      definition_complexity(
        instance.definition,
        context,
        BALANCED_STRUCTURAL_WRAPPER_THRESHOLD
      ) <= BALANCED_STRUCTURAL_WRAPPER_THRESHOLD
    rescue StandardError
      false
    end

    # Imported lettering and logos are often technically dense because curved
    # glyphs are triangulated, while visually they are thin planar line art.
    # Read these definitions completely but retain mesh cleanup, which preserves
    # outlines/holes and removes side-wall triangulation.
    def outline_fidelity_block?(instance, world_transform, context)
      return false unless bounded_block_quality?(context[:quality])

      entity_limit = context[:quality] == 'balanced' ? 60_000 : 20_000
      direct_count = instance.definition.entities.length.to_i
      return false if direct_count <= 0 || direct_count > entity_limit
      recursive_limit = context[:quality] == 'balanced' ? 150_000 : 60_000
      return false if definition_complexity(instance.definition, context, recursive_limit) > recursive_limit

      projected = bounds_corners(instance.definition.bounds).map do |point|
        project(point.transform(world_transform), context[:basis])
      end
      width = projected.map { |point| point[0] }.max - projected.map { |point| point[0] }.min
      height = projected.map { |point| point[1] }.max - projected.map { |point| point[1] }.min
      depth = projected.map { |point| point[2] }.max - projected.map { |point| point[2] }.min
      major = [width.abs, height.abs].max
      return false if major / context[:mm_per_pixel] < 8.0

      depth.abs <= [major * 0.08, context[:mm_per_pixel] * 4.0].max
    rescue StandardError
      false
    end

    def definition_complexity(definition, context, limit, visiting = {})
      cache = context[:definition_complexity_cache] ||= {}
      key = [definition.persistent_id, limit]
      cached = cache[key]
      return cached if cached
      return limit + 1 if visiting[definition.object_id]

      visiting[definition.object_id] = true
      count = 0
      definition.entities.each do |entity|
        count += 1
        if entity.is_a?(Sketchup::Group) || entity.is_a?(Sketchup::ComponentInstance)
          count += definition_complexity(entity.definition, context, limit, visiting)
        end
        break if count > limit
      end
      visiting.delete(definition.object_id)
      cache[key] = [count, limit + 1].min
    ensure
      visiting.delete(definition.object_id) if definition
    end

    def dense_occlusion_profile(profile, quality)
      if quality == 'balanced'
        profile.merge(
          sample_pixels: [profile[:sample_pixels], 8.0].max,
          max_visibility_intervals: [profile[:max_visibility_intervals], 4].min,
          max_clip_depth: [profile[:max_clip_depth], 3].min,
          depth_tile_pixels: [profile[:depth_tile_pixels], 6.0].max
        )
      else
        profile.merge(
          sample_pixels: [profile[:sample_pixels], 12.0].max,
          max_visibility_intervals: [profile[:max_visibility_intervals], 2].min,
          max_clip_depth: [profile[:max_clip_depth], 2].min,
          depth_tile_pixels: [profile[:depth_tile_pixels], 8.0].max
        )
      end
    end

    def full_fidelity_occlusion_profile(profile, quality)
      if quality == 'balanced'
        profile.merge(
          sample_pixels: [profile[:sample_pixels], 4.0].max,
          max_visibility_intervals: [profile[:max_visibility_intervals], 8].min,
          max_clip_depth: [profile[:max_clip_depth], 4].min,
          depth_tile_pixels: [profile[:depth_tile_pixels], 4.0].max
        )
      else
        profile.merge(
          sample_pixels: [profile[:sample_pixels], 8.0].max,
          max_visibility_intervals: [profile[:max_visibility_intervals], 4].min,
          max_clip_depth: [profile[:max_clip_depth], 3].min,
          depth_tile_pixels: [profile[:depth_tile_pixels], 6.0].max
        )
      end
    end

    def block_entity_budget(quality)
      quality == 'balanced' ? BALANCED_BLOCK_ENTITY_BUDGET : LIGHT_BLOCK_ENTITY_BUDGET
    end

    def entity_sample_limit(quality)
      quality == 'balanced' ? BALANCED_ENTITY_SAMPLES_PER_COLLECTION : LIGHT_ENTITY_SAMPLES_PER_COLLECTION
    end

    def append_block_reference(
      context, instance, block_name, insert, depth,
      layer: nil, rotation: 0.0, xscale: 1.0, yscale: 1.0
    )
      source_name = instance.name.to_s.empty? ? instance.definition.name.to_s : instance.name.to_s
      context[:block_references] << {
        block: block_name,
        insert: insert,
        depth: depth,
        rotation: rotation,
        xscale: xscale,
        yscale: yscale,
        layer: layer || tag_name(instance) || 'Untagged',
        sourceId: instance.persistent_id,
        sourceName: source_name
      }
    end

    def light_block_cache_key(instance, world_transform, outer_tag, outer_material)
      linear = world_transform.to_a.each_with_index.map do |value, index|
        index.between?(12, 14) ? 0.0 : value.to_f.round(6)
      end
      material_id = outer_material && outer_material.respond_to?(:persistent_id) ? outer_material.persistent_id : nil
      [instance.definition.persistent_id, linear, outer_tag.to_s, material_id]
    end

    def light_planar_block_cache_key(instance, outer_tag, outer_material)
      material_id = outer_material && outer_material.respond_to?(:persistent_id) ? outer_material.persistent_id : nil
      [instance.definition.persistent_id, outer_tag.to_s, material_id]
    end

    def projected_planar_linear(world_transform, basis)
      origin = project(Geom::Point3d.new(0, 0, 0).transform(world_transform), basis)
      point_x = project(Geom::Point3d.new(1, 0, 0).transform(world_transform), basis)
      point_y = project(Geom::Point3d.new(0, 1, 0).transform(world_transform), basis)
      point_z = project(Geom::Point3d.new(0, 0, 1).transform(world_transform), basis)
      z_shift = Math.sqrt((point_z[0] - origin[0])**2 + (point_z[1] - origin[1])**2)
      return nil if z_shift > 0.01

      [
        [point_x[0] - origin[0], point_y[0] - origin[0]],
        [point_x[1] - origin[1], point_y[1] - origin[1]]
      ]
    rescue StandardError
      nil
    end

    def planar_block_reference(cached, world_transform, basis)
      source = cached[:linear]
      target = projected_planar_linear(world_transform, basis)
      return nil unless source && target

      determinant = source[0][0] * source[1][1] - source[0][1] * source[1][0]
      return nil if determinant.abs < 1.0e-9

      inverse = [
        [source[1][1] / determinant, -source[0][1] / determinant],
        [-source[1][0] / determinant, source[0][0] / determinant]
      ]
      matrix = multiply_matrix2(target, inverse)
      column_x = [matrix[0][0], matrix[1][0]]
      column_y = [matrix[0][1], matrix[1][1]]
      xscale = Math.sqrt(column_x[0]**2 + column_x[1]**2)
      yscale_abs = Math.sqrt(column_y[0]**2 + column_y[1]**2)
      return nil if xscale < 1.0e-8 || yscale_abs < 1.0e-8

      orthogonality = (column_x[0] * column_y[0] + column_x[1] * column_y[1]).abs
      return nil if orthogonality > xscale * yscale_abs * 1.0e-5

      matrix_determinant = matrix[0][0] * matrix[1][1] - matrix[0][1] * matrix[1][0]
      yscale = matrix_determinant.negative? ? -yscale_abs : yscale_abs
      rotation = Math.atan2(column_x[1], column_x[0]) * 180.0 / Math::PI
      origin = project(Geom::Point3d.new(0, 0, 0).transform(world_transform), basis)
      local_insert = [
        cached[:insert][0] - cached[:origin][0],
        cached[:insert][1] - cached[:origin][1]
      ]
      transformed_insert = multiply_matrix2_vector(matrix, local_insert)
      {
        insert: [
          (origin[0] + transformed_insert[0]).round(4),
          (origin[1] + transformed_insert[1]).round(4)
        ],
        depth: (cached[:depth] + origin[2] - cached[:origin][2]).round(4),
        rotation: rotation.round(8),
        xscale: xscale.round(8),
        yscale: yscale.round(8)
      }
    rescue StandardError
      nil
    end

    def multiply_matrix2(left, right)
      [
        [
          left[0][0] * right[0][0] + left[0][1] * right[1][0],
          left[0][0] * right[0][1] + left[0][1] * right[1][1]
        ],
        [
          left[1][0] * right[0][0] + left[1][1] * right[1][0],
          left[1][0] * right[0][1] + left[1][1] * right[1][1]
        ]
      ]
    end

    def multiply_matrix2_vector(matrix, vector)
      [
        matrix[0][0] * vector[0] + matrix[0][1] * vector[1],
        matrix[1][0] * vector[0] + matrix[1][1] * vector[1]
      ]
    end

    def instance_fully_inside_view?(instance, world_transform, context)
      projected = bounds_corners(instance.definition.bounds).map do |point|
        project(point.transform(world_transform), context[:basis])
      end
      viewport = context[:viewport]
      projected.all? do |point|
        point[0] >= viewport[:min_x] && point[0] <= viewport[:max_x] &&
          point[1] >= viewport[:min_y] && point[1] <= viewport[:max_y]
      end
    rescue StandardError
      false
    end

    def instance_fully_on_kept_section_side?(instance, world_transform, context)
      section = context[:section]
      return true unless section

      bounds_corners(instance.definition.bounds).all? do |point|
        world_point = point.transform(world_transform)
        plane_distance(world_point, section[:plane]) * section[:keep_multiplier] >= SECTION_TOLERANCE_INCH
      end
    rescue StandardError
      false
    end

    # Test the whole projected instance before expanding its definition. This is
    # deliberately conservative: only a block covered at every grid sample is
    # discarded. A mixture of covered and clear samples is marked partial so
    # simple blocks receive exact edge clipping and dense blocks receive bounded
    # coarse clipping instead of leaking all rear geometry through the facade.
    def instance_visibility_state(instance, world_transform, context, fine: false)
      return :visible unless context[:occlusion]

      projected = bounds_corners(instance.definition.bounds).map do |point|
        project(point.transform(world_transform), context[:basis])
      end
      viewport = context[:viewport]
      min_x = [projected.map { |point| point[0] }.min, viewport[:min_x]].max
      max_x = [projected.map { |point| point[0] }.max, viewport[:max_x]].min
      min_y = [projected.map { |point| point[1] }.min, viewport[:min_y]].max
      max_y = [projected.map { |point| point[1] }.max, viewport[:max_y]].min
      return :visible if min_x >= max_x || min_y >= max_y

      nearest_depth = projected.map { |point| point[2].to_f }.max
      ratios = context[:quality] == 'balanced' ?
                 [0.08, 0.5, 0.92] :
                 [0.15, 0.5, 0.85]
      covered = 0
      clear = 0
      direction = context[:basis][:forward]
      tolerance_mm = VISIBILITY_TOLERANCE_INCH * MM_PER_INCH
      tile_pixels = if fine
                      [context[:profile][:depth_tile_pixels].to_f, 2.0].max
                    else
                      context[:quality] == 'balanced' ? 24.0 : 32.0
                    end
      tile_mm = context[:mm_per_pixel] * tile_pixels
      depth_cache = context[:instance_depth_cache] ||= {}
      ratios.product(ratios).each do |x_ratio, y_ratio|
        x = min_x + ((max_x - min_x) * x_ratio)
        y = min_y + ((max_y - min_y) * y_ratio)
        cache_key = [
          fine ? :fine : :coarse,
          tile_pixels,
          ((x - viewport[:min_x]) / tile_mm).floor,
          ((y - viewport[:min_y]) / tile_mm).floor
        ]
        if depth_cache.key?(cache_key)
          hit_depth = depth_cache[cache_key]
        else
          point = unproject(x, y, nearest_depth, context[:basis])
          origin = ray_origin(point, direction, context)
          hit = raytest_with_retry(context[:model], origin, direction)
          hit_depth = hit && project(hit[0], context[:basis])[2].to_f
          depth_cache[cache_key] = hit_depth
        end
        if hit_depth && hit_depth > nearest_depth + tolerance_mm
          covered += 1
        else
          clear += 1
        end
        return :partial if covered.positive? && clear.positive?
      end
      covered.positive? && clear.zero? ? :occluded : :visible
    rescue StandardError
      context[:ray_errors] += 1
      :partial
    end

    def instance_intersects_view?(instance, world_transform, context)
      points = bounds_corners(instance.definition.bounds).map { |point| point.transform(world_transform) }
      section = context[:section]
      if section
        kept = points.any? do |point|
          plane_distance(point, section[:plane]) * section[:keep_multiplier] >= -SECTION_TOLERANCE_INCH
        end
        return false unless kept
      end

      projected = points.map { |point| project(point, context[:basis]) }
      xs = projected.map { |point| point[0] }
      ys = projected.map { |point| point[1] }
      viewport = context[:viewport]
      return false if xs.max < viewport[:min_x] || xs.min > viewport[:max_x]
      return false if ys.max < viewport[:min_y] || ys.min > viewport[:max_y]

      min_pixels = context[:profile][:min_object_pixels]
      return true unless min_pixels.positive?

      width_pixels = (xs.max - xs.min) / context[:mm_per_pixel]
      height_pixels = (ys.max - ys.min) / context[:mm_per_pixel]
      width_pixels >= min_pixels || height_pixels >= min_pixels
    rescue StandardError
      true
    end

    def normalize_block_geometry(lines, curves, fills)
      points = lines.flat_map { |line| [line[:start], line[:end]] } +
               curves.flat_map { |curve| curve[:points] } +
               fills.flat_map { |fill| fill[:loops].flat_map { |loop| loop[:points] } }
      return nil if points.empty?

      min_x = points.map { |point| point[0] }.min
      min_y = points.map { |point| point[1] }.min
      min_depth = points.map { |point| point[2].to_f }.min
      shift = lambda do |point|
        [(point[0] - min_x).round(4), (point[1] - min_y).round(4), (point[2].to_f - min_depth).round(4)]
      end
      normalized_lines = lines.map do |line|
        line.merge(start: shift.call(line[:start]), end: shift.call(line[:end]))
      end
      normalized_curves = curves.map do |curve|
        curve.merge(points: curve[:points].map { |point| shift.call(point) })
      end
      normalized_fills = fills.map do |fill|
        fill.merge(
          depth: (fill[:depth].to_f - min_depth).round(4),
          loops: fill[:loops].map { |loop| loop.merge(points: loop[:points].map { |point| shift.call(point) }) }
        )
      end
      {
        insert: [min_x.round(4), min_y.round(4)],
        depth: min_depth.round(4),
        lines: normalized_lines,
        curves: normalized_curves,
        fills: normalized_fills
      }
    end

    def block_geometry_hash(lines, curves, fills)
      line_keys = lines.map do |line|
        [line[:layer], line[:fidelity], [line[:start].first(2), line[:end].first(2)].sort]
      end.sort_by(&:to_s)
      curve_keys = curves.map do |curve|
        [curve[:layer], curve[:curveType], curve[:closed], curve[:points].map { |point| point.first(2) }]
      end.sort_by(&:to_s)
      fill_keys = fills.map do |fill|
        [
          fill[:layer],
          fill[:color],
          fill[:alpha],
          fill[:depth],
          fill[:loops].map { |loop| [loop[:outer], loop[:points].map { |point| point.first(3) }] }
        ]
      end.sort_by(&:to_s)
      Digest::SHA1.hexdigest(JSON.generate([line_keys, curve_keys, fill_keys]))
    end

    def projected_definition_size(definition, world_transform, basis)
      projected = bounds_corners(definition.bounds).map { |point| project(point.transform(world_transform), basis) }
      xs = projected.map { |point| point[0] }
      ys = projected.map { |point| point[1] }
      [xs.max - xs.min, ys.max - ys.min]
    end

    def bounds_corners(bounds)
      min = bounds.min
      max = bounds.max
      [min.x, max.x].product([min.y, max.y], [min.z, max.z]).map do |x, y, z|
        Geom::Point3d.new(x, y, z)
      end
    end

    def emit_edge(edge, entities, transform, outer_tag, context)
      return if edge.hidden?

      tag = effective_tag_name(edge, outer_tag) || 'Untagged'
      curve = edge.curve
      if curve
        key = [entities.object_id, curve.object_id, transform.to_a.map { |value| value.round(8) }]
        return if context[:processed_curves][key]

        curve_edges = curve.respond_to?(:edges) ? curve.edges : [edge]
        context[:processed_curves][key] = true
        visible_curve_edges = curve_edges.select do |item|
          entity_visible?(item) && curve_edge_display?(item, transform, context)
        end
        return if visible_curve_edges.empty?

        if visible_curve_edges.length != curve_edges.length
          visible_curve_edges.each { |item| emit_straight_edge(item, transform, tag, context, 'curve') }
          return
        end

        points = curve.vertices.map { |vertex| vertex.position.transform(transform) }
        closed = curve_closed?(curve)
        curve_type = curve.respond_to?(:typename) ? curve.typename.to_s : curve.class.name.split('::').last
        points << points.first if closed && !close_points?(points.first, points.last)
        runs = clip_polyline_to_section(points, context[:section])
        runs.each { |run| emit_curve(run, closed && runs.length == 1, tag, context, curve_type) }
      else
        role = edge_export_role(edge, transform, context)
        return unless role

        emit_straight_edge(edge, transform, tag, context, role)
      end
    end

    def emit_straight_edge(edge, transform, tag, context, edge_role = 'hard')
      start_point = edge.start.position.transform(transform)
      end_point = edge.end.position.transform(transform)
      section_pair = clip_segment_to_section(start_point, end_point, context[:section])
      return unless section_pair

      viewport_pair = clip_world_segment_to_viewport(section_pair[0], section_pair[1], context)
      unless viewport_pair
        context[:skipped_offscreen] += 1
        return
      end

      fragments = clip_visible_segment(viewport_pair[0], viewport_pair[1], context)
      if fragments.empty?
        context[:skipped_occluded] += 1
        return
      end
      fragments.each do |pair|
        a = project(pair[0], context[:basis])
        b = project(pair[1], context[:basis])
        clipped = clip_projected_segment(a, b, context[:viewport])
        next unless clipped
        next if distance2(clipped[0], clipped[1]) < 0.01

        context[:lines] << {
          start: clipped[0],
          end: clipped[1],
          layer: tag,
          edgeRole: edge_role,
          fidelity: context[:fidelity] || 'full'
        }
      end
    end

    def display_edge?(edge, transform, context)
      !edge_export_role(edge, transform, context).nil?
    end

    def curve_edge_display?(edge, transform, context)
      faces = edge.faces.to_a
      return true if faces.length < 2

      normals = faces.map { |face| world_face_normal(face, transform) }
      dots = normals.map { |normal| normal.dot(context[:basis][:forward]) }
      silhouette = dots.min < -1.0e-5 && dots.max > 1.0e-5
      context[:silhouette_edges] += 1 if silhouette
      return silhouette if edge.soft? || edge.smooth?

      true
    rescue StandardError
      false
    end

    def edge_export_role(edge, transform, context)
      faces = edge.faces.to_a
      return 'boundary' if faces.length < 2

      normals = faces.map { |face| world_face_normal(face, transform) }
      dots = normals.map { |normal| normal.dot(context[:basis][:forward]) }
      silhouette = dots.min < -1.0e-5 && dots.max > 1.0e-5
      context[:silhouette_edges] += 1 if silhouette
      return 'silhouette' if silhouette
      return nil if edge.soft? || edge.smooth?

      if context[:light_mesh_cleanup]
        # Back-facing faces and sub-pixel hard facets are not visible furniture
        # drafting information. Keep boundaries, silhouettes, curves and long
        # structural seams, but remove the dense imported mesh that otherwise
        # appears as random interior scratches in CAD.
        return nil if dots.all? { |dot| dot > 1.0e-4 }

        start_point = edge.start.position.transform(transform)
        end_point = edge.end.position.transform(transform)
        projected_start = project(start_point, context[:basis])
        projected_end = project(end_point, context[:basis])
        projected_length = Math.sqrt(distance2(projected_start, projected_end))
        cleanup_pixels = context[:mesh_cleanup_pixels] || 0.75
        micro_threshold = [context[:mm_per_pixel].to_f * cleanup_pixels, 2.0].max
        same_material = faces.map { |face| face_material_key(face, normal_facing_dot(face, transform, context)) }.uniq.length <= 1
        coplanar = normals.combination(2).all? { |a, b| a.dot(b).abs > 0.9999 }
        return nil if projected_length < micro_threshold
        return nil if coplanar && same_material && projected_length < micro_threshold * 4.0
      end

      # A regular SketchUp edge is intentional drafting geometry. Do not remove
      # shallow/coplanar hard edges merely because adjacent faces share material;
      # that rule erased furniture seams and short construction details.
      'hard'
    rescue StandardError
      nil
    end

    def normal_facing_dot(face, transform, context)
      world_face_normal(face, transform).dot(context[:basis][:forward])
    end

    def face_material_key(face, facing_dot)
      material = facing_dot < 0 ? face.material : (face.back_material || face.material)
      material && material.persistent_id
    end

    def world_face_normal(face, transform)
      vertices = face.outer_loop.vertices.first(3).map { |vertex| vertex.position.transform(transform) }
      normal = vertices[0].vector_to(vertices[1]).cross(vertices[0].vector_to(vertices[2]))
      normal.normalize!
      normal
    end

    def emit_face_material(face, transform, outer_tag, outer_material, context)
      normal = world_face_normal(face, transform)
      facing = normal.dot(context[:basis][:forward])
      return if facing.abs < 1.0e-6

      material = facing < 0 ? (face.material || outer_material) : (face.back_material || outer_material)
      alpha = material && material.respond_to?(:alpha) ? material.alpha.to_f : 1.0
      if alpha <= 0.01
        context[:skipped_material_faces] += 1
        return
      end

      loops = face.loops.filter_map do |loop|
        world_points = loop.vertices.map { |vertex| vertex.position.transform(transform) }
        clipped = clip_polygon_to_section(world_points, context[:section])
        next if clipped.length < 3

        projected = dedupe_adjacent(clipped.map { |point| project(point, context[:basis]) })
        projected = clip_projected_polygon(projected, context[:viewport])
        next if projected.length < 3 || polygon_area2(projected).abs < MIN_FILL_AREA_MM2

        { outer: loop.outer?, points: projected }
      end
      return if loops.none? { |loop| loop[:outer] }

      projected_area = loops.sum do |loop|
        area = polygon_area2(loop[:points])
        loop[:outer] ? area : -area
      end.abs
      projected_area_pixels2 = projected_area / (context[:mm_per_pixel]**2)
      if projected_area_pixels2 < context[:profile][:material_sample_pixels2]
        context[:skipped_material_faces] += 1
        return
      end
      if context[:material_prefilter] && projected_area < MATERIAL_PREFILTER_AREA_MM2
        sample_count = projected_area_pixels2 >= 64.0 ? 3 : 1
        samples = face_visibility_samples(face, transform, sample_count, projected_area_pixels2 >= 256.0)
        if context[:occlusion] && samples.none? { |point| visible_point?(point, context) }
          context[:skipped_material_faces] += 1
          return
        end
      end

      color = material && material.color
      outer_points = loops.select { |loop| loop[:outer] }.flat_map { |loop| loop[:points] }
      context[:fills] << {
        materialName: material && material.display_name.to_s,
        color: color && [color.red.to_i, color.green.to_i, color.blue.to_i],
        alpha: alpha.round(4),
        paint: !material.nil?,
        layer: material ? "MATERIAL_#{material.display_name}" : 'SUCAD-OCCLUDER',
        sourceLayer: effective_tag_name(face, outer_tag) || 'Untagged',
        depth: (outer_points.sum { |point| point[2].to_f } / outer_points.length).round(4),
        loops: loops
      }
      context[:material_faces] += 1
    rescue StandardError
      context[:skipped_material_faces] += 1
    end

    def face_visibility_samples(face, transform, count = 3, include_boundary = false)
      mesh = face.mesh(0)
      polygons = mesh.polygons.to_a
      return face.outer_loop.vertices.first(3).map { |vertex| vertex.position.transform(transform) } if polygons.empty?

      indexes = count <= 1 ? [polygons.length / 2] : [0, polygons.length / 2, polygons.length - 1].uniq
      centroids = indexes.map do |index|
        vertices = polygons[index].first(3).map { |vertex_index| mesh.point_at(vertex_index.abs).transform(transform) }
        Geom::Point3d.new(
          vertices.sum(&:x) / vertices.length,
          vertices.sum(&:y) / vertices.length,
          vertices.sum(&:z) / vertices.length
        )
      end
      return centroids unless include_boundary

      boundary = face.outer_loop.vertices.to_a
      boundary_indexes = if boundary.length <= 4
                           (0...boundary.length).to_a
                         else
                           [0, boundary.length / 4, boundary.length / 2, (boundary.length * 3) / 4]
                         end
      centroids + boundary_indexes.uniq.map { |index| boundary[index].position.transform(transform) }
    rescue StandardError
      face.outer_loop.vertices.first(3).map { |vertex| vertex.position.transform(transform) }
    end

    def clip_polygon_to_section(points, section)
      return points unless section
      return [] if points.empty?

      output = []
      previous = points.last
      previous_distance = plane_distance(previous, section[:plane]) * section[:keep_multiplier]
      previous_inside = previous_distance >= -SECTION_TOLERANCE_INCH
      points.each do |current|
        current_distance = plane_distance(current, section[:plane]) * section[:keep_multiplier]
        current_inside = current_distance >= -SECTION_TOLERANCE_INCH
        if current_inside != previous_inside
          ratio = previous_distance / (previous_distance - current_distance)
          output << Geom.linear_combination(1.0 - ratio, previous, ratio, current)
        end
        output << current if current_inside
        previous = current
        previous_distance = current_distance
        previous_inside = current_inside
      end
      output
    end

    def polygon_area2(points)
      points.each_with_index.sum do |point, index|
        following = points[(index + 1) % points.length]
        (point[0] * following[1]) - (following[0] * point[1])
      end.abs * 0.5
    end

    def emit_section_intersections(face, transform, context)
      plane = context[:section][:plane]
      face.loops.each do |loop|
        points = loop.vertices.map { |vertex| vertex.position.transform(transform) }
        intersections = polygon_plane_intersections(points, plane)
        intersections.each_slice(2) do |pair|
          next unless pair.length == 2
          next if pair[0].distance(pair[1]) < MIN_FRAGMENT_INCH

          a = project(pair[0], context[:basis])
          b = project(pair[1], context[:basis])
          clipped = clip_projected_segment(a, b, context[:viewport])
          next unless clipped

          context[:lines] << {
            start: clipped[0],
            end: clipped[1],
            layer: 'SUCAD-SECTION',
            section: true,
            edgeRole: 'section',
            fidelity: 'full'
          }
          context[:section_lines] += 1
        end
      end
    rescue StandardError
      nil
    end

    def polygon_plane_intersections(points, plane)
      output = []
      points.each_with_index do |point, index|
        following = points[(index + 1) % points.length]
        d1 = plane_distance(point, plane)
        d2 = plane_distance(following, plane)
        if d1.abs <= SECTION_TOLERANCE_INCH && d2.abs <= SECTION_TOLERANCE_INCH
          output << point << following
        elsif d1.abs <= SECTION_TOLERANCE_INCH
          output << point
        elsif d1 * d2 < 0
          ratio = d1 / (d1 - d2)
          output << Geom.linear_combination(1.0 - ratio, point, ratio, following)
        end
      end
      dedupe_world_points(output)
    end

    def clip_segment_to_section(start_point, end_point, section)
      return [start_point, end_point] unless section

      d1 = plane_distance(start_point, section[:plane]) * section[:keep_multiplier]
      d2 = plane_distance(end_point, section[:plane]) * section[:keep_multiplier]
      return [start_point, end_point] if d1 >= -SECTION_TOLERANCE_INCH && d2 >= -SECTION_TOLERANCE_INCH
      return nil if d1 < -SECTION_TOLERANCE_INCH && d2 < -SECTION_TOLERANCE_INCH

      ratio = d1 / (d1 - d2)
      intersection = Geom.linear_combination(1.0 - ratio, start_point, ratio, end_point)
      d1 >= 0 ? [start_point, intersection] : [intersection, end_point]
    end

    def clip_polyline_to_section(points, section)
      return [points] unless section

      runs = []
      current = []
      points.each_cons(2) do |start_point, end_point|
        pair = clip_segment_to_section(start_point, end_point, section)
        if pair
          if current.empty? || current.last.distance(pair[0]) > SECTION_TOLERANCE_INCH
            runs << current if current.length > 1
            current = [pair[0]]
          end
          current << pair[1]
        elsif current.length > 1
          runs << current
          current = []
        end
      end
      runs << current if current.length > 1
      runs
    end

    def emit_curve(points, closed, tag, context, curve_type)
      return if points.length < 2

      runs = context[:occlusion] ? clip_polyline_visibility(points, context) : [points]
      if runs.empty?
        context[:skipped_occluded] += 1
        return
      end

      visibility_preserved = same_world_polyline?(points, runs)
      projected_source = dedupe_adjacent(points.map { |point| project(point, context[:basis]) })
      projected_runs = runs.flat_map do |run|
        projected = dedupe_adjacent(run.map { |point| project(point, context[:basis]) })
        clip_projected_polyline(projected, context[:viewport])
      end
      fully_preserved = visibility_preserved && same_projected_polyline?(projected_source, projected_runs)

      projected_runs.each do |run|
        next if run.length < 2

        run_closed = closed && fully_preserved && distance2(run.first, run.last) < 0.01

        context[:curves] << {
          points: run,
          closed: run_closed,
          curveType: curve_type,
          layer: tag,
          partiallyOccluded: !fully_preserved
        }
      end
    end

    def clip_polyline_visibility(points, context)
      runs = []
      current = []
      points.each_cons(2) do |start_point, end_point|
        viewport_pair = clip_world_segment_to_viewport(start_point, end_point, context)
        fragments = viewport_pair ? clip_visible_segment(viewport_pair[0], viewport_pair[1], context) : []
        if fragments.empty?
          runs << current if current.length > 1
          current = []
          next
        end

        fragments.each do |pair|
          if current.empty? || current.last.distance(pair[0]) <= MIN_FRAGMENT_INCH
            current << pair[0] if current.empty?
            current << pair[1]
          else
            runs << current if current.length > 1
            current = [pair[0], pair[1]]
          end
        end
      end
      runs << current if current.length > 1
      runs
    end

    def clip_world_segment_to_viewport(start_point, end_point, context)
      projected_start = project(start_point, context[:basis])
      projected_end = project(end_point, context[:basis])
      ratios = clip_segment_ratios(projected_start, projected_end, context[:viewport])
      return nil unless ratios

      [
        Geom.linear_combination(1.0 - ratios[0], start_point, ratios[0], end_point),
        Geom.linear_combination(1.0 - ratios[1], start_point, ratios[1], end_point)
      ]
    end

    def same_world_polyline?(source, runs)
      return false unless runs.length == 1 && source.length == runs[0].length

      source.zip(runs[0]).all? { |expected, actual| expected.distance(actual) < 1.0e-6 }
    end

    def same_projected_polyline?(source, runs)
      return false unless runs.length == 1 && source.length == runs[0].length

      source.zip(runs[0]).all? { |expected, actual| distance2(expected, actual) < 0.01 }
    end

    def clip_projected_polyline(points, viewport)
      return [] if points.length < 2

      runs = []
      current = []
      points.each_cons(2) do |start_point, end_point|
        pair = clip_projected_segment(start_point, end_point, viewport)
        if pair
          if current.empty? || distance2(current.last, pair[0]) <= 0.01
            current << pair[0] if current.empty?
            current << pair[1]
          else
            runs << dedupe_adjacent(current) if current.length > 1
            current = [pair[0], pair[1]]
          end
        elsif current.length > 1
          runs << dedupe_adjacent(current)
          current = []
        end
      end
      runs << dedupe_adjacent(current) if current.length > 1
      runs.select { |run| run.length > 1 }
    end

    def clip_projected_segment(start_point, end_point, viewport)
      ratios = clip_segment_ratios(start_point, end_point, viewport)
      return nil unless ratios

      [
        interpolate_projected(start_point, end_point, ratios[0]),
        interpolate_projected(start_point, end_point, ratios[1])
      ]
    end

    def clip_segment_ratios(start_point, end_point, viewport)
      return [0.0, 1.0] unless viewport

      dx = end_point[0] - start_point[0]
      dy = end_point[1] - start_point[1]
      lower = 0.0
      upper = 1.0
      checks = [
        [-dx, start_point[0] - viewport[:min_x]],
        [dx, viewport[:max_x] - start_point[0]],
        [-dy, start_point[1] - viewport[:min_y]],
        [dy, viewport[:max_y] - start_point[1]]
      ]
      checks.each do |coefficient, distance|
        if coefficient.abs < 1.0e-12
          return nil if distance.negative?

          next
        end

        ratio = distance / coefficient
        if coefficient.negative?
          lower = [lower, ratio].max
        else
          upper = [upper, ratio].min
        end
        return nil if lower > upper
      end
      [lower, upper]
    end

    def clip_projected_polygon(points, viewport)
      return points unless viewport
      return [] if points.empty?

      output = points
      [
        [0, viewport[:min_x], true],
        [0, viewport[:max_x], false],
        [1, viewport[:min_y], true],
        [1, viewport[:max_y], false]
      ].each do |axis, boundary, keep_greater|
        return [] if output.empty?

        clipped = []
        previous = output.last
        previous_inside = keep_greater ? previous[axis] >= boundary : previous[axis] <= boundary
        output.each do |current|
          current_inside = keep_greater ? current[axis] >= boundary : current[axis] <= boundary
          if current_inside != previous_inside
            denominator = current[axis] - previous[axis]
            ratio = denominator.abs < 1.0e-12 ? 0.0 : (boundary - previous[axis]) / denominator
            clipped << interpolate_projected(previous, current, ratio)
          end
          clipped << current if current_inside
          previous = current
          previous_inside = current_inside
        end
        output = dedupe_adjacent(clipped)
      end
      output
    end

    def interpolate_projected(start_point, end_point, ratio)
      [
        start_point[0] + ((end_point[0] - start_point[0]) * ratio),
        start_point[1] + ((end_point[1] - start_point[1]) * ratio),
        start_point[2].to_f + ((end_point[2].to_f - start_point[2].to_f) * ratio)
      ].map { |value| value.round(4) }
    end

    def clip_visible_segment(start_point, end_point, context)
      return [[start_point, end_point]] unless context[:occlusion]

      projected_start = project(start_point, context[:basis])
      projected_end = project(end_point, context[:basis])
      projected_length_mm = Math.sqrt(distance2(projected_start, projected_end))
      sample_step_mm = context[:mm_per_pixel] * context[:profile][:sample_pixels]
      desired_intervals = (projected_length_mm / [sample_step_mm, 1.0e-6].max).ceil
      intervals = [[desired_intervals, 2].max, context[:profile][:max_visibility_intervals]].min
      points = (0..intervals).map do |index|
        ratio = index.to_f / intervals
        Geom.linear_combination(1.0 - ratio, start_point, ratio, end_point)
      end
      states = points.map { |point| visible_point?(point, context) }
      return [[start_point, end_point]] if states.all?
      return [] if states.none?

      fragments = []
      points.each_cons(2).with_index do |(a, b), index|
        state_a = states[index]
        state_b = states[index + 1]
        if state_a && state_b
          fragments << [a, b]
        elsif state_a != state_b
          boundary = visibility_boundary(a, b, state_a, context)
          fragments << (state_a ? [a, boundary] : [boundary, b])
        end
      end
      merge_visible_fragments(fragments)
    end

    def visibility_boundary(start_point, end_point, start_visible, context)
      left = start_point
      right = end_point
      context[:profile][:max_clip_depth].times do
        midpoint = Geom.linear_combination(0.5, left, 0.5, right)
        if visible_point?(midpoint, context) == start_visible
          left = midpoint
        else
          right = midpoint
        end
      end
      Geom.linear_combination(0.5, left, 0.5, right)
    end

    def merge_visible_fragments(fragments)
      fragments.each_with_object([]) do |pair, output|
        next if pair[0].distance(pair[1]) < MIN_FRAGMENT_INCH

        if !output.empty? && output.last[1].distance(pair[0]) <= MIN_FRAGMENT_INCH
          output.last[1] = pair[1]
        else
          output << pair
        end
      end
    end

    def visible_point?(point, context)
      return true unless context[:occlusion]

      projected = project(point, context[:basis])
      tile_mm = context[:mm_per_pixel] * context[:profile][:depth_tile_pixels]
      viewport = context[:viewport]
      key = [
        ((projected[0] - viewport[:min_x]) / tile_mm).floor,
        ((projected[1] - viewport[:min_y]) / tile_mm).floor
      ]
      if context[:depth_cache].key?(key)
        hit_depth = context[:depth_cache][key]
        return true if hit_depth.nil?

        return (hit_depth - projected[2]).abs <= (VISIBILITY_TOLERANCE_INCH * MM_PER_INCH)
      end

      direction = context[:basis][:forward]
      origin = ray_origin(point, direction, context)
      hit = raytest_with_retry(context[:model], origin, direction)
      hit_depth = hit && project(hit[0], context[:basis])[2]
      context[:depth_cache][key] = hit_depth
      visible = hit_depth.nil? || (hit_depth - projected[2]).abs <= (VISIBILITY_TOLERANCE_INCH * MM_PER_INCH)
      context[:visibility_cache][key] = visible
      visible
    rescue StandardError
      context[:ray_errors] += 1
      true
    end

    def raytest_with_retry(model, origin, direction)
      model.raytest([origin, direction], true)
    rescue StandardError
      model.raytest([origin, direction], true)
    end

    def ray_origin(point, direction, context)
      section = context[:section]
      return point.offset(direction.reverse, context[:ray_distance]) unless section

      plane = section[:plane]
      denominator = (plane[0] * direction.x) + (plane[1] * direction.y) + (plane[2] * direction.z)
      return point.offset(direction.reverse, context[:ray_distance]) if denominator.abs < 1.0e-9

      distance = plane_distance(point, plane)
      travel = distance / denominator
      intersection = point.offset(direction.reverse, travel)
      intersection.offset(direction, 2.0 / MM_PER_INCH)
    end

    def camera_basis(camera)
      forward = camera.direction.clone.normalize
      up_hint = camera.up.clone.normalize
      right = forward.cross(up_hint)
      right = forward.cross(Z_AXIS) if right.length < 1.0e-9
      right = forward.cross(Y_AXIS) if right.length < 1.0e-9
      right.normalize!
      up = right.cross(forward).normalize
      { forward: forward, right: right, up: up, origin: camera.target }
    end

    def viewport_bounds(view, camera)
      height_pixels = [view.vpheight.to_f, 1.0].max
      aspect = [view.vpwidth.to_f / height_pixels, 0.01].max
      if camera.perspective?
        target_distance = [camera.eye.distance(camera.target), 1.0e-6].max
        half_fov = camera.fov.to_f * Math::PI / 360.0
        if camera.respond_to?(:fov_is_height?) && !camera.fov_is_height?
          half_width = target_distance * Math.tan(half_fov)
          half_height = half_width / aspect
        else
          half_height = target_distance * Math.tan(half_fov)
          half_width = half_height * aspect
        end
      else
        half_height = [camera.height.to_f / 2.0, 1.0e-6].max
        half_width = half_height * aspect
      end
      {
        min_x: -half_width * MM_PER_INCH,
        max_x: half_width * MM_PER_INCH,
        min_y: -half_height * MM_PER_INCH,
        max_y: half_height * MM_PER_INCH
      }
    end

    def active_section(model, camera)
      found = find_active_section(model.entities, Geom::Transformation.new)
      return nil unless found

      plane = transform_plane(found[:entity].get_plane, found[:transform])
      eye_distance = plane_distance(camera.eye, plane)
      found.merge(plane: plane, keep_multiplier: eye_distance >= 0 ? -1.0 : 1.0)
    end

    def find_active_section(entities, transform)
      entities.each do |entity|
        if entity.is_a?(Sketchup::SectionPlane) && entity.active?
          return { entity: entity, transform: transform, name: entity.name.to_s }
        end
        next unless entity.is_a?(Sketchup::Group) || entity.is_a?(Sketchup::ComponentInstance)

        found = find_active_section(entity.definition.entities, transform * entity.transformation)
        return found if found
      end
      nil
    end

    def transform_plane(plane, transform)
      a, b, c, d = plane.map(&:to_f)
      normal = Geom::Vector3d.new(a, b, c)
      length_sq = normal.dot(normal)
      origin = Geom::Point3d.new(-a * d / length_sq, -b * d / length_sq, -c * d / length_sq)
      axis = normal.parallel?(Z_AXIS) ? X_AXIS : Z_AXIS
      tangent1 = normal.cross(axis).normalize
      tangent2 = normal.cross(tangent1).normalize
      p0 = origin.transform(transform)
      p1 = origin.offset(tangent1, 1.0).transform(transform)
      p2 = origin.offset(tangent2, 1.0).transform(transform)
      world_normal = p0.vector_to(p1).cross(p0.vector_to(p2)).normalize
      offset = -((world_normal.x * p0.x) + (world_normal.y * p0.y) + (world_normal.z * p0.z))
      [world_normal.x, world_normal.y, world_normal.z, offset]
    end

    def plane_distance(point, plane)
      (plane[0] * point.x) + (plane[1] * point.y) + (plane[2] * point.z) + plane[3]
    end

    def project(point, basis)
      delta = basis[:origin].vector_to(point)
      [
        (delta.dot(basis[:right]) * MM_PER_INCH).round(4),
        (delta.dot(basis[:up]) * MM_PER_INCH).round(4),
        (-delta.dot(basis[:forward]) * MM_PER_INCH).round(4)
      ]
    end

    def unproject(x, y, depth, basis)
      point = basis[:origin].offset(basis[:right], x.to_f / MM_PER_INCH)
      point = point.offset(basis[:up], y.to_f / MM_PER_INCH)
      point.offset(basis[:forward].reverse, depth.to_f / MM_PER_INCH)
    end

    def entity_visible?(entity)
      own = !entity.respond_to?(:visible?) || entity.visible?
      layer = entity.respond_to?(:layer) ? entity.layer : nil
      layer_visible = layer.nil? || !layer.respond_to?(:visible?) || layer.visible?
      own && layer_visible
    end

    def tag_name(entity)
      layer = entity.respond_to?(:layer) ? entity.layer : nil
      return nil unless layer

      name = layer.name.to_s
      name.empty? || name == 'Untagged' || name == 'Layer0' ? nil : name
    end

    # SketchUp geometry on Untagged inherits the containing instance tag, but a
    # deliberately tagged nested entity must keep its own tag. The former
    # `outer_tag || tag_name(entity)` rule flattened every descendant onto the
    # first parent tag and made the CAD layer structure inaccurate.
    def effective_tag_name(entity, outer_tag)
      tag_name(entity) || outer_tag
    end

    def export_layers(model)
      model.layers.filter_map do |layer|
        name = layer.name.to_s
        next if name.empty? || name == 'Untagged' || name == 'Layer0'

        color = layer.respond_to?(:color) ? layer.color : nil
        folder = layer.respond_to?(:folder) ? layer.folder : nil
        {
          name: name,
          folder: folder && folder.respond_to?(:name) ? folder.name.to_s : nil,
          visible: !layer.respond_to?(:visible?) || layer.visible?,
          color: color && [color.red.to_i, color.green.to_i, color.blue.to_i]
        }
      end
    rescue StandardError
      []
    end

    def point_array(point)
      point.to_a.first(3).map { |value| (value.to_f * MM_PER_INCH).round(4) }
    end

    def vector_array(vector)
      vector.to_a.first(3).map { |value| value.to_f.round(8) }
    end

    def close_points?(a, b)
      a && b && a.distance(b) < 1.0e-6
    end

    def curve_closed?(curve)
      if curve.respond_to?(:start_angle) && curve.respond_to?(:end_angle)
        sweep = (curve.end_angle.to_f - curve.start_angle.to_f).abs
        return true if (sweep - (2.0 * Math::PI)).abs < 1.0e-6
      end

      edges = curve.respond_to?(:edges) ? curve.edges.to_a : []
      vertices = curve.respond_to?(:vertices) ? curve.vertices.to_a : []
      return false if edges.empty? || vertices.empty?

      edges.length == vertices.length && vertices.all? do |vertex|
        (vertex.edges.to_a & edges).length == 2
      end
    end

    def distance2(a, b)
      Math.sqrt(((a[0] - b[0])**2) + ((a[1] - b[1])**2))
    end

    def dedupe_adjacent(points)
      points.each_with_object([]) do |point, output|
        output << point if output.empty? || distance2(output.last, point) >= 0.01
      end
    end

    def dedupe_world_points(points)
      points.each_with_object([]) do |point, output|
        output << point unless output.any? { |existing| existing.distance(point) <= SECTION_TOLERANCE_INCH }
      end
    end
  end
end
