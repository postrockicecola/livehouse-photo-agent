import { NextRequest } from "next/server";
import { buildShowcaseAgentReply } from "@/lib/agentShowcase";
import { isReadOnlyAgentDeploy, proxyAgentApi } from "@/lib/agentApiProxy";

export const dynamic = "force-dynamic";

function sseData(obj: unknown): string {
  return `data: ${JSON.stringify(obj)}\n\n`;
}

/** Showcase: fake SSE tokens from the scripted reply, then tool_call + done. */
export async function POST(req: NextRequest) {
  if (!isReadOnlyAgentDeploy()) {
    return proxyAgentApi(req, "chat/stream");
  }

  let message = "";
  try {
    const body = (await req.json()) as { message?: string };
    message = String(body?.message ?? "");
  } catch {
    message = "";
  }

  const payload = buildShowcaseAgentReply(message);
  const encoder = new TextEncoder();

  const stream = new ReadableStream({
    start(controller) {
      const reply = payload.reply || "";
      // Chunk roughly by short phrases for a light “typing” feel.
      const parts = reply.split(/(?<=[。！？\n])/).filter(Boolean);
      const chunks = parts.length ? parts : [reply];
      for (const text of chunks) {
        controller.enqueue(encoder.encode(sseData({ type: "token", text })));
      }
      for (const call of payload.tool_calls ?? []) {
        controller.enqueue(
          encoder.encode(
            sseData({
              type: "tool_call",
              tool: call.tool,
              args: call.args,
              ok: call.ok,
              metadata: call.metadata,
            }),
          ),
        );
      }
      controller.enqueue(
        encoder.encode(
          sseData({
            type: "done",
            reply: payload.reply,
            tool_calls: payload.tool_calls,
            guardrail_events: payload.guardrail_events,
            memory_turns: payload.memory_turns,
            base_dir: payload.base_dir,
          }),
        ),
      );
      controller.close();
    },
  });

  return new Response(stream, {
    status: 200,
    headers: {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache, no-transform",
      connection: "keep-alive",
    },
  });
}
