require 'sketchup.rb'
require 'socket'
require 'json'
require 'stringio'
require 'timeout'
require 'fileutils'

module SU2CADBridge
  HOST = '127.0.0.1'
  PORT = 8765
  TOKEN = '8f17a0c4d6b24e8ba99c1465f512a741'
  DEFAULT_TIMEOUT_MS = 30_000
  APP_DATA_DIR = ENV['APPDATA'] || File.join(ENV['USERPROFILE'], 'AppData', 'Roaming')
  WORKSPACE_DIR = File.join(APP_DATA_DIR, 'SU2CAD', 'SketchUpBridge').tr('\\', '/')
  LOG_PATH = File.join(WORKSPACE_DIR, 'bridge.log')
  BRIDGE_DIR = File.join(WORKSPACE_DIR, 'bridge')
  REQUESTS_DIR = File.join(BRIDGE_DIR, 'requests')
  RESPONSES_DIR = File.join(BRIDGE_DIR, 'responses')

  class << self
    def log(message)
      File.open(LOG_PATH, 'a') { |f| f.puts("#{Time.now} #{message}") }
    rescue
      nil
    end

    def start
      return status if running?

      @jobs ||= Queue.new
      FileUtils.mkdir_p(REQUESTS_DIR)
      FileUtils.mkdir_p(RESPONSES_DIR)
      log("starting bridge on #{HOST}:#{PORT}")
      @server = TCPServer.new(HOST, PORT)
      @server_thread = Thread.new { accept_loop }
      @server_thread.abort_on_exception = false
      @timer_id = UI.start_timer(0.05, true) do
        drain_jobs
        poll_file_requests
      end
      puts "[SU2CADBridge] bridge ready at #{BRIDGE_DIR} and http://#{HOST}:#{PORT}"
      log("bridge ready at #{BRIDGE_DIR} and http://#{HOST}:#{PORT}")
      status
    rescue => e
      puts "[SU2CADBridge] failed to start: #{e.class}: #{e.message}"
      log("failed to start #{e.class}: #{e.message}\n#{e.backtrace&.join("\n")}")
      { ok: false, error: "#{e.class}: #{e.message}" }
    end

    def stop
      UI.stop_timer(@timer_id) if @timer_id
      @timer_id = nil
      @server.close if @server && !@server.closed?
      @server = nil
      @server_thread.kill if @server_thread && @server_thread.alive?
      @server_thread = nil
      { ok: true, running: false }
    rescue => e
      { ok: false, error: "#{e.class}: #{e.message}" }
    end

    def running?
      @server && !@server.closed? && @server_thread && @server_thread.alive?
    end

    def status
      model = Sketchup.active_model
      {
        ok: true,
        running: running?,
        bridge_mode: 'file',
        bridge_dir: BRIDGE_DIR,
        sketchup_version: Sketchup.version,
        ruby_version: RUBY_VERSION,
        model_path: model.path,
        title: model.title
      }
    end

    def accept_loop
      loop do
        client = @server.accept
        Thread.new(client) { |socket| handle_client(socket) }
      rescue IOError, Errno::EBADF
        break
      rescue => e
        puts "[SU2CADBridge] accept error: #{e.class}: #{e.message}"
      end
    end

    def poll_file_requests
      Dir.glob(File.join(REQUESTS_DIR, '*.json')).sort.each do |request_path|
        payload = JSON.parse(File.read(request_path))
        File.delete(request_path) rescue nil
        id = payload['id'].to_s
        response_path = File.join(RESPONSES_DIR, "#{id}.json")

        result =
          if secure_compare(payload['token'].to_s, TOKEN)
            dispatch(payload)
          else
            { ok: false, error: 'Unauthorized' }
          end

        temp_path = "#{response_path}.tmp"
        File.write(temp_path, JSON.generate(result))
        File.rename(temp_path, response_path)
      rescue => e
        fallback_id = begin
          payload && payload['id'].to_s
        rescue
          File.basename(request_path, '.json')
        end
        response_path = File.join(RESPONSES_DIR, "#{fallback_id}.json")
        File.write(response_path, JSON.generate({
          ok: false,
          error: "#{e.class}: #{e.message}",
          backtrace: e.backtrace
        }))
        File.delete(request_path) rescue nil
      end
    end

    def handle_client(socket)
      request = read_http_request(socket)
      unless request
        write_http(socket, 400, { ok: false, error: 'Bad request' })
        return
      end

      if request[:method] == 'GET' && request[:path] == '/health'
        write_http(socket, 200, status)
        return
      end

      unless request[:method] == 'POST' && request[:path] == '/command'
        write_http(socket, 404, { ok: false, error: 'Not found' })
        return
      end

      unless secure_compare(request[:headers]['x-codex-sketchup-token'], TOKEN)
        write_http(socket, 401, { ok: false, error: 'Unauthorized' })
        return
      end

      payload = JSON.parse(request[:body].to_s)
      timeout_ms = positive_number(payload['timeout_ms'], DEFAULT_TIMEOUT_MS)
      result = run_on_main_thread(payload, timeout_ms)
      status_code = result[:ok] ? 200 : 500
      write_http(socket, status_code, result)
    rescue JSON::ParserError => e
      write_http(socket, 400, { ok: false, error: "Invalid JSON: #{e.message}" })
    rescue => e
      write_http(socket, 500, { ok: false, error: "#{e.class}: #{e.message}", backtrace: e.backtrace })
    ensure
      socket.close rescue nil
    end

    def read_http_request(socket)
      raw = ''.b
      until raw.include?("\r\n\r\n")
        raw << socket.readpartial(4096)
      end

      head, body = raw.split("\r\n\r\n", 2)
      lines = head.split("\r\n")
      method, path, = lines.shift.to_s.split(' ')
      headers = {}
      lines.each do |line|
        key, value = line.split(':', 2)
        headers[key.downcase] = value.to_s.strip if key
      end

      content_length = headers.fetch('content-length', '0').to_i
      while body.bytesize < content_length
        body << socket.readpartial(content_length - body.bytesize)
      end

      {
        method: method,
        path: path,
        headers: headers,
        body: body.byteslice(0, content_length)
      }
    rescue EOFError
      nil
    end

    def write_http(socket, status_code, payload)
      body = JSON.generate(payload)
      reason = {
        200 => 'OK',
        400 => 'Bad Request',
        401 => 'Unauthorized',
        404 => 'Not Found',
        500 => 'Internal Server Error'
      }[status_code] || 'OK'

      socket.write "HTTP/1.1 #{status_code} #{reason}\r\n"
      socket.write "Content-Type: application/json; charset=utf-8\r\n"
      socket.write "Content-Length: #{body.bytesize}\r\n"
      socket.write "Connection: close\r\n"
      socket.write "\r\n"
      socket.write body
    end

    def secure_compare(left, right)
      return false unless left && right
      return false unless left.bytesize == right.bytesize

      diff = 0
      left.bytes.zip(right.bytes) { |a, b| diff |= a ^ b }
      diff.zero?
    end

    def run_on_main_thread(payload, timeout_ms)
      response_queue = Queue.new
      @jobs << [payload, response_queue]
      Timeout.timeout(timeout_ms / 1000.0) { response_queue.pop }
    rescue Timeout::Error
      { ok: false, error: "Timed out after #{timeout_ms}ms waiting for SketchUp main thread" }
    end

    def drain_jobs
      processed = 0
      while @jobs && !@jobs.empty? && processed < 20
        payload, response_queue = @jobs.pop(true)
        response_queue << dispatch(payload)
        processed += 1
      end
    rescue ThreadError
      nil
    rescue => e
      response_queue << { ok: false, error: "#{e.class}: #{e.message}", backtrace: e.backtrace } if response_queue
    end

    def dispatch(payload)
      command = payload['command'].to_s
      args = payload['args'] || {}

      case command
      when 'ping'
        status
      when 'run_ruby'
        run_ruby(args['code'].to_s, args['file'])
      when 'create_box'
        create_box(args)
      when 'get_model_summary'
        get_model_summary
      when 'clear_model'
        clear_model
      when 'save_model'
        save_model(args)
      when 'open_model'
        open_model(args)
      else
        { ok: false, error: "Unknown command: #{command}" }
      end
    rescue => e
      { ok: false, error: "#{e.class}: #{e.message}", backtrace: e.backtrace }
    end

    def run_ruby(code, file = nil)
      return { ok: false, error: 'Ruby code is empty' } if code.strip.empty?

      old_stdout = $stdout
      old_stderr = $stderr
      stdout = StringIO.new
      stderr = StringIO.new
      $stdout = stdout
      $stderr = stderr

      result = eval(code, TOPLEVEL_BINDING, file || 'codex_sketchup_bridge_eval', 1)

      {
        ok: true,
        result_class: result.class.name,
        result_inspect: safe_inspect(result),
        stdout: stdout.string,
        stderr: stderr.string
      }
    rescue Exception => e
      {
        ok: false,
        error: "#{e.class}: #{e.message}",
        backtrace: e.backtrace,
        stdout: stdout ? stdout.string : '',
        stderr: stderr ? stderr.string : ''
      }
    ensure
      $stdout = old_stdout
      $stderr = old_stderr
    end

    def create_box(args)
      width = positive_number(args['width_mm'], nil)
      depth = positive_number(args['depth_mm'], nil)
      height = positive_number(args['height_mm'], nil)
      return { ok: false, error: 'width_mm, depth_mm, and height_mm must be positive numbers' } unless width && depth && height

      origin = args['origin_mm'].is_a?(Array) ? args['origin_mm'] : [0, 0, 0]
      x = number(origin[0], 0).mm
      y = number(origin[1], 0).mm
      z = number(origin[2], 0).mm

      model = Sketchup.active_model
      model.start_operation('Codex Create Box', true)
      group = model.active_entities.add_group
      group.name = args['name'].to_s unless args['name'].to_s.empty?

      points = [
        Geom::Point3d.new(x, y, z),
        Geom::Point3d.new(x + width.mm, y, z),
        Geom::Point3d.new(x + width.mm, y + depth.mm, z),
        Geom::Point3d.new(x, y + depth.mm, z)
      ]
      face = group.entities.add_face(points)
      face.reverse! if face.normal.z < 0
      face.pushpull(height.mm)

      if args['material'] && !args['material'].to_s.empty?
        material = model.materials[args['material'].to_s] || model.materials.add(args['material'].to_s)
        group.material = material
      end

      model.commit_operation
      {
        ok: true,
        entity_id: group.entityID,
        persistent_id: group.persistent_id,
        name: group.name,
        dimensions_mm: [width, depth, height]
      }
    rescue => e
      model.abort_operation if model
      { ok: false, error: "#{e.class}: #{e.message}", backtrace: e.backtrace }
    end

    def get_model_summary
      model = Sketchup.active_model
      entities = model.entities
      {
        ok: true,
        title: model.title,
        path: model.path,
        modified: model.modified?,
        counts: {
          entities: entities.length,
          faces: entities.grep(Sketchup::Face).length,
          edges: entities.grep(Sketchup::Edge).length,
          groups: entities.grep(Sketchup::Group).length,
          component_instances: entities.grep(Sketchup::ComponentInstance).length,
          materials: model.materials.length,
          layers: model.layers.length
        },
        selection_count: model.selection.length
      }
    end

    def clear_model
      model = Sketchup.active_model
      model.start_operation('Codex Clear Model', true)
      model.entities.erase_entities(model.entities.to_a)
      model.commit_operation
      { ok: true, message: 'Model entities cleared' }
    rescue => e
      model.abort_operation if model
      { ok: false, error: "#{e.class}: #{e.message}", backtrace: e.backtrace }
    end

    def save_model(args)
      model = Sketchup.active_model
      path = args['path'].to_s
      result = path.empty? ? model.save : model.save(path)
      { ok: !!result, path: path.empty? ? model.path : path }
    end

    def open_model(args)
      path = args['path'].to_s
      return { ok: false, error: 'path is required' } if path.empty?

      result = Sketchup.open_file(path)
      { ok: !!result, path: path }
    end

    def positive_number(value, fallback)
      parsed = Float(value)
      parsed.positive? ? parsed : fallback
    rescue
      fallback
    end

    def number(value, fallback)
      Float(value)
    rescue
      fallback
    end

    def safe_inspect(value)
      text = value.inspect
      text.length > 10_000 ? "#{text[0, 10_000]}..." : text
    rescue => e
      "#<inspect failed: #{e.class}: #{e.message}>"
    end
  end

  unless defined?(@loaded) && @loaded
    @loaded = true
    menu = UI.menu('Extensions').add_submenu('SU2CAD Bridge')
    menu.add_item('Start') { start }
    menu.add_item('Stop') { UI.messagebox(JSON.pretty_generate(stop)) }
    menu.add_item('Status') { UI.messagebox(JSON.pretty_generate(status)) }
    start
  end
end

