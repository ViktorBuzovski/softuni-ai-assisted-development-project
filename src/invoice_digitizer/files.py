from pathlib import PurePosixPath
from uuid import uuid4

# File signature -> (content type, extension for the stored file)
SIGNATURES = {
    b"%PDF-": ("application/pdf", ".pdf"),
    b"\x89PNG\r\n\x1a\n": ("image/png", ".png"),
    b"\xff\xd8\xff": ("image/jpeg", ".jpg"),
}


def detect_file_type(content: bytes) -> tuple[str, str] | None:
    for signature, file_type in SIGNATURES.items():
        if content.startswith(signature):
            return file_type
    return None


def display_name(filename: str | None) -> str:
    """Reduce a client-supplied filename to its last path part."""
    name = PurePosixPath((filename or "").replace("\\", "/")).name
    return name if name not in ("", "..") else "invoice"


def generate_stored_name(extension: str) -> str:
    return f"{uuid4().hex}{extension}"
