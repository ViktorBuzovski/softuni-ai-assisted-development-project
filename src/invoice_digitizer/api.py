from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlmodel import Session, SQLModel, create_engine

from invoice_digitizer.files import detect_file_type, display_name, generate_stored_name
from invoice_digitizer.models import Invoice, InvoiceRead
from invoice_digitizer.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        f"sqlite:///{settings.database_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)

    def get_session():
        with Session(engine) as session:
            yield session

    SessionDep = Annotated[Session, Depends(get_session)]

    def get_invoice(invoice_id: int, session: SessionDep) -> Invoice:
        invoice = session.get(Invoice, invoice_id)
        if invoice is None:
            raise HTTPException(404, "Invoice not found")
        return invoice

    InvoiceDep = Annotated[Invoice, Depends(get_invoice)]

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

    @app.get("/api/invoices/{invoice_id}", response_model=InvoiceRead)
    def read_invoice(invoice: InvoiceDep) -> Invoice:
        return invoice

    @app.get("/api/invoices/{invoice_id}/file")
    def read_invoice_file(invoice: InvoiceDep) -> FileResponse:
        return FileResponse(
            settings.uploads_dir / invoice.stored_name,
            media_type=invoice.content_type,
            filename=invoice.filename,
            content_disposition_type="inline",
        )

    return app
