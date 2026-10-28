"""Blob client protocol. Tests use MemoryBlob. Azure is imported only when selected."""

import os


class BlobExists(Exception):
    """A put refused to replace an object that is already there."""


class MemoryBlob:
    """In-memory objects. There is no delete."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def get(self, name: str) -> bytes | None:
        return self.objects.get(name)

    def put(self, name: str, data: bytes, *, if_absent: bool = False) -> None:
        if if_absent and name in self.objects:
            raise BlobExists(name)
        self.objects[name] = bytes(data)

    def list(self, prefix: str) -> list[str]:
        return sorted(name for name in self.objects if name.startswith(prefix))


class AzureBlob:
    def __init__(self, container) -> None:
        self._container = container

    def get(self, name: str) -> bytes | None:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return self._container.download_blob(name).readall()
        except ResourceNotFoundError:
            return None

    def put(self, name: str, data: bytes, *, if_absent: bool = False) -> None:
        from azure.core.exceptions import ResourceExistsError

        try:
            self._container.upload_blob(name, data, overwrite=not if_absent)
        except ResourceExistsError as exc:
            raise BlobExists(name) from exc

    def list(self, prefix: str) -> list[str]:
        return [blob.name for blob in self._container.list_blobs(name_starts_with=prefix)]


def azure_container(container: str) -> AzureBlob:
    try:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient
    except ImportError as exc:
        raise RuntimeError("Install the cloud extra to use Blob storage.") from exc
    account = os.environ.get("AZURE_STORAGE_ACCOUNT") or ""
    if not account:
        raise RuntimeError("AZURE_STORAGE_ACCOUNT is required when a Blob backend is selected.")
    service = BlobServiceClient(
        account_url=f"https://{account}.blob.core.windows.net",
        credential=DefaultAzureCredential(),
    )
    return AzureBlob(service.get_container_client(container))
