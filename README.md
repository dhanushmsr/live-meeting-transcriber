# Ink

A meeting assistant that works with any meeting app (Meet, Zoom, Teams, WhatsApp, ...).
It listens, shows live captions, translates, and when the meeting ends opens an **online report** (kept for 72 hours) in your browser.

## Run it straight from GitHub (no clone)

Repo: https://github.com/dhanushmsr/live-meeting-transcriber

**Option 1: one command, nothing installed permanently** (uses [uv](https://docs.astral.sh/uv/))

```bash
uvx --from git+https://github.com/dhanushmsr/live-meeting-transcriber inkwake
```

Install uv first if you don't have it:

```powershell
# Windows
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```
```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Option 2: plain pip** (installs all requirements automatically)

```bash
pip install git+https://github.com/dhanushmsr/live-meeting-transcriber.git
inkwake
```

Update later with `pip install -U git+https://github.com/dhanushmsr/live-meeting-transcriber.git`.

## First run

Ink asks for a Gemini API key once (or set the `GEMINI_API_KEY` environment variable).
The key is stored only on your machine in `~/.inkwake/config.json`. Never commit it.

## Using it

- A round **Ink** bubble floats on screen (drag it anywhere). Click it and the icons spin out:
  play (start / resume), pause, stop, catch me up, bookmark, settings, close.
- When a meeting is detected, Ink asks permission before listening. Nothing is captured before you agree.
  You can also start it manually from the bubble.
- Settings (icons only): profile, API key, live captions, live translation + language, auto-detect.
- When you stop (or the meeting ends), Ink opens the report in your default browser at
  `http://localhost:8731/r/<id>`. It reads like an online doc, with the summary, topics, decisions, action items,
  speaking-time chart and the full transcript, and has buttons to **export DOCX, PDF or Markdown** and to share the link.
  - **Local mode (default).** The report is served by Ink itself on your own computer. It is only reachable from
    this machine, only while Ink is running, and is deleted after **72 hours**. Reports are stored in
    `~/.inkwake/reports/`. Nothing leaves your computer except the audio sent to Gemini.
  - **Share online (opt-in).** Turn on the cloud icon in Settings and each finished report is also uploaded to Ink's
    report server, so you get a public link such as `https://inkwake-reports.inkwake.workers.dev/r/<id>` that opens in any
    browser on any device, with no install, for 72 hours (then it is deleted). Anyone who has the link can read the
    transcript, so share it only with people who should see it. It is **off by default**, and Ink asks you to confirm
    when you turn it on. If the server cannot be reached, Ink falls back to the local report.
  - **Backup copy.** A `.docx` is also saved in `Desktop/<your name>/` (or `Desktop/inkwake/`).

## During the meeting

- **Catch me up** (page icon). Looked away or joined late? A small panel shows what is being discussed right now,
  a short summary so far, decisions, action items and open questions. The latest lines of the conversation appear
  instantly; the AI notes fill in a few seconds later.
- **Name alert.** When someone else says your name (set it in Settings), Ink beeps, flashes the bubble purple and
  shows a notification with the line, so you never miss "Dhanush, can you take this?". It is also marked in the report.
- **Bookmark** (star icon). One click flags the moment (the ring flashes gold). Bookmarks show up under Highlights in
  the report and the AI treats them as important.

## The report

Built to be read in 30 seconds. The first screen answers the three questions that matter:

- **In short:** one plain sentence with the main outcome.
- **Decisions:** what was agreed.
- **Action items:** who does what, and by when (tick them off on the page).

Below that, only if there is something to say: context, things worth remembering, questions still open, a timeline of
the main stretches of the meeting, highlights (your bookmarks and the times someone mentioned you, with the words said
at that moment), who spoke how much (only when more than one speaker is known), and a ready-to-send **follow-up email**
(copy, open in your email app, or send via WhatsApp). The full transcript sits at the end, one paragraph per speaker turn,
with an English line only where the original was in another language; on the page it is folded away and opens when you
search it. Empty sections never appear.

- **Ask this meeting.** A box on the report page answers questions such as "What did we decide about pricing?" using
  only that meeting's transcript, with timestamps. It uses your own Gemini key, so it works on the local report page
  (not on shared cloud links).
- Export as DOCX, PDF or Markdown from the top bar. The Word file has the same layout as the page.

## Using Ink on another laptop (no code needed)

1. Install [uv](https://docs.astral.sh/uv/) once, then run `uvx --from git+https://github.com/dhanushmsr/live-meeting-transcriber inkwake`
   (or `pip install git+https://github.com/dhanushmsr/live-meeting-transcriber.git` and run `inkwake`).
2. Enter that laptop's own Gemini API key on first launch.
3. Optional: click the cloud icon in Settings to get shareable report links.

To just **read a report** on another device, open its link in a browser. Nothing needs to be installed.

## Troubleshooting

- **`uvx` or `inkwake` is "not recognized".** Install uv (see above), then **close and reopen the terminal**. Prefer `uvx`
  over `pip`: with the Microsoft Store version of Python, `pip install` can fail with "No such file or directory ...
  Windows Long Path support", and then the `inkwake` command is never created. (You can instead enable long paths, see
  <https://pip.pypa.io/warnings/enable-long-paths>.)
- **"Ink can't hear your microphone".** Ink noticed your microphone is sending pure silence. Check, in this order:
  the laptop's **mic-mute key** (often F4 or Fn+F4, which Windows does not show as muted), Windows Settings > System >
  Sound > Input (speak and watch the level bar move), Settings > Privacy & security > Microphone (allow desktop apps),
  other apps holding the microphone (Zoom, Teams, browser tabs), and then try another microphone in Ink's Settings.
- **Captions never appear but nothing is wrong on screen.** Ink only sends audio while someone is speaking above the
  room's noise level. If your voice is very quiet, raise the microphone level in Windows Sound settings.
- **A report link opens the wrong page.** Close every old Ink window and start Ink once; each Ink now uses its own port.

## How audio is captured

- Your microphone is "You".
- Everything the meeting app plays is "Others" (remote participants are grouped together).
- Windows: system audio works out of the box (WASAPI loopback).
- macOS: install [BlackHole](https://existential.audio/blackhole/) and route output through a Multi-Output Device.

## Notes

- Tell participants you are transcribing; consent rules vary by country.
- Audio is streamed live to the Gemini Live API (`gemini-3.5-live-translate-preview`), so words appear about 1.5-2.5 s after they are spoken; only silence is not sent. The report analysis uses `gemini-flash-latest`.
- Names: audio can not tell remote people apart, so while others speak Ink reads the active-speaker name from the meeting screen (Gemini vision, a small screenshot every ~2 s) and attaches it to the words. Your own speech comes from the microphone. Turn this off with the people icon in settings. On macOS grant Screen Recording permission.
- Meeting detection is heuristic (Zoom process, window titles for Meet/Teams/WhatsApp). Manual start always works.
- The logo is `src/inkwake/assets/ink.png`.

## Development, CI/CD and releases

- **Tests:** `pip install -e . pytest` then `pytest tests`. They need no microphone, screen or API key.
- **CI** (`.github/workflows/ci.yml`) runs on every push and pull request: the tests on Windows (Python 3.10 and
  3.12), a check that the built package contains the report page and logo, and a build of the Cloudflare Worker.
- **Deploy** (`.github/workflows/deploy-worker.yml`) publishes the report server to Cloudflare when `cloud/` changes.
  Setup: [`cloud/README.md`](cloud/README.md).
- **Release:** bump `version` in `pyproject.toml`, then `git tag v0.3.0 && git push origin v0.3.0`. The release workflow
  checks the tag matches, builds the package and creates a GitHub Release with the files. Set the repository variable
  `PUBLISH_TO_PYPI=true` (and add the repo as a trusted publisher on pypi.org) to also publish to PyPI.

## License

MIT, see [LICENSE](LICENSE).
