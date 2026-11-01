from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class Invoice(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    filename: str
    stored_name: str
    content_type: str
    size_bytes: int
    status: str = "uploaded"
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class InvoiceRead(SQLModel):
    id: int
    filename: str
    content_type: str
    size_bytes: int
    status: str
    uploaded_at: datetime
