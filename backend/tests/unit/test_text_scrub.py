"""
Removing what PostgreSQL cannot store from device text

A NUL reaching psycopg2 does not fail one column - it fails the statement, and
`DiscoveryService.crawl` turns a failed statement into a failed run. The bug
these cover reached a user as "A string literal cannot contain NUL (0x00)
characters" on the Discovery page, with nothing discovered.
"""
import pytest

from app.utils.text import has_unstorable, scrub, scrub_parameters


# --------------------------------------------------------------------------
# scrub
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        # Telnet: RFC 854 encodes a bare CR as CR NUL, so a neighbour table
        # read over telnet carries one per line.
        ("Gi1/0/24\r\x00", "Gi1/0/24\r"),
        # SNMP: LLDP names and ENTITY-MIB serials are padded to a fixed width.
        ("sw-core-01\x00\x00\x00", "sw-core-01"),
        ("\x00FOC2137L0AB", "FOC2137L0AB"),
        # In the middle of otherwise good text.
        ("Cisco\x00IOS", "CiscoIOS"),
    ],
)
def test_nul_is_removed(value, expected):
    assert scrub(value) == expected


def test_lone_surrogates_are_removed():
    """
    Bytes that are not valid UTF-8, decoded with surrogateescape

    These have no UTF-8 encoding at all, so psycopg2 raises UnicodeEncodeError
    rather than the NUL ValueError - a different exception from the same
    cause, and just as fatal to a run.
    """
    decoded = b"port-\xff-01".decode("utf-8", errors="surrogateescape")

    assert "\udcff" in decoded
    assert scrub(decoded) == "port--01"
    # And what comes out can actually be encoded, which is the whole point.
    assert scrub(decoded).encode("utf-8")


@pytest.mark.parametrize("value", ["", None])
def test_empty_and_none_pass_through(value):
    assert scrub(value) == value


def test_clean_text_is_returned_unchanged():
    """The common case must not pay for a rebuild"""
    value = "GigabitEthernet1/0/24"
    assert scrub(value) is value


def test_legitimate_characters_survive():
    """Only the two unstorable things go; everything else is text"""
    value = "Télécom Sudparis\tab\ncd\r\n北京小米移动软件 \x1b[0m"
    assert scrub(value) == value


def test_has_unstorable():
    assert has_unstorable("sw\x0001") is True
    assert has_unstorable("sw-01") is False
    assert has_unstorable(42) is False


# --------------------------------------------------------------------------
# scrub_parameters - the shapes SQLAlchemy hands to before_cursor_execute
# --------------------------------------------------------------------------


def test_dict_parameters():
    assert scrub_parameters({"hostname": "sw\x0001", "port": 22}) == {
        "hostname": "sw01",
        "port": 22,
    }


def test_sequence_parameters():
    assert scrub_parameters(("sw\x0001", 22, None)) == ("sw01", 22, None)


def test_executemany_parameters():
    """A list of parameter sets, which is what create_many produces"""
    rows = [
        {"remote_hostname": "a\x00"},
        {"remote_hostname": "b"},
    ]
    assert scrub_parameters(rows) == [
        {"remote_hostname": "a"},
        {"remote_hostname": "b"},
    ]


def test_clean_parameters_are_returned_unchanged():
    """
    Identity, not equality

    The engine hook logs when it had to change something, and every statement
    the application runs goes through it - so "nothing to do" has to be
    distinguishable and cheap.
    """
    parameters = {"hostname": "sw-01", "port": 22}
    assert scrub_parameters(parameters) is parameters

    rows = [{"a": "one"}, {"a": "two"}]
    assert scrub_parameters(rows) is rows


def test_non_string_parameters_are_left_alone():
    from datetime import datetime

    now = datetime.now()
    parameters = {"when": now, "count": 3, "ok": True, "nothing": None}
    assert scrub_parameters(parameters) is parameters
