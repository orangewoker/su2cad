# frozen_string_literal: true

require 'json'
require 'fileutils'
require 'digest'

module SketchupCurrentViewCad
  MM_PER_INCH = 25.4
  VISIBILITY_TOLERANCE_INCH = 2.0 / MM_PER_INCH
  SECTION_TOLERANCE_INCH = 0.5 / MM_PER_INCH
  MIN_FRAGMENT_INCH = 2.0 / MM_PER_INCH
  MAX_CLIP_DEPTH = 7

  class << self
    def export(output_path, occlusion: true, strict_section_occlusion: true)
      model = Sketchup.active_model
      raise 'No active SketchUp model' unless model

      camera = model.active_view.camera
      basis = camera_basis(camera)
      section = active_section(model, camera)
      effective_occlusion = section ? strict_section_occlusion : occlusion
      diagonal = [model.bounds.diagonal.to_f, 1000.0].max
      context = {
        model: model,
        basis: basis,
        section: section,
        ray_distance: diagonal * 3.0,
        occlusion: effective_occlusion,
        visibility_cache: {},
        processed_curves: {},
        lines: [],
        curves: [],
        blocks: [],
        block_references: [],
        block_hashes: {},
        block_mode: true,
        skipped_hidden: 0,
        skipped_occluded: 0,
        section_lines: 0,
        silhouette_edges: 0
      }

      walk_entities(model.entities, Geom::Transformation.new, nil, context)
      payload = {
        format: 'sketchup-current-view-linework',
        version: 1,
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
          projection: 'orthographic'
        },
        section: section && {
          name: section[:name],
          planeMm: section[:plane].map.with_index { |value, index| index == 3 ? (value * MM_PER_INCH).round(4) : value.round(8) }
        },
        lines: context[:lines],
        curves: context[:curves],
        blocks: context[:blocks],
        blockReferences: context[:block_references],
        stats: {
          lineFragments: context[:lines].length,
          curves: context[:curves].length,
          skippedHidden: context[:skipped_hidden],
          skippedOccluded: context[:skipped_occluded],
          sectionLines: context[:section_lines],
          silhouetteEdges: context[:silhouette_edges],
          blocks: context[:blocks].length,
          blockReferences: context[:block_references].length,
          occlusion: effective_occlusion,
          requestedOcclusion: occlusion,
          strictSectionOcclusion: strict_section_occlusion
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

    def walk_entities(entities, transform, outer_tag, context)
      entities.each do |entity|
        unless entity_visible?(entity)
          context[:skipped_hidden] += 1
          next
        end

        case entity
        when Sketchup::Group, Sketchup::ComponentInstance
          child_tag = outer_tag || tag_name(entity)
          world_transform = transform * entity.transformation
          emitted = context[:block_mode] && block_candidate?(entity, world_transform, context) &&
                    emit_instance_block(entity, world_transform, child_tag, context)
          walk_entities(entity.definition.entities, world_transform, child_tag, context) unless emitted
        when Sketchup::Edge
          emit_edge(entity, entities, transform, outer_tag, context)
        when Sketchup::Face
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

    def emit_instance_block(instance, world_transform, outer_tag, context)
      return false unless instance_visible_for_block?(instance, world_transform, context)

      block_context = {
        model: context[:model],
        basis: context[:basis],
        section: context[:section],
        ray_distance: context[:ray_distance],
        occlusion: false,
        visibility_cache: context[:visibility_cache],
        processed_curves: {},
        lines: [],
        curves: [],
        blocks: [],
        block_references: [],
        block_hashes: {},
        block_mode: false,
        skipped_hidden: 0,
        skipped_occluded: 0,
        section_lines: 0,
        silhouette_edges: 0
      }
      walk_entities(instance.definition.entities, world_transform, outer_tag, block_context)
      return false if block_context[:lines].empty? && block_context[:curves].empty?

      normalized = normalize_block_geometry(block_context[:lines], block_context[:curves])
      return false unless normalized

      geometry_hash = block_geometry_hash(normalized[:lines], normalized[:curves])
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
          curves: normalized[:curves]
        }
        context[:block_hashes][geometry_hash] = block_name
      end

      context[:block_references] << {
        block: block_name,
        insert: normalized[:insert],
        layer: "BLOCK_#{source_name}",
        sourceId: instance.persistent_id,
        sourceName: instance.name.to_s.empty? ? instance.definition.name.to_s : instance.name.to_s
      }
      context[:section_lines] += block_context[:section_lines]
      context[:silhouette_edges] += block_context[:silhouette_edges]
      true
    rescue StandardError
      false
    end

    def instance_visible_for_block?(instance, world_transform, context)
      points = bounds_corners(instance.definition.bounds).map { |point| point.transform(world_transform) }
      if context[:section]
        kept = points.select do |point|
          plane_distance(point, context[:section][:plane]) * context[:section][:keep_multiplier] >= -SECTION_TOLERANCE_INCH
        end
        return false if kept.empty?
        points = kept
      end
      return true if instance.is_a?(Sketchup::ComponentInstance) && !instance.is_a?(Sketchup::Group)
      return true unless context[:occlusion]

      center = Geom::Point3d.new(
        points.sum(&:x) / points.length,
        points.sum(&:y) / points.length,
        points.sum(&:z) / points.length
      )
      visible = (points + [center]).any? { |point| visible_point?(point, context) }
      visible || instance.definition.entities.length <= 500
    end

    def normalize_block_geometry(lines, curves)
      points = lines.flat_map { |line| [line[:start], line[:end]] } + curves.flat_map { |curve| curve[:points] }
      return nil if points.empty?

      min_x = points.map { |point| point[0] }.min
      min_y = points.map { |point| point[1] }.min
      shift = lambda do |point|
        [(point[0] - min_x).round(4), (point[1] - min_y).round(4), point[2].to_f.round(4)]
      end
      normalized_lines = lines.map do |line|
        line.merge(start: shift.call(line[:start]), end: shift.call(line[:end]))
      end
      normalized_curves = curves.map do |curve|
        curve.merge(points: curve[:points].map { |point| shift.call(point) })
      end
      { insert: [min_x.round(4), min_y.round(4)], lines: normalized_lines, curves: normalized_curves }
    end

    def block_geometry_hash(lines, curves)
      line_keys = lines.map do |line|
        [line[:layer], [line[:start].first(2), line[:end].first(2)].sort]
      end.sort_by(&:to_s)
      curve_keys = curves.map do |curve|
        [curve[:layer], curve[:curveType], curve[:closed], curve[:points].map { |point| point.first(2) }]
      end.sort_by(&:to_s)
      Digest::SHA1.hexdigest(JSON.generate([line_keys, curve_keys]))
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
        return unless curve_edges.any? { |item| display_edge?(item, transform, context) }

        context[:processed_curves][key] = true
        points = curve.vertices.map { |vertex| vertex.position.transform(transform) }
        closed = curve_closed?(curve)
        curve_type = curve.respond_to?(:typename) ? curve.typename.to_s : curve.class.name.split('::').last
        points << points.first if closed && !close_points?(points.first, points.last)
        runs = clip_polyline_to_section(points, context[:section])
        runs.each { |run| emit_curve(run, closed && runs.length == 1, tag, context, curve_type) }
      else
        return unless display_edge?(edge, transform, context)

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
          next if distance2(a, b) < 0.01

          context[:lines] << { start: a, end: b, layer: tag }
        end
      end
    end

    def display_edge?(edge, transform, context)
      return true unless edge.soft? || edge.smooth?

      faces = edge.faces.to_a
      return true if faces.length < 2

      dots = faces.map { |face| world_face_normal(face, transform).dot(context[:basis][:forward]) }
      silhouette = dots.min < -1.0e-5 && dots.max > 1.0e-5
      context[:silhouette_edges] += 1 if silhouette
      silhouette
    rescue StandardError
      false
    end

    def world_face_normal(face, transform)
      vertices = face.outer_loop.vertices.first(3).map { |vertex| vertex.position.transform(transform) }
      normal = vertices[0].vector_to(vertices[1]).cross(vertices[0].vector_to(vertices[2]))
      normal.normalize!
      normal
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
          context[:lines] << { start: a, end: b, layer: 'SUCAD-SECTION', section: true }
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

      runs.each do |run|
        projected = dedupe_adjacent(run.map { |point| project(point, context[:basis]) })
        next if projected.length < 2

        context[:curves] << {
          points: projected,
          closed: closed && runs.length == 1,
          curveType: curve_type,
          layer: tag,
          partiallyOccluded: context[:occlusion] && runs.length > 1
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

    def clip_visible_segment(start_point, end_point, context, depth = 0)
      return [[start_point, end_point]] unless context[:occlusion]

      midpoint = Geom.linear_combination(0.5, start_point, 0.5, end_point)
      states = [start_point, midpoint, end_point].map { |point| visible_point?(point, context) }
      return [[start_point, end_point]] if states.all?
      return [] if states.none?

      if depth >= MAX_CLIP_DEPTH || start_point.distance(end_point) <= MIN_FRAGMENT_INCH
        output = []
        output << [start_point, midpoint] if states[0] || states[1]
        output << [midpoint, end_point] if states[1] || states[2]
        return output
      end

      clip_visible_segment(start_point, midpoint, context, depth + 1) +
        clip_visible_segment(midpoint, end_point, context, depth + 1)
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
      true
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
