#!/usr/bin/env python3
"""Pack card-hashes.json into the binary index the browser fetches.

The scanner used to build its packed index from 40k hash strings on every page
load: 4.9 MB of JSON to transfer and parse, then 4.2M single-character tests to
fill the words. The same bits now ship as one blob (~1.1 MB) that decodes into
typed-array views, so a page load pays a transfer instead of a parse and a loop.

Format, little-endian:

    0   "PHX1"                    magic
    4   uint32                    card count
    8   uint32                    words per hash
    12  uint32                    bits per hash (105)
    16  uint32[count * words]     packed bits
    16+ uint16[count]             hash length per card
    ..  UTF-8, newline-joined ids, in blob order

Character b of a hash is bit (b & 31) of word (b >> 5) — the same layout the
matcher uses when it has to build the index from JSON itself, so a blob and a
JSON build of the same data are comparable word for word.

Rows follow card-ids.json order, keeping only ids the hash file has, which is
exactly the order the browser iterates when it builds entries from card ids.

The artifact's name and directory live here too: every writer goes through
write_blob(), so no script has to guess where the browser expects the file.

    python scripts/pack_hash_index.py
"""
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src" / "static"
MAGIC = b"PHX1"
BLOB_NAME = "card-hashes.bin"


def _words_for(bits: int) -> int:
    return max(1, (bits + 31) // 32)


def pack_bits(bits: str, words: int) -> list:
    """One hash string as 32-bit words: char b -> word b>>5, bit b&31."""
    out = []
    for w in range(words):
        chunk = bits[w * 32:(w + 1) * 32]
        # Reversed so the chunk's first character lands on bit 0: int() reads
        # its string most significant first.
        out.append(int(chunk[::-1], 2) if chunk else 0)
    return out


def pack(hashes: dict, ids: list) -> bytes:
    """The blob for `hashes`, in `ids` order. Empty when there is nothing to pack."""
    ordered = [card_id for card_id in ids if hashes.get(card_id)]
    if not ordered:
        return b""
    lengths = [len(hashes[card_id]) for card_id in ordered]
    bits = max(lengths)
    words = _words_for(bits)

    body = bytearray()
    for card_id in ordered:
        for word in pack_bits(hashes[card_id], words):
            body += struct.pack("<I", word)

    header = struct.pack("<4sIII", MAGIC, len(ordered), words, bits)
    lens = struct.pack(f"<{len(ordered)}H", *lengths)
    return header + bytes(body) + lens + "\n".join(ordered).encode("utf-8")


def write_blob(hashes: dict, ids: list, directory: Path = STATIC) -> Path | None:
    """Write the blob the browser fetches; None when there is nothing to pack."""
    blob = pack(hashes, ids)
    if not blob:
        return None
    out = Path(directory) / BLOB_NAME
    out.write_bytes(blob)
    return out


def main() -> int:
    hash_file = STATIC / "card-hashes.json"
    ids_file = STATIC / "card-ids.json"
    if not hash_file.exists():
        print(f"no {hash_file} to pack; run build_browser_hashes.py first", file=sys.stderr)
        return 1
    hashes = json.loads(hash_file.read_text(encoding="utf-8"))
    ids = json.loads(ids_file.read_text(encoding="utf-8"))

    blob_path = write_blob(hashes, ids)
    if blob_path is None:
        print("no hashes to pack", file=sys.stderr)
        return 1

    packed = sum(1 for card_id in ids if hashes.get(card_id))
    print(f"packed {packed} hashes -> {blob_path} "
          f"({blob_path.stat().st_size / 1048576:.2f} MB from {hash_file.stat().st_size / 1048576:.2f} MB)")
    orphans = len(hashes) - packed
    if orphans:
        print(f"  {orphans} hash(es) not in card-ids.json, left out")
    return 0


if __name__ == "__main__":
    sys.exit(main())
