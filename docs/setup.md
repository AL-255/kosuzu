# Setup and daily use

Download an archive or wheel from [Releases](https://github.com/AL-255/kosuzu/releases). Extract the archive and run `kosuzu` from a terminal (`kosuzu.exe` on Windows); choose the matching Intel/Apple Silicon archive on macOS. The first launch prints an administrator access key and opens `http://127.0.0.1:8765`. Enter the key and keep the program running. Python 3.11+ users can install a downloaded wheel with `python -m pip install /path/to/package.whl`; developers can use `python -m pip install .`.

## Guided setup

After your first sign-in, a four-step guide opens automatically:

1. **GitHub connection:** enter the inventory repository, branch and your client token. Server administrators also enter a separate server token. People joining an existing server workspace only enter their own client token; the administrator manages the repository.
2. **Suppliers:** enable the distributors you use. API keys are optional. Each API supplier has switches for API access and public-page fallback; turn off API access to use public pages only. LCSC and Adafruit need no key. Disabled suppliers disappear from the importer.
3. **Review preferences:** turn off LLM review to check details yourself, or enable it with your provider key. Manual fallback is on by default, so missing keys and failed LLM requests keep the original part details and display an explicit warning. Turn fallback off to require a successful LLM review.
4. **Finish:** initialize a new database if needed, then check the saved GitHub connection. The server administrator's check verifies both client and server reads. Only a successful connection check enables Finish.

Each completed step is saved. Reloading resumes at that step; **Continue setup later** opens regular Settings. Reopen the guide with **Open step-by-step setup**. Supplier and LLM preferences belong to each browser/client profile, and disabling a feature retains saved keys for later use.

No supplier or LLM credentials are needed to start. In New component, choose **Enter details manually**, provide manufacturer, MPN and description, then check the datasheet, set location/quantity and confirm. No supplier lookup runs. LLM review runs only if enabled and configured; manual review is recorded as such in the inventory. Human confirmation and a saved review draft are required for every path.

## GitHub database

Create a **separate private repository**. It can be empty; Kosuzu can create its first inventory commit. Invite trusted collaborators with write access. The application source repository is not your production inventory.

Create separate fine-grained GitHub tokens for the server and each client, restricted to the database repository with **Contents: read/write**, **Pull requests: read/write**, and **Metadata: read**. Give the server **Commit statuses: read/write** and clients **Commit statuses: read** so desktop clients receive actionable server warnings through [GitHub statuses](https://docs.github.com/en/rest/commits/statuses). Your organization may require SSO/token approval.

In Settings, configure `owner/repository`, its branch (usually `main`), and the dedicated tokens. Save first, then click **Initialize database** once. It adds an empty snapshot only if absent, preserving existing inventory. For an empty repository, use its GitHub default branch; Initialize database creates the first commit using the [Contents API](https://docs.github.com/en/rest/guides/using-the-rest-api-to-interact-with-your-git-database). It does not replace existing files. All programs must use the same repo and branch.

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

On iPhone/iPad use **Safari → Share → Add to Home Screen**. The PWA supplies an icon, standalone layout, and cached application shell. v0.2 uses this PWA rather than an App Store binary. An unreachable server cannot accept new browser operations. The running service caches inventory and marks it stale when GitHub is unavailable; this is not disconnected iOS inventory editing.

Desktop programs submit directly to GitHub; the Linux server discovers their requests. Requests remain pending until a server runs. Use `--port` and `--state-dir` for multiple local instances. Default client state is `%LOCALAPPDATA%\Kosuzu\client` on Windows, `~/Library/Application Support/Kosuzu/client` on macOS, and `$XDG_STATE_HOME/kosuzu/client` or `~/.local/state/kosuzu/client` on Linux.

Session cookies are HttpOnly and SameSite, valid for 30 days. A separate HttpOnly identity cookie preserves the browser profile for a year, allowing saved credentials/outbox recovery after signing in again. Signing out revokes the session and deletes its credentials; sign in from that same browser and save your keys again to retry queued requests. Deleting all browser cookies loses access to that local profile, so synchronize first. Revoke a lost device's dedicated GitHub/LLM keys and clear its state.

## Supplier and LLM credentials

| Supplier | Connection | Lookup |
|---|---|---|
| DigiKey | Client ID, secret, and account ID from its [developer portal](https://developer.digikey.com/products/product-information-v4) | OAuth client credentials, v4 ProductDetails, exact MPN or packaging variation code. The account ID is required for two-legged OAuth. Account API access required. |
| Mouser | [Search API](https://www.mouser.com/en/api-search/) key | Exact MPN or Mouser code; image and datasheet links. |
| Arrow | Login and [API key](https://developers.arrow.com/api/index.php/site/page?view=Itemservice) | v4 search, MPN/item/source identity; imagery from the linked product page. |
| LCSC | Public product page | `C` followed by digits; structured metadata and specifications. |
| Adafruit | Public product page | Numeric product ID such as `3406` or `PID 3406`; exact product identity, image and review evidence. When manufacturer/MPN is absent, uses an explicitly labeled catalog identity (`Adafruit (catalog)`, `ADA-3406`) and warns you to verify it. |

Public pages can be blocked or changed. With public-page fallback enabled, DigiKey/Mouser/Arrow use structured product pages when API credentials are incomplete or the API request fails. With API access off, they only use public pages. Turn off fallback to stop instead of switching sources. Ambiguous part identities stop the import; they never trigger another-source lookup. Search pages may require an exact product URL. Arrow's separate image-page lookup can also be blocked. Failures are shown instead of guessing a part. Images are stored as external references and depend on supplier/CDN availability. Exact product URLs must belong to the selected supplier.

Save an OpenAI-compatible LLM base URL, model, and key. DeepSeek defaults to `https://api.deepseek.com` and its current documented [deepseek-flash model](https://api-docs.deepseek.com/quick_start/pricing/); enter a model enabled on your account. The adapter calls `/chat/completions` with [JSON output](https://api-docs.deepseek.com/guides/json_mode/) and disables DeepSeek thinking mode to keep normalization within the bounded output budget. Other providers do not receive that provider-specific field. Administrators can approve other HTTPS hosts.

The provider receives candidate metadata and supplier evidence. It normalizes fields/units, flags inconsistencies, and cannot replace provenance URLs. Review original data and warnings, check the datasheet, correct fields, assign a location and positive initial stock, then confirm. LLM review checks consistency rather than independently proving electrical truth. LLM keys are optional when manual review or manual fallback is selected. Fallback preserves the original details and records a warning; it does not claim an LLM checked them. Saving unreviewed metadata cannot bypass the confirmation gate.

## Boxes and stock placement

Open **Boxes → New box** to give a box a unique name, an optional HTTPS picture URL, and a description. Its request appears in My requests; the server applies it before it can receive parts. Edit box details from its card. Box edits detect stale versions rather than overwrite another person's changes.

A component's quantity is split between boxes, for example **BOXA: 10 · BOXB: 20**, with **30** pieces in total. Inventory shows each allocation, searches box names, and filters by box; View contents on a box card applies that filter. The stock count in a filtered row is the count in that box, followed by the overall total. Your location note remains separate descriptive metadata.

Select an **Initial box** during import review. With one stock placement, Adjust stock selects it for you. With several placements, you must choose which box receives an addition or supplies a removal. You can add stock to any existing box, including one the component has not used yet. The server checks the selected box's quantity; it cannot borrow from another box to cover a shortage.

Choose **Move between boxes** to move existing stock. Select a source and destination, then a positive quantity. Transfers preserve total stock and obey the same atomic/retry rules as stock changes. Empty placements disappear; the last placement stays available when a part runs out. **Unboxed** holds stock without an assigned box and is also a valid source/destination.

### Upgrade from v0.1

Update the server and all clients to v0.2 together before sending new requests. v0.2 reads schema 1 snapshots without changing counts or transaction receipts, assigns their stock to Unboxed, and writes schema 2 on the first applied transaction. v0.1 programs cannot read schema 2. Existing location notes are preserved. Pending schema 1 events remain readable; an adjustment without a box choice is rejected if the part now has multiple placements. Reject that request and submit a corrected request choosing a box. Back up the repository and server state before upgrading.

## Stock changes and errors

Search by MPN, supplier code, description, attributes, or location. **Adjust stock** creates an addition/removal with an optional note. Counts change only when the server applies the request. **My requests** shows queued, pending, applied, or rejected state; **Sync requests** retries saved operations and refreshes status. The running program retries even when its browser is closed.

An overspending removal stays open in the error queue with the available/requested stock and a fix prompt. All server web clients see warnings. Independent desktop clients receive a blocked request with the failure reason through GitHub commit statuses when their outbox synchronizes. Administrators can retry after fixing the cause, or reject and have the user submit a smaller removal. Credentials, network, branch rules, malformed events, and duplicate components also appear here. Existing manufacturer/MPN entries need stock adjustment rather than another component. Rejected requests are never reopened silently.

GitHub is authoritative. SQLite stores credentials, cache, queues, and drafts. Credentials are **plaintext at rest**, protected by private filesystem permissions; use disk encryption and private backups. Back up GitHub and server state, stopping the service for SQLite backups. Never commit local state. v0.2 targets snapshots below 1 MB (GitHub Contents limit) and fewer than 10,000 open proposals. Oversized/missing snapshots produce errors rather than replacement. Prune merged branches after audit/backup.


## Public search catalog (GitHub Pages)

On the **server**, sign in as administrator and open **Settings → A searchable public catalog**. Enable **Publish catalog with GitHub Pages**, then click **Save & publish catalog**. The saved database connection and server token are used; visitors need no GitHub token, supplier credentials, or LLM key.

Grant the server's fine-grained token **Pages: read/write**, in addition to its existing Contents/Pull requests/Commit statuses permissions, restricted to the database repository. GitHub Pages must be available for your repository and account plan. GitHub documents [Pages eligibility and public visibility](https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site) and [branch publishing configuration](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site).

The server creates a separate `kosuzu-pages` branch in the database repository and configures Pages to deploy from that branch's **/(root)**. It leaves the inventory branch, stock, and proposal history unchanged. Existing Pages sites using another source and preexisting unmanaged `kosuzu-pages` branches are preserved; use another database repository if they conflict. The publisher stores only static HTML/CSS/JavaScript, a catalog JSON snapshot, a publisher marker, and `.nojekyll` in the branch's current tree. It ships with the Python package and standalone server builds.

**The exported catalog is publicly readable, including when its database repository is private.** It includes manufacturer, part and supplier numbers, descriptions, category/package, attributes, images/product/datasheet links, location notes, quantities, and box names/descriptions/picture links/allocations. Transaction receipts, event notes, review logs, tokens and local settings are excluded. Enable publishing only for inventory you want to share. It is read-only; stock changes still use Kosuzu's authenticated request flow.

If the token lacks Pages permission, the server can still prepare/update the branch using Contents: write. Open the database repository's **Settings → Pages → Build and deployment → Source: Deploy from a branch → Branch: kosuzu-pages → /(root) → Save** using an account with the required access. Once manually configured, ordinary server branch updates trigger Pages deployments; the application shows “prepared” and configuration instructions because that token cannot inspect the site's status. Grant Pages permission to enable automatic configuration, a verified site URL/status, and the Unpublish action. The publisher does not change repository visibility.

After each successful server sync, enabled publishing exports the latest merged inventory. Identical data/assets do not create another commit. Publication failures appear in Settings, persist across restarts, and are retried on the next sync; stock transactions already applied stay applied. GitHub builds can take a few minutes. **Refresh snapshot** fetches the latest published JSON. Ordinary queries, including fuzzy part numbers and descriptions, run entirely in the visitor's browser against the loaded snapshot; they keep working if the network disconnects after loading. Search does not invoke an API or LLM. Filters cover category, box and positive stock, and results show individual box counts.

Turning off the publishing switch and saving stops updates but leaves the website online. **Unpublish website** removes the Pages site and turns off automatic publishing. Exported files remain accessible to repository readers through the branch and Git history. Unpublishing does not retract copies visitors already downloaded.

Developer checks: `node tests/catalog_search.js`, `python -m unittest tests.test_catalog -v`, and `python -m tests.catalog_browser_smoke`. To exercise the isolated publisher against an explicitly designated test database, run `python -m scripts.live_catalog_smoke --repo owner/test-database`; the hidden prompt does not save your token. It creates or updates the catalog branch/site, and verifies that the database branch's HEAD stays unchanged.
