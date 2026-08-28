"""
Making text from a network device storable

A device is under no obligation to send clean text, and two things it commonly
sends cannot be stored at all:

- **NUL (U+0000).** PostgreSQL holds any character except this one, in `text`
  and in JSONB alike, and psycopg2 refuses the parameter before the statement
  is even sent::

      ValueError: A string literal cannot contain NUL (0x00) characters.

  Telnet is the usual source - RFC 854 encodes a bare CR as ``CR NUL``, so a
  telnet crawl collects them by the line - and SNMP is the other, where LLDP
  names and ENTITY-MIB serial numbers are routinely NUL-padded to a fixed
  width. Neither carries information: ``"sw-core-01\\x00\\x00"`` is the name
  ``sw-core-01``.

- **Lone surrogates (U+D800-U+DFFF).** These arrive when bytes that are not
  valid UTF-8 have been decoded with ``errors="surrogateescape"``, which is
  what Python does by default for a good deal of I/O. They have no UTF-8
  encoding, so psycopg2 raises ``UnicodeEncodeError`` on the way out.

One such byte anywhere in a crawl used to fail the INSERT for the hop it was
in, and `DiscoveryService.crawl` turns a failed INSERT into a failed run - so
a single chatty switch could take down the discovery of an entire estate.

Cleaning happens where device text enters the application, because the value
has to be right and not merely storable: a NUL-padded LLDP name is compared
against existing rows, used as an upsert key and matched to a device, and all
three want the name rather than the padding. `scrub_parameters` is the net
under that, applied to every statement the engine sends, so a path nobody
thought of degrades to a dropped character instead of a failed run.
"""
import re
from typing import Any, Optional

# Everything PostgreSQL cannot be given, in one pass.
_UNSTORABLE = re.compile("[\x00\ud800-\udfff]")


def has_unstorable(value: Any) -> bool:
    """
    Whether a value is text carrying something PostgreSQL would refuse

    Deliberately not a single regex. Every statement the application runs is
    checked, so this is on the hot path and almost always answers no: a `in`
    test and `isascii()` are C-level scans, and an ASCII string cannot hold a
    surrogate, which leaves the regex for the rare non-ASCII case.
    """
    if not isinstance(value, str):
        return False
    if "\x00" in value:
        return True
    if value.isascii():
        return False
    return _UNSTORABLE.search(value) is not None


def scrub(value: Optional[str]) -> Optional[str]:
    """
    Remove the characters PostgreSQL cannot store from device text

    Args:
        value: Text from a device, or None

    Returns:
        The same text with NUL and lone surrogates removed. None and the empty
        string pass through unchanged, and a string with nothing to remove is
        returned as-is rather than rebuilt.
    """
    if not value or not has_unstorable(value):
        return value

    return _UNSTORABLE.sub("", value)


def scrub_parameters(parameters: Any) -> Any:
    """
    Apply `scrub` to the string values in a DBAPI parameter set

    Handles the shapes SQLAlchemy passes to ``before_cursor_execute``: a dict
    or a sequence for one statement, or a list of either for an executemany.

    Args:
        parameters: Bind parameters as the DBAPI will receive them

    Returns:
        The parameters, with any unstorable text cleaned. The original object
        is returned untouched when there was nothing to clean - checked before
        anything is rebuilt, because a batch insert of several hundred rows
        goes through here and copying it would cost more than the check.
    """
    if isinstance(parameters, dict):
        if not any(has_unstorable(value) for value in parameters.values()):
            return parameters
        return {
            key: scrub(value) if isinstance(value, str) else value
            for key, value in parameters.items()
        }

    if isinstance(parameters, (list, tuple)):
        cleaned_items = [_scrub_item(item) for item in parameters]

        # Both helpers return the object they were given when there was
        # nothing to do, so identity is the "unchanged" test.
        if all(
            cleaned is original
            for cleaned, original in zip(cleaned_items, parameters)
        ):
            return parameters

        return (
            tuple(cleaned_items) if isinstance(parameters, tuple) else cleaned_items
        )

    return parameters


def _scrub_item(item: Any) -> Any:
    """One element of a parameter sequence, whatever it turns out to be"""
    if isinstance(item, str):
        return scrub(item)
    if isinstance(item, (dict, list, tuple)):
        return scrub_parameters(item)
    return item
