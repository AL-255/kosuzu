# Kosuzu v0.1.0

First release: GitHub transaction PRs, atomic stock updates, concurrency validation, and idempotent retries; modular DigiKey/Arrow/LCSC/Mouser importers with images and user-key LLM review; Linux synchronization server with persistent error queue; responsive web client, iOS PWA, and Windows/Linux/macOS executables; separate saved server/client tokens; regression and tag-triggered packaging CI.

Extract your platform archive and run `kosuzu` from a terminal (`kosuzu.exe` on Windows). Linux service: `kosuzu server`. Python users can install the wheel. See `docs/setup.md` for GitHub permissions and HTTPS deployment.

iOS uses Safari Add to Home Screen. Desktop executables open a local browser UI. macOS binaries are unsigned; signing/notarization is not included. Suppliers may block public pages, requiring supported API credentials. LLM review needs your API key and checks consistency followed by your datasheet confirmation. Credentials are local plaintext protected by filesystem permissions. v0.1 targets snapshots below 1 MB and trusted collaborators.
