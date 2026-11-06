# ADR-0006: Admission and per-key load shedding

## Status
Accepted in Phase 3.

## Context
A queue can absorb a short burst, but cannot create compute capacity. Accepting
unlimited work turns overload into long waits, deadline expiry and retry storms.
Multiple gateways must enforce shared limits consistently.

## Decision
Authenticate inference submissions and validate the model/input. Apply a Lua
token bucket stored in rl:{key_id}; Redis TIME refills tokens without gateway
clock skew. Reply 429 with Retry-After when empty. Expire inactive bucket state.
Limits are shared by gateways and apply to inference submissions, not result polls.

Discover accepting workers using SCAN and typed heartbeat GETs. In one Lua
enqueue operation, recheck that a candidate heartbeat still exists, reject if
XLEN req:{model} >= max_queue_depth, initialize result:{id} as QUEUED with a Redis
deadline and TTL, then XADD. Redis executes this without interleaving competing
gateway enqueues. Reply 503 with Retry-After for no workers, retained-depth limit,
or exhausted gateway waiter capacity.

The retained-depth bound includes pending work, not only undelivered backlog.
This intentionally enforces a stricter total-work limit and requires no extra
reservation key. It uses the prescribed key schema. Track backlog and pending
separately for diagnosis; a delivered request is not finished work.

Use a configurable byte cap before JSON parsing, count actual body chunks, and
apply per-model character limits too. Async submission returns 202 and a request
ID rather than reserving a gateway waiter. GET /v1/results/{id} distinguishes
known pending work (202), final success (200), terminal error, and unknown/expired
cache IDs (404). A different API key sees 404 rather than another owner's result.

## Alternatives considered
- Only local rate limits: cheap, but adding gateways multiplies allowed traffic.
- Separate XLEN check and XADD: simple, but concurrent requests can all observe room
  and overrun the bound.
- Admission on undelivered backlog only: keeps compute fed, but permits much more
  total work when workers prefetch. Retained depth is simpler and conservative.
- Always enqueue: absorbs bursts, but makes prolonged overload waste time/compute
  and increases tail latency for clients that could otherwise succeed quickly.
- CPU utilization threshold: simple to read, but does not account for batch shape,
  queue age or worker warmup and is not a good admission policy by itself.

## Consequences
503 and 429 are useful explicit outcomes, not promises that a retry will succeed.
Clients should respect Retry-After, add jitter, and stop at their own deadline.
Rejected work never gets an accepted-result placeholder. Rate tokens are consumed
before admission, so repeated overload attempts still spend a caller's budget.

Heartbeat expiry is an imperfect capacity signal. There is a stale-liveness
window after SIGKILL; a request may still be accepted during that interval and
wait for recovery. A worker can stop after the admission check. Admission is not
a guarantee of completion or fairness.

INFER_ADMISSION_ENABLED=false exists only for the controlled E5 comparison.
Rate limits, worker prefetch bounds, body limits and deadlines remain active in
that experiment. Disabling admission is not a production recommendation.

The bounded in-memory body reader caps retained bytes, but reads still rely on
the HTTP server/ingress for slow-client timeouts. API keys in examples are public
local demonstration values, not committed production credentials.
