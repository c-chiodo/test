"""Connections to the systems PIMS does not own.

Each integration is a *source adapter* plus a sync function. The adapter is the
only part that knows about the other system, so the scaffolding here can be
exercised end to end with a stub — locally, in tests, and in the browser
sandbox — and pointed at the real thing by swapping one class once IT grants
access.

    lims_ingest   LabWare results into the local projection
    gp_sync       Great Plains customers, vendors and order headers
    scale         truck-scale weights, posted by an agent at the plant

Every run records a ``job_run`` row, so a silent integration failure shows up
in the support console instead of as missing data three weeks later.
"""

from . import gp_sync, lims_ingest, scale  # noqa: F401
