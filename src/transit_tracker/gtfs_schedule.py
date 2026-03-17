"""
GTFS static schedule lookup for wake-up messages and ferry fallback.

The index is built by scripts/download_gtfs.py and stored at data/gtfs_index.sqlite.
At runtime, this module queries the SQLite index for the next scheduled departures
from a given stop, used in two scenarios:
  1. Wake-up: send immediate GTFS data when OBA cache is empty (client just connected)
  2. Ferry fallback: always show next scheduled ferry when no live OBA data is available
"""

import datetime
import os
import sqlite3
import time
from typing import Optional

# Path relative to this file: src/transit_tracker/gtfs_schedule.py
# data/ is at project root (4 levels up from src/transit_tracker/)
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
DEFAULT_DB_PATH = os.path.join(_PROJECT_ROOT, "data", "gtfs_index.sqlite")


class GTFSSchedule:
    """Provides next-departure lookups from a pre-built GTFS SQLite index."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def is_available(self) -> bool:
        return os.path.exists(self.db_path)

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    @staticmethod
    def _strip_agency_prefix(id_str: str) -> str:
        """Strip numeric agency prefix: '95_7' → '7', '1_12345' → '12345'.

        OBA IDs use format '{agency}_{gtfs_id}'. GTFS stores bare IDs.
        Only strips if the prefix is purely numeric (to avoid corrupting
        IDs like 'WSF028' that don't have agency prefixes).
        """
        if id_str and "_" in id_str:
            prefix, _, rest = id_str.partition("_")
            if prefix.isdigit():
                return rest
        return id_str

    def get_active_service_ids(self, date: datetime.date) -> set[str]:
        """Return service_ids active on the given date.

        Consults calendar.txt (weekday/date-range rules) and applies
        calendar_dates.txt overrides (exception_type 1=added, 2=removed).
        """
        conn = self._get_conn()
        date_str = date.strftime("%Y%m%d")
        day_col = date.strftime("%A").lower()  # e.g. "monday"

        # Base set from calendar (weekday + date range)
        rows = conn.execute(
            f"SELECT service_id FROM services "
            f"WHERE {day_col}=1 AND start_date<=? AND end_date>=?",
            (date_str, date_str),
        ).fetchall()
        active = {r["service_id"] for r in rows}

        # Apply calendar_dates exceptions
        exceptions = conn.execute(
            "SELECT service_id, exception_type FROM service_exceptions WHERE date=?",
            (date_str,),
        ).fetchall()
        for ex in exceptions:
            if ex["exception_type"] == 1:
                active.add(ex["service_id"])
            elif ex["exception_type"] == 2:
                active.discard(ex["service_id"])

        return active

    def get_next_departures(
        self,
        stop_id: str,
        route_ids: set[str],
        now: float,
        count: int = 5,
    ) -> list[dict]:
        """Return the next `count` scheduled departures from a stop.

        Args:
            stop_id:   Normalized stop ID (agency prefix already stripped, e.g. "7" or "12345").
                       The method further strips numeric prefixes in case the caller
                       passes something like "95_7".
            route_ids: Set of normalized route IDs to filter (empty = all routes).
                       Numeric prefixes are stripped here as well.
            now:       Current Unix timestamp.
            count:     Maximum number of trips to return.

        Returns:
            List of trip dicts compatible with the all_trips format used in send_update():
            {tripId, routeId, routeName, routeColor, stopId, headsign,
             arrivalTime, departureTime, isRealtime=False}
        """
        if not self.is_available():
            return []

        # Normalize IDs by stripping agency prefix
        clean_stop_id = self._strip_agency_prefix(stop_id)
        clean_route_ids = {self._strip_agency_prefix(r) for r in route_ids if r}

        now_local = datetime.datetime.fromtimestamp(now)
        today = now_local.date()
        secs_from_midnight = (
            now_local.hour * 3600 + now_local.minute * 60 + now_local.second
        )

        trips: list[dict] = []

        # Check today and tomorrow to find enough upcoming departures.
        # We also handle post-midnight GTFS times (e.g., 25:30:00 = 1:30 AM
        # the next calendar day but still part of yesterday's service run).
        for day_offset in range(3):
            if len(trips) >= count:
                break

            service_date = today + datetime.timedelta(days=day_offset)
            active_services = self.get_active_service_ids(service_date)
            if not active_services:
                continue

            service_midnight = datetime.datetime.combine(
                service_date, datetime.time.min
            ).timestamp()

            # For day_offset=0 (today), find trips departing from now onward.
            # For day_offset=1+ (future days), start from midnight of that day.
            if day_offset == 0:
                min_sec = secs_from_midnight
            else:
                min_sec = 0

            trips.extend(
                self._query_departures(
                    clean_stop_id,
                    clean_route_ids,
                    active_services,
                    service_midnight,
                    min_sec,
                    count - len(trips),
                )
            )

        # Also check yesterday's service for post-midnight wrap (e.g., 25:30 trips)
        if len(trips) < count and secs_from_midnight < 7200:  # within 2h of midnight
            yesterday = today - datetime.timedelta(days=1)
            yesterday_services = self.get_active_service_ids(yesterday)
            yesterday_midnight = datetime.datetime.combine(
                yesterday, datetime.time.min
            ).timestamp()
            # Only interested in yesterday's trips that run past midnight
            # i.e., departure_sec >= 86400 (past midnight) and >= 86400 + current secs
            min_sec_yesterday = 86400 + secs_from_midnight
            trips.extend(
                self._query_departures(
                    clean_stop_id,
                    clean_route_ids,
                    yesterday_services,
                    yesterday_midnight,
                    min_sec_yesterday,
                    count - len(trips),
                    only_post_midnight=True,
                )
            )

        trips.sort(key=lambda t: t["arrivalTime"])
        return trips[:count]

    def _query_departures(
        self,
        stop_id: str,
        route_ids: set[str],
        service_ids: set[str],
        service_midnight_unix: float,
        min_sec: int,
        limit: int,
        only_post_midnight: bool = False,
    ) -> list[dict]:
        """Query stop_departures for upcoming trips within given service day."""
        if not service_ids or limit <= 0:
            return []

        conn = self._get_conn()
        placeholders = ",".join("?" * len(service_ids))

        params: list = [stop_id, min_sec] + list(service_ids)

        route_clause = ""
        if route_ids:
            route_ph = ",".join("?" * len(route_ids))
            route_clause = f" AND route_id IN ({route_ph})"
            params += list(route_ids)

        midnight_clause = " AND departure_sec >= 86400" if only_post_midnight else ""

        params.append(limit)

        rows = conn.execute(
            f"SELECT trip_id, route_id, headsign, departure_sec "
            f"FROM stop_departures "
            f"WHERE stop_id=? AND departure_sec>=? "
            f"AND service_id IN ({placeholders})"
            f"{route_clause}{midnight_clause} "
            f"ORDER BY departure_sec ASC LIMIT ?",
            params,
        ).fetchall()

        # Load route info for names/colors
        route_cache: dict[str, sqlite3.Row] = {}
        result = []
        for row in rows:
            route_id = row["route_id"]
            if route_id not in route_cache:
                r = conn.execute(
                    "SELECT short_name, long_name, color FROM routes WHERE route_id=?",
                    (route_id,),
                ).fetchone()
                route_cache[route_id] = r

            route_info = route_cache.get(route_id)
            route_name = (route_info["short_name"] if route_info else None) or route_id
            route_color = route_info["color"] if route_info else None

            dep_unix = int(service_midnight_unix + row["departure_sec"])

            result.append(
                {
                    "tripId": str(row["trip_id"]),
                    "routeId": str(route_id),
                    "routeName": str(route_name),
                    "routeColor": route_color or None,
                    "stopId": stop_id,  # caller overrides with original stop_id
                    "headsign": str(row["headsign"] or ""),
                    "arrivalTime": dep_unix,
                    "departureTime": dep_unix,
                    "isRealtime": False,
                }
            )
        return result
