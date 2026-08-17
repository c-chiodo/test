"""Business logic.

Each module owns one area of the system and is callable without FastAPI, so
the rules can be tested — and read — on their own. In the legacy system these
rules lived in SQL Server stored procedures that most of the team could not
even view, which is why a validation change shipped in build 1.2.23.0 took a
decompile to explain.
"""

from . import inquiry, inventory, lims, orders, qc, query, reference, specs  # noqa: F401
