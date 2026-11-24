"""Reading a card search box: an optional name, and the number on the card.

The correction pickers list printings, and one name can have more printings
than a picker will ever show (Nebulak has 20, Pikachu more), so the number
printed on the card is the only reliable way to point at one of them. Both the
public search and the admin correction search read their query through here,
so the two accept the same thing: "nebulak 102", "nebulak 102/162", "102/162".
"""
import re

# The number is its own token, optionally with the set's printed total after a
# slash. Requiring that boundary keeps ids like "tg01" or "sv05-102" whole
# instead of splitting a trailing digit off them.
_QUERY = re.compile(r"^(?:(?P<name>.*?)\s+)?(?P<number>\d{1,3})(?:\s*/\s*\d{1,3})?$")


def split_name_number(query: str):
    """Return (name, number); number is None when the query names no number."""
    q = (query or "").strip()
    match = _QUERY.match(q)
    if not match:
        return q, None
    return (match.group("name") or "").strip(), int(match.group("number"))
