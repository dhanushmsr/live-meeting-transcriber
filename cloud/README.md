# Ink report server (Cloudflare Worker)

> Optional. Ink already serves reports on `http://localhost` by itself (see the main README). Deploy this only when
> you want links that other people can open.

Stores each meeting report for **72 hours**, then deletes it automatically (Cloudflare KV expiry). Anyone with the
link sees a clean online document with buttons to **share**, and **export to DOCX, PDF and Markdown**.

- Free tier is enough for personal use. No database to run.
- Link IDs are 96-bit random, so they cannot be guessed. Pages are marked `noindex` and are not cached.
- The page itself is `src/inkwake/assets/viewer.html`, the same file Ink's local server uses, so both look and export identically.
- Uploads are validated, size-limited (1 MB) and reduced to the fields Ink renders. All text is HTML-escaped.

## Deploy (about 5 minutes, once)

You need a free Cloudflare account and Node.js.

```bash
npm install -g wrangler
wrangler login
cd cloud
wrangler kv namespace create REPORTS
```

Wrangler prints a block with an `id`. Paste that id into `cloud/wrangler.toml`
(replace `REPLACE_WITH_YOUR_KV_NAMESPACE_ID`), then:

```bash
wrangler deploy
```

You get a URL like `https://inkwake-reports.<your-subdomain>.workers.dev`.

## Point Ink at it

- **For everyone who installs Ink:** set `DEFAULT_CLOUD_URL` in `src/inkwake/config.py` to that URL and publish.
  Users then need no setup at all.
- **For one person / testing:** set `cloud_report_url` in `~/.inkwake/config.json`, or the environment variable
  `INKWAKE_CLOUD_URL`. If no built-in URL exists, Settings also shows a field for it.

## Stop strangers using your server (optional but recommended)

By default anyone who knows the Worker URL can upload a report to it. To require a key:

```bash
wrangler secret put INGEST_TOKEN
```

and give your users the same value as `cloud_report_token` in `~/.inkwake/config.json` (or `INKWAKE_CLOUD_TOKEN`).
Note that a token shipped inside a public app is not secret; for a public release prefer Cloudflare's
**Rate limiting rules** (dashboard > Security > WAF) on `POST /api/report`.

## Test locally without deploying

```bash
cd cloud
wrangler dev --local --port 8787
INKWAKE_CLOUD_URL=http://localhost:8787 inkwake
```

## Privacy notes

- Reports contain the transcript and summary, so treat the link like a password for 72 hours.
- Ink says in its permission pop-up that it records, so tell participants before you share anything.
- KV is eventually consistent: a brand-new link can take up to about a minute to work from another continent.
