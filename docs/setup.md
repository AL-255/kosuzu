# Setup and daily use

Download an archive or wheel from [Releases](https://github.com/AL-255/kosuzu/releases). Extract the archive and run `kosuzu` from a terminal (`kosuzu.exe` on Windows); choose the matching Intel/Apple Silicon archive on macOS. The first launch prints an administrator access key and opens `http://127.0.0.1:8765`. Enter the key and keep the program running. Python 3.11+ users can install a downloaded wheel with `python -m pip install /path/to/package.whl`; developers can use `python -m pip install .`.

## GitHub database

Create a **separate private repository** with an initial README commit. Invite trusted collaborators with write access. The application source repository is not your production inventory.

Create separate fine-grained GitHub tokens for the server and each client, restricted to the database repository with **Contents: read/write**, **Pull requests: read/write**, and **Metadata: read**. Give the server **Commit statuses: read/write** and clients **Commit statuses: read** so desktop clients receive actionable server warnings through [GitHub statuses](https://docs.github.com/en/rest/commits/statuses). Your organization may require SSO/token approval.

In Settings, configure `owner/repository`, its branch (usually `main`), and the dedicated tokens. Save first, then click **Initialize database** once. It adds an empty snapshot only if absent, preserving existing inventory. A repository with no commits must be initialized in GitHub first. All programs must use the same repo and branch.

The server must be allowed to update Git references with merge commits. Strict linear history, required checks, or PR-only rules may require bypass rights for the server identity. Do not edit inventory snapshots or manually merge stock PRs. This release assumes trusted repository writers; GitHub write tokens are not a security boundary against malicious collaborators.

## Linux server running 24/7

Local trial: `kosuzu server --state-dir ./server-state --no-browser --interval 60`. The server polls GitHub, applies valid requests, and retains failures in a persistent queue. **Run server sync** starts an immediate pass. Restarting preserves its token, queue, and transaction receipts.

For deployment, install the Linux binary as `/opt/kosuzu/kosuzu`, create an unprivileged system user named `kosuzu`, and adapt [kosuzu.service](../deploy/kosuzu.service) and [Caddyfile](../deploy/Caddyfile). Replace `inventory.example.com` with your domain. Caddy supplies HTTPS and preserves that Host header. The example listens on loopback behind the proxy; do not expose the Python listener directly.

```sh
sudo install -d -o kosuzu -g kosuzu -m 700 /var/lib/kosuzu
sudo -u kosuzu /opt/kosuzu/kosuzu keys --state-dir /var/lib/kosuzu
sudo install -m 644 deploy/kosuzu.service /etc/systemd/system/kosuzu.service
sudo systemctl daemon-reload
sudo systemctl enable --now kosuzu
```

Keep the administrator key private; distribute the client access key to collaborators. Sign in at your HTTPS origin to configure the server token. Network listeners require an HTTPS `--public-url`; requests validate Host and Origin. Recover access keys locally with `kosuzu keys --state-dir /var/lib/kosuzu` as the service user. Without `--state-dir`, `keys` uses desktop client state.

## Web, desktop, and iOS clients

Browser users visit the Linux server's HTTPS origin, sign in with the client key, and configure their own client GitHub, LLM, and supplier credentials. Each browser profile has dedicated credentials separate from the server token.

On iPhone/iPad use **Safari → Share → Add to Home Screen**. The PWA supplies an icon, standalone layout, and cached application shell. v0.1 uses this PWA rather than an App Store binary. An unreachable server cannot accept new browser operations. The running service caches inventory and marks it stale when GitHub is unavailable; this is not disconnected iOS inventory editing.

Desktop programs submit directly to GitHub; the Linux server discovers their requests. Requests remain pending until a server runs. Use `--port` and `--state-dir` for multiple local instances. Default client state is `%LOCALAPPDATA%\Kosuzu\client` on Windows, `~/Library/Application Support/Kosuzu/client` on macOS, and `$XDG_STATE_HOME/kosuzu/client` or `~/.local/state/kosuzu/client` on Linux.

Session cookies are HttpOnly and SameSite, valid for 30 days. A separate HttpOnly identity cookie preserves the browser profile for a year, allowing saved credentials/outbox recovery after signing in again. Signing out revokes the session and deletes its credentials; sign in from that same browser and save your keys again to retry queued requests. Deleting all browser cookies loses access to that local profile, so synchronize first. Revoke a lost device's dedicated GitHub/LLM keys and clear its state.

## Supplier and LLM credentials

| Supplier | Connection | Lookup |
|---|---|---|
| DigiKey | Client ID, secret, and account ID from its [developer portal](https://developer.digikey.com/products/product-information-v4) | OAuth client credentials, v4 ProductDetails, exact MPN or packaging variation code. The account ID is required for two-legged OAuth. Account API access required. |
| Mouser | [Search API](https://www.mouser.com/en/api-search/) key | Exact MPN or Mouser code; image and datasheet links. |
| Arrow | Login and [API key](https://developers.arrow.com/api/index.php/site/page?view=Itemservice) | v4 search, MPN/item/source identity; imagery from the linked product page. |
| LCSC | Public product page | `C` followed by digits; structured metadata and specifications. |

Public pages can be blocked or changed. DigiKey/Mouser/Arrow use structured product pages when API credentials are absent. Search pages may require an exact product URL. Arrow's separate image-page lookup can also be blocked. Failures are shown instead of guessing a part. Images are stored as external references and depend on supplier/CDN availability. Exact product URLs must belong to the selected supplier.

Save an OpenAI-compatible LLM base URL, model, and key. DeepSeek defaults to `https://api.deepseek.com` and `deepseek-chat`; enter a model enabled on your account. The adapter calls `/chat/completions` with [JSON output](https://api-docs.deepseek.com/guides/json_mode/). Administrators can approve other HTTPS hosts.

The provider receives candidate metadata and supplier evidence. It normalizes fields/units, flags inconsistencies, and cannot replace provenance URLs. Review original data and warnings, check the datasheet, correct fields, assign a location and positive initial stock, then confirm. LLM review checks consistency rather than independently proving electrical truth. A key and completed review are required; saving unreviewed metadata cannot bypass failed imports.

## Stock changes and errors

Search by MPN, supplier code, description, attributes, or location. **Adjust stock** creates an addition/removal with an optional note. Counts change only when the server applies the request. **My requests** shows queued, pending, applied, or rejected state; **Sync requests** retries saved operations and refreshes status. The running program retries even when its browser is closed.

An overspending removal stays open in the error queue with the available/requested stock and a fix prompt. All server web clients see warnings. Independent desktop clients receive a blocked request with the failure reason through GitHub commit statuses when their outbox synchronizes. Administrators can retry after fixing the cause, or reject and have the user submit a smaller removal. Credentials, network, branch rules, malformed events, and duplicate components also appear here. Existing manufacturer/MPN entries need stock adjustment rather than another component. Rejected requests are never reopened silently.

GitHub is authoritative. SQLite stores credentials, cache, queues, and drafts. Credentials are **plaintext at rest**, protected by private filesystem permissions; use disk encryption and private backups. Back up GitHub and server state, stopping the service for SQLite backups. Never commit local state. v0.1 targets snapshots below 1 MB (GitHub Contents limit) and fewer than 10,000 open proposals. Oversized/missing snapshots produce errors rather than replacement. Prune merged branches after audit/backup.
