/**
 * Scripted Agent turns for SHOWCASE_MODE (no FastAPI / VLM).
 * Photos are EXIF-stripped keepers from the 2026-04-16 session.
 *
 * Three-step Studio ladder only:
 * 1) select top-10 by score
 * 2) film / dreamcore style grades
 * 3) find guitarist — returns subject=guitarist frames only
 */
import agentDemoManifest from "@/fixtures/agent-showcase-manifest.json";
import type { AgentChatResponse, AgentToolCall } from "@/components/agent/agentChat";

export type AgentDemoFrame = {
  id: string;
  file: string;
  src_file: string;
  path: string;
  overall_score: number;
  category: string;
  orient: string;
  subject?: string;
};

export type ShowcaseFilmVibe = {
  film_variant: string;
  label_zh: string;
  /** CSS class applied in ShowcasePreviewModal */
  grade_class: string;
  prompt: string;
};

type ScriptedIntent = {
  id: string;
  /** Match if any keyword appears in the user message (lowercase). */
  keywords: string[];
  reply: string;
  /** 1-based frame indices into the demo pool (stable across deploys). */
  frameIds: number[];
  filmVibe?: ShowcaseFilmVibe;
};

const SESSION_LABEL = "2026-04-16 · Session 35";

const FRAMES: AgentDemoFrame[] = (agentDemoManifest.frames ?? []) as AgentDemoFrame[];

function frameByIndex(n: number): AgentDemoFrame | null {
  return FRAMES[n - 1] ?? null;
}

function pickFrames(ids: number[]): AgentDemoFrame[] {
  return ids.map(frameByIndex).filter((f): f is AgentDemoFrame => Boolean(f));
}

/** Hand-verified guitarist keepers only (see manifest `subject`). */
function guitaristFrameIds(): number[] {
  const ids: number[] = [];
  FRAMES.forEach((f, i) => {
    if (f.subject === "guitarist") ids.push(i + 1);
  });
  return ids;
}

function filmReply(label: string, hint: string): string {
  return (
    `已套用「${label}」修图风格（Showcase CSS 模拟，${SESSION_LABEL}）。\n` +
    `${hint}\n` +
    `点「打开风格预览」全屏对比；下一步可以找出吉他手特写。`
  );
}

const GUITAR_IDS = guitaristFrameIds();

const INTENTS: ScriptedIntent[] = [
  {
    id: "select_delivery",
    keywords: ["得分最高", "最高的 10", "最高的10", "10 张", "10张", "选出", "交片", "初选"],
    reply:
      `这是 ${SESSION_LABEL} 按 overall 从高到低选出的 10 张（Showcase Fixture，池内最高 92.5）。\n` +
      `点「打开预览」浏览；下一步可以试试修成一种胶片 / 梦核风格。`,
    frameIds: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
  },
  {
    id: "guitar",
    keywords: ["吉他"],
    reply:
      `「吉他手弹琴的特写」——只返回已核对过主体为吉他手的 ${GUITAR_IDS.length} 张（${SESSION_LABEL}）。\n` +
      `不含键盘手、鼓手、贝斯特写。点「打开预览」查看。`,
    frameIds: GUITAR_IDS,
  },
  // Specific styles first, then generic「胶片/复古/风格」→ Cinestill (the ladder step).
  {
    id: "vibe_dreamcore",
    keywords: ["梦核", "dreamcore", "梦幻", "liminal", "迷幻", "梦核式"],
    reply: filmReply("梦核式修图", "偏雾面、低对比、粉青洗色 — 和钨丝胶片差一截。"),
    frameIds: [1, 3, 6, 8, 9, 11],
    filmVibe: {
      film_variant: "dreamcore",
      label_zh: "梦核式修图（Showcase 模拟）",
      grade_class: "showcase-grade-dreamcore",
      prompt: "梦核式修图 / dreamcore",
    },
  },
  {
    id: "vibe_portra",
    keywords: ["portra", "暖调", "柔和肤色", "柯达暖"],
    reply: filmReply("Kodak Portra", "暖调低对比、肤色偏粉橘 — 和 Cinestill 冷青相反。"),
    frameIds: [2, 4, 5, 7, 9, 12],
    filmVibe: {
      film_variant: "kodak_portra",
      label_zh: "Kodak Portra（Showcase 模拟）",
      grade_class: "showcase-grade-portra",
      prompt: "Kodak Portra 暖调胶片",
    },
  },
  {
    id: "vibe_superia",
    keywords: ["富士", "superia", "青绿", "翠绿"],
    reply: filmReply("Fuji Superia", "青绿偏色、饱和度拉高 — 舞台绿光会更「假日感」。"),
    frameIds: [1, 3, 6, 8, 10, 11],
    filmVibe: {
      film_variant: "fuji_superia",
      label_zh: "Fuji Superia（Showcase 模拟）",
      grade_class: "showcase-grade-superia",
      prompt: "Fuji Superia 青绿胶片",
    },
  },
  {
    id: "vibe_hp5",
    keywords: ["hp5", "银盐", "黑白胶片"],
    reply: filmReply("Ilford HP5", "硬调银盐黑白 — 和彩色胶片完全两套观感。"),
    frameIds: [3, 8, 1, 6, 11],
    filmVibe: {
      film_variant: "ilford_hp5",
      label_zh: "Ilford HP5（Showcase 模拟）",
      grade_class: "showcase-grade-hp5",
      prompt: "Ilford HP5 银盐黑白",
    },
  },
  {
    id: "vibe_cinestill",
    keywords: ["cinestill", "800t", "钨丝", "冷青"],
    reply: filmReply("Cinestill 800T", "钨丝冷青 + 品红高光 — 夜场 Livehouse 经典。"),
    frameIds: [1, 2, 4, 6, 9, 12],
    filmVibe: {
      film_variant: "cinestill_800t",
      label_zh: "Cinestill 800T（Showcase 模拟）",
      grade_class: "showcase-grade-cinestill",
      prompt: "复古胶片风 / Cinestill 800T",
    },
  },
  {
    id: "vibe_film_default",
    keywords: ["胶片", "复古", "修图", "风格"],
    reply: filmReply("Cinestill 800T", "Showcase 默认复古胶片（与预设文案一致）。"),
    frameIds: [1, 2, 4, 6, 9, 12],
    filmVibe: {
      film_variant: "cinestill_800t",
      label_zh: "Cinestill 800T（Showcase 模拟）",
      grade_class: "showcase-grade-cinestill",
      prompt: "复古胶片风 / Cinestill 800T",
    },
  },
  {
    id: "export",
    keywords: ["导出", "打包", "raw"],
    reply:
      `导出在只读 Showcase 里不可用（无后端卷）。\n` +
      `完整打包请本地 ./start_all.sh。`,
    frameIds: [],
  },
];

