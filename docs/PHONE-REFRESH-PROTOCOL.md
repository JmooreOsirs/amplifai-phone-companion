# Phone source-page authority refresh

RC30 distinguishes a bounded recheck of previously current authority from an
uncertain or terminal authorization failure. It does not extend an approval,
transfer, or save deadline.

## Authority states

- `current`: the exact, unexpired run authority was verified.
- `refreshing`: a recheck of previously current, unexpired authority is in flight.
  Data delivery is denied. The model and bridge each enforce a 65-second bound;
  the original run expiry can end it sooner. Repeated commands do not renew it.
- `paused`: uncertain, malformed, refused, replaced, or timed-out authority.
  Starting another recheck does not make paused authority transient.

Concurrent status consumers for the same run join one request. Stop, expiry,
replacement, and timeout invalidate its token. Late replies cannot restore it.
All existing account, enrollment, approval, course, category, disclosure, and
fixed-deadline comparisons remain required.

## Typed response

Only an otherwise valid authenticated
`GET /v2/source/{category}?cursor={cursor}` may receive this HTTP 409 JSON:

```json
{
  "code": "phone_run_authority_refreshing",
  "handoffId": "<current handoff UUID>",
  "payloadSha256": "<current manifest SHA-256>",
  "category": "contacts",
  "cursor": 50,
  "retryAfterMs": 250
}
```

The response contains no records. Origin, token, transfer expiry, source
disposition, and cursor validity checks precede this response. Rejection cannot
advance a cursor, change selection or generation, acknowledge a save, or renew
any deadline. Legacy endpoints retain their existing response.

All other 409 responses remain terminal. Invalid credentials/origin retain 403;
closed, used, expired, or declined transfers retain 410.

## Website contract

The website checks the exact six-field shape, literal code and 250 ms hint,
current handoff/digest/category/cursor, and a 1 KiB response limit. Unknown fields
and untyped 409 responses are terminal.

Retry uses the identical request and current preview generation:

- 70-second total page deadline, never renewed.
- 30-second individual request/body deadline.
- At most 40 GET attempts.
- Delays of 250, 500, 1,000, then 2,000 ms, capped at 2,000 ms.
- Abort, Stop, and a replaced preview prevent further requests.
- No automatic source POST retry.

No checkpoint, saved count, or hash chain advances until a page and its exact
save receipt pass the existing validation.

## Compatibility and release order

| Website | Native | Result |
| --- | --- | --- |
| Updated | RC30 | Bounded retry of the exact typed refresh response |
| Updated | RC29 | Untyped 409 stays terminal |
| Previous | RC30 | Safe 409; the older caller may stop |
| Previous | RC29 | Existing behavior |

Deploy compatible website handling before admitting the new signed native
package and feeds. Preserve immutable RC29 artifacts. Do not move an active,
unsaved captured review between app versions.

## Qualification boundary

The mandatory native model harness uses the production 30-second timer,
process/command channel, production bridge, synthetic capture, and synthetic
account transport. The incident-sized conservative fixture contains 311,149
records across 3,114 compact pages: 52 Contacts, 38 Calls, and 3,024 Messages.
Larger records may require more pages under the existing byte cap.

Focused bridge checks cover typed responses, authentication, cursor and
generation conflicts, expiry, repeated commands, exact replays, and receipts.
Website qualification separately exercises its actual client against synthetic
transport and an isolated production bridge. These checks do not prove physical
iPhone transfer, customer review/Save, or production database acceptance.
