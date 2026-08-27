"""
The client encoding must be ours, not the server's.

psycopg2 encodes query parameters using client_encoding, which defaults to the
database's own encoding. A cluster initialised without a locale - the norm in a
bare Debian or Ubuntu container, where initdb falls back to SQL_ASCII - made
psycopg2 encode as ASCII, so any non-ASCII character raised UnicodeEncodeError
before the statement was ever sent:

    'ascii' codec can't encode character '\xa0' in position 7

Reported from a real LXC install, importing the IEEE OUI registry: vendor names
in it contain non-breaking spaces. The same fault was waiting for any SNMP
sysDescr, LLDP neighbour name or device hostname that was not plain ASCII.

These run against a throwaway SQL_ASCII database, because that is the only
condition under which the bug appears - on a UTF8 server the wrong setting and
the right one behave identically, which is why nothing caught it.
"""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.core.database import CONNECT_ARGS

# A real entry from the IEEE registry: the space after "Atron" is U+00A0.
NBSP_VENDOR = "Atron\xa0electronic GmbH"
ACCENTED = "Télécom Sudparis"

PROBE_DB = "netconfig_sql_ascii_probe"


def _url_for(database: str):
    return make_url(str(settings.DATABASE_URL)).set(database=database)


@pytest.fixture(scope="module")
def sql_ascii_url():
    """A throwaway SQL_ASCII database, dropped afterwards"""
    admin = create_engine(_url_for("postgres"), isolation_level="AUTOCOMMIT")

    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{PROBE_DB}"'))
            conn.execute(
                text(
                    f'CREATE DATABASE "{PROBE_DB}" ENCODING \'SQL_ASCII\' '
                    f"TEMPLATE template0 LC_COLLATE 'C' LC_CTYPE 'C'"
                )
            )
    except Exception as e:  # noqa: BLE001 - no CREATEDB right, say why and move on
        admin.dispose()
        pytest.skip(f"cannot create a SQL_ASCII database here: {e}")

    yield _url_for(PROBE_DB)

    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{PROBE_DB}"'))
    admin.dispose()


def _write_and_read(url, connect_args, value):
    engine = create_engine(url, connect_args=connect_args)
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS probe"))
            conn.execute(text("CREATE TABLE probe (id int primary key, name text)"))
            conn.execute(
                text("INSERT INTO probe (id, name) VALUES (1, :name)"),
                {"name": value},
            )
            return conn.execute(text("SELECT name FROM probe WHERE id = 1")).scalar()
    finally:
        engine.dispose()


def test_the_connection_pins_the_client_encoding():
    """
    Guards the setting itself

    The behavioural test below can only fail where a SQL_ASCII database can be
    created, so this states the requirement unconditionally.
    """
    assert CONNECT_ARGS.get("client_encoding") == "utf8"


def test_the_probe_database_really_is_sql_ascii(sql_ascii_url):
    """Otherwise the test below proves nothing"""
    engine = create_engine(sql_ascii_url)
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SHOW server_encoding")).scalar() == "SQL_ASCII"
    finally:
        engine.dispose()


@pytest.mark.parametrize("value", [NBSP_VENDOR, ACCENTED])
def test_non_ascii_survives_a_sql_ascii_database(sql_ascii_url, value):
    """What the OUI import needs: the text goes in and comes back unchanged"""
    assert _write_and_read(sql_ascii_url, CONNECT_ARGS, value) == value


def test_without_the_setting_the_same_write_fails(sql_ascii_url):
    """
    The failure mode is still there, and this is what holds it off

    If this ever stops raising, either psycopg2 changed its default or the
    server did, and the comment in app/core/database.py needs revisiting - but
    a test that passes for the wrong reason is worse than no test.
    """
    inherited = {key: value for key, value in CONNECT_ARGS.items()
                 if key != "client_encoding"}

    with pytest.raises(UnicodeEncodeError) as raised:
        _write_and_read(sql_ascii_url, inherited, NBSP_VENDOR)

    assert raised.value.encoding == "ascii"
    assert "\xa0" in raised.value.object
