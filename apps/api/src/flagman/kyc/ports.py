from http import HTTPStatus

from flagman.core.errors import AppError
from flagman.core.storage import ObjectStorage

# Document photos live in the shared private storage under `kyc/` (docs/adr/0012).
DocumentStorage = ObjectStorage


class DocumentStorageUnavailableError(AppError):
    status = HTTPStatus.SERVICE_UNAVAILABLE
    slug = "document-storage-unavailable"
    title = "Document storage is unavailable"


def require_document_storage(storage: ObjectStorage | None) -> DocumentStorage:
    if storage is None:
        raise DocumentStorageUnavailableError("No document storage is configured.")
    return storage
