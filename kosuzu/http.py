"""Bounded HTTP transport. Never expose upstream bodies containing credentials."""
import json
import ssl
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler, HTTPSHandler


class RemoteError(RuntimeError):
    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def secure_opener(redirects):
    # Frozen distributions carry a CA bundle; Python installs use OS trust.
    bundle = Path(__file__).with_name("cacert.pem")
    context = ssl.create_default_context(cafile=str(bundle) if bundle.is_file() else None)
    return build_opener(redirects, HTTPSHandler(context=context))


class Transport:
    def request(self, method, url, data=None, headers=None, raw=False):
        body = None if data is None else json.dumps(data).encode()
        req = Request(url, data=body, method=method, headers={"User-Agent": "Kosuzu/0.1", "Content-Type": "application/json", **(headers or {})})
        try:
            with secure_opener(NoRedirect()).open(req, timeout=25) as response:
                content = response.read(4_000_001)
                if len(content) > 4_000_000:
                    raise RemoteError("Upstream response exceeds 4 MB")
                if raw:
                    return content.decode("utf-8", errors="replace")
                return json.loads(content) if content else None
        except HTTPError as exc:
            messages = {401: "Authentication failed; check saved API credentials", 403: "Access denied, rate limited, or supplier blocked automated access", 404: "Resource not found", 409: "Concurrent update; retry", 422: "Upstream rejected request or concurrent update", 429: "Rate limited; retry later"}
            raise RemoteError(messages.get(exc.code, f"Upstream HTTP {exc.code}"), exc.code) from None
        except (URLError, TimeoutError, OSError):
            raise RemoteError("Network request failed; check connection and retry") from None
        except (json.JSONDecodeError, UnicodeError):
            raise RemoteError("Upstream returned invalid JSON") from None
