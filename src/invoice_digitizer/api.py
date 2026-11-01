import logging
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Response, UploadFile
from fastapi import Path as PathParameter
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func
from sqlmodel import Session, SQLModel, create_engine, delete, select, update

from invoice_digitizer.extraction import ExtractionError, extract_invoice
from invoice_digitizer.files import detect_file_type, display_name, generate_stored_name
from invoice_digitizer.models import (
    DocumentType,
    ExtractedInvoice,
    Invoice,
    InvoicePage,
    InvoiceRead,
    InvoiceStatus,
)
from invoice_digitizer.records import enable_search, invoices_to_csv, record_filters
from invoice_digitizer.settings import Settings

logger = logging.getLogger(__name__)

CAN_EXTRACT = (InvoiceStatus.uploaded, InvoiceStatus.failed)
SQLITE_MAX_INTEGER = 2**63 - 1
InvoiceId = Annotated[int, PathParameter(le=SQLITE_MAX_INTEGER)]


def create_app(
    settings: Settings | None = None,
    extract: Callable[[Path], ExtractedInvoice] | None = None,
) -> FastAPI:
    settings = settings or Settings()
    extract = extract or partial(extract_invoice, settings=settings)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        f"sqlite:///{settings.database_path}",
        # Wait out a temporary lock, such as a backup or a database browser.
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    enable_search(engine)
    SQLModel.metadata.create_all(engine)

    # Nothing is still running after a restart, so these would otherwise stay stuck.
    with Session(engine) as session:
        session.exec(
            update(Invoice)
            .where(Invoice.status == InvoiceStatus.extracting)
            .values(
                status=InvoiceStatus.failed,
                extraction_error="The server restarted during extraction",
            )
        )
        session.commit()

    def get_session():
        with Session(engine) as session:
            yield session

    SessionDep = Annotated[Session, Depends(get_session)]

    def get_invoice(invoice_id: InvoiceId, session: SessionDep) -> Invoice:
        invoice = session.get(Invoice, invoice_id)
        if invoice is None:
            raise HTTPException(404, "Invoice not found")
        return invoice

    InvoiceDep = Annotated[Invoice, Depends(get_invoice)]

    def refusal(invoice_id: int, session: Session) -> HTTPException:
        """Say why a conditional write changed nothing, as the record may have gone since."""
        status = session.exec(select(Invoice.status).where(Invoice.id == invoice_id)).first()
        if status is None:
            return HTTPException(404, "Invoice not found")
        return HTTPException(409, f"This invoice is already {status.value}")

    app = FastAPI(title="Invoice Digitizer")

    @app.post("/api/invoices", response_model=InvoiceRead, status_code=201)
    def upload_invoice(file: UploadFile, session: SessionDep) -> Invoice:
        content = file.file.read(settings.max_upload_bytes + 1)
        if not content:
            raise HTTPException(400, "The file is empty")
        if len(content) > settings.max_upload_bytes:
            raise HTTPException(413, "The file is too large")

        file_type = detect_file_type(content)
        if file_type is None:
            raise HTTPException(415, "Only PDF, PNG and JPEG files are accepted")
        content_type, extension = file_type

        invoice = Invoice(
            filename=display_name(file.filename),
            stored_name=generate_stored_name(extension),
            content_type=content_type,
            size_bytes=len(content),
        )
        path = settings.uploads_dir / invoice.stored_name
        path.write_bytes(content)
        try:
            session.add(invoice)
            session.commit()
        except Exception:
            path.unlink(missing_ok=True)
            raise
        session.refresh(invoice)
        return invoice

    @app.get("/api/invoices", response_model=InvoicePage)
    def list_invoices(
        session: SessionDep,
        q: str = "",
        status: InvoiceStatus | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0, le=SQLITE_MAX_INTEGER)] = 0,
    ) -> dict:
        filters = record_filters(q, status)
        total = session.exec(select(func.count()).select_from(Invoice).where(*filters)).one()
        items = session.exec(
            select(Invoice).where(*filters).order_by(Invoice.id.desc()).limit(limit).offset(offset)
        ).all()
        return {"items": items, "total": total}

    # Declared before /{invoice_id}, which would otherwise try to read this as an id.
    @app.get("/api/invoices/export.csv")
    def export_invoices(
        session: SessionDep,
        q: str = "",
        status: InvoiceStatus | None = None,
    ) -> Response:
        invoices = session.exec(
            select(Invoice).where(*record_filters(q, status)).order_by(Invoice.id.desc())
        ).all()
        return Response(
            invoices_to_csv(invoices),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="invoices.csv"'},
        )

    @app.get("/api/invoices/{invoice_id}", response_model=InvoiceRead)
    def read_invoice(invoice: InvoiceDep) -> Invoice:
        return invoice

    @app.get("/api/invoices/{invoice_id}/file")
    def read_invoice_file(invoice: InvoiceDep) -> FileResponse:
        path = settings.uploads_dir / invoice.stored_name
        if not path.exists():
            raise HTTPException(404, "The stored file is missing")
        return FileResponse(
            path,
            media_type=invoice.content_type,
            filename=invoice.filename,
            content_disposition_type="inline",
        )

    @app.delete("/api/invoices/{invoice_id}", status_code=204)
    def delete_invoice(invoice_id: InvoiceId, invoice: InvoiceDep, session: SessionDep) -> None:
        path = settings.uploads_dir / invoice.stored_name

        # One atomic delete, so an extraction can't start between a check and the delete.
        deleted = session.exec(
            delete(Invoice).where(
                Invoice.id == invoice_id,
                Invoice.status != InvoiceStatus.extracting,
            )
        )
        session.commit()
        if not deleted.rowcount:
            raise refusal(invoice_id, session)
        path.unlink(missing_ok=True)

    @app.post("/api/invoices/{invoice_id}/extract", response_model=InvoiceRead)
    def extract_fields(invoice_id: InvoiceId, invoice: InvoiceDep, session: SessionDep) -> Invoice:
        path = settings.uploads_dir / invoice.stored_name

        # One atomic update, so two requests can't both start the same extraction.
        claim = session.exec(
            update(Invoice)
            .where(Invoice.id == invoice_id, Invoice.status.in_(CAN_EXTRACT))
            .values(status=InvoiceStatus.extracting, extraction_error=None)
        )
        session.commit()
        if not claim.rowcount:
            raise refusal(invoice_id, session)

        try:
            extracted = extract(path)
            if extracted.document_type == DocumentType.other:
                raise ExtractionError("This file doesn't look like an invoice")
            invoice.record_extraction(extracted)
            session.commit()
        except Exception as error:
            if isinstance(error, ExtractionError):
                message = str(error)
            else:
                logger.exception("Extraction of invoice %s failed", invoice_id)
                message = "Extraction failed unexpectedly"
            # A failed commit must be rolled back before the failure can be saved.
            session.rollback()
            invoice.record_failure(message)
            session.commit()

        session.refresh(invoice)
        return invoice

    app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="ui")
    return app
