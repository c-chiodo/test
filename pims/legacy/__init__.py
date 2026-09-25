"""Read-only companion to the legacy PIMS.

The legacy desktop client stays the system of record; this package reads its
database (``ProductionData`` on ``FESQLPROD01\\PRODUCTION``) and mirrors it into
the local store, so every read-side feature of the replacement — dashboards,
inquiry, data quality, alerts, the corrected LIMS matrix — runs on real data
without anyone needing write access to production.

Nothing in this package can write to the legacy database. That is enforced
three ways, and the code is only the second of them:

1. the SQL login is read-only (``db_datareader``) — the guarantee that matters;
2. :class:`~pims.legacy.readonly.ReadOnlyConnection` refuses any statement that
   is not a single ``SELECT``/``WITH``, before it reaches the driver;
3. the connection string asks for ``ApplicationIntent=ReadOnly``, so a server
   with a readable secondary routes these reads away from the primary.
"""
