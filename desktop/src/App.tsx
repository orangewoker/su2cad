import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { getCurrentWindow } from "@tauri-apps/api/window";
import {
  Box,
  ChevronDown,
  Clock3,
  FileClock,
  FileOutput,
  FolderOpen,
  Layers3,
  Minus,
  PanelLeftClose,
  PanelLeftOpen,
  Play,
  Puzzle,
  RefreshCw,
  RotateCcw,
  Sparkles,
  Square,
  Trash2,
  X,
} from "lucide-react";
import "./App.css";
import { sidecar } from "./sidecarClient";
import {
  DEFAULT_SETTINGS,
  type ExportResult,
  type ExportSettings,
  type Integrations,
  type Quality,
  type RecentOutput,
  type SidecarEvent,
} from "./types";

type TaskState = "idle" | "running" | "success" | "error" | "cancelled";
type ConfirmAction = { kind: "delete"; item: RecentOutput } | { kind: "clear" } | { kind: "quit" } | null;

const qualityLines: Record<Quality, number> = { light: 800, balanced: 2500, precise: 5000 };
const qualityLabel: Record<Quality, string> = { light: "轻量", balanced: "平衡", precise: "精细" };

function normalizeSettings(raw: Partial<ExportSettings>): ExportSettings {
  const qualityAliases: Record<string, Quality> = {
    light: "light",
    balanced: "balanced",
    precise: "precise",
    "轻量": "light",
    "平衡": "balanced",
    "精细": "precise",
  };
  return {
    ...DEFAULT_SETTINGS,
    ...raw,
    quality: qualityAliases[String(raw.quality || "balanced")] || "balanced",
    max_block_lines: Number(raw.max_block_lines ?? 2500),
    recent: Array.isArray(raw.recent) ? raw.recent.slice(0, 10) : [],
  };
}

