import asyncpg
import urllib.parse as up


async def setup_database_pool(config):
    """Initializes and returns a database connection pool.

    Uses the raw DATABASE_URL as a DSN for asyncpg.create_pool and derives
    an appropriate `ssl` value from the URL query (sslmode=require) or the
    hostname (localhost -> no ssl).
    """
    dsn = config.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not provided in config")

    url = up.urlparse(dsn)
    qs = up.parse_qs(url.query)
    sslmode = qs.get("sslmode", [None])[0]
    host = url.hostname or ""

    # Determine SSL behaviour:
    # - if sslmode=require in query -> True
    # - if host is localhost/127.0.0.1 -> False (local dev)
    # - otherwise default to True (prefer secure connection for remote DBs)
    if sslmode == "require":
        ssl_arg = True
    elif host in ("localhost", "127.0.0.1"):
        ssl_arg = False
    else:
        ssl_arg = True

    pool = await asyncpg.create_pool(
        dsn=dsn,
        ssl=ssl_arg,
        min_size=5,
        max_size=10,
        timeout=60,
    )
    return pool