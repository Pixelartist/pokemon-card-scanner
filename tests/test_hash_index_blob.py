"""The shipped card-hashes.bin must be a fresh pack of card-hashes.json.

The blob is what the browser actually fetches, and a stale one fails silently:
it still decodes, so the scanner just matches against older bits than the JSON
holds. Every writer goes through write_blob(), but a hand edit or a new writer
would not — this compares the committed bytes against pack() output, the only
definition of the format.

    python scripts/pack_hash_index.py     # rewrite the blob after any hash change
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src" / "static"
sys.path.insert(0, str(ROOT / "scripts"))

from pack_hash_index import pack, write_blob  # noqa: E402  (needs the path above)


def _first_diff(a: bytes, b: bytes) -> str:
    """Where two blobs first disagree, so a failure names a byte, not 1.1 MB."""
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return f"byte {i}"
    return "the end of the shorter blob" if len(a) != len(b) else "nowhere"


def test_blob_is_a_fresh_pack_of_the_committed_hashes():
    hash_file = STATIC / "card-hashes.json"
    ids_file = STATIC / "card-ids.json"
    blob_file = STATIC / "card-hashes.bin"

    if not hash_file.exists() or not ids_file.exists():
        pytest.skip("no committed hash index to compare the blob against")

    hashes = json.loads(hash_file.read_text(encoding="utf-8"))
    ids = json.loads(ids_file.read_text(encoding="utf-8"))
    expected = pack(hashes, ids)

    assert blob_file.exists(), (
        f"{blob_file} is missing; run python scripts/pack_hash_index.py"
    )
    actual = blob_file.read_bytes()
    # pytest.fail, not assert: a plain == on two 1.1 MB blobs would print both
    # of them, and the useful part is the byte where they diverge.
    if actual != expected:
        pytest.fail(
            f"{blob_file.name} is stale: {len(actual)} bytes on disk, "
            f"{len(expected)} from a fresh pack of {hash_file.name}, first "
            f"difference at {_first_diff(actual, expected)}. "
            "Run python scripts/pack_hash_index.py"
        )


def test_write_blob_names_and_places_the_artifact(tmp_path):
    """The build script and the pocket downloader both write the blob, so its
    name and directory come from the packer rather than from each caller."""
    hashes = {"A1-001": "01" * 52 + "1", "A1-002": "10" * 52 + "1"}
    ids = ["A1-002", "A1-001"]

    out = write_blob(hashes, ids, tmp_path)

    assert out == tmp_path / "card-hashes.bin"
    assert out.read_bytes() == pack(hashes, ids)
    assert write_blob({}, ids, tmp_path) is None, "nothing to pack, no blob"
