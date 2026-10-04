# Kosuzu v0.3.1

Publish a read-only GitHub Pages inventory catalog in the database repository. Visitors can enter a part number or description and search with fuzzy matching entirely in their browser, without tokens or LLM calls. The responsive catalog includes category/box/in-stock filters, specifications, supplier/datasheet/picture links, and quantities per box. Searches keep working after the loaded page loses its network connection.

Server administrators can enable publishing, publish immediately, see status/warnings, and unpublish from Settings. A separate `kosuzu-pages` branch updates after server synchronization; identical exports do not create commits. Existing Pages sites and unmanaged branches are preserved. Pages failures do not block stock merges. Tokens without Pages permission can prepare the branch for manual GitHub configuration.

Publishing explicitly exposes component metadata, stock, box information and location notes publicly. Receipts, transaction notes, review logs and local credentials are excluded. Stopping updates leaves the website online; unpublishing removes the website but preserves exported Git history. See `docs/setup.md` for permissions and visibility details.

Inventory remains schema 2, compatible with v0.2.0. Earlier v0.1 installations still need the documented server/client schema migration. Includes Windows, Linux, Intel/Apple Silicon macOS archives, Python wheel and source packages. iOS uses the existing Safari PWA. macOS binaries remain unsigned; credentials remain local plaintext protected by filesystem permissions. Supplier/manual/LLM and box workflows are unchanged.
