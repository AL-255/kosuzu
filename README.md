# Kosuzu

Collaborative electronics inventory with GitHub as the database. A Linux server validates and merges transaction pull requests; clients import distributor parts, review them with a user-provided LLM, and propose stock changes.

Requires Python 3.11 or newer. No runtime dependencies.

```sh
python -m pip install .
kosuzu client
kosuzu server --state-dir /var/lib/kosuzu --no-browser
```

Desktop clients run a local web UI on Windows, macOS, and Linux. The server hosts the same responsive web app for browser and iOS use. iOS installation uses Safari's **Add to Home Screen** over HTTPS.

Development: `python -m unittest discover -s tests -v`.

This project is under active development toward its first release. See the setup, architecture, and release documentation added with that release for the supported deployment procedure.
