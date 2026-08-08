require 'sketchup.rb'
require 'extensions.rb'

module SU2CADBridgeLoader
  unless file_loaded?(__FILE__)
    extension = SketchupExtension.new(
      'SU2CAD Bridge',
      File.join('su2cad_bridge', 'main')
    )
    extension.description = 'Local bridge used by SU2CAD to read the active SketchUp view.'
    extension.version = '0.8.2'
    extension.creator = 'SU2CAD'
    extension.copyright = '2026 SU2CAD'
    Sketchup.register_extension(extension, true)
    file_loaded(__FILE__)
  end
end
