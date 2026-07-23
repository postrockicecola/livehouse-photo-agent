/**
 * Scripted Agent turns for SHOWCASE_MODE (no FastAPI / VLM).
 * Photos are EXIF-stripped keepers from the 2026-04-16 session.
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
};

type ScriptedIntent = {
  id: string;
  /** Match if any keyword appears in the user message (lowercase). */
  keywords: string[];
  reply: string;
  /** 1-based frame indices into the demo pool (stable across deploys). */
  frameIds: number[];
};

const SESSION_LABEL = "2026-04-16 · Session 35";

const FRAMES: AgentDemoFrame[] = (agentDemoManifest.frames ?? []) as AgentDemoFrame[];

function frameByIndex(n: number): AgentDemoFrame | null {
  return FRAMES[n - 1] ?? null;
}

function pickFrames(ids: number[]): AgentDemoFrame[] {
  return ids.map(frameByIndex).filter((f): f is AgentDemoFrame => Boolean(f));
}

const INTENTS: ScriptedIntent[] = [
  {
    id: "select_delivery",
    keywords: ["选出", "交片", "初选", "20 张", "20张"],
    reply:
      `这是 ${SESSION_LABEL} 的预录选片结果（Showcase Fixture）。\n` +
      `按 overall 从高到低取了 8 张 keepers（池内最高 92.5）。\n` +
      `线上只读演示：完整 Agent + 全场分析请本地启动 gallery_server。`,
    frameIds: [1, 2, 3, 4, 5, 6, 7, 8],
  },
  {
    id: "reject_quality",
    keywords: ["糊", "过曝", "剔掉", "低质量"],
    reply:
      `已按「先剔技术问题」的脚本跑完 ${SESSION_LABEL}。\n` +
      `从高分 keep 里留了 6 张更干净的帧；糊/过曝类在预录池外已过滤。`,
    frameIds: [1, 3, 6, 8, 2, 4],
  },
  {
    id: "burst",
    keywords: ["连拍", "每组", "只留一张"],
    reply:
      `连拍去重（预录）：${SESSION_LABEL} 每组只留一张代表帧，共 5 张。`,
    frameIds: [1, 3, 5, 7, 9],
  },
  {
    id: "sort_score",
    keywords: ["分数", "从高到低", "排序"],
    reply: `${SESSION_LABEL} 按 overall 排序的前 6 张（预录账本）。`,
    frameIds: [1, 2, 3, 4, 5, 6],
  },
  {
    id: "guitar",
    keywords: ["吉他"],
    reply:
      `「吉他手特写」预录短名单（${SESSION_LABEL}）。\n` +
      `从高分 keepers 里挑了偏近景/竖构图的 5 张，便于你扫一眼。`,
    frameIds: [2, 4, 5, 7, 10],
  },
  {
    id: "wide_stage",
    keywords: ["全景", "舞台", "观众", "灯都在"],
    reply: `全景舞台向的预录帧（${SESSION_LABEL}）— 偏横构图、场面更大的几张。`,
    frameIds: [1, 3, 6, 8],
  },
  {
    id: "drum",
    keywords: ["鼓手", "打鼓"],
    reply: `鼓手相关预录短名单（${SESSION_LABEL}）。点击缩略图可查看。`,
    frameIds: [3, 6, 8, 11],
  },
  {
    id: "singer",
    keywords: ["歌手", "表情"],
    reply: `表情/近景向预录 keepers（${SESSION_LABEL}）。`,
    frameIds: [2, 4, 5, 9, 12],
  },
  {
    id: "front_row",
    keywords: ["前排", "气氛", "互动"],
    reply: `前排气氛向预录选片（${SESSION_LABEL}）。`,
    frameIds: [1, 6, 8, 3, 11],
  },
  {
    id: "silhouette",
    keywords: ["逆光", "剪影"],
    reply: `逆光/剪影向预录帧（${SESSION_LABEL}）。`,
    frameIds: [8, 3, 6, 1],
  },
  {
    id: "film",
    keywords: ["胶片", "cinestill", "复古", "800t"],
    reply:
      `胶片风预览在线上是 Showcase Fixture：先给出 ${SESSION_LABEL} 的 6 张底片候选。\n` +
      `完整 Cinestill / 光学预览请在本地 Gallery 跑 vibe 工具。`,
    frameIds: [1, 2, 4, 6, 9, 12],
  },
  {
    id: "bw",
    keywords: ["黑白", "纪实"],
    reply: `黑白/纪实向预录短名单（${SESSION_LABEL}）。`,
    frameIds: [3, 8, 1, 6, 11],
  },
  {
    id: "export",
    keywords: ["导出", "打包", "raw"],
    reply:
      `导出在只读 Showcase 里不可用（无后端卷）。\n` +
      `这是 ${SESSION_LABEL} 已筛好的 8 张交片候选；本地可对同结构场次一键打包 Previews + RAW。`,
    frameIds: [1, 2, 3, 4, 5, 6, 7, 8],
  },
  {
    id: "energy",
    keywords: ["energy", "能量"],
    reply: `${SESSION_LABEL} 预录「energy 向」前 5 张（用 overall 代理排序）。`,
    frameIds: [1, 2, 3, 6, 8],
  },
  {
    id: "tech_comp",
    keywords: ["技术分", "构图一般", "标出来"],
    reply:
      `预录对比：左边高分 keepers，脚本标出「技术高但构图一般」的占位说明。\n` +
      `完整维度拆解见本地 analysis_results / Infra job timeline。`,
    frameIds: [1, 3, 6, 10, 12],
  },
];

const FALLBACK: ScriptedIntent = {
  id: "fallback",
  keywords: [],
  reply:
    `这是只读 Showcase：Agent 不会真的调 VLM。\n` +
    `试试滚动预设（选出交片 / 吉他特写 / 胶片风…），我会用 ${SESSION_LABEL} 的预录 keepers 回答。\n` +
    `完整对话与检索请本地 ./start_all.sh。`,
  frameIds: [1, 2, 3, 4],
};

function matchIntent(message: string): ScriptedIntent {
  const q = message.trim().toLowerCase();
  if (!q) return FALLBACK;
  for (const intent of INTENTS) {
    if (intent.keywords.some((k) => q.includes(k.toLowerCase()))) return intent;
  }
  return FALLBACK;
}

function toolCallFor(frames: AgentDemoFrame[], query: string): AgentToolCall {
  return {
    tool: "gallery_search",
    args: { query, limit: frames.length, mode: "showcase_fixture" },
    ok: true,
    metadata: {
      ui_action: "search",
      showcase: true,
      session_date: agentDemoManifest.session_date,
      session_key: agentDemoManifest.session_key,
      files: frames.map((f) => f.file),
      paths: frames.map((f) => f.path),
      scores: Object.fromEntries(frames.map((f) => [f.file, f.overall_score])),
    },
  };
}

/** Build a non-streaming Agent chat payload for showcase mode. */
export function buildShowcaseAgentReply(message: string): AgentChatResponse {
  const intent = matchIntent(message);
  const frames = pickFrames(intent.frameIds);
  const tool_calls = frames.length ? [toolCallFor(frames, message.trim() || intent.id)] : [];
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
