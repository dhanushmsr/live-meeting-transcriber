# Ink

A meeting assistant that works with any meeting app (Meet, Zoom, Teams, WhatsApp, ...).
It listens, shows live captions, translates, and **downloads a `.docx` report automatically when the meeting ends**.

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
- When you stop (or the meeting ends), the report is saved automatically as a `.docx` in `Desktop/<your name>/` (or `Desktop/inkwake/`), named after the meeting title or just the date and time:
  summary, topics, key points, decisions, action items, talk-time per speaker (charts),
  meeting minutes, total minutes across all your meetings, full transcript.

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
