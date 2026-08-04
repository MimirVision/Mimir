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
 *   POST /v1/multipart/create      -- begin a chunked upload
 *   POST /v1/multipart/part        -- one chunk
 *   POST /v1/multipart/complete    -- assemble and accept
 *   POST /v1/multipart/abort       -- discard
 *
 * WHY THE MULTIPART ROUTES EXIST
 *
 * Cloudflare caps request bodies at the edge -- 100 MB on Free/Pro -- before
 * a Worker runs. The single-shot /v1/submit routes therefore cannot accept a
 * contribution: those carry raw video for every camera angle in an event
 * group and routinely exceed 100 MB. A real 106 MB package sent on
 * 2026-08-04 came back 413 with an HTML body, i.e. the edge error page, not
 * this Worker's JSON. That is why 22 small feedback packages arrived during
 * the free beta and not one contribution ever did. The maxBytes below for
 * contributions (2 GB) was unreachable fiction: the request died before this
 * code could answer.
 *
 * The chunked routes split the package client-side into parts under the edge
 * limit and reassemble them with R2's multipart API.
 *
 * Presigned PUT straight to R2 would avoid the Worker entirely and is the
 * right answer at scale (it is what the cloud design specifies). It is not
 * what this does, deliberately: presigning needs an R2 S3 access key held as
 * a Worker secret plus SigV4 signing, which trades a testable path for an
 * untestable one and adds a credential to leak. Streaming parts through the
 * Worker keeps every check below -- rate limit, token, package id, duplicate
 * rejection, age-magic shape check -- and needs no new secret. Revisit when
 * upload volume makes Worker bandwidth the cost that matters.
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

// Comfortably under Cloudflare's 100 MB edge cap, leaving room for headers
// and any transfer encoding. R2 requires every part except the last to be
// the same size and at least 5 MiB, so the client must use exactly this.
const PART_SIZE = 64 * 1024 * 1024

// R2 allows 10,000 parts; at 64 MB that is far more than any package needs,
// but a bound stops a malformed client opening an unbounded upload.
const MAX_PARTS = 10_000

// R2 multipart upload ids are opaque; this only guards the shape so a junk
// value cannot be reflected into an R2 call.
//
// The bound was 256 and real R2 ids are longer than that -- an observed one
// was 343 base64url characters -- so every chunked upload was rejected with
// `invalid_upload_id` on its first part. The dev mock generated
// `uuid4().hex`, 32 characters, so nothing caught it until this ran against
// production. The mock now issues realistically long ids for that reason.
// 1024 leaves headroom without letting an unbounded string through.
const UPLOAD_ID_RE = /^[A-Za-z0-9._~-]{1,1024}$/

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

/**
 * A multipart upload spans several requests, so the client has to name the
 * object it is continuing. That means an attacker-controlled string reaching
 * an R2 call, so it is matched against exactly the shape this Worker mints
 * rather than trusted: a known prefix, a four-digit year, a two-digit month,
 * a 32-hex package id, and the suffix belonging to that prefix. Nothing else
 * can be addressed -- no traversal, no writing outside the intake namespace,
 * no overwriting an unrelated key.
 *
 * Recomputing the key server-side instead would look safer but is wrong: the
 * month rolls over mid-upload and the second request would derive a
 * different key from the first.
 */
function isMintedObjectKey(objectKey: string): boolean {
  for (const route of Object.values(ROUTES)) {
    const pattern = new RegExp(
      `^${route.prefix}/\\d{4}/\\d{2}/[0-9a-f]{32}${route.suffix.replace(/\./g, '\\.')}$`,
    )
    if (pattern.test(objectKey)) {
      return true
    }
  }
  return false
}

function json(status: number, body: Record<string, unknown>): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

async function readJsonBody(request: Request): Promise<Record<string, unknown> | null> {
  try {
    const parsed = await request.json()
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : null
  } catch {
    return null
  }
}

/**
 * Chunked upload, for anything the edge would refuse in one request.
 *
 * The checks are the same ones the single-shot route applies, just spread
 * across the lifecycle: token and rate limit happen before this is called,
 * package id and size are validated at create, duplicate rejection happens
 * at create (so a doomed upload is refused before any bytes move), and the
 * age-magic shape check runs on part 1.
 */
