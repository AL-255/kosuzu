# Kosuzu

Collaborative electronics component inventory backed by GitHub pull requests. Import distributor parts, review them yourself or with your LLM, and keep shared shelves in sync.

- Step-by-step setup with a saved progress checkpoint and GitHub connection check.
- Modular DigiKey, Arrow, LCSC, Mouser, and Adafruit importers with product images, optional user-key LLM refinement, and human confirmation.
- Supplier/API switches, public-page fallback, and manual part entry without supplier or LLM credentials.
- Named boxes with pictures/descriptions, quantities per box, explicit selection for parts in multiple boxes, and stock transfers.
- Atomic quantity updates prevent concurrent overspending; persistent client outboxes recover from network failures.
- Linux server automatically applies valid PRs and exposes warnings plus retry/reject actions in a persistent error queue.
- Responsive web UI, standalone Windows/macOS/Linux clients, and an installable iOS PWA.

Requires Python 3.11 or newer. No runtime dependencies.

```sh
python -m pip install .
kosuzu client
kosuzu server --state-dir /var/lib/kosuzu --no-browser
```

Download a platform archive or Python wheel from [Releases](https://github.com/AL-255/kosuzu/releases). Extract the archive and run `kosuzu` (`kosuzu.exe` on Windows) from a terminal. The first launch prints an access key and opens the local web UI. Create a separate GitHub database repository, follow the setup guide to save dedicated GitHub tokens and initialize the library. Supplier and LLM keys are optional; their features can be enabled later in Settings. The server hosts the same responsive app for browsers and iOS via Safari **Add to Home Screen** over HTTPS.

Development: `python -m unittest discover -s tests -v`.

Follow [setup and deployment](docs/setup.md) for permissions, API keys, a systemd service, and HTTPS/iOS installation. See [architecture and supplier extensions](docs/architecture.md) and [release notes](docs/release-notes.md).

Browser regression: install `requirements-dev.txt`, run `python -m playwright install --with-deps chromium webkit`, then `python -m tests.browser_smoke`. Build Python packages with `python -m build` or standalone executables with `python scripts/build_distribution.py`. Pushes/PRs run regression CI across Windows/macOS/Linux and desktop/mobile browsers. Tags `v*` test and publish distribution packages with checksums.

Optional live import check: `python -m scripts.live_import_smoke` retrieves LCSC C25804 and reviews it with DeepSeek. Enter your key at the hidden prompt; the script does not save it. This makes a billable API request. Use `--code` or `--model` to test another part or model.

iOS support is a PWA; desktop executables open a local browser interface. Credentials are plaintext at rest with private filesystem permissions. Supplier pages may block automation; use available API credentials. LLM reviews check consistency and require datasheet confirmation. v0.2 targets snapshots below 1 MB and trusted repository collaborators.

Upgrading to v0.2: update the server and all clients together. Legacy inventory is read with stock in Unboxed and written as schema 2 on the first applied transaction. Pending legacy requests remain valid, but ambiguous box changes require a corrected request. See the setup guide for box use and migration.
