"""
db_pool.py — SQLAlchemy-backed connection pool for Flask / Gunicorn.

Strategy
--------
We use SQLAlchemy's QueuePool *only for connection management* — not as an ORM.
`engine.raw_connection()` returns a real DBAPI connection (psycopg2 or pg8000)
that the existing ParseHubDatabase cursor code can use without modification.

This gives us:
  • pool_size        – connections kept open permanently per worker
  • max_overflow     – extra connections allowed under burst load
  • pool_timeout     – seconds to wait before raising "pool exhausted"
  • pool_recycle     – close & reopen connections older than N seconds
  • pool_pre_ping    – test connection before handing it out (prevents stale conn bugs)

Connection math for Railway (100-connection limit)
---------------------------------------------------
  2 workers × (pool_size=2 + max_overflow=1) = 6 total connections
  With generous headroom: 94 free connections for other processes / Railway internals.

Zero connections at import time
--------------------------------
The engine is created lazily inside get_engine(), called only when the first
actual HTTP request arrives.  Gunicorn workers that fork will each create
their own engine independently (forking a live engine would corrupt the pool).
"""

import os
import logging
import threading
from contextlib import contextmanager
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)

# ── Pool Configuration (tune via Railway env vars) ─────────────────────────────
_POOL_SIZE     = int(os.getenv('DB_POOL_SIZE',     '2'))   # permanent connections
_MAX_OVERFLOW  = int(os.getenv('DB_MAX_OVERFLOW',  '1'))   # burst connections
_POOL_TIMEOUT  = int(os.getenv('DB_POOL_TIMEOUT',  '10'))  # wait timeout (seconds)
_POOL_RECYCLE  = int(os.getenv('DB_POOL_RECYCLE',  '300')) # recycle after 5 minutes
_POOL_PRE_PING = True                                       # always test before use

# ── Globals ────────────────────────────────────────────────────────────────────
_engine = None
_engine_lock = threading.Lock()


def _build_engine_url(database_url: str) -> str:
    """
    Translate any postgres:// or postgresql+pg8000:// URL into the correct
    SQLAlchemy dialect URL.

    Priority:
    1. If psycopg2 is importable → use postgresql+psycopg2://
    2. Otherwise                 → use postgresql+pg8000://
    """
    try:
        import psycopg2  # noqa: F401
        driver = 'psycopg2'
    except ImportError:
        driver = 'pg8000'

    parsed = urlparse(database_url)
    # Replace scheme — keep everything else
    scheme = f'postgresql+{driver}'
    new_url = urlunparse((scheme, parsed.netloc, parsed.path,
                          parsed.params, parsed.query, parsed.fragment))
    return new_url


def get_engine():
    """
    Return the process-level SQLAlchemy engine, creating it on first call.

    IMPORTANT: Never call this at module import time.
    Call it only inside a request handler or the lazy initializer.
    """
    global _engine
    if _engine is not None:
        return _engine

    with _engine_lock:
        if _engine is not None:   # double-check after acquiring lock
            return _engine

        db_url = os.getenv('DATABASE_URL', '')
        if not db_url:
            raise RuntimeError(
                '[db_pool] DATABASE_URL is not set. '
                'Add it to Railway → backend service → Variables.'
            )

        # Validate production safety
        if os.getenv('NODE_ENV') == 'production' or os.getenv('RAILWAY_ENVIRONMENT'):
            lower = db_url.lower()
            if 'localhost' in lower or '127.0.0.1' in lower:
                raise RuntimeError(
                    f'[db_pool] DATABASE_URL points to localhost in production: {db_url}'
                )

        try:
            from sqlalchemy import create_engine, event
            from sqlalchemy.pool import QueuePool

            sa_url = _build_engine_url(db_url)
            logger.info(f'[db_pool] Creating engine (driver={sa_url.split("+")[1].split(":")[0]})')

            engine = create_engine(
                sa_url,
                poolclass=QueuePool,
                pool_size=_POOL_SIZE,
                max_overflow=_MAX_OVERFLOW,
                pool_timeout=_POOL_TIMEOUT,
                pool_recycle=_POOL_RECYCLE,
                pool_pre_ping=_POOL_PRE_PING,
                connect_args={
                    'connect_timeout': 10,  # fail fast if PG is unreachable
                },
            )

            # Verify connectivity at startup (one connection, then returned to pool)
            with engine.connect() as probe:
                probe.execute(__import__('sqlalchemy').text('SELECT 1'))
            logger.info(
                f'[db_pool] Engine ready: pool_size={_POOL_SIZE}, '
                f'max_overflow={_MAX_OVERFLOW}'
            )

            _engine = engine
        except ImportError:
            # SQLAlchemy not installed — fall back to bare psycopg2 / pg8000
            logger.warning('[db_pool] SQLAlchemy not found, using fallback bare pool')
            _engine = _build_fallback_pool(db_url)

    return _engine


