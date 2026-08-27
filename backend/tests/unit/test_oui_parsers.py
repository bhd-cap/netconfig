"""
OUI list parsing, against real fixtures from each supported format.

The vendor name is the whole point of this table: a prefix mapped to the wrong
name is worse than no mapping at all, because nothing looks broken. These
cover the formats separately, because the two whitespace-delimited ones are
easy to conflate.
"""
import pytest

from app.services import oui

# --------------------------------------------------------------------------
# Wireshark manuf: tab separated, short name then long name
# --------------------------------------------------------------------------

MANUF = """\
# This file is generated
00:00:0C\tCisco\tCisco Systems, Inc
00:1B:2C\tAtron\tAtron electronic GmbH
F0:D5:BF\tIntel\tIntel Corporate
3C:22:FB\tApple\tApple, Inc.
00:50:56\tVMware
40:D8:55:0C:00:00/36\tIEEE-RA\tIEEE Registration Authority
"""


def test_manuf_prefers_the_long_name():
    entries = dict(oui.parse_manuf(MANUF))

    assert entries["00000c"] == "Cisco Systems, Inc"
    assert entries["001b2c"] == "Atron electronic GmbH"
    assert entries["f0d5bf"] == "Intel Corporate"


def test_manuf_falls_back_to_the_short_name():
    """A manuf line with no long name still yields the short one"""
    assert dict(oui.parse_manuf(MANUF))["005056"] == "VMware"


def test_manuf_skips_longer_than_mal_prefixes():
    """
    manuf lists 28- and 36-bit assignments; only 24-bit MA-L fits the column
    """
    entries = dict(oui.parse_manuf(MANUF))
    assert "40d855" not in entries


# --------------------------------------------------------------------------
# nmap-mac-prefixes: prefix, one space, then the whole vendor name
# --------------------------------------------------------------------------

NMAP = """\
# Generated from the IEEE registry
000000 Xerox
00000C Cisco Systems
F0D5BF Intel Corporate
001B2C Atron electronic GmbH
3C22FB Apple
005056 VMware
"""


def test_nmap_keeps_the_whole_vendor_name():
    """
    A space-separated name must not be split into fields

    Splitting on "tab or space" made 'F0D5BF Intel Corporate' three fields
    and, preferring the third as a "long name", stored 'Corporate' - dropping
    the manufacturer from every multi-word entry in the file. That is roughly
    half the registry, silently mislabelled.
    """
    entries = dict(oui.parse_manuf(NMAP))

    assert entries["f0d5bf"] == "Intel Corporate"
    assert entries["001b2c"] == "Atron electronic GmbH"
    assert entries["00000c"] == "Cisco Systems"
    assert entries["3c22fb"] == "Apple"


def test_nmap_and_manuf_agree_where_they_overlap():
    from_manuf = dict(oui.parse_manuf(MANUF))
    from_nmap = dict(oui.parse_manuf(NMAP))

    for prefix in set(from_manuf) & set(from_nmap):
        # Same vendor, however the file spelled the separator.
        assert from_manuf[prefix].split(",")[0].split()[0] == (
            from_nmap[prefix].split(",")[0].split()[0]
        )


# --------------------------------------------------------------------------
# IEEE CSV
# --------------------------------------------------------------------------

IEEE_CSV = """\
Registry,Assignment,Organization Name,Organization Address
MA-L,00000C,"Cisco Systems, Inc",170 West Tasman Drive San Jose CA US 94568
MA-L,F0D5BF,Intel Corporate,Lot 8 Jalan Hi-Tech 2/3 Kulim MY 09000
MA-L,3C22FB,"Apple, Inc.",1 Infinite Loop Cupertino CA US 95014
MA-M,0055DA0,Should Be Skipped,Nowhere
MA-L,BADHEX,Not Hex,Nowhere
MA-L,005056,,No Name
"""


def test_ieee_csv_parses_quoted_names():
    entries = dict(oui.parse_ieee_csv(IEEE_CSV))

    assert entries["00000c"] == "Cisco Systems, Inc"
    assert entries["3c22fb"] == "Apple, Inc."
    assert entries["f0d5bf"] == "Intel Corporate"


def test_ieee_csv_skips_unusable_rows():
    entries = dict(oui.parse_ieee_csv(IEEE_CSV))

    assert "0055da0" not in entries   # not a 24-bit assignment
    assert "badhex" not in entries    # not hex
    assert "005056" not in entries    # no organization name


# --------------------------------------------------------------------------
# Format detection
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,content,expected_prefix,expected_vendor",
    [
        ("ieee csv", IEEE_CSV, "f0d5bf", "Intel Corporate"),
        ("manuf", MANUF, "f0d5bf", "Intel Corporate"),
        ("nmap", NMAP, "f0d5bf", "Intel Corporate"),
    ],
)
def test_parse_any_detects_each_format(name, content, expected_prefix, expected_vendor):
    entries = dict(oui.parse_any(content))
    assert entries[expected_prefix] == expected_vendor


def test_parse_any_returns_nothing_for_rubbish():
    """
    A wrong file must yield nothing rather than garbage rows

    An HTML error page is the realistic case: a proxy or WAF returns one with
    a 200, and treating it as data would fill the table with nonsense.
    """
    html = "<html><head><title>403 Forbidden</title></head><body>no</body></html>"
    assert oui.parse_any(html) == []
    assert oui.parse_any("") == []


def test_a_long_vendor_name_is_clamped_to_the_column():
    """
    One over-long name must not abort a 39,000-row import

    The column is VARCHAR(255); Postgres rejects rather than truncates, so an
    unclamped name would fail the whole statement and lose the registry.
    """
    long_name = "X" * 400
    entries = dict(oui.parse_manuf(f"001122 {long_name}\n"))

    assert len(entries["001122"]) == oui.VENDOR_NAME_LIMIT


def test_ieee_sources_are_all_https():
    """A registry fetched over plain http could be tampered with in transit"""
    assert oui.IEEE_OUI_SOURCES
    for url in oui.IEEE_OUI_SOURCES:
        assert url.startswith("https://"), url
