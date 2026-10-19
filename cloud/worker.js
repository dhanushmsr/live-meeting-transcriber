/**
 * Inkwake Cloudflare Worker: 72-Hour Ephemeral Meeting Report Server
 *
 * Provides:
 * - POST /api/report: Stores report with 72-hour KV expiration (259200s).
 * - GET /r/:id: Renders a Google Docs/Notion style interactive web document.
 * - Single-click export to PDF, DOCX, and sharing via WhatsApp/Gmail.
 */

// One page for both places: the same file is served by Ink's local server (src/inkwake/localserver.py).
import VIEWER from "../src/inkwake/assets/viewer.html";

const TTL_SECONDS = 72 * 60 * 60; // 72 hours = 259,200 seconds
const MAX_BODY_BYTES = 1_000_000; // a long meeting transcript is well under this
const MAX_TRANSCRIPT_LINES = 10_000;

const SECURITY_HEADERS = {
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
  "X-Robots-Tag": "noindex, nofollow, noarchive", // report links are private: keep them out of search engines
  "Cache-Control": "private, no-store",
};

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*", ...SECURITY_HEADERS },
  });
}

function html(body, status = 200) {
  return new Response(body, {
    status,
    headers: { "Content-Type": "text/html; charset=utf-8", ...SECURITY_HEADERS },
  });
}

const str = (v, max) => (typeof v === "string" ? v.slice(0, max) : "");
const list = (v, max) => (Array.isArray(v) ? v.slice(0, max) : []);

/** Keep only the fields we render, with bounded sizes, so an upload can never inject unexpected structure. */
function sanitize(body) {
  const a = body && typeof body.analysis === "object" && body.analysis ? body.analysis : {};
  const talk = {};
  if (body && typeof body.talk === "object" && body.talk) {
    for (const [spk, per] of Object.entries(body.talk).slice(0, 100)) {
      if (per && typeof per === "object") {
        talk[str(spk, 80)] = Object.fromEntries(
          Object.entries(per).slice(0, 2000).map(([m, v]) => [String(m).slice(0, 8), Number(v) || 0])
        );
      }
    }
  }
  return {
    title: str(body.title, 200) || "Meeting Report",
    started_at: str(body.started_at, 40),
    duration_minutes: Number(body.duration_minutes) || 0,
    platform: str(body.platform, 60),
    host_name: str(body.host_name, 120),
    participants: list(body.participants, 200).map((x) => str(x, 120)).filter(Boolean),
    languages: list(body.languages, 40).map((x) => str(x, 40)).filter(Boolean),
    analysis: {
      title: str(a.title, 200),
      tldr: str(a.tldr, 600),
      summary: str(a.summary, 8000),
      sentiment: str(a.sentiment, 80),
      topics: list(a.topics, 100).map((t) => ({ title: str(t && t.title, 300), detail: str(t && t.detail, 2000) })),
      key_points: list(a.key_points, 200).map((x) => str(x, 1000)),
      takeaways: list(a.takeaways, 50).map((x) => str(x, 1000)),
      decisions: list(a.decisions, 200).map((x) => str(x, 1000)),
      follow_ups: list(a.follow_ups, 200).map((x) => str(x, 1000)),
      open_questions: list(a.open_questions, 200).map((x) => str(x, 1000)),
      chapters: list(a.chapters, 100).map((c) => ({ start: str(c && c.start, 12), title: str(c && c.title, 300), summary: str(c && c.summary, 1000) })),
      follow_up_email: {
        subject: str(a.follow_up_email && a.follow_up_email.subject, 300),
        body: str(a.follow_up_email && a.follow_up_email.body, 8000),
      },
      action_items: list(a.action_items, 200).map((x) => ({ task: str(x && x.task, 1000), owner: str(x && x.owner, 120), due: str(x && x.due, 120) })),
    },
    bookmarks: list(body.bookmarks, 200).map((b) => ({ t: Number(b && b.t) || 0, text: str(b && b.text, 300) })),
    transcript: list(body.transcript, MAX_TRANSCRIPT_LINES).map((l) => ({
      t: Number(l && l.t) || 0,
      speaker: str(l && l.speaker, 120),
      text: str(l && l.text, 4000),
      translation: str(l && l.translation, 4000),
      mention: l && l.mention === true,
    })),
    talk,
  };
}

function newId() {
  const bytes = crypto.getRandomValues(new Uint8Array(12)); // 96 bits: not guessable
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // Enable CORS for API requests
    if (request.method === "OPTIONS") {
      return new Response(null, {
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
          "Access-Control-Allow-Headers": "Content-Type, Authorization",
        },
      });
    }

    // 1. Create Report: POST /api/report
    if (request.method === "POST" && url.pathname === "/api/report") {
      // Optional shared secret: set with `wrangler secret put INGEST_TOKEN` to stop strangers uploading.
      if (env.INGEST_TOKEN && request.headers.get("Authorization") !== `Bearer ${env.INGEST_TOKEN}`) {
        return json({ error: "Unauthorized" }, 401);
      }
      if (!env.REPORTS) return json({ error: "KV namespace REPORTS is not configured" }, 500);
      const declared = Number(request.headers.get("Content-Length") || 0);
      if (declared > MAX_BODY_BYTES) return json({ error: "Report too large" }, 413);
      try {
        const raw = await request.text();
        if (raw.length > MAX_BODY_BYTES) return json({ error: "Report too large" }, 413);
        const body = JSON.parse(raw);
        if (!body || typeof body !== "object" || Array.isArray(body)) return json({ error: "Invalid report" }, 400);

        const id = newId();
        const createdAt = new Date();
        const expiresAt = new Date(createdAt.getTime() + TTL_SECONDS * 1000);
        const record = { ...sanitize(body), id, created_at: createdAt.toISOString(), expires_at: expiresAt.toISOString() };

        await env.REPORTS.put(id, JSON.stringify(record), { expirationTtl: TTL_SECONDS });
        return json({ success: true, id, url: `${url.origin}/r/${id}`, expires_at: record.expires_at });
      } catch (err) {
        return json({ error: "Invalid JSON body" }, 400);
      }
    }

    // 2. View Raw JSON: GET /api/report/:id
    if (request.method === "GET" && url.pathname.startsWith("/api/report/")) {
      const id = url.pathname.replace("/api/report/", "").trim();
      if (!env.REPORTS) return json({ error: "KV namespace REPORTS is not configured" }, 500);
      if (!/^[a-f0-9]{8,64}$/.test(id)) return json({ error: "Not found or expired" }, 404);
      const raw = await env.REPORTS.get(id);
      if (!raw) return json({ error: "Not found or expired" }, 404);
      return new Response(raw, {
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*", ...SECURITY_HEADERS },
      });
    }

    // 3. Online document: GET /r/:id (the page fetches /api/report/:id itself)
    if (request.method === "GET" && url.pathname.startsWith("/r/")) {
      return html(VIEWER);
    }

    // Anything else is a 404; "/" explains what this server is
    if (url.pathname !== "/") return json({ error: "Not found" }, 404);
    return new Response(
      "Inkwake 72-Hour Ephemeral Meeting Report Server is running.\nEndpoints: POST /api/report, GET /r/:id",
      { headers: { "Content-Type": "text/plain", ...SECURITY_HEADERS } }
    );
  },
};
