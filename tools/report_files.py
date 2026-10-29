"""Copy the status-page files for one run. Other files stay out."""

import os
from pathlib import Path

_LOGS = {"run.jsonl", "mcp.jsonl"}


class ContainerExists(Exception):
    """The reports container is already there."""


def safe_run_id(run_id: str) -> bool:
    if not run_id or run_id in {".", ".."}:
        return False
    return all(ch.isalnum() or ch in "-_" for ch in run_id)


def allowed_relative(rel: str) -> bool:
    if not rel or rel.startswith("/") or "\\" in rel:
        return False
    parts = rel.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    if parts[0] == "reports":
        if parts == ["reports", "audit.json"]:
            return True
        if len(parts) == 3 and parts[1] == "traces" and _jsonl(parts[2]):
            return True
        if len(parts) == 3 and parts[1] == "logs" and parts[2] in _LOGS:
            return True
        if len(parts) >= 4 and parts[1] == "classification" and _json(parts[-1]) and _names(parts[2:-1]):
            return True
        return False
    if (
        len(parts) >= 4
        and parts[0] == "submissions"
        and parts[1] == "classification"
        and _json(parts[-1])
        and _names(parts[2:-1])
    ):
        return True
    return False


def _json(name: str) -> bool:
    return name.endswith(".json") and _safe_name(name)


def _jsonl(name: str) -> bool:
    return name.endswith(".jsonl") and _safe_name(name)


def _names(parts: list[str]) -> bool:
    return all(_safe_name(part) for part in parts)


def _safe_name(name: str) -> bool:
    if not name or name in {".", ".."}:
        return False
    return "/" not in name and "\\" not in name and ".." not in name


def selected_files(root: Path) -> list[tuple[str, Path]]:
    root = root.resolve()
    found: list[tuple[str, Path]] = []
    audit = root / "reports" / "audit.json"
    if audit.is_file():
        found.append(("reports/audit.json", audit))
    folders = (
        root / "reports" / "traces",
        root / "reports" / "logs",
        root / "reports" / "classification",
        root / "submissions" / "classification",
    )
    for folder in folders:
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*")):
            if not path.is_file():
                continue
            try:
                rel = path.resolve().relative_to(root).as_posix()
            except ValueError:
                continue
            if allowed_relative(rel):
                found.append((rel, path))
    return found


def upload_run(blob, root: Path, run_id: str) -> None:
    if not safe_run_id(run_id):
        raise RuntimeError("run")
    prefix = f"runs/{run_id}/"
    for rel, path in selected_files(root):
        blob.put(prefix + rel, path.read_bytes())
    blob.put("latest", run_id.encode("utf-8"))


def download_latest(blob, dest: Path) -> str | None:
    try:
        raw = blob.get("latest")
    except Exception:
        return None
    if not isinstance(raw, (bytes, bytearray)):
        return None
    run_id = bytes(raw).decode("utf-8", errors="replace").strip()
    if not safe_run_id(run_id):
        return None
    prefix = f"runs/{run_id}/"
    try:
        names = list(blob.list(prefix))
    except Exception:
        return None
    root = dest.resolve()
    for name in names:
        if not isinstance(name, str) or not name.startswith(prefix):
            continue
        rel = name[len(prefix) :]
        if not allowed_relative(rel):
            continue
        try:
            data = blob.get(name)
        except Exception:
            continue
        if not isinstance(data, (bytes, bytearray)):
            continue
        target = (dest / rel).resolve()
        if os.path.commonpath([str(root), str(target)]) != str(root):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(bytes(data))
    return run_id


def ensure_container(create, exists: tuple[type[BaseException], ...] = (ContainerExists,)) -> None:
    try:
        create()
    except exists:
        return


def azure_reports(name: str, *, create: bool = False):
    from dispute_copilot.adapters.blob_store import AzureBlob, azure_container

    if not create:
        return azure_container(name)
    client = _container_client(name)

    def create_container() -> None:
        from azure.core.exceptions import ResourceExistsError

        try:
            client.create_container()
        except ResourceExistsError as exc:
            raise ContainerExists(name) from exc

    ensure_container(create_container)
    return AzureBlob(client)


def _container_client(name: str):
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient

    account = os.environ.get("AZURE_STORAGE_ACCOUNT") or ""
    if not account:
        raise RuntimeError("AZURE_STORAGE_ACCOUNT is required when a Blob backend is selected.")
    service = BlobServiceClient(
        account_url=f"https://{account}.blob.core.windows.net",
        credential=DefaultAzureCredential(),
    )
    return service.get_container_client(name)