# ── Raw DBAPI connection helpers ───────────────────────────────────────────────

@contextmanager
def get_db_connection():
    """
    Context manager — borrow a raw DBAPI connection from the pool.

    The connection has autocommit=True to match the existing ParseHubDatabase
    behaviour.  It is returned to the pool when the `with` block exits.

    Usage:
        from db_pool import get_db_connection
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
    """
    engine = get_engine()

    # SQLAlchemy engine
    if hasattr(engine, 'raw_connection'):
        raw = engine.raw_connection()
        raw.autocommit = True
        broken = False
        try:
            yield raw
        except Exception:
            broken = True
            raise
        finally:
            try:
                raw.close()   # returns to pool (not a real close)
            except Exception:
                pass

    # Fallback bare pool
    else:
        with engine.get_connection() as conn:
            yield conn


def ping_db() -> bool:
    """Quick DB health-check — returns True if reachable."""
    try:
        from sqlalchemy import text
        engine = get_engine()
        if hasattr(engine, 'raw_connection'):
            with engine.connect() as conn:
                conn.execute(text('SELECT 1'))
            return True
        # fallback pool
        with engine.get_connection() as conn:
            conn.cursor().execute('SELECT 1')
        return True
    except Exception as exc:
        logger.warning(f'[db_pool] ping_db failed: {exc}')
        return False


# ── Fallback bare pool (no SQLAlchemy) ────────────────────────────────────────

class _BarePool:
    """Minimal thread-safe pool used only when SQLAlchemy is unavailable."""

    import queue as _queue_mod

    def __init__(self, db_url: str, size: int):
        import queue
        self._q: queue.Queue = queue.Queue(maxsize=size)
        self._lock = threading.Lock()
        self._db_url = db_url
        self._size = size
        self._filled = False

    def _connect(self):
        parsed = urlparse(self._db_url)
        try:
            import psycopg2
            c = psycopg2.connect(self._db_url)
            c.autocommit = True
            return c
        except ImportError:
            import pg8000
            c = pg8000.connect(
                user=parsed.username, password=parsed.password,
                host=parsed.hostname, port=parsed.port or 5432,
                database=parsed.path.lstrip('/'),
            )
            c.autocommit = True
            return c

    def _ensure_filled(self):
        if self._filled:
            return
        with self._lock:
            if self._filled:
                return
            for _ in range(self._size):
                try:
                    self._q.put_nowait(self._connect())
                except Exception as e:
                    logger.warning(f'[bare_pool] pre-fill failed: {e}')
            self._filled = True

    @contextmanager
    def get_connection(self):
        self._ensure_filled()
        import queue
        try:
            conn = self._q.get(timeout=_POOL_TIMEOUT)
        except queue.Empty:
            conn = self._connect()
        broken = False
        try:
            yield conn
        except Exception:
            broken = True
            raise
        finally:
            if broken:
                try:
                    conn.close()
                except Exception:
                    pass
                try:
                    self._q.put_nowait(self._connect())
                except Exception:
                    pass
            else:
                try:
                    self._q.put_nowait(conn)
                except Exception:
                    pass


def _build_fallback_pool(db_url: str) -> '_BarePool':
    pool = _BarePool(db_url, size=_POOL_SIZE)
    return pool
