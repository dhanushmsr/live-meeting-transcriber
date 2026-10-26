// What kind of meeting is this tab? Best effort from the address and title; used to name the report.

const RULES = [
  [/(^|\.)meet\.google\.com$/, "Google Meet"],
  [/(^|\.)zoom\.(us|com)$/, "Zoom"],
  [/(^|\.)teams\.(microsoft|live)\.com$/, "Microsoft Teams"],
  [/(^|\.)web\.whatsapp\.com$/, "WhatsApp"],
  [/(^|\.)webex\.com$/, "Webex"],
  [/(^|\.)whereby\.com$/, "Whereby"],
  [/(^|\.)meet\.jit\.si$/, "Jitsi"],
  [/(^|\.)discord\.com$/, "Discord"],
  [/(^|\.)slack\.com$/, "Slack"],
  [/(^|\.)gotomeeting\.com$/, "GoToMeeting"],
  [/(^|\.)bluejeans\.com$/, "BlueJeans"],
];

const GENERIC = new Set(["", "meet", "google meet", "zoom", "zoom meeting", "microsoft teams", "teams", "whatsapp", "meeting", "webex", "discord"]);
const SUFFIXES = [" - google chrome", " - microsoft edge", " - mozilla firefox", " - brave", " - opera", " | microsoft teams",
  " - microsoft teams", " - zoom", " - meet", " - google meet", " - webex", " - discord"];

export function platformFromUrl(url) {
  let host = "";
  try { host = new URL(url).hostname; } catch { return ""; }
  return (RULES.find(([re]) => re.test(host)) || [])[1] || "";
}

/** 'Weekly sync - Google Meet' -> 'Weekly sync'; the generic 'Meet - abc-defg-hij' -> 'abc-defg-hij' is kept out. */
export function titleFromTab(title) {
  let t = String(title || "").trim();
  for (let i = 0; i < 3; i++) {
    const low = t.toLowerCase();
    const suf = SUFFIXES.find((s) => low.endsWith(s));
    if (suf) t = t.slice(0, -suf.length).trim();
  }
  t = t.replace(/^Meet\s*[-–]\s*/i, "").trim();
  if (/^[a-z]{3}-[a-z]{4}-[a-z]{3}$/.test(t)) return "";        // a Meet room code is not a name
  return GENERIC.has(t.toLowerCase()) ? "" : t;
}
