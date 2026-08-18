"""Accounts-payable PO agent.

Watches a mailbox for vendor purchase-order documents (order confirmations,
packing slips, invoices), extracts the order data with Claude, validates it
against Dynamics GP master data, and emits eConnect ``POPReceivingsType``
XML so the receipt can be posted in GP without manual entry.

The agent keeps a per-vendor memory: every human correction updates that
vendor's item aliases, unit-of-measure mappings and layout notes, so
extraction accuracy improves with use and trusted vendors graduate to
straight-through (auto-post) processing.
"""

__version__ = "0.1.0"
