import asyncpg
import urllib.parse as up


async def setup_database_pool(dsn: str):
    """Create and return an asyncpg pool from a DATABASE_URL-like DSN.

    Keeps the same heuristics used elsewhere: if query param `sslmode=require`
    is present, enable ssl=True; if host is localhost, disable SSL.
    """
    if not dsn:
        raise RuntimeError("DATABASE_URL not provided")

    url = up.urlparse(dsn)
    qs = up.parse_qs(url.query)
    sslmode = qs.get("sslmode", [None])[0]
    host = url.hostname or ""

    if sslmode == "require":
        ssl_arg = True
    elif host in ("localhost", "127.0.0.1"):
        ssl_arg = False
    else:
        ssl_arg = True

    pool = await asyncpg.create_pool(dsn=dsn, ssl=ssl_arg, min_size=5, max_size=10, timeout=60)
    return pool
