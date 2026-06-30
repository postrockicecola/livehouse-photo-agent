"use client";

type Props = {
  activeDir: string | null;
  loadSource: "results_api" | "analysis_json" | "none";
  apiError: string | null;
  onRetry: () => void;
};

export function GalleryEmptyState({ activeDir, loadSource, apiError, onRetry }: Props) {
  return (
    <div className="mx-auto flex max-w-lg flex-col gap-4 px-6 py-16 text-center">
      <p className="text-[13px] leading-relaxed text-white/55">
        当前没有可展示的条目。Lab 依赖 FastAPI 的{" "}
        <code className="rounded bg-white/[0.06] px-1 py-0.5 font-mono text-[11px] text-white/70">
          /api/gallery/results
        </code>{" "}
        或同源的{" "}
        <code className="rounded bg-white/[0.06] px-1 py-0.5 font-mono text-[11px] text-white/70">
          /analysis_results.json
        </code>
        。
      </p>
      {activeDir ? (
        <p className="break-all font-mono text-[11px] leading-relaxed text-white/35" title={activeDir}>
          active_base_dir: {activeDir}
        </p>
      ) : null}
      {apiError ? (
        <p className="rounded border border-rose-500/20 bg-rose-500/[0.07] px-3 py-2 text-left font-mono text-[11px] text-rose-200/90">
          {apiError}
        </p>
      ) : null}
      <ul className="list-inside list-disc space-y-1.5 text-left text-[11px] leading-relaxed text-white/40">
        <li>确认已启动 <span className="text-white/55">gallery_server</span>（默认 8080），且 Next 的 <span className="text-white/55">GALLERY_API_ORIGIN</span> 指向同一端口。</li>
        <li>
          在 Previews 下需有{" "}
          <code className="text-white/50">analysis_results.json</code>（或 AI_Best / AI_Keep / AI_Trash 等子目录里的
          JPG），并已跑过管线生成结果。
        </li>
        <li>若刚改过代码，请重启 FastAPI 与 <span className="text-white/55">pnpm dev</span>（Next 重写才会带上新路由）。</li>
      </ul>
      <p className="text-[10px] uppercase tracking-wider text-white/25">
        last path: {loadSource}
      </p>
      <button
        type="button"
        onClick={onRetry}
        className="mx-auto rounded-md border border-white/[0.08] bg-white/[0.04] px-4 py-2 text-[12px] text-white/70 transition-colors hover:bg-white/[0.07] hover:text-white/90"
      >
        重新加载
      </button>
    </div>
  );
}
