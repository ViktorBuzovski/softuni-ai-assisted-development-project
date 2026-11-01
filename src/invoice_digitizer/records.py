import csv
from collections.abc import Iterable
from io import StringIO

from sqlalchemy import Engine, event, func
from sqlmodel import or_

from invoice_digitizer.models import Invoice, InvoiceRead, InvoiceStatus

SEARCHED_COLUMNS = (
    Invoice.filename,
    Invoice.invoice_number,
    Invoice.supplier_name,
    Invoice.buyer_name,
)
CSV_COLUMNS = (
    "id",
    "filename",
    "status",
    "document_type",
    "invoice_number",
    "invoice_date",
    "due_date",
    "supplier_name",
    "supplier_tax_id",
    "buyer_name",
    "buyer_tax_id",
    "currency",
    "subtotal",
    "tax_amount",
    "total",
    "uploaded_at",
    "extracted_at",
)
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r", "\n", "＝", "＋", "－", "＠")


def enable_search(engine: Engine) -> None:
    """Add a casefold function, because SQLite's own lower() only handles ASCII."""

    @event.listens_for(engine, "connect")
    def add_casefold(connection, _record):
        connection.create_function(
            "casefold",
            1,
            lambda text: None if text is None else text.casefold(),
            deterministic=True,
        )


def record_filters(q: str, status: InvoiceStatus | None) -> list:
    filters = []
    if search := q.strip().casefold():
        filters.append(
            or_(
                *(
                    func.casefold(column).contains(search, autoescape=True)
                    for column in SEARCHED_COLUMNS
                )
            )
        )
    if status:
        filters.append(Invoice.status == status)
    return filters


def csv_cell(value):
    """Neutralize text a spreadsheet would run as a formula. Only text can be untrusted."""
    if value is None:
        return ""
    if isinstance(value, str) and value.startswith(FORMULA_STARTS):
        return f"'{value}"
    return value


def invoices_to_csv(invoices: Iterable[Invoice]) -> bytes:
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(CSV_COLUMNS)
    for invoice in invoices:
        record = InvoiceRead.model_validate(invoice)
        writer.writerow(csv_cell(getattr(record, column)) for column in CSV_COLUMNS)
    # The byte order mark tells Excel that the file is UTF-8.
    return output.getvalue().encode("utf-8-sig")
