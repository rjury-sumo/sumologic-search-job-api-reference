# Slide: Discover Partitions via the Partitions API

## Subtitle
List, match, and confirm — the fast path to finding the right index for a search

## The idea

Every partition (index) in an org is enumerable through a single read-only API
call — no scanning required. Pull the full list once, then match it against
the use case you're searching for by partition **name** and **routing
expression**, instead of guessing `_index=` values or scanning raw logs to
find where data lives.

```
GET /api/v1/partitions
```

Each entry returns:

| Field | Meaning |
|---|---|
| `name` | The value used to scope a search (`_index=<name>`) |
| `routingExpression` | The metadata filter that routes logs into this partition |
| `analyticsTier` | `Continuous`, `Infrequent`, etc. — determines query cost/behavior |
| `isActive` | Excludes decommissioned partitions when filtering |

## Requirement

Listing partitions requires a **list-partitions** permission on the
credential/role in use. Without it, this API returns nothing usable and
discovery has to fall back to other techniques (e.g. sampling raw logs and
reading the routed partition off individual events).

## Where this strategy shines

It works very well when an org's partitions are **named and scoped to align
with log use cases** — e.g. a partition literally named `cloudtrail` with
`routingExpression: _sourceCategory=aws/cloudtrail*`. In that world, a plain
keyword match against the partition name or routing expression reliably
finds the right partition for a given search, with zero scan cost and no log
sampling needed.

## Where a naive match falls short

Not every routing scope is a simple, readable filter. When routing expressions
are complex — built from multiple conditions, or scoped on a **tag field
computed by a field extraction rule** rather than a raw metadata field — the
partition name or routing text alone won't obviously match the keyword you're
searching for. In these cases, a more advanced match is needed: sample actual
log events for the use case and inspect which partition they landed in,
rather than relying on the partition list text alone.

## Implementation options

| Route | How |
|---|---|
| API | `sumo_search_client.py`'s `list_partitions()` (wraps `GET /api/v1/partitions`) |
| `sumosearch` CLI | `sumosearch discover partitions --grep <keyword>` |
| Sumo MCP | `listPartitions` tool |

## Talk track

The partitions list is the cheapest possible discovery step — one API call,
no scan cost. It's a great primary strategy when your partition and routing
design is use-case-aligned and self-descriptive. But it's a text match against
configuration, not against data — when routing logic gets complex or hides
behind computed tag fields, fall back to sampling real events and reading the
partition they actually resolved to.
