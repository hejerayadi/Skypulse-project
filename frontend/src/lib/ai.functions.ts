import { createServerFn } from "@tanstack/react-start";
import { requireSupabaseAuth } from "@/integrations/supabase/auth-middleware";

type ChatMsg = { role: "system" | "user" | "assistant"; content: string };

async function callLovableAI(messages: ChatMsg[]): Promise<string> {
  const key = process.env.LOVABLE_API_KEY;
  if (!key) throw new Error("LOVABLE_API_KEY missing");
  const res = await fetch("https://ai.gateway.lovable.dev/v1/chat/completions", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${key}`,
    },
    body: JSON.stringify({
      model: "google/gemini-3-flash-preview",
      messages,
    }),
  });
  if (res.status === 429) throw new Error("Rate limit reached, try again in a moment.");
  if (res.status === 402) throw new Error("AI credits exhausted for this workspace.");
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`AI request failed (${res.status}): ${text.slice(0, 200)}`);
  }
  const json = (await res.json()) as { choices?: { message?: { content?: string } }[] };
  return json.choices?.[0]?.message?.content?.trim() ?? "";
}

function summarize(arr: number[]) {
  if (arr.length === 0) return { min: 0, max: 0, avg: 0, trend: "flat" as const };
  const min = Math.min(...arr);
  const max = Math.max(...arr);
  const avg = arr.reduce((a, b) => a + b, 0) / arr.length;
  const third = Math.max(1, Math.floor(arr.length / 3));
  const first = arr.slice(0, third).reduce((a, b) => a + b, 0) / third;
  const last = arr.slice(-third).reduce((a, b) => a + b, 0) / third;
  const diff = last - first;
  const range = max - min || 1;
  const trend = Math.abs(diff) < range * 0.15 ? "stable" : diff > 0 ? "rising" : "falling";
  return { min, max, avg, trend };
}

export const interpretSensor = createServerFn({ method: "POST" })
  .middleware([requireSupabaseAuth])
  .inputValidator((raw: unknown) => {
    const input = raw as { sensor: string; unit: string; recent: number[] };
    if (!input?.sensor || !Array.isArray(input.recent)) throw new Error("Invalid payload");
    return {
      sensor: String(input.sensor).slice(0, 40),
      unit: String(input.unit ?? "").slice(0, 10),
      recent: input.recent.slice(-30).map((n) => Number(n)),
    };
  })
  .handler(async ({ data }) => {
    const stats = summarize(data.recent);
    const prompt = `You are the flight-analytics copilot on a satellite ground station named SkyPulse.
Sensor: ${data.sensor} (${data.unit})
Recent readings (oldest → newest): ${data.recent.map((v) => v.toFixed(2)).join(", ")}
Min: ${stats.min.toFixed(2)} ${data.unit} | Max: ${stats.max.toFixed(2)} ${data.unit} | Avg: ${stats.avg.toFixed(2)} ${data.unit} | Trend: ${stats.trend}

In 2-3 concise sentences, interpret the variation for a mission operator: note trend, stability, and any anomaly worth watching. Keep it technical but readable. No preamble.`;
    const content = await callLovableAI([
      { role: "system", content: "You are a concise satellite telemetry analyst. Reply in plain sentences, no markdown, no lists." },
      { role: "user", content: prompt },
    ]);
    return { interpretation: content };
  });

export const chatWithCopilot = createServerFn({ method: "POST" })
  .middleware([requireSupabaseAuth])
  .inputValidator((raw: unknown) => {
    const input = raw as { messages: ChatMsg[] };
    if (!Array.isArray(input?.messages)) throw new Error("Invalid payload");
    const clean = input.messages
      .slice(-20)
      .filter((m) => m && (m.role === "user" || m.role === "assistant"))
      .map((m) => ({ role: m.role, content: String(m.content).slice(0, 4000) }));
    return { messages: clean as ChatMsg[] };
  })
  .handler(async ({ data }) => {
    const system: ChatMsg = {
      role: "system",
      content:
        "You are SkyPulse Copilot, an AI assistant for a satellite/CubeSat ground station. You help operators interpret telemetry (temperature, humidity, pressure, acoustic, motion/vibration) and answer mission-ops questions. Be concise, technical, and friendly. Prefer short paragraphs.",
    };
    const reply = await callLovableAI([system, ...data.messages]);
    return { reply };
  });