function formatElapsed(seconds: number): string {
  const value = Math.max(0, Math.round(seconds));
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const secs = value % 60;
  return hours
    ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`
    : `${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

function formatBytes(bytes: number): string {
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function SwitchRow({
  title,
  description,
  checked,
  onChange,
}: {
  title: string;
  description: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <div className="setting-row">
      <div className="setting-copy">
        <strong>{title}</strong>
        <span>{description}</span>
      </div>
      <button
        type="button"
        className={`liquid-switch ${checked ? "is-on" : ""}`}
        aria-pressed={checked}
        aria-label={title}
        onClick={() => onChange(!checked)}
      >
        <span />
      </button>
    </div>
  );
}

function WindowControls({ onRequestClose }: { onRequestClose: () => void }) {
  return (
    <div className="window-controls">
      <button type="button" aria-label="最小化" onClick={() => void getCurrentWindow().minimize()}><Minus size={16} /></button>
      <button type="button" aria-label="最大化" onClick={() => void getCurrentWindow().toggleMaximize()}><Square size={13} /></button>
      <button type="button" className="window-close" aria-label="关闭" onClick={onRequestClose}><X size={16} /></button>
    </div>
  );
}

function App() {
  const [settings, setSettings] = useState<ExportSettings>(DEFAULT_SETTINGS);
  const [settingsReady, setSettingsReady] = useState(false);
  const [connected, setConnected] = useState(false);
  const [sketchupRunning, setSketchupRunning] = useState(false);
  const [cadRunning, setCadRunning] = useState(false);
  const [modelTitle, setModelTitle] = useState("正在连接 SketchUp…");
  const [taskState, setTaskState] = useState<TaskState>("idle");
  const [progress, setProgress] = useState(0);
  const [phase, setPhase] = useState("等待任务");
  const [result, setResult] = useState<ExportResult | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [logs, setLogs] = useState<string[]>([]);
  const [showLogs, setShowLogs] = useState(false);
  const [activeTab, setActiveTab] = useState<"task" | "recent">("task");
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [advancedOpen, setAdvancedOpen] = useState(true);
  const [confirmAction, setConfirmAction] = useState<ConfirmAction>(null);
  const [serviceError, setServiceError] = useState("");
  const [integrations, setIntegrations] = useState<Integrations | null>(null);
  const [showPlugins, setShowPlugins] = useState(false);
  const [installingPlugins, setInstallingPlugins] = useState(false);
  const [pluginNotice, setPluginNotice] = useState("");
  const startedAt = useRef<number | null>(null);

  const appendLog = useCallback((message: string) => {
    const time = new Date().toLocaleTimeString("zh-CN", { hour12: false });
    setLogs((previous) => [...previous.slice(-299), `[${time}] ${message}`]);
  }, []);

  const saveSettings = useCallback((next: ExportSettings) => {
    void sidecar.send("saveSettings", { settings: next }).catch((error) => setServiceError(String(error)));
  }, []);

  const updateSetting = useCallback(<K extends keyof ExportSettings>(key: K, value: ExportSettings[K]) => {
    setSettings((previous) => ({ ...previous, [key]: value }));
  }, []);

  useEffect(() => {
    const unsubscribe = sidecar.subscribe((event: SidecarEvent) => {
      if (event.type === "ready") {
        setServiceError("");
        appendLog(`SU2CAD ${event.version || "0.8.3"} 核心服务已就绪`);
        void sidecar.send("loadSettings");
        void sidecar.send("status");
        return;
      }
      if (event.type === "settings" && event.settings) {
        setSettings(normalizeSettings(event.settings));
        setSettingsReady(true);
        return;
      }
      if (event.type === "status") {
        const health = event.health || {};
        const running = Boolean(health.ok && health.running);
        setConnected(running);
        setSketchupRunning(Boolean(event.sketchupRunning) || running);
        setCadRunning(Boolean(event.cadRunning));
        if (event.integrations) setIntegrations(event.integrations);
        if (running) {
          const title = String(health.title || "未命名模型");
          const version = String(health.sketchup_version || "");
          setModelTitle(`${title}${version ? `  ·  SketchUp ${version}` : ""}`);
        } else if (event.sketchupRunning) {
          setModelTitle("SketchUp 已打开，正在等待 Bridge 插件连接");
        } else {
          setModelTitle("打开 SketchUp 并启动 SU2CAD / Codex Bridge");
        }
        return;
      }
      if (event.type === "integrations" && event.integrations) {
        setIntegrations(event.integrations);
        return;
      }
      if (event.type === "pluginsInstalled" && event.integrations) {
        setIntegrations(event.integrations);
        setInstallingPlugins(false);
        setPluginNotice(event.message || "插件已安装，重启 SketchUp 和 CAD 后生效。");
        appendLog(event.message || "SketchUp / CAD 插件已安装");
        return;
      }
      if (event.type === "exportStarted") {
        startedAt.current = performance.now();
        setTaskState("running");
        setProgress(2);
        setPhase("正在启动");
        setResult(null);
        setElapsed(0);
        appendLog("开始导出当前 SketchUp 视图");
        setActiveTab("task");
        return;
      }
      if (event.type === "progress") {
        setProgress(Number(event.progress || 0));
        setPhase(event.message || "正在处理");
        setElapsed(Number(event.elapsed || 0));
        if (event.message) appendLog(event.message);
        return;
      }
      if (event.type === "result" && event.result) {
        const output = event.result;
        setTaskState("success");
        setProgress(100);
        setPhase("导出完成");
        setResult(output);
        setElapsed(output.elapsed_seconds);
        startedAt.current = null;
        appendLog(`完成：${output.dxf}`);
        setSettings((previous) => {
          const item: RecentOutput = {
            path: output.dxf,
            time: new Date().toLocaleString("zh-CN", { hour12: false }).replace(/\//g, "-"),
            layout: output.layout,
            size: formatBytes(output.file_size),
          };
          const recent = [item, ...previous.recent.filter((entry) => entry.path !== item.path)].slice(0, 10);
          const next = { ...previous, recent };
          saveSettings(next);
          return next;
        });
        return;
      }
      if (event.type === "cancelled") {
        setTaskState("cancelled");
        setPhase("任务已取消");
        startedAt.current = null;
        appendLog(event.message || "任务已取消");
        return;
      }
      if (event.type === "error") {
        setInstallingPlugins(false);
        setTaskState((current) => current === "running" ? "error" : current);
        setPhase(event.message || "处理失败");
        startedAt.current = null;
        appendLog(`错误：${event.message || "未知错误"}`);
        if (event.details) appendLog(event.details);
        setShowLogs(true);
        setServiceError(event.message || "处理失败");
        return;
      }
      if (event.type === "fileDeleted") {
        const deletedPath = String(event.path || "");
        setSettings((previous) => {
          const next = { ...previous, recent: previous.recent.filter((item) => item.path !== deletedPath) };
          saveSettings(next);
          return next;
        });
        return;
      }
      if (event.type === "sidecarError" || event.type === "sidecarClosed") {
        setServiceError(event.message || "核心服务无法连接");
      }
    });

    if (!("__TAURI_INTERNALS__" in window)) {
      setSettingsReady(true);
      setModelTitle("界面预览模式");
      return unsubscribe;
    }
    void sidecar.start()
      .then(async () => {
        // Do not rely solely on the sidecar's first stdout line: on very fast
        // packaged starts it can arrive before WebView scheduling settles.
        await sidecar.send("loadSettings");
        await sidecar.send("status");
      })
      .catch((error) => setServiceError(String(error)));
    return unsubscribe;
  }, [appendLog, saveSettings]);

  useEffect(() => {
    const statusTimer = window.setInterval(() => {
      if (taskState !== "running") void sidecar.send("status").catch(() => undefined);
    }, connected && cadRunning ? 8000 : 2500);
    return () => window.clearInterval(statusTimer);
  }, [taskState, connected, cadRunning]);

  useEffect(() => {
    if (taskState !== "running") return;
    const elapsedTimer = window.setInterval(() => {
      if (startedAt.current !== null) setElapsed((performance.now() - startedAt.current) / 1000);
    }, 1000);
    return () => window.clearInterval(elapsedTimer);
  }, [taskState]);

  const statusText = useMemo(() => {
    if (taskState === "running") return "处理中";
    if (taskState === "success") return "已完成";
    if (taskState === "error") return "失败";
    if (taskState === "cancelled") return "已取消";
    return connected ? "就绪" : "待连接";
  }, [taskState, connected]);

  const browseOutput = async () => {
    const selected = await open({ directory: true, multiple: false, title: "选择 CAD 输出目录", defaultPath: settings.output_directory || undefined });
    if (typeof selected === "string") updateSetting("output_directory", selected);
  };

  const startExport = async () => {
    if (!settings.output_directory.trim()) {
      setServiceError("请先选择 CAD 输出目录");
      return;
    }
    setServiceError("");
    saveSettings(settings);
    await sidecar.send("export", { settings }).catch((error) => setServiceError(String(error)));
  };

  const selectQuality = (quality: Quality) => {
    setSettings((previous) => ({ ...previous, quality, max_block_lines: qualityLines[quality] }));
  };

  const executeConfirm = async () => {
    if (!confirmAction) return;
    if (confirmAction.kind === "quit") {
      await sidecar.send("cancel").catch(() => undefined);
      await getCurrentWindow().close();
    } else if (confirmAction.kind === "clear") {
      const next = { ...settings, recent: [] };
      setSettings(next);
      saveSettings(next);
    } else {
      await sidecar.send("deleteFile", { path: confirmAction.item.path });
    }
    setConfirmAction(null);
  };

  const requestClose = () => {
    if (taskState === "running") setConfirmAction({ kind: "quit" });
    else void getCurrentWindow().close();
  };

  const handleTitlebarPointerDown = (event: ReactPointerEvent<HTMLElement>) => {
    if (event.button !== 0 || !("__TAURI_INTERNALS__" in window)) return;
    const target = event.target as HTMLElement;
    if (target.closest("button, input, select, textarea, a, [role='button']")) return;
    event.preventDefault();
    if (event.detail >= 2) void getCurrentWindow().toggleMaximize();
    else void getCurrentWindow().startDragging();
  };

  const openPluginManager = () => {
    setPluginNotice("");
    setShowPlugins(true);
    void sidecar.send("detectApplications").catch((error) => setServiceError(String(error)));
  };

  const installAllPlugins = () => {
    setInstallingPlugins(true);
    setPluginNotice("");
    void sidecar.send("installPlugins").catch((error) => {
      setInstallingPlugins(false);
      setServiceError(String(error));
    });
  };

  const resultSummary = result
    ? `${result.material_count} 种材质 · ${result.material_hatches} 个色块 · ${result.block_references} 个块参照 · 审计错误 ${result.audit_errors}`
    : "连接 SketchUp 后即可导出当前视图";

  return (
    <div className="app-shell">
      <div className="ambient ambient-one" />
      <div className="ambient ambient-two" />
      <header className="titlebar" data-tauri-drag-region onPointerDown={handleTitlebarPointerDown}>
        <div className="brand" data-tauri-drag-region>
          <div className="brand-mark"><Layers3 size={22} strokeWidth={2.2} /></div>
          <div><strong>SU2CAD</strong><span>VIEW TO DWG</span></div>
        </div>
        <div className="model-identity" data-tauri-drag-region>
          <span>当前模型</span>
          <strong>{modelTitle}</strong>
        </div>
        <div className="connection-cluster">
          <span className={`status-chip ${connected ? "online" : sketchupRunning ? "standby" : "offline"}`}><i />SketchUp {connected ? "已连接" : sketchupRunning ? "已打开 · 插件未连接" : "未运行"}</span>
          <span className={`status-chip ${cadRunning ? "online" : "standby"}`}><i />CAD {cadRunning ? "已运行" : "未运行"}</span>
          <button className="plugin-button" type="button" onClick={openPluginManager}><Puzzle size={16} /><span>插件</span></button>
          <button className="icon-button" type="button" aria-label="刷新连接" onClick={() => void sidecar.send("status")}><RefreshCw size={17} /></button>
        </div>
        <WindowControls onRequestClose={requestClose} />
      </header>

      <main className={`workspace ${sidebarOpen ? "" : "sidebar-collapsed"}`}>
        <aside className="glass-panel settings-panel">
          <div className="panel-heading">
            <div><span className="eyebrow">EXPORT SETUP</span><h2>导出设置</h2></div>
            <button className="icon-button mobile-sidebar" type="button" onClick={() => setSidebarOpen(false)}><PanelLeftClose size={18} /></button>
          </div>
          <div className="settings-scroll">
            <section className="settings-section">
              <label className="field-label" htmlFor="paper">标准图幅</label>
              <div className="select-shell">
                <select id="paper" value={settings.paper_size} onChange={(event) => updateSetting("paper_size", event.target.value)}>
                  <option value="AUTO">AUTO · 智能适配</option>
                  {['A4', 'A3', 'A2', 'A1', 'A0'].map((paper) => <option value={paper} key={paper}>{paper}</option>)}
                </select>
                <ChevronDown size={16} />
              </div>
              <p className="field-note">AUTO 会根据图形比例自动选择纸张和横竖向。</p>
            </section>

            <section className="settings-section switches">
              <SwitchRow title="仅输出可见线" description="过滤被墙体、家具和切割面遮挡的背面线。" checked={settings.occlusion} onChange={(value) => updateSetting("occlusion", value)} />
              <SwitchRow title="材质色块" description="将 SketchUp 可见面材质转为 CAD 实色填充。" checked={settings.material_fills} onChange={(value) => updateSetting("material_fills", value)} />
              <SwitchRow title="生成总尺寸" description="自动标注当前图形的总宽和总高。" checked={settings.dimensions} onChange={(value) => updateSetting("dimensions", value)} />
              <SwitchRow title="完成后打开 CAD" description="输出后自动使用 AutoCAD 或天正打开。" checked={settings.open_in_cad} onChange={(value) => updateSetting("open_in_cad", value)} />
            </section>

            <section className="settings-section">
              <label className="field-label">场景精度</label>
              <div className="segmented quality-control">
                {(["light", "balanced", "precise"] as Quality[]).map((quality) => (
                  <button type="button" key={quality} className={settings.quality === quality ? "active" : ""} onClick={() => selectQuality(quality)}>{qualityLabel[quality]}</button>
                ))}
              </div>
              <p className="field-note">平衡模式优先完整保留简单组件，仅压缩高密度物体。</p>
            </section>

            <section className={`advanced-card ${advancedOpen ? "is-open" : ""}`}>
              <button className="advanced-title" type="button" onClick={() => setAdvancedOpen(!advancedOpen)}><span><Sparkles size={16} />高级设置</span><ChevronDown size={16} /></button>
              {advancedOpen && <div className="advanced-body">
                <label htmlFor="max-lines">块最大线数 <span>限制单个高密度 CAD 块的细节量。</span></label>
                <input id="max-lines" type="number" min={0} value={settings.max_block_lines} onChange={(event) => updateSetting("max_block_lines", Math.max(0, Number(event.target.value)))} />
                <SwitchRow title="切割面严格遮挡" description="启用 SketchUp 切割面时，严格剔除切割面后的线。" checked={settings.strict_section_occlusion} onChange={(value) => updateSetting("strict_section_occlusion", value)} />
              </div>}
            </section>
          </div>
        </aside>

        {!sidebarOpen && <button className="glass-fab sidebar-fab" type="button" onClick={() => setSidebarOpen(true)}><PanelLeftOpen size={18} /><span>设置</span></button>}

        <section className="glass-panel task-panel">
          <nav className="tab-bar">
            <button type="button" className={activeTab === "task" ? "active" : ""} onClick={() => setActiveTab("task")}><Play size={15} />当前任务</button>
            <button type="button" className={activeTab === "recent" ? "active" : ""} onClick={() => setActiveTab("recent")}><FileClock size={15} />最近输出 <span>{settings.recent.length}</span></button>
          </nav>

          {activeTab === "task" ? (
            <div className="task-content">
              <div className={`hero-card state-${taskState}`}>
                <div className="hero-glow" />
                <span className="state-badge"><i />{statusText}</span>
                <div className="hero-title-row">
                  <div>
                    <h1>{result ? `${result.layout}  ${result.scale}` : taskState === "running" ? "正在读取当前视图" : "准备生成 CAD"}</h1>
                    <p>{taskState === "error" ? phase : resultSummary}</p>
                  </div>
                  <div className="hero-orbit"><Box size={27} /><span /></div>
                </div>
                {result && <div className="result-metrics">
                  <span><strong>{result.full_fidelity_blocks}</strong>完整简单对象</span>
                  <span><strong>{result.optimized_dense_blocks}</strong>优化复杂块</span>
                  <span><strong>{result.occluded_blocks}</strong>遮挡剔除</span>
                </div>}
              </div>

              <div className="progress-block">
                <div className="progress-header">
                  <div><span>任务进度</span><strong><Clock3 size={15} />{taskState === "running" ? "已用时" : "总耗时"} {formatElapsed(elapsed)}</strong></div>
                  <span>{phase}</span>
                </div>
                <div className="liquid-progress"><span style={{ width: `${Math.max(1, progress)}%` }}><i /></span></div>
                <div className="entity-summary">
                  {result
                    ? <><span>模型总实体 <strong>{result.unique_entities.toLocaleString()}</strong></span><span>展开估算 <strong>{result.expanded_entities.toLocaleString()}</strong></span><span>当前视图计划 <strong>{result.planned_entities.toLocaleString()}</strong></span></>
                    : <span className="phase-wide">{phase.startsWith("模型总实体") ? phase : "连接后将先统计总实体，再进行分批处理"}</span>}
                </div>
              </div>

              <div className="task-actions">
                <button type="button" className="glass-button" disabled={!result} onClick={() => result && void sidecar.send("openCad", { path: result.dxf })}><FileOutput size={17} />打开 CAD</button>
                <button type="button" className="glass-button" onClick={() => void sidecar.send("openPath", { path: settings.output_directory, createDirectory: true })}><FolderOpen size={17} />打开目录</button>
                {taskState === "running" && <button type="button" className="danger-button" onClick={() => void sidecar.send("cancel")}><X size={16} />取消任务</button>}
              </div>

              <button className="log-toggle" type="button" onClick={() => setShowLogs(!showLogs)}><ChevronDown size={16} className={showLogs ? "rotated" : ""} />{showLogs ? "收起" : "查看"}详细日志</button>
              {showLogs && <pre className="log-viewer">{logs.join("\n") || "暂无日志"}</pre>}
            </div>
          ) : (
            <div className="recent-content">
              <div className="recent-heading"><div><span className="eyebrow">OUTPUT HISTORY</span><h2>最近生成的 CAD</h2></div><button type="button" className="text-button" disabled={!settings.recent.length} onClick={() => setConfirmAction({ kind: "clear" })}><RotateCcw size={15} />清空列表</button></div>
              <div className="recent-list">
                {!settings.recent.length && <div className="empty-state"><FileClock size={34} /><strong>还没有输出记录</strong><span>完成的 DXF 会出现在这里。</span></div>}
                {settings.recent.map((item) => (
                  <article className="recent-item" key={item.path}>
                    <div className="file-icon"><FileOutput size={21} /></div>
                    <div className="recent-copy"><strong>{item.path.split(/[\\/]/).pop()}</strong><span>{item.time} · {item.layout} · {item.size}</span></div>
                    <button type="button" className="glass-button small" onClick={() => void sidecar.send("openCad", { path: item.path })}>打开</button>
                    <button type="button" className="icon-button delete" aria-label="删除文件" onClick={() => setConfirmAction({ kind: "delete", item })}><Trash2 size={17} /></button>
                  </article>
                ))}
              </div>
            </div>
          )}
        </section>
      </main>

      <footer className="floating-dock glass-panel">
        <span className="dock-label"><FolderOpen size={16} />输出目录</span>
        <button type="button" className="path-field" onClick={() => void browseOutput()}><span>{settings.output_directory || "选择输出目录"}</span><ChevronDown size={15} /></button>
        <button type="button" className="browse-button" onClick={() => void browseOutput()}>浏览</button>
        <button type="button" className="primary-action" disabled={!settingsReady || !connected || taskState === "running"} onClick={() => void startExport()}><Sparkles size={18} />生成 CAD</button>
      </footer>

      {serviceError && <div className="toast-error"><X size={16} /><span>{serviceError}</span><button type="button" onClick={() => setServiceError("")}><X size={14} /></button></div>}

      {showPlugins && <div className="modal-backdrop" onMouseDown={() => setShowPlugins(false)}>
        <div className="plugin-modal glass-panel" onMouseDown={(event) => event.stopPropagation()}>
          <div className="plugin-modal-head">
            <div className="plugin-modal-icon"><Puzzle size={22} /></div>
            <div><span className="eyebrow">APPLICATION CONNECTORS</span><h3>SketchUp / CAD 插件</h3></div>
            <button className="icon-button" type="button" aria-label="关闭插件管理" onClick={() => setShowPlugins(false)}><X size={16} /></button>
          </div>
          <p className="plugin-intro">会自动识别本机已安装的 SketchUp 与 AutoCAD 版本，并把连接插件安装到每个用户目录。</p>
          <div className="plugin-columns">
            <section className="plugin-card">
              <div className="plugin-card-title"><strong>SketchUp Bridge</strong><span>{integrations?.sketchup.length || 0} 个版本</span></div>
              <div className="plugin-list">
                {!integrations?.sketchup.length && <div className="plugin-empty">未检测到 SketchUp 安装或用户配置。</div>}
                {integrations?.sketchup.map((item) => <div className="plugin-row" key={`${item.version}-${item.pluginDirectory}`}><div><strong>SketchUp {item.version}</strong><span>{item.pluginDirectory}</span></div><em className={item.nativePluginInstalled ? "installed" : "missing"}>{item.nativePluginInstalled ? "已安装" : "待安装"}</em></div>)}
              </div>
            </section>
            <section className="plugin-card">
              <div className="plugin-card-title"><strong>AutoCAD Helper</strong><span>{integrations?.cad.length || 0} 个版本</span></div>
              <div className="plugin-list">
                {!integrations?.cad.length && <div className="plugin-empty">未检测到 acad.exe，仍可预先安装通用插件。</div>}
                {integrations?.cad.map((item) => <div className="plugin-row" key={item.executable}><div><strong>{item.name || `AutoCAD ${item.version}`}</strong><span>{item.executable}</span></div><em className={integrations.cadPluginInstalled ? "installed" : "missing"}>{integrations.cadPluginInstalled ? "已安装" : "待安装"}</em></div>)}
              </div>
            </section>
          </div>
          {pluginNotice && <div className="plugin-notice">{pluginNotice}</div>}
          <div className="plugin-foot"><p>安装或修复后请重启 SketchUp 和 CAD。分享给别人时也可使用独立 RBZ / Bundle 安装包。</p><div><button type="button" className="glass-button" onClick={() => void sidecar.send("detectApplications")}><RefreshCw size={15} />重新检测</button><button type="button" className="primary-action compact" disabled={installingPlugins} onClick={installAllPlugins}><Puzzle size={16} />{installingPlugins ? "正在安装…" : "一键安装 / 修复"}</button></div></div>
        </div>
      </div>}

      {confirmAction && <div className="modal-backdrop" onMouseDown={() => setConfirmAction(null)}>
        <div className="confirm-modal glass-panel" onMouseDown={(event) => event.stopPropagation()}>
          <div className="modal-icon"><Trash2 size={22} /></div>
          <h3>{confirmAction.kind === "delete" ? "删除输出文件？" : confirmAction.kind === "quit" ? "导出仍在进行" : "清空输出列表？"}</h3>
          <p>{confirmAction.kind === "delete" ? `将同时删除列表记录和磁盘上的 ${confirmAction.item.path.split(/[\\/]/).pop()}。` : confirmAction.kind === "quit" ? "现在退出会先请求取消当前任务。是否继续退出？" : "只会清空最近输出记录，不会删除磁盘上的 DXF 文件。"}</p>
          <div className="modal-actions"><button type="button" className="glass-button" onClick={() => setConfirmAction(null)}>取消</button><button type="button" className={confirmAction.kind === "delete" || confirmAction.kind === "quit" ? "danger-button" : "primary-action compact"} onClick={() => void executeConfirm()}>{confirmAction.kind === "delete" ? "删除文件" : confirmAction.kind === "quit" ? "取消并退出" : "清空列表"}</button></div>
        </div>
      </div>}
    </div>
  );
}

export default App;
