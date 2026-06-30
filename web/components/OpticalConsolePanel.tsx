"use client";

import {
  OPTICAL_PRESETS,
  OPTICAL_SLIDERS,
  type OpticalConsoleState,
  type OpticalPresetId,
} from "@/lib/opticalConsole";

type Props = {
  value: OpticalConsoleState;
  onChange: (next: OpticalConsoleState) => void;
  /** Flush debounced film-render after slider release or preset jump. */
  onScrubEnd?: () => void;
  /** Film strip selected — optical only applies to film-render previews. */
  filmMode: boolean;
  pendingPreview?: boolean;
};

export function OpticalConsolePanel({
  value,
  onChange,
  onScrubEnd,
  filmMode,
  pendingPreview,
}: Props) {
  const setField = (key: keyof OpticalConsoleState, raw: number) => {
    const n = Math.max(0, Math.min(100, Math.round(raw)));
    onChange({ ...value, [key]: n });
  };

  const applyPreset = (id: OpticalPresetId) => {
    const hit = OPTICAL_PRESETS.find((p) => p.id === id);
    if (hit) {
      onChange({ ...hit.values });
      onScrubEnd?.();
    }
  };

  return (
    <aside
      className="flex min-h-0 flex-col border-l border-white/[0.06] bg-[#0c0c0e]/95"
      aria-label="光呼吸控制台"
    >
      <div className="shrink-0 border-b border-white/[0.05] px-3 py-2.5">
        <p className="text-[11px] font-light tracking-[0.18em] text-white/55">光呼吸</p>
        <p className="mt-0.5 text-[9px] font-light leading-snug text-white/22">
          光学响应 · 非 LUT
        </p>
        {!filmMode ? (
          <p className="mt-2 text-[9px] leading-snug text-amber-200/45">
            选择胶片风格后生效
          </p>
        ) : null}
        {pendingPreview ? (
          <p className="mt-1 text-[9px] text-white/28" aria-live="polite">
            渲染中…
          </p>
        ) : null}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2">
        <div className="mb-3 flex flex-wrap gap-1">
          {OPTICAL_PRESETS.filter((p) => p.id !== "neutral").map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => applyPreset(p.id)}
              className="rounded-sm border border-white/[0.08] px-1.5 py-0.5 text-[9px] font-light text-white/38 transition-colors hover:border-white/18 hover:text-white/58"
            >
              {p.label}
            </button>
          ))}
          <button
            type="button"
            onClick={() => applyPreset("neutral")}
            className="rounded-sm px-1.5 py-0.5 text-[9px] font-light text-white/22 transition-colors hover:text-white/45"
          >
            重置
          </button>
        </div>

        <div className="space-y-3.5">
          {OPTICAL_SLIDERS.map((def) => {
            const v = value[def.key] as number;
            return (
              <label key={def.key} className="block">
                <div className="mb-1 flex items-baseline justify-between gap-2">
                  <span className="text-[10px] font-light text-white/48">{def.label}</span>
                  <span className="text-[8px] tracking-widest text-white/18">{def.micro}</span>
                </div>
                <input
                  type="range"
                  min={0}
                  max={100}
                  step={1}
                  value={v}
                  disabled={!filmMode}
                  onChange={(e) => setField(def.key, Number(e.target.value))}
                  onPointerUp={() => onScrubEnd?.()}
                  onKeyUp={() => onScrubEnd?.()}
                  className="optical-slider w-full disabled:opacity-30"
                  aria-valuetext={`${def.label} ${v}`}
                />
                <p className="mt-0.5 text-[8px] font-light text-white/16">{def.hint}</p>
              </label>
            );
          })}
        </div>
      </div>
    </aside>
  );
}
