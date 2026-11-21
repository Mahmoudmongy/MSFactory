"""Every report the app can produce, behind one entry point.

``ReportService`` is a composition of the subject modules rather than a single
class, so the files stay readable while callers still get one object to
construct. Adding a report means adding a method to a mixin, not editing the
API layer.

Reports are grouped as:

* ``statements`` — trial balance, income statement, balance sheet, cash flow
* ``ledger`` — general journal, general ledger, account activity
* ``parties`` — receivables, payables, aging, statements, top customers
* ``inventory`` — valuation, movement, aging, reorder, item profitability
* ``sales`` — sales and purchases, tax, discounts, profit by origin, trends
* ``custom`` — the whitelisted pivot the client can shape themselves
* ``dashboards`` — the single-screen overviews

Nothing here caches a total. Every figure is aggregated from the entries and
movements that produced it, so a report cannot disagree with the ledger.
"""
from __future__ import annotations

from .base import ReportBase
from .custom import CustomReportMixin
from .dashboards import DashboardMixin
from .inventory import InventoryMixin
from .ledger import LedgerMixin
from .parties import PartiesMixin
from .sales import SalesMixin
from .statements import FinancialStatementsMixin


class ReportService(
    FinancialStatementsMixin,
    LedgerMixin,
    PartiesMixin,
    InventoryMixin,
    SalesMixin,
    CustomReportMixin,
    DashboardMixin,
    ReportBase,
):
    """All reports for one company."""


__all__ = ["ReportService"]
