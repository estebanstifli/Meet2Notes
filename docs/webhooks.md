# Webhooks

This document is the authoritative contract for Meet2Notes outbound webhooks.
It is written for operators, integration authors, and coding agents changing the
application. Keep this file synchronized with the event catalog in
`domain/webhooks.py` and the API in `api/webhook_routes.py`.

## What a webhook is in Meet2Notes

Meet2Notes makes an **outbound** HTTP request to a configured destination. The
local PC does not need a public hostname, an open inbound port, or a TLS
certificate. Internet destinations must expose HTTPS with a valid certificate.
Plain HTTP is allowed only for loopback, or for a private network explicitly
trusted on that endpoint.

Webhooks are disabled by default. An endpoint must also be enabled and subscribe
to an event before any delivery is created. Each endpoint independently chooses
one of these content levels:

| Level | Data sent |
|---|---|
| `metadata` | IDs, timestamps, status, model/job and meeting metadata; transcript and note text is removed |
| `segments` | Metadata plus transcript segments; completed note Markdown is removed |
| `full` | All data available for the event, including completed note text |

Treat every enabled destination as a data export. Consent, retention and the
destination's own privacy policy remain the operator's responsibility.

## Delivery architecture

Capture and inference only write an event and its deliveries to the local
SQLite outbox. A separate asynchronous dispatcher performs network I/O. A slow
or unavailable endpoint therefore cannot block recording, Live ASR, final ASR,
diarization, summaries, or plugin hooks.

Final events are durable across restarts. Live segment batches are deliberately
ephemeral and expire after 120 seconds; old commentary is less useful than the
current conversation. Session-state events expire after 300 seconds. Delivery
history is retained for the configured number of days.

Delivery is **at least once**, not exactly once. Consumers must deduplicate by
`X-Meet2Notes-Delivery` or the CloudEvent `id`. Ordering is not guaranteed across
endpoints. A consumer that needs ordering should use `data.sequence` for Live
batches and tolerate gaps caused by expiry.

Responses `2xx` succeed. Network errors, 408, 425, 429 and 5xx are retried with
bounded exponential backoff; `Retry-After` is honored when valid. Redirects are
not followed. Other 4xx responses fail immediately. Timeout and maximum attempts
are configured per endpoint.

## Envelope and headers

Requests are `POST` with `Content-Type: application/cloudevents+json`. The body
uses CloudEvents structured mode:

```json
{
  "specversion": "1.0",
  "id": "event UUID",
  "source": "meet2notes://local-instance",
  "type": "com.meet2notes.live.segment.batch.v1",
  "time": "2026-08-13T12:34:56.000+00:00",
  "subject": "meeting/42",
  "datacontenttype": "application/json",
  "data": {}
}
```

Headers:

- `X-Meet2Notes-Event`: unversioned event name.
- `X-Meet2Notes-Delivery`: unique delivery ID; use it for idempotency.
- `X-Meet2Notes-Timestamp`: Unix seconds used in the signature.
- `X-Meet2Notes-Signature-256`: `sha256=<hex digest>`.

The secret is shown once when an endpoint is created or rotated and is stored in
the operating-system credential vault. Verify the signature over the exact raw
body bytes:

```python
import hashlib, hmac

signed = timestamp.encode() + b"." + raw_request_body
expected = "sha256=" + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
valid = hmac.compare_digest(expected, signature_header)
```

Reject stale timestamps (five minutes is a practical default) and remember
delivery IDs. Rotate a leaked secret from Settings; the old value stops working
immediately.

## Event catalog

| Event | When it fires | Typical minimum content |
|---|---|---|
| `live.session.started` | Capture and Live transcription started | metadata |
| `live.session.paused` | User paused capture | metadata |
| `live.session.resumed` | User resumed capture | metadata |
| `live.segment.batch` | New provisional, deduplicated Live segments | segments |
| `live.session.stopped` | Capture stopped; final work may continue | metadata |
| `recording.ready` / `recording.failed` | Import or recording registration ended | metadata |
| `transcription.final.completed/failed/cancelled` | Final transcript job ended | segments on success |
| `diarization.completed/failed/cancelled` | Speaker job ended | segments on success |
| `summary.completed/failed/cancelled` | Meeting-note job ended | full on success |
| `meeting.processing.completed` | Requested final pipeline stages finished | metadata |

Events are facts, not commands. Adding a new event is backward compatible.
Changing the meaning or shape of an existing event requires a new CloudEvent
type version (`.v2`) while retaining the old contract for a documented period.

## Live agent mode

An endpoint in `live_agent` mode may respond to `live.segment.batch` with up to
ten suggestions. Meet2Notes stores them separately and shows them beside the
meeting; it never silently edits the transcript or notes.

```json
{
  "suggestions": [
    {
      "kind": "question",
      "text": "Ask who owns the migration deadline.",
      "confidence": 0.84,
      "related_segment_ids": ["live-123-17"]
    }
  ]
}
```

The response body must be JSON and at most 64 KiB. Unknown fields are ignored.
Empty suggestions are ignored. Users can accept or dismiss each insight; an
accepted insight remains an annotation and does not execute an action.

## Network and threat model

- HTTPS certificate validation is always enabled for HTTPS.
- Proxy environment variables are ignored, redirects are disabled, URL
  credentials are rejected, and DNS targets are checked before every attempt.
- Private, link-local, reserved, multicast and unspecified addresses are blocked
  unless `allow_private_network` is enabled. Loopback is always permitted.
- Enabling private-network access is a trust decision and also permits HTTP; it
  should be used only for services controlled by the operator.
- Response excerpts are bounded and stored only for diagnostics. Remote text is
  untrusted and must be rendered as text, never as HTML.

DNS can change between validation and connection. Deployments with a hostile DNS
environment should terminate outbound integration through a controlled gateway
with its own egress policy.

## Settings and operations

Open **Settings → Webhooks** to enable the subsystem, add endpoints, select
events/content, send a test, rotate secrets, inspect deliveries and retry a
completed or failed attempt. Disabling the global switch stops new events and
pauses queued delivery; durable items resume when it is enabled again. Disabling
an endpoint prevents its queued deliveries from being sent.

The local API is rooted at `/api/webhooks`: catalog and settings, endpoint CRUD,
test/secret rotation, delivery history/retry, and meeting insight status. It is
an application-internal API for the loopback UI; it is not an inbound webhook
receiver or a remote administration API.

## Rules for future core changes

1. Never perform remote I/O on capture, inference, job-listener or plugin-hook
   paths. Publish to the outbox and return.
2. Never add transcript text to `metadata`; update content filtering tests when
   a payload gains a new text-bearing field.
3. Use stable event names and CloudEvent versions. Document payload examples.
4. Bound payload, response, timeout, attempts, retention and concurrency.
5. Preserve plugin hooks. Hooks are synchronous in-process extension points;
   webhooks are durable outbound integration points and neither replaces the
   other.
6. Add a migration, repository tests, signature/filter/retry tests, API tests,
   and a UI check for every material contract change.
