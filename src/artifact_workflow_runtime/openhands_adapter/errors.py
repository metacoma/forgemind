from __future__ import annotations

import httpx


class OpenHandsError(RuntimeError):
    pass


class OpenHandsHTTPError(OpenHandsError):
    def __init__(self, method: str, path: str, response: httpx.Response) -> None:
        self.method = method
        self.path = path
        self.status_code = response.status_code
        self.text = response.text
        self.allow = response.headers.get("allow")
        allow_suffix = f" allow={self.allow!r}" if self.allow else ""
        super().__init__(
            f"{method} {path} failed: HTTP {response.status_code}{allow_suffix}: {response.text}"
        )
