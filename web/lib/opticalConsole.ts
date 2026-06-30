/** Lab single-image optical console (情绪控制台) — maps to ``GET /api/lab/film-render?optical=``. */

export type OpticalConsoleState = {
  air: number;
  halation: number;
  night: number;
  dream: number;
  flow: number;
  time: number;
  wear: number;
  flow_angle: number;
};

export type OpticalSliderDef = {
  key: keyof OpticalConsoleState;
  label: string;
  micro: string;
  hint: string;
};

export const OPTICAL_SLIDERS: OpticalSliderDef[] = [
  { key: "air", label: "空气", micro: "AIR", hint: "高光漫开、湿度感" },
  { key: "halation", label: "燃烧", micro: "HALATION", hint: "红灯乳剂溢光" },
  { key: "night", label: "夜深", micro: "NIGHT", hint: "暗部呼吸、抬黑" },
  { key: "dream", label: "梦境", micro: "DREAM", hint: "镜头边缘柔化" },
  { key: "flow", label: "声波", micro: "FLOW", hint: "慢门光流" },
  { key: "time", label: "时间", micro: "TIME", hint: "银盐颗粒" },
  { key: "wear", label: "磨损", micro: "WEAR", hint: "VHS / 扫描感" },
];

export type OpticalPresetId =
  | "neutral"
  | "livehouse"
  | "shoegaze"
  | "jazz_night"
  | "vhs_memory"
  | "dream_pop";

export type OpticalPreset = {
  id: OpticalPresetId;
  label: string;
  values: OpticalConsoleState;
};

export const OPTICAL_NEUTRAL: OpticalConsoleState = {
  air: 0,
  halation: 0,
  night: 0,
  dream: 0,
  flow: 0,
  time: 0,
  wear: 0,
  flow_angle: -15,
};

export const OPTICAL_PRESETS: OpticalPreset[] = [
  { id: "neutral", label: "重置", values: { ...OPTICAL_NEUTRAL } },
  {
    id: "livehouse",
    label: "Livehouse",
    values: {
      air: 58,
      halation: 52,
      night: 62,
      flow: 38,
      dream: 32,
      time: 55,
      wear: 22,
      flow_angle: -15,
    },
  },
  {
    id: "shoegaze",
    label: "Shoegaze",
    values: {
      air: 72,
      halation: 38,
      night: 68,
      dream: 62,
      flow: 52,
      time: 68,
      wear: 32,
      flow_angle: -12,
    },
  },
  {
    id: "jazz_night",
    label: "Jazz",
    values: {
      air: 42,
      halation: 28,
      night: 52,
      dream: 38,
      flow: 28,
      time: 62,
      wear: 18,
      flow_angle: -18,
    },
  },
  {
    id: "vhs_memory",
    label: "VHS",
    values: {
      air: 55,
      halation: 48,
      night: 72,
      dream: 45,
      flow: 42,
      time: 72,
      wear: 65,
      flow_angle: -10,
    },
  },
  {
    id: "dream_pop",
    label: "Dream Pop",
    values: {
      air: 68,
      halation: 42,
      night: 58,
      dream: 72,
      flow: 35,
      time: 52,
      wear: 28,
      flow_angle: -14,
    },
  },
];

const SLIDER_KEYS: (keyof OpticalConsoleState)[] = [
  "air",
  "halation",
  "night",
  "dream",
  "flow",
  "time",
  "wear",
];

export function opticalStateIsNeutral(state: OpticalConsoleState): boolean {
  return SLIDER_KEYS.every((k) => state[k] <= 0);
}

/** Payload for API — omit zeros and fixed defaults where inactive. */
export function opticalStateToApiPayload(state: OpticalConsoleState): Record<string, number> | null {
  if (opticalStateIsNeutral(state)) return null;
  const out: Record<string, number> = {};
  for (const k of SLIDER_KEYS) {
    const v = Math.round(state[k]);
    if (v > 0) out[k] = v;
  }
  if (state.flow > 0 && state.flow_angle !== -15) {
    out.flow_angle = state.flow_angle;
  }
  return Object.keys(out).length > 0 ? out : null;
}

export function opticalCacheKey(state: OpticalConsoleState): string {
  const p = opticalStateToApiPayload(state);
  if (!p) return "";
  return JSON.stringify(p);
}
