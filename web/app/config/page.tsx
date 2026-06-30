"use client";

import { useCallback, useEffect, useState } from "react";
import {
  fetchIngestConfig,
  saveIngestConfig,
  shortenPath,
  type StudioIngestConfig,
} from "@/lib/studioApi";
import { StudioAppNav } from "@/components/studio/StudioAppNav";

export default function StudioConfigPage() {
  const [loaded, setLoaded] = useState<StudioIngestConfig | null>(null);
  const [monitor, setMonitor] = useState("");
  const [archive, setArchive] = useState("");
  const [sessionName, setSessionName] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    const cfg = await fetchIngestConfig();
    setLoaded(cfg);
    setMonitor(cfg.ingest_monitor_path || "");
    setArchive(cfg.archive_root || "");
    setSessionName(cfg.session_folder_name || "");
    return cfg;
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await load();
        if (!cancelled) setError(null);
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : "加载配置失败");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [load]);

  const onSave = async () => {
    setBusy(true);
    setMessage(null);
    setError(null);
    try {
      const saved = await saveIngestConfig({
        ingest_monitor_path: monitor.trim(),
        archive_root: archive.trim(),
        session_folder_name: sessionName.trim(),
      });
      setLoaded(saved);
      setMonitor(saved.ingest_monitor_path);
      setArchive(saved.archive_root);
      setSessionName(saved.session_folder_name);
      setMessage("已保存");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="studio-grain relative min-h-screen">
      <StudioAppNav />
      <main className="relative px-4 py-8 sm:px-8">
      <header className="relative z-10 mx-auto mb-12 max-w-lg">
        <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-white/35">Settings</p>
        <h1 className="mt-2 text-2xl font-light tracking-tight text-white/90">入库与归档</h1>
        <p className="mt-3 max-w-sm font-mono text-[10px] leading-relaxed text-white/30">
          Go ingest（<code className="text-white/45">--sd-mount</code>）启动时会读取{" "}
          <code className="text-white/45">configs/studio_ingest.json</code>。
        </p>
      </header>

      <section className="relative z-10 mx-auto w-full max-w-lg">
        {loading ? <p className="font-mono text-[11px] text-white/30">loading config…</p> : null}
        {error ? <p className="mb-4 font-mono text-[11px] text-rose-400/90">{error}</p> : null}
        {message ? <p className="mb-4 font-mono text-[10px] text-emerald-400/80">{message}</p> : null}

        {!loading ? (
          <form
            className="space-y-8"
            onSubmit={(e) => {
              e.preventDefault();
              void onSave();
            }}
          >
            <Field
              label="ingest 监控目录"
              hint="SD 卡或 DCIM 路径。留空保存时保留上次填入的值。"
              value={monitor}
              onChange={setMonitor}
              placeholder={loaded?.ingest_monitor_path || "/Volumes/CAMERA_SD/DCIM/100MSDCF"}
            />
            <Field
              label="归档保存根目录"
              hint="RAW / Previews 的父路径（Livehouse_Archive）。留空保存时保留上次值。"
              value={archive}
              onChange={setArchive}
              placeholder={loaded?.archive_root || "/Volumes/…/Livehouse_Archive"}
            />
            <Field
              label="场次文件夹名"
              hint="留空 → 入库时按 ARW 拍摄日期（EXIF CreateDate）命名，如 2026-05-24；可填 2026-05-24_乐队名。"
              value={sessionName}
              onChange={setSessionName}
              placeholder="留空 = 按拍摄日期"
            />

            <div className="border-t border-white/[0.05] pt-6">
              <button
                type="submit"
                disabled={busy}
                className="rounded-[2px] border border-white/[0.12] bg-white/[0.04] px-5 py-2.5 font-mono text-[10px] uppercase tracking-[0.16em] text-white/70 transition-colors hover:bg-white/[0.07] disabled:opacity-40"
              >
                {busy ? "…" : "保存配置"}
              </button>
              {loaded?.config_path ? (
                <p className="mt-4 truncate font-mono text-[8px] text-white/15" title={loaded.config_path}>
                  {shortenPath(loaded.config_path, 64)}
                </p>
              ) : null}
            </div>
          </form>
        ) : null}
      </section>
      </main>
    </div>
  );
}

function Field({
  label,
  hint,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  hint: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <label className="block">
      <span className="font-mono text-[9px] uppercase tracking-[0.2em] text-white/35">{label}</span>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="mt-2 w-full rounded-[2px] border border-white/[0.08] bg-white/[0.03] px-3 py-2.5 font-mono text-[11px] text-white/80 placeholder:text-white/20 focus:border-white/[0.16] focus:outline-none"
        spellCheck={false}
      />
      <span className="mt-2 block font-mono text-[9px] leading-relaxed text-white/25">{hint}</span>
    </label>
  );
}
