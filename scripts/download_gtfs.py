#!/usr/bin/env python3
"""
Download Seattle-area GTFS static schedule data and build a SQLite index.

Run from the project root:
    uv run python scripts/download_gtfs.py

Downloads GTFS feeds for:
  - King County Metro (OBA agency 1)
  - Sound Transit Rail (OBA agency 40)
  - Washington State Ferries (OBA agency 95)

Extracts raw GTFS files to data/gtfs/{agency}/ and builds
data/gtfs_index.sqlite for runtime use by gtfs_schedule.py.
"""

import csv
import io
import os
import sqlite3
import sys
import urllib.request
import zipfile
from pathlib import Path

AGENCIES: dict[str, str] = {
    "1": "https://metro.kingcounty.gov/GTFS/google_transit.zip",
    "40": "https://www.soundtransit.org/GTFS-rail/40_gtfs.zip",
    "95": "https://gtfs.sound.obaweb.org/prod/95_gtfs.zip",
}

AGENCY_NAMES = {
    "1": "King County Metro",
    "40": "Sound Transit Rail",
    "95": "Washington State Ferries",
}


def _strip_prefix(id_str: str) -> str:
    """Strip numeric agency prefix: '95_7' → '7', '1_100479' → '100479'."""
    if id_str and "_" in id_str:
        prefix, _, rest = id_str.partition("_")
        if prefix.isdigit():
            return rest
    return id_str


