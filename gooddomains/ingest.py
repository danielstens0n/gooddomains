"""Streaming input adapters. No network calls and no whole-file reads."""

import csv
import gzip
from pathlib import Path

from .store import put


def zone_domains(lines):
    """Read delegation owners from standard .com master files (not a general DNS parser)."""
    origin, owner = "com.", None
    depth = 0
    for raw in lines:
        line = raw.split(";", 1)[0].rstrip()
        if not line.strip():
            continue
        fields = line.split()
        if fields[0].upper() == "$ORIGIN":
            origin = fields[1].lower()
            if not origin.endswith("."):
                raise ValueError("Zone $ORIGIN must be absolute")
            continue
        if fields[0].upper() in ("$INCLUDE", "$GENERATE"):
            raise ValueError("Expand $INCLUDE/$GENERATE directives before importing")
        if fields[0].startswith("$"):
            continue
        previous_depth = depth
        depth += line.count("(") - line.count(")")
        if previous_depth:
            continue
        if not line[0].isspace():
            name = fields.pop(0).lower()
            owner = origin if name == "@" else name if name.endswith(".") else name + "." + origin
        if not owner:
            continue
        # TTL and class can be omitted or appear in either order.
        while fields and (fields[0].upper() == "IN" or fields[0][0].isdigit()):
            fields.pop(0)
        if fields and fields[0].upper() == "NS" and owner.endswith(".com.") and owner.count(".") == 2:
            yield {"domain": owner}


def records(stream, format):
    if format == "csv":
        reader = csv.DictReader(stream)
        if not reader.fieldnames or "domain" not in reader.fieldnames:
            raise ValueError("CSV must have a domain header")
        yield from reader
    elif format == "zone":
        yield from zone_domains(stream)
    else:
        for line in stream:
            value = line.strip()
            if value and not value.startswith("#"):
                yield {"domain": value}


def ingest(db, path, *, source, format=None, batch_size=5000):
    path = Path(path)
    suffix = path.with_suffix("").suffix if path.suffix == ".gz" else path.suffix
    format = format or {".csv": "csv", ".zone": "zone"}.get(suffix, "txt")
    if format not in ("csv", "txt", "zone"):
        raise ValueError("Unsupported input format")
    opener = gzip.open if path.suffix == ".gz" else open
    accepted = skipped = 0
    errors = []
    with opener(path, "rt", encoding="utf-8-sig") as stream:
        for index, row in enumerate(records(stream, format), 1):
            try:
                put(db, row.get("domain") or "", source=source,
                    kind="zone" if format == "zone" else "listing" if row.get("price_usd") else "candidate",
                    price_usd=row.get("price_usd"), observed_at=row.get("observed_at"),
                    listing_url=row.get("listing_url"))
                accepted += 1
            except (ValueError, TypeError) as exc:
                skipped += 1
                if len(errors) < 10:
                    errors.append(f"Record {index}: {exc}")
            if index % batch_size == 0:
                db.commit()
    return {"accepted_observations": accepted, "skipped": skipped, "first_errors": errors}
