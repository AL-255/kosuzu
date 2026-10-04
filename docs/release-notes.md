# Kosuzu v0.1.1

Guided onboarding now saves progress across reloads and walks through GitHub, suppliers, review preferences, and a final connection check. Supplier and LLM credentials are optional. Initialize database can create the first commit in an empty GitHub repository.

Enable or disable each supplier, choose public pages or API access, and control public-page fallback. Turn LLM review off to check details yourself, or enable manual fallback when the key is missing or the provider fails. Manual component entry works without supplier credentials. Every import still requires a saved review draft and explicit human confirmation, and fallback warnings identify what was checked.

Windows, Linux, Intel/Apple Silicon macOS archives, a Python wheel, and source packages are included. Install the update and restart the program; existing inventory and saved credentials are retained. The guide opens on first sign-in after this update and can be reopened from Settings. No database migration is required.

See `docs/setup.md` for GitHub permissions and HTTPS deployment. iOS uses Safari Add to Home Screen. Desktop executables open a local browser UI. macOS binaries are unsigned. Supplier public pages may block lookups; manual entry remains available. Credentials are local plaintext protected by filesystem permissions. v0.1 targets snapshots below 1 MB and trusted collaborators.
