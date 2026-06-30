import { execFile } from "node:child_process";
import path from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

function repoRoot(): string {
  // ``next dev`` cwd is usually ``web/``; repo root is one level up.
  const fromWeb = path.resolve(process.cwd(), "..");
  if (path.basename(process.cwd()) === "web") {
    return fromWeb;
  }
  return process.cwd();
}

function pythonBin(): string {
  return process.env.LIVEHOUSE_PYTHON || process.env.PYTHON || "python3";
}

export async function runStudioCli<T>(
  cmd:
    | "sessions"
    | "status"
    | "featured-frames"
    | "set-active"
    | "ingest-config-get"
    | "ingest-config-put"
    | "stats"
    | "landing-gallery"
    | "landing-brain"
    | "landing-infra"
    | "infra-overview",
  args: string[] = [],
): Promise<T> {
  const root = repoRoot();
  const script = path.join(root, "scripts", "studio_cli.py");
  const { stdout, stderr } = await execFileAsync(pythonBin(), [script, cmd, ...args], {
    cwd: root,
    env: {
      ...process.env,
      PYTHONPATH: root,
    },
    maxBuffer: 8 * 1024 * 1024,
    timeout: 60_000,
  });
  if (stderr?.trim()) {
    // exiftool-style warnings on stderr are ok; only fail on empty stdout
  }
  const text = stdout.trim();
  if (!text) {
    throw new Error(`studio_cli ${cmd} produced no output`);
  }
  const parsed = JSON.parse(text) as T & { error?: string };
  if (parsed && typeof parsed === "object" && "error" in parsed && parsed.error) {
    throw new Error(String(parsed.error));
  }
  return parsed;
}

export function galleryApiOrigin(): string {
  return (process.env.GALLERY_API_ORIGIN || "http://127.0.0.1:8080").replace(/\/$/, "");
}
