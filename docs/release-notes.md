# Kosuzu v0.2.0

Boxes now have unique names, pictures via HTTPS URLs, and descriptions. A part can hold separate quantities in several boxes, shown together in Inventory. Box cards show contents and totals, and Inventory can search/filter by box.

Stock additions and removals require a box choice when a part has several placements. Transfers move stock between boxes without changing totals. The server checks each selected box's availability, keeps retries idempotent, and rejects stale box edits. Box creation and edits use the existing GitHub request/outbox/error queue flow.

Adafruit joins DigiKey, Arrow, LCSC and Mouser. Enter a numeric product ID such as 3406 or PID 3406. Its public page supplies metadata, imagery and LLM/manual review evidence without a supplier key. Missing manufacturer/MPN fields use a labeled catalog identity with an explicit review warning.

**Upgrade the server and all clients together.** Existing schema 1 stock appears in Unboxed; the first applied transaction upgrades the snapshot to schema 2 while preserving counts, receipts and location notes. v0.1 programs cannot read schema 2. Pending old requests remain readable; ambiguous stock changes need replacement requests with a box choice. Back up inventory/server state before upgrading. See `docs/setup.md`.

Windows, Linux, Intel/Apple Silicon macOS archives, Python wheel and source packages are included. iOS uses Safari Add to Home Screen; desktop executables open a browser UI. macOS binaries are unsigned. Credentials remain local plaintext protected by filesystem permissions. Public supplier pages can block lookups; manual entry remains available. Snapshots should stay below 1 MB with trusted repository collaborators.
