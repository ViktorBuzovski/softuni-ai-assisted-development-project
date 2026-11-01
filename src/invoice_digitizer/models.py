from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from pydantic import AfterValidator, ConfigDict, StringConstraints, WithJsonSchema
from sqlalchemy import JSON, String
from sqlalchemy.types import TypeDecorator
from sqlmodel import Field, SQLModel


class InvoiceStatus(StrEnum):
    uploaded = "uploaded"
    extracting = "extracting"
    extracted = "extracted"
    failed = "failed"


class DocumentType(StrEnum):
    invoice = "invoice"
    credit_note = "credit_note"
    other = "other"


class ExactDecimal(TypeDecorator):
    """Stores decimals as text, because SQLite would turn them into floats."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else str(value)

    def process_result_value(self, value, dialect):
        return None if value is None else Decimal(value)


# The claude command parses JSON numbers as floats, which would round large amounts,
# so the model answers with strings, and they stay exact decimals from there on.
Amount = Annotated[
    Decimal, WithJsonSchema({"type": "string", "pattern": r"^-?[0-9]+(\.[0-9]+)?$"})
]
CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]


def as_utc(value: datetime) -> datetime:
    """SQLite drops the time zone, and every stored time is UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


UtcDatetime = Annotated[datetime, AfterValidator(as_utc)]


def require_every_field(schema: dict[str, Any]) -> None:
    """Make the model answer every field, using null when a value isn't printed."""
    schema["required"] = list(schema["properties"])


class LineItem(SQLModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra=require_every_field)

    description: str | None = None
    quantity: Amount | None = None
    unit_price: Amount | None = None
    amount: Amount | None = None


class InvoiceFields(SQLModel):
    """The values read from a document."""

    document_type: DocumentType | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    supplier_name: str | None = None
    supplier_tax_id: str | None = None
    buyer_name: str | None = None
    buyer_tax_id: str | None = None
    currency: str | None = None
    subtotal: Amount | None = Field(default=None, sa_type=ExactDecimal)
    tax_amount: Amount | None = Field(default=None, sa_type=ExactDecimal)
    total: Amount | None = Field(default=None, sa_type=ExactDecimal)
    line_items: list[LineItem] = Field(default_factory=list, sa_type=JSON)


class ExtractedInvoice(InvoiceFields):
    """What the model must return. It generates the JSON schema and validates the answer."""

    model_config = ConfigDict(extra="forbid", json_schema_extra=require_every_field)

    document_type: DocumentType
    currency: CurrencyCode | None = None


class Invoice(InvoiceFields, table=True):
    # A reused id would make a stale request act on a different invoice.
    __table_args__ = {"sqlite_autoincrement": True}

    id: int | None = Field(default=None, primary_key=True)
    filename: str
    stored_name: str
    content_type: str
    size_bytes: int
    status: InvoiceStatus = InvoiceStatus.uploaded
    extraction_error: str | None = None
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    extracted_at: datetime | None = None

    def record_extraction(self, extracted: ExtractedInvoice) -> None:
        self.sqlmodel_update(extracted.model_dump(exclude={"line_items"}))
        # The column holds JSON, so the decimals inside it are stored as strings.
        self.line_items = [item.model_dump(mode="json") for item in extracted.line_items]
        self.status = InvoiceStatus.extracted
        self.extraction_error = None
        self.extracted_at = datetime.now(UTC)

    def record_failure(self, message: str) -> None:
        self.status = InvoiceStatus.failed
        self.extraction_error = message


class InvoiceRead(InvoiceFields):
    id: int
    filename: str
    content_type: str
    size_bytes: int
    status: InvoiceStatus
    extraction_error: str | None
    uploaded_at: UtcDatetime
    extracted_at: UtcDatetime | None


class InvoicePage(SQLModel):
    items: list[InvoiceRead]
    total: int
