"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { PersonalChrome } from "@/components/personal/PersonalChrome";
import {
  absoluteOutputUrl,
  createPortraitCartoonJob,
  fetchComfyHealth,
  fetchPortraitJob,
  type ComfyHealth,
  type GenerationMode,
} from "@/lib/personalPortraitCartoon";

const PRESETS: { label: string; prompt: string }[] = [
  {
    label: "爵士舞 · 舞蹈室",
    prompt: "full body, dancing jazz in a bright dance studio, mirror wall, wooden floor, dynamic pose, modest dancewear",
  },
  {
    label: "街舞 · 街头",
    prompt: "full body, hip hop street dance, urban background, energetic dynamic pose, modest streetwear",
  },
  {
    label: "芭蕾 · 舞台",
    prompt: "full body, ballet dance on stage, spotlight, graceful pose, theater background, ballet leotard and tights",
  },
  {
    label: "宅舞 · 室内",
    prompt: "full body, idol dance in cozy bedroom, cheerful pose, soft indoor lighting, casual modest outfit",
  },
];

const SERIES_SEED_KEY = "portrait-cartoon-series-seed";

const GENERATION_MODES: {
  id: GenerationMode;
  label: string;
  denoise: number;
  hint: string;
}[] = [
  {
    id: "likeness",
    label: "保脸优先",
    denoise: 0.58,
    hint: "更像上传的肖像，场景变化较小",
  },
  {
    id: "balanced",
    label: "平衡",
    denoise: 0.68,
    hint: "脸与场景折中（推荐先试）",
  },
  {
    id: "scene",
    label: "场景优先",
    denoise: 0.84,
    hint: "有半身/全身参考时用；脸可能不太像",
  },
  {
    id: "face_only",
    label: "仅大头照",
    denoise: 0.9,
    hint: "只有脸时试这个：靠 prompt 生成全身+场景（像不像看运气）",
  },
];

