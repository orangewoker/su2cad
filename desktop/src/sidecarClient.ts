import { Command, type Child } from "@tauri-apps/plugin-shell";
import type { SidecarEvent } from "./types";

type Listener = (event: SidecarEvent) => void;

class SidecarClient {
  private child: Child | null = null;
  private starting: Promise<void> | null = null;
  private listeners = new Set<Listener>();
  private buffer = "";

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private publish(event: SidecarEvent): void {
    this.listeners.forEach((listener) => listener(event));
  }

  async start(): Promise<void> {
    if (this.child) return;
    if (this.starting) return this.starting;
    this.starting = this.spawn();
    try {
      await this.starting;
    } finally {
      this.starting = null;
    }
  }

  private async spawn(): Promise<void> {
    const command = Command.sidecar("binaries/su2cad-core");
    command.stdout.on("data", (chunk) => this.consume(chunk));
    command.stderr.on("data", (chunk) => {
      const detail = String(chunk).trim();
      if (detail) this.publish({ type: "sidecarStderr", details: detail });
    });
    command.on("error", (error) => {
      this.child = null;
      this.publish({ type: "sidecarError", message: String(error) });
    });
    command.on("close", ({ code }) => {
      this.child = null;
      this.publish({ type: "sidecarClosed", message: `核心服务已退出（${code ?? "-"}）` });
    });
    this.child = await command.spawn();
  }

  private consume(chunk: string): void {
    this.buffer += chunk.replace(/\r\n/g, "\n");
    let newline = this.buffer.indexOf("\n");
    while (newline >= 0) {
      const line = this.buffer.slice(0, newline).trim();
      this.buffer = this.buffer.slice(newline + 1);
      if (line) {
        try {
          this.publish(JSON.parse(line) as SidecarEvent);
        } catch {
          this.publish({ type: "sidecarStderr", details: line });
        }
      }
      newline = this.buffer.indexOf("\n");
    }
  }

  async send(command: string, payload: Record<string, unknown> = {}): Promise<string> {
    await this.start();
    if (!this.child) throw new Error("核心服务未启动");
    const requestId = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    await this.child.write(`${JSON.stringify({ command, requestId, ...payload })}\n`);
    return requestId;
  }

  async stop(): Promise<void> {
    if (!this.child) return;
    try {
      await this.send("shutdown");
    } catch {
      await this.child.kill();
    }
    this.child = null;
  }
}

export const sidecar = new SidecarClient();
