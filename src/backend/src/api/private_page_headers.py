"""Response headers shared by the token-scoped, script-free notification HTML pages."""


def private_page_headers(content_security_policy: str) -> dict[str, str]:
    """Return no-store, no-referrer and no-index headers around a page-specific CSP."""
    return {
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Robots-Tag": "noindex, nofollow",
        "Content-Security-Policy": content_security_policy,
    }
