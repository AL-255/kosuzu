# Architecture and supplier extensions

Python's HTTP service serves a vanilla JavaScript PWA with no third-party runtime dependencies. `kosuzu client` opens a local browser interface on Windows/macOS/Linux. `kosuzu server` adds periodic merging and the persistent error queue. iOS uses the HTTPS PWA.

## Atomic GitHub transactions

The database contains `inventory.json` and immutable `events/<uuid>.json`. Clients create `kosuzu/<uuid>` branches and PRs adding exactly one event. Deltas avoid stale count overwrites. A durable outbox pins each request to its original repo/branch. Stable IDs recover after lost branch, commit, PR, and HTTP responses.

The server validates proposal state, base/source repository, branch/filename identity, all changed files, event schema, component identity, review record, and current stock. It refuses unrelated edits, drafts, forks, malformed payloads, duplicate components, and negative stock.

It reads current base SHA, applies the reducer, constructs the event/snapshot tree, creates a two-parent merge commit (base plus reviewed PR head), and advances the branch with `force: false`. This uses the [Git References API's fast-forward guarantee](https://docs.github.com/en/rest/git/refs#update-a-reference). GitHub recognizes the incorporated PR head as merged.

A competing write rejects the stale ref update. The server rereads and revalidates stock, up to four attempts. Receipts map IDs to canonical SHA-256 payload hashes: same payload is a no-op; changed content under an old ID fails. Crash/retry after a successful update cannot repeat a delta. Cross-process safety comes from Git history rather than the local lock.

Snapshot and event change atomically. Ordering follows successful commits rather than client clocks. History/events form the audit trail. Manual snapshot changes or manual PR merges bypass these invariants; v0.1 trusts repository collaborators.

## Credentials and HTTP

SQLite state uses a 0700 directory/0600 file on POSIX and user-profile ACLs on Windows. Credentials are plaintext at rest; protect disks and backups. They never enter stock events, commits, public settings, browser storage, API responses, or request logs. Server-hosted client profiles live on Linux, identified by HttpOnly SameSite cookies. Desktop profiles stay on each user's machine. Server/client GitHub tokens are separate.

HTTPS uses Secure cookies and an explicit origin. Host/Origin checks plus JSON/custom headers protect mutations; there is no cross-origin API. CSP blocks inline scripts/framing/arbitrary script origins. Product requests only accept selected-supplier HTTPS hosts, including redirects. Credentialed requests do not follow redirects. LLM hosts require administrator approval. Images load externally without application credentials.

Queue entries identify a repo/PR or system sync failure and survive restarts. Errors omit upstream bodies and credentials. The periodic worker serializes local merges, checks closed proposals, and retries outboxes. The server publishes a `kosuzu/inventory` commit status so independent desktop clients can show blocked proposals and actionable failure descriptions. Reporting failures have their own queue entries; they cannot cause an applied delta to repeat and retry on later passes.

## Extend suppliers

Declare an adapter class with `key`, `name`, `domains`, `credential_fields`, and:

```python
lookup(code, credentials, transport, product_url="") -> (candidate_dict, evidence_text)
```

Use `@register` from `kosuzu.suppliers` and import your module at startup. Settings discovers adapters/credential fields automatically. Use `draft()` for normalized metadata, image/datasheet/source URLs, and electrical attributes. Verify exact identity and reject ambiguity; add representative fixture tests. Never log credentials or include them in LLM evidence.

`llm.refine` sends bounded untrusted evidence requesting JSON, validates the result, prevents provenance edits, flags identity changes, and saves a review draft requiring human confirmation. Profile feature switches enforce disabled suppliers, public-only/API-required modes and explicit manual fallback. Supplier API failures can fall back to public pages; ambiguous identities do not. Disabled LLM review sends no provider request; missing keys or failed output can retain validated original metadata with `review.model = "manual"` and an explanatory warning. Manual entry uses the same saved draft and confirmation path. The setup wizard stores its completed step per profile and checks saved GitHub connections before finishing. `Service.create` retrieves the actual saved draft; clients cannot fabricate a review by supplying metadata. Manual GitHub writers can forge events, which remains part of the trusted-collaborator model.

## Verification

Unit/HTTP tests cover reducer invariants, REST Git DAG races, retries, all suppliers, LLM output, persistent queues/outboxes, roles, credentials, auth/CSRF, and assets. `python -m tests.browser_smoke` exercises resumed guided setup, feature switches, connection failures, manual and LLM import/review/submission, stock changes, oversell handling, rejection, and responsive layout in Chromium/iPhone-sized WebKit using deterministic external-service fixtures. These tests are not live paid-provider certification.

`scripts/live_github_smoke.py` optionally runs real GitHub operations in a scratch branch and cleans up temporary branches. `scripts/package_smoke.py` launches built executables to verify startup/health/bundled assets. Tag CI tests, builds wheel/source/platform distributions, and publishes a release with checksums.