const FALLBACK: ScriptedIntent = {
  id: "fallback",
  keywords: [],
  reply:
    `这是只读 Showcase：Agent 不会真的调 VLM。\n` +
    `请按三步预设走：选出得分最高的 10 张 → 试试一种修图风格 → 找出吉他手弹琴的特写。\n` +
    `完整对话与检索请本地 ./start_all.sh。`,
  frameIds: [],
};

function matchIntent(message: string): ScriptedIntent {
  const q = message.trim().toLowerCase();
  if (!q) return FALLBACK;
  for (const intent of INTENTS) {
    if (intent.keywords.some((k) => q.includes(k.toLowerCase()))) return intent;
  }
  return FALLBACK;
}

function frameMeta(frames: AgentDemoFrame[]) {
  return {
    showcase: true,
    session_date: agentDemoManifest.session_date,
    session_key: agentDemoManifest.session_key,
    files: frames.map((f) => f.file),
    paths: frames.map((f) => f.path),
    scores: Object.fromEntries(frames.map((f) => [f.file, f.overall_score])),
  };
}

function searchToolCall(frames: AgentDemoFrame[], query: string): AgentToolCall {
  return {
    tool: "gallery_search",
    args: { query, limit: frames.length, mode: "showcase_fixture" },
    ok: true,
    metadata: {
      ui_action: "search",
      ...frameMeta(frames),
    },
  };
}

function filmVibeToolCall(frames: AgentDemoFrame[], vibe: ShowcaseFilmVibe): AgentToolCall {
  return {
    tool: "apply_film_vibe",
    args: { film_variant: vibe.film_variant, label_zh: vibe.label_zh },
    ok: true,
    metadata: {
      ui_action: "reload_vibe",
      ...frameMeta(frames),
      session_vibe: {
        film_variant: vibe.film_variant,
        label_zh: vibe.label_zh,
        prompt: vibe.prompt,
        grade_class: vibe.grade_class,
        matched: true,
      },
    },
  };
}

/** Build a non-streaming Agent chat payload for showcase mode. */
export function buildShowcaseAgentReply(message: string): AgentChatResponse {
  const intent = matchIntent(message);
  const frames = pickFrames(intent.frameIds);
  const tool_calls: AgentToolCall[] = [];
  if (frames.length) {
    tool_calls.push(searchToolCall(frames, message.trim() || intent.id));
    if (intent.filmVibe) {
      tool_calls.push(filmVibeToolCall(frames, intent.filmVibe));
    }
  }
  return {
    reply: intent.reply,
    tool_calls,
    guardrail_events: [],
    memory_turns: 0,
    base_dir: "/showcase/agent-demo",
    error: null,
  };
}

export function showcaseAgentSessionMeta() {
  return {
    session_date: agentDemoManifest.session_date as string,
    session_key: agentDemoManifest.session_key as string,
    band_name: (agentDemoManifest as { band_name?: string }).band_name,
    frame_count: FRAMES.length,
  };
}
