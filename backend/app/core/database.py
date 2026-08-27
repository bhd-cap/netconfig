"""
Database connection and session management
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from app.core.config import settings

# Connection arguments, kept as a module constant so the encoding setting
# below is visible to tests rather than buried in a call.
CONNECT_ARGS = {
    "application_name": "netconfig-backup",
    "keepalives": 1,
    "keepalives_idle": 30,
    "keepalives_interval": 10,
    "keepalives_count": 5,
    # Do not inherit the server's idea of the client encoding.
    #
    # psycopg2 encodes query parameters using client_encoding, which defaults
    # to the database's encoding. A PostgreSQL cluster initialised without a
    # locale - the norm in a bare Debian or Ubuntu container, where initdb
    # falls back to SQL_ASCII - therefore made psycopg2 encode as ASCII, and
    # any non-ASCII character raised UnicodeEncodeError before the statement
    # was ever sent:
    #
    #   'ascii' codec can't encode character '\xa0' in position 7
    #
    # That is not obscure input. Vendor names in the IEEE OUI registry contain
    # non-breaking spaces, an SNMP sysDescr is whatever the device felt like
    # sending, and a device hostname or LLDP neighbour name is not required to
    # be ASCII either. Asking for UTF-8 explicitly makes the client encoding a
    # property of this application instead of a property of how someone's
    # container happened to be built. On a SQL_ASCII database the server does
    # no conversion, so the bytes round-trip unchanged; on a UTF8 one this is
    # already the default and changes nothing.
    "client_encoding": "utf8",
}

# Create database engine.
#
# Pool sizing is deliberately conservative and environment-driven: every
# pooled connection holds a socket and a server-side backend process, and the
# API, the Celery worker and beat each build their own pool. pool_recycle
# keeps us from handing out connections the server has already closed, which
# otherwise costs a failed round trip plus a retry on every stale checkout.
engine = create_engine(
    str(settings.DATABASE_URL),
    pool_pre_ping=True,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    echo=settings.DB_ECHO,
    # Cache compiled SQL per connection; repeated ORM queries then skip
    # statement compilation entirely.
    query_cache_size=1200,
    connect_args=CONNECT_ARGS,
)

# Create session factory.
#
# expire_on_commit=False stops SQLAlchemy from invalidating every loaded
# attribute at commit time. Without it, touching any field of an object after
# a commit issues a fresh SELECT — the single largest source of redundant
# queries in the write paths of this app.
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    bind=engine,
)

# Create base class for models.
#
# eager_defaults makes INSERT use RETURNING for server-side defaults
# (created_at, backed_up_at, ...), so a create no longer needs a follow-up
# refresh() round trip to populate them.
class _ModelBase:
    __mapper_args__ = {"eager_defaults": True}


Base = declarative_base(cls=_ModelBase)


def get_db():
    """
    Dependency function to get database session

    Yields:
        Session: Database session
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
