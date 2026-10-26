// Chrome only: an offscreen document keeps the audio engine alive (a service worker would be shut down).
// Offscreen documents may only use chrome.runtime, so there is no chrome.storage here: the background script sends the
// settings along with "start" and again whenever they change.
import { Engine } from "./engine.js";

let settings = null;
const engine = new Engine({
  loadSettings: async () => settings,
  emit: (event) => chrome.runtime.sendMessage({ type: "ink:event", event }).catch(() => {}),
});

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg?.target !== "offscreen") return false;
  const done = (p) => p.then((v) => reply({ ok: true, value: v }), (e) => reply({ ok: false, error: String(e.message || e) }));
  switch (msg.type) {
    case "start": settings = msg.settings; done(engine.start(msg)); return true;
    case "settings": engine.updateSettings(msg.settings); reply({ ok: true }); return false;
    case "stop": done(engine.stop()); return true;
    case "pause": engine.pause(); reply({ ok: true }); return false;
    case "resume": engine.resume(); reply({ ok: true }); return false;
    case "bookmark": engine.bookmark(); reply({ ok: true }); return false;
    case "names": engine.screenNames(msg.names); reply({ ok: true }); return false;
    case "catchup": done(Promise.resolve(engine.catchUp())); return true;
    case "status": reply({ ok: true, value: engine.active ? engine.session.state : "idle" }); return false;
    default: return false;
  }
});
