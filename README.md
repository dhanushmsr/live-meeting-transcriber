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
  play (start / resume), pause, stop, settings.
- When a meeting is detected, Ink asks permission before listening. Nothing is captured before you agree.
  You can also start it manually from the bubble.
- Settings (icons only): profile, API key, live captions, live translation + language, auto-detect.
- When you stop (or the meeting ends), Ink opens the report in your default browser at
  `http://localhost:8731/r/<id>`. It reads like an online doc, with the summary, topics, decisions, action items,
  speaking-time chart and the full transcript, and has buttons to **export DOCX, PDF or Markdown** and to share the link.
  - **Local mode (default).** The report is served by Ink itself on your own computer. It is only reachable from
    this machine, only while Ink is running, and is deleted after **72 hours**. Reports are stored in
    `~/.inkwake/reports/`.
  - **Cloud mode (optional, for sharing).** Deploy `cloud/` to Cloudflare and set its URL, and the same page is
    published at a public link anyone can open for 72 hours. See [`cloud/README.md`](cloud/README.md).
  - **Backup copy.** A `.docx` is also saved in `Desktop/<your name>/` (or `Desktop/inkwake/`).

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

## License

MIT, see [LICENSE](LICENSE).
