"""PIMS — Production Inventory Management System.

A replacement for the Feed Energy VB.NET WinForms client of the same name:
same workflows (orders, receiving, production, movement, loading, shipping,
shrinkage, quality control, inquiry and custom query), rebuilt as a documented
HTTP API with a browser front end, and with the business rules in readable,
tested Python instead of stored procedures.

Entry points:
    ``pims.app:app``        FastAPI application (``uvicorn pims.app:app``)
    ``python -m pims``      CLI: init-db, seed, serve, diagnose, check
"""

__version__ = "1.0.0"

__all__ = ["__version__"]
