# SPDX-License-Identifier: GPL-3.0-or-later
import secrets
from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class Protection:
    def __init__(self, app: ASGIApp, origin: str, token: str):
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
        ):
            raise ValueError("SDR_ORIGIN must be an exact origin without a trailing slash")
        self.app = app
        self.origin = origin
        self.host = parsed.netloc
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        pairs = scope["headers"]
        headers = {k.decode("latin1"): v.decode("latin1") for k, v in pairs}
        duplicate = any(
            sum(k == name for k, _ in pairs) > 1
            for name in (b"host", b"origin", b"x-csrf-token", b"sec-fetch-site")
        )
        origin = headers.get("origin")
        safe = scope["method"] in {"GET", "HEAD", "OPTIONS"}
        code = None
        if duplicate or headers.get("host") != self.host:
            code = "invalid_host"
        elif origin is not None and origin != self.origin:
            code = "invalid_origin"
        elif headers.get("sec-fetch-site") not in {None, "same-origin", "none"}:
            code = "cross_site_request"
        elif not safe and (
            origin != self.origin
            or not secrets.compare_digest(
                headers.get("x-csrf-token", "").encode(), self.token.encode()
            )
        ):
            code = "operation_protection_required"
        if code:
            await JSONResponse({"code": code, "message": "Request rejected"}, status_code=403)(
                scope, receive, send
            )
            return

        async def protected_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"referrer-policy", b"no-referrer"),
                        (b"x-frame-options", b"DENY"),
                        (
                            b"content-security-policy",
                            b"default-src 'self'; script-src 'self'; "
                            b"style-src 'self'; connect-src 'self'; media-src 'self' blob:; "
                            b"img-src 'self' data:; object-src 'none'; base-uri 'none'; "
                            b"frame-ancestors 'none'",
                        ),
                        (b"cache-control", b"no-store"),
                    ]
                )
            await send(message)

        await self.app(scope, receive, protected_send)
