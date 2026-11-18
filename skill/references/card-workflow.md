# Adding and maintaining Movies cards

Use the app's MCP/API, never direct owner-store edits. The app id is `movies`; `cinequeue_*` MCP method names, legacy environment variables and data paths remain compatibility contracts. Discover the installed view/API base from its manifest or runtime registry. Daily and staged copies are separate: never infer one queue's records from the other or overwrite either.

## Resolve, enrich, verify

1. For an in-app request, mark its durable request `processing` before work. A successful dispatch is not completion.
2. Match the supplied title and release year exactly, including upcoming releases. Do not silently substitute an older film for a future year. IMDb suggestion lookup (`https://v3.sg.media-imdb.com/suggestion/x/<title>.json`) can resolve candidates; `https://letterboxd.com/imdb/<tt-id>/` gives a canonical Letterboxd card. Premiere year and general release year can differ.
3. If multiple films match, do not write a guess. Mark the request `blocked` with one short question (no URLs) and 2–6 tappable choices, or ask the owner if there is no app request.
4. Reuse an existing exact-title/year record or confirmed placeholder. Otherwise create one with `cinequeue_add`, then enrich via `cinequeue_update`. Do not leave a duplicate title-only card.
   With the `bump-existing` switch on (movies#23), an existing record also moves to the top: queued → `cinequeue_bump`, watched → `cinequeue_watch_again`, archived → restore with `cinequeue_archive` then `cinequeue_bump`; tell the owner it was already there and is now first.
5. Supply title, year, poster, canonical Letterboxd URL/slug, synopsis, genres, director, cast (normally 3–5), IMDb id/URL, runtime and trailer when available. JSON-LD `actor` is a useful cast source when a page parser misses it. Do not fabricate missing fields.
6. Keep the owner's viewing guidance in `notes`, separate from the synopsis. Put requested episode highlights in `featured_episodes` with title/season/episode/runtime when known.
7. Populate generic `rating`, `rating_count`, `rating_source: imdb` only from IMDb's official `title.ratings.tsv.gz`. Never relabel a Letterboxd score. When using `scripts/enrich_imdb_ratings.py`, produce a scratch preview, verify the target association and unchanged source hash, then promote only that card's fields through MCP. Never replace the whole live store with a preview containing unrelated drifting ratings.
8. Read the card back with `cinequeue_get`, verify queue position/count and no duplicate, inspect the rendered detail card, then mark the durable request `completed`. Leave unavailable ratings blank.

## Queue and request behaviour

- To move a card, read the exact instance's queue, resolve its id, call `cinequeue_bump`, then verify both API order and the rendered default queue sort. Preserve all unrelated metadata/history.
- Archive through MCP instead of deleting. If an explicit deletion is required, save the single target's API response for undo before the mutation.
- Already-fulfilled stale requests can be closed as `completed` after reading the existing card. Retry genuinely unfulfilled stalled requests and verify them before completion.
- Completed/dismissed request cards disappear: the film card is the receipt, not an extra success message. Blocked requests show the question, choices and Skip. Failed/unsent/stalled requests offer retry and dismiss; in-flight requests show a short finding message. Do not surface raw agent diagnostics/URLs in cards.
- Request status/action changes belong to the app's durable API. Keep MCP schemas, API documentation, UI and add-request prompts in agreement. Never send a Movies request to an unrelated task service.
- No live agent submissions during QA. Use temporary stores and injected dispatch fakes.

## Maintenance invariants

- Updates preserve populated and deliberately emptied owner stores. Seed demo content only when the data file does not exist.
- Build/release from committed source on a named PR branch. Keep the previous immutable release and an accurate source receipt for rollback. Never copy the previous receipt into a new release.
- Private image publication does not establish pull authorization; verify from the actual installer. Never make private owner data or an app public to bypass authentication.
- Use shared WizardApp navigation/theme/update controls, not copied platform code.
- Keep runtime in the existing compact metadata line (`YEAR · Genre · N min`, detail `Genre / Genre · YEAR · N min`). No new runtime pill/card.
- Secondary detail actions (including bump-to-top) belong in the existing compact link/action row, not standalone action cards.