async function handleMultipart(request: Request, env: Env, pathname: string): Promise<Response> {
  const bucket = env.MIMIR_INTAKE_BUCKET

  if (pathname === '/v1/multipart/create') {
    const body = await readJsonBody(request)
    if (!body) {
      return rejected(400, 'bad_request')
    }

    const kind = String(body.kind ?? '')
    const route = ROUTES[`/v1/submit/${kind}`]
    if (!route) {
      return rejected(404, 'not_found')
    }

    const packageId = String(body.package_id ?? '')
    if (!PACKAGE_ID_RE.test(packageId)) {
      return rejected(400, 'invalid_package_id')
    }

    const totalBytes = Number(body.total_bytes)
    if (!Number.isFinite(totalBytes) || totalBytes < 0) {
      return rejected(411, 'length_required')
    }
    if (totalBytes > route.maxBytes) {
      return rejected(413, 'too_large')
    }
    if (Math.ceil(totalBytes / PART_SIZE) > MAX_PARTS) {
      return rejected(413, 'too_many_parts')
    }

    const objectKey = objectKeyFor(route, packageId)

    // Refuse before any bytes move, not after the whole upload.
    if (await bucket.head(objectKey)) {
      return rejected(409, 'duplicate')
    }

    const upload = await bucket.createMultipartUpload(objectKey, {
      httpMetadata: { contentType: 'application/octet-stream' },
    })

    return json(201, {
      accepted: true,
      object_key: objectKey,
      upload_id: upload.uploadId,
      part_size: PART_SIZE,
    })
  }

  if (pathname === '/v1/multipart/part') {
    const objectKey = request.headers.get('X-Mimir-Object-Key') ?? ''
    const uploadId = request.headers.get('X-Mimir-Upload-Id') ?? ''
    const partNumber = Number.parseInt(request.headers.get('X-Mimir-Part-Number') ?? '', 10)

    if (!isMintedObjectKey(objectKey)) {
      return rejected(400, 'invalid_object_key')
    }
    if (!UPLOAD_ID_RE.test(uploadId)) {
      return rejected(400, 'invalid_upload_id')
    }
    if (!Number.isInteger(partNumber) || partNumber < 1 || partNumber > MAX_PARTS) {
      return rejected(400, 'invalid_part_number')
    }
    if (!request.body) {
      return rejected(400, 'incomplete_body')
    }

    // The shape check the single-shot route does before storing anything. It
    // can only run on the first part, which is where the age header lives.
    let payload: ReadableStream<Uint8Array> | ArrayBuffer = request.body
    if (partNumber === 1) {
      const buffered = await request.arrayBuffer()
      if (!startsWithAgeMagicBytes(new Uint8Array(buffered))) {
        // Leave no orphaned upload behind when rejecting.
        await bucket.resumeMultipartUpload(objectKey, uploadId).abort().catch(() => {})
        return rejected(400, 'bad_content')
      }
      payload = buffered
    }

    const upload = bucket.resumeMultipartUpload(objectKey, uploadId)
    const uploaded = await upload.uploadPart(partNumber, payload)

    return json(200, { accepted: true, part_number: uploaded.partNumber, etag: uploaded.etag })
  }

  if (pathname === '/v1/multipart/complete') {
    const body = await readJsonBody(request)
    if (!body) {
      return rejected(400, 'bad_request')
    }

    const objectKey = String(body.object_key ?? '')
    const uploadId = String(body.upload_id ?? '')
    if (!isMintedObjectKey(objectKey)) {
      return rejected(400, 'invalid_object_key')
    }
    if (!UPLOAD_ID_RE.test(uploadId)) {
      return rejected(400, 'invalid_upload_id')
    }

    const rawParts = Array.isArray(body.parts) ? body.parts : null
    if (!rawParts || rawParts.length === 0) {
      return rejected(400, 'no_parts')
    }

    const parts = rawParts.map(item => {
      const record = (item ?? {}) as Record<string, unknown>
      return { partNumber: Number(record.part_number), etag: String(record.etag ?? '') }
    })
    if (parts.some(part => !Number.isInteger(part.partNumber) || part.partNumber < 1 || !part.etag)) {
      return rejected(400, 'invalid_parts')
    }

    const upload = bucket.resumeMultipartUpload(objectKey, uploadId)
    await upload.complete(parts)

    return accepted(objectKey)
  }

  if (pathname === '/v1/multipart/abort') {
    const body = await readJsonBody(request)
    if (!body) {
      return rejected(400, 'bad_request')
    }

    const objectKey = String(body.object_key ?? '')
    const uploadId = String(body.upload_id ?? '')
    if (!isMintedObjectKey(objectKey) || !UPLOAD_ID_RE.test(uploadId)) {
      return rejected(400, 'bad_request')
    }

    await bucket.resumeMultipartUpload(objectKey, uploadId).abort()
    return json(200, { accepted: true, aborted: true })
  }

  return rejected(404, 'not_found')
}

function startsWithAgeMagicBytes(bytes: Uint8Array): boolean {
  const needed = new TextEncoder().encode(AGE_MAGIC)
  if (bytes.length < needed.length) {
    return false
  }
  return new TextDecoder().decode(bytes.slice(0, needed.length)) === AGE_MAGIC
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
    const isMultipart = url.pathname.startsWith('/v1/multipart/')
    if (!route && !isMultipart) {
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

    // Chunked uploads share the rate limit and token gate above, then carry
    // their own validation -- their requests do not have a single package
    // body to measure.
    if (isMultipart) {
      return handleMultipart(request, env, url.pathname)
    }

    // Unreachable given the check above; narrows the type for what follows.
    if (!route) {
      return rejected(404, 'not_found')
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
