# Redis Streams: the work ledger

A stream is an ordered sequence of entries. Each entry has an ID and string
fields. The ID normally begins with Redis's millisecond timestamp and ends with
a sequence number. Our payload field contains JSON; Python objects never cross
the process boundary directly.

A consumer group tracks delivery for one logical application. All model workers
join the workers group. Within that group, a new entry is delivered to one
consumer, not broadcast to all of them. An independent group could read the same
stream separately, but we do not create one per replica: doing so would duplicate
every inference across replicas.

XREADGROUP with > means “entries not yet delivered to this group.” Delivery adds
an entry to the pending-entry list (PEL): a ledger of who owns unacknowledged work.
It does not remove the entry from the stream. XACK removes the pending record;
XDEL removes the stream entry. They are different operations with different jobs.

Our completion order is response write, XACK, XDEL. A worker crash before the
response leaves pending work. A crash after response but before ACK leaves pending
work too, even though the client might already have succeeded. Retrying can
therefore execute an input twice. This is the reason to design inference as
idempotent: repeating it should not create an extra external side effect such as
charging a card. Random model sampling can still change the output; idempotency
here does not promise identical text from a stochastic model.

Phase 1 records pending work but does not reclaim it. A new worker reads > and
therefore skips entries already delivered to a dead consumer. Phase 3 will use
XAUTOCLAIM to transfer sufficiently idle pending work, inspect delivery counts,
and send repeatedly failing entries to a dead-letter queue. “Idle” must be long
enough that slow live inference is not mistaken for a dead worker.

| Command used in E1 | Interpretation |
|---|---|
| XINFO STREAM req:fake-classifier | Stream length, first/last entry and group count |
| XINFO GROUPS req:fake-classifier | Group pending count and delivery cursor |
| XPENDING req:fake-classifier workers | Summary of delivered but unacknowledged work |
| XLEN req:fake-classifier | Retained undelivered plus in-flight entries in normal operation |

Run the executable [E1 drill](../../scripts/experiment_e1.py) using the command in
[EXPERIMENTS](../EXPERIMENTS.md). It uses a disposable stream/model name and removes
only its own keys. The drill inspects a successful request and a worker killed
after delivery. It also sends an invalid API key and requests an unknown model,
so the teaching lessons can connect validation to observable outcomes.

An inbox uses ordinary XREAD, not XREADGROUP: exactly one gateway task owns its
reader. Its cursor advances after each entry. Initializing a concrete cursor
before accepting requests avoids the ambiguity of repeatedly starting from $,
which means “new entries after this read begins.”

Redis persistence is a separate concern from stream delivery. The Compose
configuration enables AOF, which logs writes, on a named volume. This is a useful
local durability baseline, not protection against all disk failures or loss of
the machine. A consumer group and a durable stream are also not an exactly-once
transaction spanning HTTP delivery and model execution.

Further reading: [Redis Streams official documentation](https://redis.io/docs/latest/develop/data-types/streams/).
The link was checked during Phase 1. Runtime versions and measured command
outputs are recorded in [EXPERIMENTS](../EXPERIMENTS.md); latest documentation can
describe features beyond our tested Redis 7 runtime.
