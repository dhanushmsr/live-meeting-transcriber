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

## Deploy from GitHub Actions (automatic, recommended)

`.github/workflows/deploy-worker.yml` redeploys the server every time `cloud/` or the report page changes on `main`,
then uploads a test report to the live server to prove it works. It stays idle until you finish this one-time setup.

1. **Cloudflare API token.** Cloudflare dashboard > My Profile > API Tokens > Create Token > use the
   **Edit Cloudflare Workers** template. Copy the token. Also copy your **Account ID** (Workers & Pages > right sidebar).
2. **Create the storage once** (dashboard: Storage & Databases > KV > Create, or `wrangler kv namespace create REPORTS`)
   and copy the namespace **id**.
3. **GitHub repo > Settings > Secrets and variables > Actions:**
   - Secrets: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`
     (optional: `INGEST_TOKEN`, the same value you set with `wrangler secret put INGEST_TOKEN`).
   - Variables: `KV_NAMESPACE_ID` = the id from step 2.
4. **Run it:** push to `main`, or Actions > *Deploy report server* > *Run workflow*. The log prints your live URL
   (`https://inkwake-reports.<you>.workers.dev`). Put it in `DEFAULT_CLOUD_URL` (see below).

A custom domain such as `reports.yourname.me` (for example the free domain from the GitHub Student Developer Pack)
can be attached to the Worker under Workers & Pages > inkwake-reports > Settings > Domains & Routes. Offers in the
pack change, so check them on <https://education.github.com/pack>.

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