def download_and_extract(agency_id: str, url: str, dest_dir: Path) -> bool:
    """Download a GTFS zip and extract to dest_dir. Returns True on success."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dest_dir / "gtfs.zip"

    print(f"  Downloading {AGENCY_NAMES[agency_id]} from {url} ...")
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "TransitTracker/1.0 (gtfs-downloader)"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
        zip_path.write_bytes(data)
        print(f"  Downloaded {len(data) // 1024} KB")
    except Exception as e:
        print(f"  ERROR downloading {url}: {e}", file=sys.stderr)
        return False

    print(f"  Extracting to {dest_dir} ...")
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(dest_dir)
        zip_path.unlink()
        print(f"  Extracted {len(os.listdir(dest_dir))} files")
    except Exception as e:
        print(f"  ERROR extracting: {e}", file=sys.stderr)
        return False

    return True


def _read_csv(path: Path) -> list[dict]:
    """Read a CSV file, returning list of dicts. Returns [] if file missing."""
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def build_index(gtfs_dir: Path, db_path: Path) -> None:
    """Parse downloaded GTFS files and build a SQLite index.

    Schema:
        stop_departures(stop_id, departure_sec, service_id, trip_id, route_id, headsign)
        services(service_id, monday..sunday, start_date, end_date)
        service_exceptions(service_id, date, exception_type)
        routes(route_id, short_name, long_name, color, route_type)

    All IDs are stored without agency prefix (numeric prefix stripped).
    """
    if db_path.exists():
        db_path.unlink()
        print(f"Removed existing index at {db_path}")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.executescript("""
        CREATE TABLE IF NOT EXISTS stop_departures (
            stop_id       TEXT    NOT NULL,
            departure_sec INTEGER NOT NULL,
            service_id    TEXT    NOT NULL,
            trip_id       TEXT    NOT NULL,
            route_id      TEXT    NOT NULL,
            headsign      TEXT,
            direction_id  INTEGER
        );
        CREATE TABLE IF NOT EXISTS services (
            service_id TEXT PRIMARY KEY,
            monday     INTEGER, tuesday  INTEGER, wednesday INTEGER,
            thursday   INTEGER, friday   INTEGER, saturday  INTEGER,
            sunday     INTEGER,
            start_date TEXT, end_date TEXT
        );
        CREATE TABLE IF NOT EXISTS service_exceptions (
            service_id     TEXT NOT NULL,
            date           TEXT NOT NULL,
            exception_type INTEGER NOT NULL,
            PRIMARY KEY (service_id, date)
        );
        CREATE TABLE IF NOT EXISTS routes (
            route_id   TEXT PRIMARY KEY,
            short_name TEXT,
            long_name  TEXT,
            color      TEXT,
            route_type INTEGER
        );
    """)
    conn.commit()

    total_stop_times = 0

    for agency_id in sorted(AGENCIES.keys()):
        agency_dir = gtfs_dir / agency_id
        if not agency_dir.exists():
            print(f"  Skipping agency {agency_id}: directory not found")
            continue

        print(f"\nIndexing {AGENCY_NAMES[agency_id]} (agency {agency_id}) ...")

        # --- routes.txt ---
        routes_inserted = 0
        for row in _read_csv(agency_dir / "routes.txt"):
            route_id = _strip_prefix(row.get("route_id", "").strip())
            if not route_id:
                continue
            color = (row.get("route_color") or "").strip() or None
            cur.execute(
                "INSERT OR REPLACE INTO routes VALUES (?,?,?,?,?)",
                (
                    route_id,
                    (row.get("route_short_name") or "").strip() or None,
                    (row.get("route_long_name") or "").strip() or None,
                    color,
                    int(row.get("route_type") or 0),
                ),
            )
            routes_inserted += 1
        print(f"  routes: {routes_inserted}")

        # --- calendar.txt ---
        services_inserted = 0
        for row in _read_csv(agency_dir / "calendar.txt"):
            cur.execute(
                "INSERT OR REPLACE INTO services VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    row.get("service_id", "").strip(),
                    int(row.get("monday") or 0),
                    int(row.get("tuesday") or 0),
                    int(row.get("wednesday") or 0),
                    int(row.get("thursday") or 0),
                    int(row.get("friday") or 0),
                    int(row.get("saturday") or 0),
                    int(row.get("sunday") or 0),
                    row.get("start_date", "").strip(),
                    row.get("end_date", "").strip(),
                ),
            )
            services_inserted += 1
        print(f"  calendar: {services_inserted} services")

        # --- calendar_dates.txt ---
        exceptions_inserted = 0
        for row in _read_csv(agency_dir / "calendar_dates.txt"):
            service_id = row.get("service_id", "").strip()
            date = row.get("date", "").strip()
            exception_type = int(row.get("exception_type") or 0)
            if service_id and date:
                cur.execute(
                    "INSERT OR REPLACE INTO service_exceptions VALUES (?,?,?)",
                    (service_id, date, exception_type),
                )
                exceptions_inserted += 1
        print(f"  calendar_dates: {exceptions_inserted} exceptions")

        # --- trips.txt → in-memory lookup (trip_id → metadata) ---
        trips: dict[str, dict] = {}
        for row in _read_csv(agency_dir / "trips.txt"):
            trip_id = row.get("trip_id", "").strip()
            if not trip_id:
                continue
            trips[trip_id] = {
                "service_id": row.get("service_id", "").strip(),
                "route_id": _strip_prefix(row.get("route_id", "").strip()),
                "headsign": (row.get("trip_headsign") or "").strip() or None,
                "direction_id": int(row.get("direction_id") or 0),
            }
        print(f"  trips loaded: {len(trips)}")

        # --- stop_times.txt (large — batch insert) ---
        stop_times_path = agency_dir / "stop_times.txt"
        if not stop_times_path.exists():
            print(f"  stop_times.txt not found, skipping agency {agency_id}")
            continue

        batch: list[tuple] = []
        batch_size = 10_000
        rows_processed = 0
        rows_inserted = 0

        with open(stop_times_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows_processed += 1

                # Skip no-pickup stops (pickup_type=1 means no boarding allowed)
                pickup_type = int(row.get("pickup_type") or 0)
                if pickup_type == 1:
                    continue

                trip_id = row.get("trip_id", "").strip()
                trip_meta = trips.get(trip_id)
                if not trip_meta:
                    continue

                stop_id = _strip_prefix(row.get("stop_id", "").strip())
                dep_time = (row.get("departure_time") or row.get("arrival_time") or "").strip()
                if not stop_id or not dep_time:
                    continue

                # Parse HH:MM:SS (H can exceed 23 for post-midnight trips)
                try:
                    parts = dep_time.split(":")
                    dep_sec = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                except (ValueError, IndexError):
                    continue

                batch.append((
                    stop_id,
                    dep_sec,
                    trip_meta["service_id"],
                    trip_id,
                    trip_meta["route_id"],
                    trip_meta["headsign"],
                    trip_meta["direction_id"],
                ))
                rows_inserted += 1

                if len(batch) >= batch_size:
                    cur.executemany(
                        "INSERT INTO stop_departures VALUES (?,?,?,?,?,?,?)", batch
                    )
                    conn.commit()
                    batch.clear()
                    if rows_processed % 500_000 == 0:
                        print(f"  ... {rows_processed:,} rows processed", flush=True)

        if batch:
            cur.executemany(
                "INSERT INTO stop_departures VALUES (?,?,?,?,?,?,?)", batch
            )
            conn.commit()

        print(f"  stop_times: {rows_inserted:,} inserted ({rows_processed:,} rows scanned)")
        total_stop_times += rows_inserted

    # Build index after all inserts for speed
    print("\nBuilding index on stop_departures ...")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_stop_dep "
        "ON stop_departures (stop_id, departure_sec)"
    )
    conn.commit()
    conn.close()

    print(f"\nDone. Total stop_times indexed: {total_stop_times:,}")
    print(f"Index written to: {db_path}")


def main() -> None:
    # Run from project root
    project_root = Path(__file__).parent.parent
    gtfs_dir = project_root / "data" / "gtfs"
    db_path = project_root / "data" / "gtfs_index.sqlite"

    print("=== GTFS Download & Index Builder ===\n")

    # Download phase
    success_count = 0
    for agency_id, url in AGENCIES.items():
        print(f"--- Agency {agency_id}: {AGENCY_NAMES[agency_id]} ---")
        agency_dir = gtfs_dir / agency_id
        ok = download_and_extract(agency_id, url, agency_dir)
        if ok:
            success_count += 1
        print()

    if success_count == 0:
        print("ERROR: No agencies downloaded successfully.", file=sys.stderr)
        sys.exit(1)

    # Index phase
    print("=== Building SQLite Index ===\n")
    build_index(gtfs_dir, db_path)

    # Quick sanity check
    conn = sqlite3.connect(db_path)
    counts = {
        "routes": conn.execute("SELECT count(*) FROM routes").fetchone()[0],
        "services": conn.execute("SELECT count(*) FROM services").fetchone()[0],
        "stop_departures": conn.execute("SELECT count(*) FROM stop_departures").fetchone()[0],
    }
    conn.close()

    print("\n=== Index Summary ===")
    for table, count in counts.items():
        print(f"  {table}: {count:,} rows")

    print("\nRun `TRANSIT_TRACKER_TESTING=1 uv run pytest tests/test_gtfs.py -v` to verify.")


if __name__ == "__main__":
    main()
