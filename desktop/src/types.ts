export type Quality = "light" | "balanced" | "precise";

export interface RecentOutput {
  path: string;
  time: string;
  layout: string;
  size: string;
}

export interface ExportSettings {
  output_directory: string;
  paper_size: string;
  max_block_lines: number;
  quality: Quality;
  dimensions: boolean;
  occlusion: boolean;
  material_fills: boolean;
  strict_section_occlusion: boolean;
  open_in_cad: boolean;
  recent: RecentOutput[];
}

export interface ExportResult {
  dxf: string;
  linework_json: string;
  model_path: string;
  model_title: string;
  paper_size: string;
  orientation: string;
  layout: string;
  scale: string;
  block_references: number;
  plant_block_references: number;
  material_hatches: number;
  material_count: number;
  simplified_blocks: number;
  block_lines_before: number;
  block_lines_after: number;
  audit_errors: number;
  opened_in_cad: boolean;
  warnings: string[];
  elapsed_seconds: number;
  full_fidelity_blocks: number;
  optimized_dense_blocks: number;
  occluded_blocks: number;
  unique_entities: number;
  expanded_entities: number;
  planned_entities: number;
  time_budget_skipped: number;
  time_budget_omitted: number;
  file_size: number;
}

export interface SketchUpIntegration {
  version: string;
  executable: string;
  pluginDirectory: string;
  pluginInstalled: boolean;
  nativePluginInstalled: boolean;
}

export interface CadIntegration {
  version: string;
  name: string;
  executable: string;
}

export interface Integrations {
  sketchup: SketchUpIntegration[];
  cad: CadIntegration[];
  cadPluginInstalled: boolean;
  cadPlugin: Record<string, unknown>;
}

export interface SidecarEvent {
  type: string;
  requestId?: string | null;
  version?: string;
  progress?: number;
  message?: string;
  elapsed?: number;
  result?: ExportResult;
  settings?: Partial<ExportSettings>;
  health?: Record<string, unknown>;
  cadRunning?: boolean;
  sketchupRunning?: boolean;
  exporting?: boolean;
  integrations?: Integrations;
  restartRequired?: boolean;
  sketchupVersions?: string[];
  cadBundle?: string;
  details?: string;
  path?: string;
  deleted?: boolean;
}

export const DEFAULT_SETTINGS: ExportSettings = {
  output_directory: "",
  paper_size: "AUTO",
  max_block_lines: 2500,
  quality: "balanced",
  dimensions: true,
  occlusion: true,
  material_fills: true,
  strict_section_occlusion: true,
  open_in_cad: true,
  recent: [],
};
