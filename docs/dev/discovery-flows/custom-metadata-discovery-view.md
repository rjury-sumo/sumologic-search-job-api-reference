# Slide: Discover Faster with a Custom Metadata View

## Subtitle
Pre-aggregate log metadata once, query it for free instead of scanning raw logs

## The idea

Build a **scheduled view** that runs every 15 minutes and pre-aggregates metadata
across all your data — source category, partition/index, data tier, byte and event
counts, plus any custom tags you care about (service, team, environment). Point
discovery and exploration queries at this view instead of scanning raw logs.

## Why it's worth doing

- **One query, two answers** — a single lookup returns both *what data exists*
  (source categories) and *where it lives* (partition/index), instead of two
  separate exploration steps.
- **Custom columns = custom filters** — tag by service, team, or environment at
  view-build time and filter on it later, even though that dimension isn't in any
  standard index.
- **Free to query** — the view is pre-aggregated, so repeated discovery queries
  against it cost nothing to scan.
- **Best fit when cross-referencing is required** — because the view's schema
  is yours to define, one query can correlate dimensions no built-in
  discovery tool combines in a single row: source category *and* partition
  together, or a custom field like `team`/`owner` alongside standard
  metadata. The data-volume index is single-dimension per query and the
  partitions API only returns partition config — neither can join across
  dimensions the way a custom view can.

## Example view definition

```sql
_datatier=all
| timeslice 15m
| count as events, sum(_size) as bytes by
   _timeslice, _sourceCategory, _view,
   _sourceHost, _source, _collector, service, team, environment
| _view as index | fields -_view        -- _view is a reserved word
| save view metadata_discovery_v1
```

## Searching the view

```sql
-- What exists, and how much of it, in the last day
_view=metadata_discovery_v1
| sum(bytes) as total_bytes, sum(events) as total_events
  by _sourceCategory, index, service

-- Narrow to one service
_view=metadata_discovery_v1 service=checkout
| sum(bytes) as total_bytes by _sourceCategory, index
```

## Querying rules to remember

- Never bare `count` on an aggregate view — use `sum()` on the pre-aggregated
  column instead.
- Put filter fields in the query **scope** (`_view=name field=value`), not in a
  `where` clause — much faster.
- Views support `field=value` filters only, not free-text keyword search.
- Expect roughly a 1-minute processing delay — not suited to real-time alerting.

## Where it doesn't apply

This is a **metadata discovery** tool, not a search-everything tool. It can't help
you find a specific transaction ID, user ID, or other high-cardinality value buried
inside a log message — that data was never captured in the aggregation. For highly
selective lookups like that, you still need a targeted raw-log search.

## Implementation options

Building the view itself is an admin action outside all three routes below —
Sumo Logic UI or the Content Management / Scheduled Views REST API, not the
Search Job API. Once built, querying it is a standard search:

| Route | How |
|---|---|
| API | `sumo_search_client.py`'s `run_search()` scoped to `_view=metadata_discovery_v1 ...` |
| `sumosearch` CLI | `sumosearch search run '_view=metadata_discovery_v1 ...' --from -1h --to now` |
| Sumo MCP | `runLogSearch` tool with the same query text |

## Talk track

Most teams explore data by running broad, expensive raw-log searches every time
they need to answer "what do we have, and where." A metadata discovery view turns
that into a standing, nearly-free lookup table — build it once, and every future
discovery question becomes a cheap query against pre-aggregated rows instead of a
fresh scan.
