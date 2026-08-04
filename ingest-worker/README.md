# Mimir Ingest Worker

Receives feedback and contribution submissions from the Mimir app and drops
them into an R2 bucket, unread. This is the piece of infrastructure that
replaces "encrypt it and email me the file" with "one click, sent."

It never has the age private key, so it can never decrypt what it stores --
see the comment at the top of `src/index.ts` for the full threat model.
`scripts/dev_intake_mock.py` in the main Mimir repo implements the exact
same contract and is what the app's Rust Outbox module
(`src-tauri/src/outbox.rs`) was actually built and tested against; this
Worker should never drift from that contract without updating both.

## One-time setup (Phase 0 -- only you can do this)

1. **Create a Cloudflare account** if you don't have one, and install
   Wrangler:
   ```bash
   npm install
   npx wrangler login
   ```

2. **Create the R2 bucket:**
   ```bash
   npx wrangler r2 bucket create mimir-intake
   ```

3. **Set the app token** to `mimir-beta-2026-a4f9c1` -- the current value of
   `OUTBOX_APP_TOKEN` in `C:\MimirDev\desktop\src-tauri\src\outbox.rs`. It must match
   exactly. Pasting the literal value here is fine: it's already compiled
   into every copy of the app and extractable by anyone, so it was never a
   secret -- it's a traffic filter, not authentication (see the top of
   `src/index.ts`). If that constant ever changes, update this value to
   match:
   ```bash
   npx wrangler secret put MIMIR_APP_TOKEN
   ```

4. **Deploy:**
   ```bash
   npx wrangler deploy
   ```
   This prints the Worker's URL -- either a `*.workers.dev` subdomain, or a
   custom domain if you've attached one in the Cloudflare dashboard.

5. **Point the app at it.** Set `MIMIR_INTAKE_URL` to that URL when building
   the app (it defaults to `http://127.0.0.1:8787`, the local dev mock,
   which is why nothing is submitted anywhere real until this step happens).

6. **Rate limiting.** Already wired into `wrangler.toml` and `src/index.ts`
   as a Workers-native Rate Limiting binding (`SUBMIT_RATE_LIMITER`), keyed
   by client IP, applied before the token check. This exists instead of a
   Cloudflare WAF rate-limiting rule because WAF rules are zone-scoped --
   they need a custom domain attached, and this Worker only has a
   `workers.dev` subdomain with no zone to attach one to. Redeploy to pick
   it up:
   ```bash
   npx wrangler deploy
   ```
   Current limit is 10 requests/minute per IP (`simple = { limit = 10,
   period = 60 }` in `wrangler.toml`) -- generous for a real user, punishing
   for abuse. This is the actual defense against a public, unauthenticated
   upload endpoint; the app-token header only filters out traffic that isn't
   Mimir at all. The Worker also enforces a hard size cap (2 GB
   contributions / 500 MB feedback) regardless, so a single request can't
   cost much even before the rate limit kicks in.

## What this Worker does and does not do

- Accepts `POST /v1/submit/contribution` and `POST /v1/submit/feedback` --
  raw encrypted bytes, nothing else.
- Rejects anything that isn't shaped like a real `age`-encrypted file (a
  cheap check on the file's magic header -- it cannot verify the contents
  are valid, since it never has the decryption key).
- Has **no GET or list route on any path.** The client that uploads here
  never gets read access back. Retrieval is `mimir_training_ground.py`'s
  job, talking to R2 directly with a separate, developer-only credential --
  it never goes through this Worker.
- Never touches, sees, or needs the age private key.

## Verifying it matches the mock server

Before deploying, `npm run typecheck` should pass. After deploying, the
fastest sanity check is pointing the app's `MIMIR_INTAKE_URL` at the real
Worker instead of the local mock and running through Submit once from a
real incident -- the same thing the mock server already proved works
end-to-end during development.

## Cost

R2's free tier (10 GB storage, no egress fees) comfortably covers a beta.
Workers' free tier is 100,000 requests/day. At beta scale this should cost
nothing; revisit if that stops being true.