export default function PortraitCartoonPage() {
  const [health, setHealth] = useState<ComfyHealth | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  const [generationMode, setGenerationMode] = useState<GenerationMode>("balanced");
  const [denoise, setDenoise] = useState(0.68);
  const [seriesMode, setSeriesMode] = useState(true);
  const [seriesSeed, setSeriesSeed] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [outputSrc, setOutputSrc] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refreshHealth = useCallback(async () => {
    try {
      const h = await fetchComfyHealth();
      setHealth(h);
    } catch {
      setHealth(null);
    }
  }, []);

  useEffect(() => {
    refreshHealth();
    try {
      const raw = localStorage.getItem(SERIES_SEED_KEY);
      if (raw) {
        const n = Number(raw);
        if (Number.isFinite(n)) setSeriesSeed(Math.trunc(n));
      }
    } catch {
      /* ignore */
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      if (preview) URL.revokeObjectURL(preview);
    };
  }, [refreshHealth, preview]);

  const onPickFile = (f: File | null) => {
    if (preview) URL.revokeObjectURL(preview);
    setFile(f);
    setPreview(f ? URL.createObjectURL(f) : null);
    setOutputSrc(null);
    setError(null);
  };

  const stopPoll = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const startPoll = (jobId: string) => {
    stopPoll();
    pollRef.current = setInterval(async () => {
      try {
        const j = await fetchPortraitJob(jobId);
        setStatus(j.status);
        setMessage(j.message ?? null);
        if (j.status === "succeeded" && j.output_url) {
          setOutputSrc(absoluteOutputUrl(j.output_url));
          if (j.seed != null) {
            setSeriesSeed(j.seed);
            try {
              localStorage.setItem(SERIES_SEED_KEY, String(j.seed));
            } catch {
              /* ignore */
            }
          }
          setBusy(false);
          stopPoll();
        } else if (j.status === "failed") {
          setError(j.error || j.message || "生成失败");
          setBusy(false);
          stopPoll();
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : "轮询失败");
        setBusy(false);
        stopPoll();
      }
    }, 2000);
  };

  const onSubmit = async () => {
    if (!file || !prompt.trim()) {
      setError("请上传肖像并填写描述");
      return;
    }
    if (!health?.ready) {
      setError("本机 ComfyUI 未就绪，请先启动 ComfyUI（默认 http://127.0.0.1:8188）并配置 checkpoint/workflow");
      return;
    }
    setBusy(true);
    setError(null);
    setOutputSrc(null);
    setStatus("queued");
    setMessage("提交中…");
    try {
      const { job_id, seed: jobSeed } = await createPortraitCartoonJob(
        file,
        prompt.trim(),
        denoise,
        seriesMode ? seriesSeed ?? undefined : undefined,
        generationMode,
      );
      if (jobSeed != null) setSeriesSeed(jobSeed);
      setMessage(`任务 ${job_id}${jobSeed != null ? ` · seed ${jobSeed}` : ""}`);
      startPoll(job_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "提交失败");
      setBusy(false);
    }
  };

  return (
    <main className="studio-grain relative flex min-h-screen flex-col px-4 py-10 sm:px-8">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_80%_50%_at_50%_-20%,rgba(56,189,248,0.08),transparent)]" />
      <PersonalChrome title="肖像卡通化" subtitle="本机 ComfyUI" />

      <section className="relative z-10 mx-auto mt-8 w-full max-w-3xl space-y-6">
        <div className="rounded-2xl border border-sky-500/15 bg-[#080a0c]/80 p-5 text-sm text-white/50">
          <p>
            数据仅在本地处理：浏览器 → Luma API → ComfyUI。请在本机安装并启动 ComfyUI，在{" "}
            <code className="text-sky-300/70">configs/comfy/</code> 中配置 checkpoint；换 FLUX 见{" "}
            <code className="text-sky-300/70">GUIDE_FLUX_STEP_BY_STEP.md</code>。
          </p>
          <p className="mt-2 text-xs text-white/40">
            <strong className="font-normal text-white/55">只有脸的照片：</strong>选模式
            <strong className="font-normal text-white/55">「仅大头照」</strong>（重绘约 0.9）+ 英文跳舞预设，会尽量从文字「补」全身和场景，但
            <strong className="font-normal text-white/55">身份与动作不稳定</strong>。若要稳定像本人又在场景里跳舞，需 Comfy 换
            <strong className="font-normal text-white/55">InstantID / Redux</strong>（见进阶指南）。有全身照时用「场景优先」效果更好。
          </p>
          <p className="mt-3 font-mono text-[10px] uppercase tracking-wider text-white/35">
            {health?.ready ? (
              <span className="text-emerald-400/80">ComfyUI 已连接</span>
            ) : (
              <span className="text-amber-300/80">ComfyUI 未就绪 — {health?.comfy_url ?? "—"}</span>
            )}
            {health?.backend ? ` · ${health.backend}` : null}
            {health?.checkpoint_name ? ` · ckpt: ${health.checkpoint_name}` : null}
          </p>
          <button
            type="button"
            onClick={() => refreshHealth()}
            className="mt-3 font-mono text-[10px] uppercase tracking-wider text-sky-400/70 hover:text-sky-300"
          >
            重新检测
          </button>
        </div>

        <div className="grid gap-6 sm:grid-cols-2">
          <div className="rounded-2xl border border-white/10 bg-[#080a0c]/60 p-5">
            <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-white/35">参考肖像</p>
            <label className="mt-3 flex min-h-[12rem] cursor-pointer flex-col items-center justify-center rounded-xl border border-dashed border-white/15 bg-black/30 px-4 py-6 text-center text-xs text-white/40 hover:border-sky-500/30">
              {preview ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={preview} alt="参考" className="max-h-48 rounded-lg object-contain" />
              ) : (
                "点击上传：全身/半身照更易出舞蹈场景；正脸清晰更像本人"
              )}
              <input
                type="file"
                accept="image/*"
                className="hidden"
                onChange={(e) => onPickFile(e.target.files?.[0] ?? null)}
              />
            </label>
          </div>

          <div className="rounded-2xl border border-white/10 bg-[#080a0c]/60 p-5">
            <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-white/35">生成结果</p>
            <div className="mt-3 flex min-h-[12rem] items-center justify-center rounded-xl border border-white/10 bg-black/40">
              {outputSrc ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={outputSrc} alt="生成" className="max-h-48 rounded-lg object-contain" />
              ) : (
                <span className="text-xs text-white/30">{busy ? "生成中…" : "等待生成"}</span>
              )}
            </div>
            {status ? (
              <p className="mt-2 font-mono text-[10px] text-white/35">
                {status}
                {message ? ` · ${message}` : null}
              </p>
            ) : null}
          </div>
        </div>

        <div className="rounded-2xl border border-white/10 bg-[#080a0c]/60 p-5">
          <label className="font-mono text-[10px] uppercase tracking-[0.2em] text-white/35">场景 / 动作（英文）</label>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={3}
            placeholder="e.g. full body, dancing jazz in a dance studio, mirror wall, dynamic pose"
            className="mt-2 w-full resize-y rounded-xl border border-white/10 bg-black/40 px-3 py-2 text-sm text-white/85 placeholder:text-white/25"
          />
          <div className="mt-3 flex flex-wrap gap-2">
            {PRESETS.map((p) => (
              <button
                key={p.label}
                type="button"
                onClick={() => setPrompt(p.prompt)}
                className="rounded-full border border-white/10 px-3 py-1 text-[11px] text-white/45 hover:border-sky-500/30 hover:text-sky-200/80"
              >
                {p.label}
              </button>
            ))}
          </div>
          <div className="mt-4">
            <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-white/35">生成模式</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {GENERATION_MODES.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  onClick={() => {
                    setGenerationMode(m.id);
                    setDenoise(m.denoise);
                  }}
                  className={`rounded-full border px-3 py-1 text-[11px] transition ${
                    generationMode === m.id
                      ? "border-sky-500/50 bg-sky-950/50 text-sky-200/90"
                      : "border-white/10 text-white/45 hover:border-sky-500/30 hover:text-sky-200/80"
                  }`}
                >
                  {m.label}
                </button>
              ))}
            </div>
            <p className="mt-2 text-[11px] text-white/35">
              {GENERATION_MODES.find((m) => m.id === generationMode)?.hint}
            </p>
          </div>
          <label className="mt-4 flex items-center gap-3 text-xs text-white/45">
            <span className="font-mono text-[10px] uppercase tracking-wider">重绘强度 {denoise.toFixed(2)}</span>
            <input
              type="range"
              min={0.35}
              max={0.90}
              step={0.01}
              value={denoise}
              onChange={(e) => setDenoise(Number(e.target.value))}
              className="flex-1"
            />
          </label>
          <label className="mt-3 flex cursor-pointer items-center gap-2 text-xs text-white/45">
            <input
              type="checkbox"
              checked={seriesMode}
              onChange={(e) => setSeriesMode(e.target.checked)}
              className="rounded border-white/20"
            />
            <span>
              系列模式（固定 seed
              {seriesSeed != null ? (
                <span className="font-mono text-sky-300/70"> {seriesSeed}</span>
              ) : (
                "，首张成功后自动锁定"
              )}
              ）
            </span>
          </label>
          {seriesMode && seriesSeed != null ? (
            <button
              type="button"
              onClick={() => {
                setSeriesSeed(null);
                try {
                  localStorage.removeItem(SERIES_SEED_KEY);
                } catch {
                  /* ignore */
                }
              }}
              className="mt-2 font-mono text-[10px] uppercase tracking-wider text-white/35 hover:text-white/55"
            >
              清除 seed，开始新系列
            </button>
          ) : null}
        </div>

        {error ? <p className="text-sm text-rose-400/90">{error}</p> : null}

        <button
          type="button"
          disabled={busy}
          onClick={onSubmit}
          className="w-full rounded-full border border-sky-500/40 bg-sky-950/40 py-3 font-mono text-[11px] uppercase tracking-[0.22em] text-sky-100/90 transition hover:border-sky-400/60 disabled:opacity-40"
        >
          {busy ? "生成中…" : "本机生成"}
        </button>
      </section>
    </main>
  );
}
