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
  VISIBILITY_SAMPLE_MM = 50.0
  MAX_VISIBILITY_INTERVALS = 4096
  MAX_CLIP_DEPTH = 7

  class << self
    def export(output_path, occlusion: true, strict_section_occlusion: true, materials: true)
      model = Sketchup.active_model
      raise 'No active SketchUp model' unless model

      camera = model.active_view.camera
      basis = camera_basis(camera)
      viewport = viewport_bounds(model.active_view, camera)
      section = active_section(model, camera)
      effective_occlusion = section ? strict_section_occlusion : occlusion
      diagonal = [model.bounds.diagonal.to_f, 1000.0].max
      context = {
        model: model,
        basis: basis,
        viewport: viewport,
        section: section,
        ray_distance: diagonal * 3.0,
        occlusion: effective_occlusion,
        visibility_cache: {},
        processed_curves: {},
        lines: [],
        curves: [],
        fills: [],
        blocks: [],
        block_references: [],
        block_hashes: {},
        block_mode: true,
        emit_materials: materials,
        material_prefilter: false,
        skipped_hidden: 0,
        skipped_occluded: 0,
        skipped_material_faces: 0,
        material_faces: 0,
        ray_errors: 0,
        section_lines: 0,
        silhouette_edges: 0
      }

      walk_entities(model.entities, Geom::Transformation.new, nil, nil, context)
      if context[:ray_errors].positive?
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
          skippedOccluded: context[:skipped_occluded],
          sectionLines: context[:section_lines],
          silhouetteEdges: context[:silhouette_edges],
          blocks: context[:blocks].length,
          blockReferences: context[:block_references].length,
          occlusion: effective_occlusion,
          requestedOcclusion: occlusion,
          strictSectionOcclusion: strict_section_occlusion,
          materials: materials,
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

    private

    def walk_entities(entities, transform, outer_tag, outer_material, context)
      entities.each do |entity|
        unless entity_visible?(entity)
          context[:skipped_hidden] += 1
          next
        end

        case entity
        when Sketchup::Group, Sketchup::ComponentInstance
          child_tag = outer_tag || tag_name(entity)
          child_material = entity.respond_to?(:material) && entity.material ? entity.material : outer_material
          world_transform = transform * entity.transformation
          block_result = if context[:block_mode] && block_candidate?(entity, world_transform, context)
                           emit_instance_block(entity, world_transform, child_tag, child_material, context)
                         else
                           :fallback
                         end
          if block_result == :fallback
            walk_entities(entity.definition.entities, world_transform, child_tag, child_material, context)
          end
        when Sketchup::Edge
          emit_edge(entity, entities, transform, outer_tag, context)
        when Sketchup::Face
          emit_face_material(entity, transform, outer_tag, outer_material, context) if context[:emit_materials]
          emit_section_intersections(entity, transform, context) if context[:section]
        end
      end
    end

    def block_candidate?(instance, world_transform, context)
      return true if instance.is_a?(Sketchup::ComponentInstance) && !instance.is_a?(Sketchup::Group)
      return true if instance.definition.instances.length > 1

      width, height = projected_definition_size(instance.definition, world_transform, context[:basis])
      width > 20.0 && height > 20.0 && width < 6000.0 && height < 6000.0
    rescue StandardError
      false
    end

    def emit_instance_block(instance, world_transform, outer_tag, outer_material, context)
      block_context = {
        model: context[:model],
        basis: context[:basis],
        viewport: context[:viewport],
        section: context[:section],
        ray_distance: context[:ray_distance],
        occlusion: context[:occlusion],
        visibility_cache: context[:visibility_cache],
        processed_curves: {},
        lines: [],
        curves: [],
        fills: [],
        blocks: [],
        block_references: [],
        block_hashes: {},
        block_mode: false,
        emit_materials: context[:emit_materials],
        material_prefilter: true,
        skipped_hidden: 0,
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
        context[:skipped_material_faces] += block_context[:skipped_material_faces]
        context[:ray_errors] += block_context[:ray_errors]
        return :occluded
      end

      normalized = normalize_block_geometry(block_context[:lines], block_context[:curves], block_context[:fills])
      return :fallback unless normalized

      geometry_hash = block_geometry_hash(normalized[:lines], normalized[:curves], normalized[:fills])
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
           lines: normalized[:lines],
           curves: normalized[:curves],
           fills: normalized[:fills]
        }
        context[:block_hashes][geometry_hash] = block_name
      end

      context[:block_references] << {
        block: block_name,
           insert: normalized[:insert],
           depth: normalized[:depth],
        layer: "BLOCK_#{source_name}",
        sourceId: instance.persistent_id,
        sourceName: instance.name.to_s.empty? ? instance.definition.name.to_s : instance.name.to_s
      }
      context[:section_lines] += block_context[:section_lines]
      context[:silhouette_edges] += block_context[:silhouette_edges]
      context[:skipped_hidden] += block_context[:skipped_hidden]
      context[:skipped_occluded] += block_context[:skipped_occluded]
      context[:skipped_material_faces] += block_context[:skipped_material_faces]
      context[:material_faces] += block_context[:material_faces]
      context[:ray_errors] += block_context[:ray_errors]
      :emitted
    rescue StandardError
      :fallback
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
        [line[:layer], [line[:start].first(2), line[:end].first(2)].sort]
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

      tag = outer_tag || tag_name(edge) || 'Untagged'
      curve = edge.curve
      if curve
        key = [entities.object_id, curve.object_id, transform.to_a.map { |value| value.round(8) }]
        return if context[:processed_curves][key]

        curve_edges = curve.respond_to?(:edges) ? curve.edges : [edge]
        context[:processed_curves][key] = true
        visible_curve_edges = curve_edges.select do |item|
          entity_visible?(item) && display_edge?(item, transform, context)
        end
        return if visible_curve_edges.empty?

        if visible_curve_edges.length != curve_edges.length
          visible_curve_edges.each { |item| emit_straight_edge(item, transform, tag, context) }
          return
        end

        points = curve.vertices.map { |vertex| vertex.position.transform(transform) }
        closed = curve_closed?(curve)
        curve_type = curve.respond_to?(:typename) ? curve.typename.to_s : curve.class.name.split('::').last
        points << points.first if closed && !close_points?(points.first, points.last)
        runs = clip_polyline_to_section(points, context[:section])
        runs.each { |run| emit_curve(run, closed && runs.length == 1, tag, context, curve_type) }
      else
        return unless display_edge?(edge, transform, context)
        emit_straight_edge(edge, transform, tag, context)
      end
    end

    def emit_straight_edge(edge, transform, tag, context)
      start_point = edge.start.position.transform(transform)
      end_point = edge.end.position.transform(transform)
      section_pair = clip_segment_to_section(start_point, end_point, context[:section])
      return unless section_pair

      fragments = clip_visible_segment(section_pair[0], section_pair[1], context)
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

        context[:lines] << { start: clipped[0], end: clipped[1], layer: tag }
      end
    end

    def display_edge?(edge, transform, context)
      faces = edge.faces.to_a
      return true if faces.length < 2

      normals = faces.map { |face| world_face_normal(face, transform) }
      dots = normals.map { |normal| normal.dot(context[:basis][:forward]) }
      silhouette = dots.min < -1.0e-5 && dots.max > 1.0e-5
      context[:silhouette_edges] += 1 if silhouette
      return silhouette if edge.soft? || edge.smooth?

      same_side = dots.all? { |dot| dot >= 0 } || dots.all? { |dot| dot <= 0 }
      alignment = normals[0].dot(normals[1]).abs
      same_material = face_material_key(faces[0], dots[0]) == face_material_key(faces[1], dots[1])
      return false if same_side && same_material && alignment >= Math.cos(15.0 * Math::PI / 180.0)

      true
    rescue StandardError
      false
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
      if context[:material_prefilter] && projected_area < MATERIAL_PREFILTER_AREA_MM2
        sample_count = projected_area >= 10_000.0 ? 3 : 1
        samples = face_visibility_samples(face, transform, sample_count, projected_area >= 100_000.0)
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
        sourceLayer: outer_tag || tag_name(face) || 'Untagged',
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

          context[:lines] << { start: clipped[0], end: clipped[1], layer: 'SUCAD-SECTION', section: true }
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
        fragments = clip_visible_segment(start_point, end_point, context)
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
      return [start_point, end_point] unless viewport

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

      [
        interpolate_projected(start_point, end_point, lower),
        interpolate_projected(start_point, end_point, upper)
      ]
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

      length = start_point.distance(end_point)
      intervals = [[(length * MM_PER_INCH / VISIBILITY_SAMPLE_MM).ceil, 2].max, MAX_VISIBILITY_INTERVALS].min
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
      MAX_CLIP_DEPTH.times do
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

      key = point.to_a.map { |value| (value.to_f * 1000).round }
      cached = context[:visibility_cache][key]
      return cached unless cached.nil?

      direction = context[:basis][:forward]
      origin = ray_origin(point, direction, context)
      hit = context[:model].raytest([origin, direction], true)
      visible = hit.nil? || hit[0].distance(point) <= VISIBILITY_TOLERANCE_INCH
      context[:visibility_cache][key] = visible
    rescue StandardError
      context[:ray_errors] += 1
      false
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
