"""Shared, SDP-independent helpers for the Event Hub order ingestion.

Modules in this package contain NO ``pyspark.pipelines`` decorators and never
touch the SDP-injected ``spark`` / ``dbutils`` globals at import time. They are
imported both by the Lakeflow SDP transformation files (via a ``sys.path``
insert, see those files) and by the local pytest harness under ``tests/``.
"""
