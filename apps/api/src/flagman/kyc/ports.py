from http import HTTPStatus

from flagman.core.config import Settings
from flagman.core.errors import AppError
from flagman.core.storage import ObjectStorage, object_storage_from_settings

# Document photos live in the shared private storage under `kyc/` (docs/adr/0012).
DocumentStorage = ObjectStorage


class DocumentStorageUnavailableError(AppError):
    status = HTTPStatus.SERVICE_UNAVAILABLE
    slug = "document-storage-unavailable"
    title = "Document storage is unavailable"


def create_document_storage(settings: Settings) -> DocumentStorage:
    storage = object_storage_from_settings(settings)
    if storage is None:
        raise DocumentStorageUnavailableError("No document storage is configured.")
    return storage
