/**
 * Mimir feedback/contribution intake -- Cloudflare Worker.
 *
 * This is the production counterpart to scripts/dev_intake_mock.py in the
 * main Mimir repo. The contract is deliberately identical -- same routes,
 * same headers, same size caps, same rejection vocabulary -- because the
 * mock server is what the Rust Outbox module (src-tauri/src/outbox.rs) was
 * built and tested against. If this file's behavior drifts from the mock's,
 * update both together.
 *
 * Routes:
 *   POST /v1/submit/contribution   -- raw bytes of a .mimir-dataset.age file
 *   POST /v1/submit/feedback       -- raw bytes of a .mimir-feedback.age file
 *
 * Required headers:
 *   X-Mimir-App-Token: <token>        (cheap traffic filter, not real auth --
 *                                       anyone can extract it from the app
 *                                       binary; the real defenses are the
 *                                       rate limit and the size cap below)
 *   X-Mimir-Package-Id: <32 hex chars>
 *   Content-Length: <bytes>
 *
 * 201 { "accepted": true, "object_key": "..." }
 * 4xx { "accepted": false, "reason": "too_large" | "bad_token" |
 *                                    "invalid_package_id" | "duplicate" |
 *                                    "bad_content" | "length_required" |
 *                                    "not_found" }
 *
 * There is deliberately no GET or list route on any path, matching
 * dev_intake_mock.py. The client that uploads here never gets read access;
 * retrieval is a separate, developer-only path (mimir_training_ground.py's
 * `sync` talks to R2 directly via the S3 API with its own credentials, and
 * never goes through this Worker at all).
 *
 * This Worker cannot validate package *content* -- it never has the age
 * private key, so it cannot decrypt anything. It can only check that the
 * payload looks like a real age file (the format's recognizable header)
 * before accepting it. Real validation happens where it always has: client-
 * side before encryption, and developer-side at intake with the private key.
 */

export interface Env {
  MIMIR_INTAKE_BUCKET: R2Bucket
  MIMIR_APP_TOKEN: string
  // Cloudflare's Workers-native Rate Limiting binding (wrangler.toml
  // [[unsafe.bindings]] type = "ratelimit"), not a WAF rule -- WAF
  // rate-limiting is zone-scoped and this Worker only has a workers.dev
  // subdomain with no zone to attach a rule to. This binding works
  // regardless, since it's attached to the script, not to DNS.
  SUBMIT_RATE_LIMITER: { limit(options: { key: string }): Promise<{ success: boolean }> }
}

const AGE_MAGIC = 'age-encryption.org/v1'
const PACKAGE_ID_RE = /^[0-9a-f]{32}$/

interface RouteConfig {
  prefix: string
  suffix: string
  maxBytes: number
}

const ROUTES: Record<string, RouteConfig> = {
  '/v1/submit/contribution': {
    prefix: 'contributions',
    suffix: '.mimir-dataset.age',
    maxBytes: 2 * 1024 * 1024 * 1024, // contributions carry raw video
  },
  '/v1/submit/feedback': {
    prefix: 'feedback',
    suffix: '.mimir-feedback.age',
    maxBytes: 500 * 1024 * 1024,
  },
}

function rejected(status: number, reason: string): Response {
  return new Response(JSON.stringify({ accepted: false, reason }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function accepted(objectKey: string): Response {
  return new Response(JSON.stringify({ accepted: true, object_key: objectKey }), {
    status: 201,
    headers: { 'Content-Type': 'application/json' },
  })
}

function objectKeyFor(route: RouteConfig, packageId: string): string {
  const now = new Date()
  const year = now.getUTCFullYear()
  const month = String(now.getUTCMonth() + 1).padStart(2, '0')
  return `${route.prefix}/${year}/${month}/${packageId}${route.suffix}`
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url)

    // No read/list route, ever, on any path -- matches dev_intake_mock.py.
    if (request.method === 'GET' || request.method === 'HEAD') {
      return rejected(404, 'not_found')
    }

    if (request.method !== 'POST') {
      return rejected(405, 'method_not_allowed')
    }

    const route = ROUTES[url.pathname]
    if (!route) {
      return rejected(404, 'not_found')
    }

    // Applied before the token check on purpose: the app token is a cheap
    // filter anyone can extract from the compiled app, so without this, a
    // flood of correctly-tokened (or even wrong-tokened, just to burn
    // compute) requests would hit every check below at unlimited rate.
    // Cloudflare's CF-Connecting-IP is the actual client IP as seen at the
    // edge -- not spoofable by the request itself.
    const clientIp = request.headers.get('CF-Connecting-IP') ?? 'unknown'
    const { success } = await env.SUBMIT_RATE_LIMITER.limit({ key: clientIp })
    if (!success) {
      return rejected(429, 'rate_limited')
    }

    if (request.headers.get('X-Mimir-App-Token') !== env.MIMIR_APP_TOKEN) {
      return rejected(401, 'bad_token')
    }

    const packageId = request.headers.get('X-Mimir-Package-Id') ?? ''
    if (!PACKAGE_ID_RE.test(packageId)) {
      return rejected(400, 'invalid_package_id')
    }

    const contentLengthHeader = request.headers.get('Content-Length')
    const contentLength = contentLengthHeader ? Number.parseInt(contentLengthHeader, 10) : Number.NaN
    if (!Number.isFinite(contentLength) || contentLength < 0) {
      return rejected(411, 'length_required')
    }
    if (contentLength > route.maxBytes) {
      return rejected(413, 'too_large')
    }

    const objectKey = objectKeyFor(route, packageId)

    // Reject-if-exists, for cheap idempotency against retries -- a retried
    // upload after a successful-but-unconfirmed attempt should not silently
    // create (or pay storage for) a duplicate.
    const existing = await env.MIMIR_INTAKE_BUCKET.head(objectKey)
    if (existing) {
      return rejected(409, 'duplicate')
    }

    if (!request.body) {
      return rejected(400, 'incomplete_body')
    }

    // Shape validation only: peek at the first bytes without buffering the
    // whole (possibly large) body, and reject obvious garbage before it ever
    // touches storage. Real content validation happens at decrypt time,
    // developer-side, with the private key this Worker never has.
    const [peekStream, storeStream] = request.body.tee()
    const magicOk = await startsWithAgeMagic(peekStream)
    if (!magicOk) {
      return rejected(400, 'bad_content')
    }

    // R2's put() enforces its own size limits and streams from the request
    // body; Content-Length was already checked above as the declared size,
    // this call is the actual transfer.
    await env.MIMIR_INTAKE_BUCKET.put(objectKey, storeStream, {
      httpMetadata: { contentType: 'application/octet-stream' },
    })

    return accepted(objectKey)
  },
}

async function startsWithAgeMagic(stream: ReadableStream<Uint8Array>): Promise<boolean> {
  const reader = stream.getReader()
  try {
    const needed = new TextEncoder().encode(AGE_MAGIC).length
    let collected = new Uint8Array(0)
    while (collected.length < needed) {
      const { done, value } = await reader.read()
      if (done) break
      const merged = new Uint8Array(collected.length + value.length)
      merged.set(collected)
      merged.set(value, collected.length)
      collected = merged
    }
    const prefix = new TextDecoder().decode(collected.slice(0, needed))
    return prefix === AGE_MAGIC
  } finally {
    // Draining is required: tee() only advances as fast as both branches are
    // read, so leaving this reader open would stall the storeStream branch's
    // put() from ever completing.
    reader.releaseLock()
    await stream.cancel().catch(() => {})
  }
}
