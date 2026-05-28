"""Observability surface beyond the in-process metrics ring buffers.

Exports a single module-level `influx` singleton built from environment
variables. When the InfluxDB token is unset the singleton is a no-op,
so the service runs unchanged without an InfluxDB backend.
"""

from .influxdb_writer import InfluxDBWriter, influx

__all__ = ["InfluxDBWriter", "influx"]
