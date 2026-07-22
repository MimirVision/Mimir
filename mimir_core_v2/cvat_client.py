"""Small standard-library client for the local CVAT Community deployment."""

from __future__ import annotations

import json
import http.client
import mimetypes
import os
import secrets
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable


DEFAULT_CVAT_URL = "http://localhost:8080"
DEFAULT_TOKEN_PATH = Path(r"C:\Mimir_Data\cvat\credentials\mimir-cvat-token.txt")
PROJECT_DEFINITION = Path(__file__).with_name("cvat_project_v1.json")


class CvatError(RuntimeError):
    """Raised for a failed or malformed CVAT operation."""


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CvatError(f"Expected a JSON object: {path}")
    return value


def configured_token(explicit: str = "", token_file: str = "") -> str:
    if explicit.strip():
        return explicit.strip()
    from_env = os.environ.get("MIMIR_CVAT_TOKEN", "").strip()
    if from_env:
        return from_env
    path = Path(token_file) if token_file else DEFAULT_TOKEN_PATH
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise CvatError(
            "CVAT API token is missing. Set MIMIR_CVAT_TOKEN or create the local token file."
        ) from exc
    if not token:
        raise CvatError(f"CVAT API token is empty: {path}")
    return token


def multipart_body(fields: dict[str, object], files: Iterable[tuple[str, Path]]) -> tuple[bytes, str]:
    """Build a multipart body for small test payloads.

    Production video uploads use ``request_multipart`` so footage is streamed
    from disk instead of being copied into one large Python bytes object.
    """
    boundary = f"mimir-{secrets.token_hex(16)}"
    chunks: list[bytes] = []
    for name, raw_value in fields.items():
        value = str(raw_value)
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("ascii"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )
    for field_name, path in files:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("ascii"),
                (
                    f'Content-Disposition: form-data; name="{field_name}"; '
                    f'filename="{path.name}"\r\n'
                ).encode("utf-8"),
                f"Content-Type: {mime}\r\n\r\n".encode("ascii"),
                path.read_bytes(),
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class CvatClient:
    def __init__(self, base_url: str = DEFAULT_CVAT_URL, token: str = "", timeout_sec: int = 120):
        self.base_url = base_url.rstrip("/")
        self.token = token.strip()
        self.timeout_sec = max(5, int(timeout_sec))

    def request(
        self,
        method: str,
        path: str,
        payload: object | None = None,
        content_type: str = "application/json",
        authenticate: bool = True,
    ) -> Any:
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        data: bytes | None = None
        if payload is not None:
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        # CVAT's versioned API may reject a plain application/json Accept
        # header. Omitting it lets the server negotiate its vendor media type.
        headers: dict[str, str] = {}
        if data is not None:
            headers["Content-Type"] = content_type
        if authenticate:
            if not self.token:
                raise CvatError("CVAT authentication token is required for this operation.")
            headers["Authorization"] = f"Token {self.token}"
        request = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
                raw = response.read()
                if not raw:
                    return {"status": response.status}
                try:
                    return json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    content_type_header = str(response.headers.get("Content-Type") or "").lower()
                    if "json" not in content_type_header:
                        return {
                            "status": response.status,
                            "body": raw.decode("utf-8", errors="replace").strip(),
                        }
                    raise
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise CvatError(f"CVAT {method.upper()} {url} failed ({exc.code}): {detail[:1000]}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise CvatError(f"CVAT is unavailable at {self.base_url}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise CvatError(f"CVAT returned malformed JSON for {method.upper()} {url}") from exc

    def request_multipart(
        self,
        path: str,
        fields: dict[str, object],
        files: Iterable[tuple[str, Path]],
    ) -> Any:
        """Stream a multipart upload without holding video files in memory."""
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise CvatError(f"Unsupported CVAT URL: {url}")
        boundary = f"mimir-{secrets.token_hex(16)}"
        segments: list[bytes | Path] = []
        for name, raw_value in fields.items():
            segments.append(
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                    f"{raw_value}\r\n"
                ).encode("utf-8")
            )
        for field_name, raw_path in files:
            media_path = raw_path.resolve()
            if not media_path.is_file():
                raise CvatError(f"CVAT upload file is missing: {media_path}")
            mime = mimetypes.guess_type(media_path.name)[0] or "application/octet-stream"
            safe_name = media_path.name.replace('"', "_")
            segments.append(
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{field_name}"; filename="{safe_name}"\r\n'
                    f"Content-Type: {mime}\r\n\r\n"
                ).encode("utf-8")
            )
            segments.extend([media_path, b"\r\n"])
        segments.append(f"--{boundary}--\r\n".encode("ascii"))
        content_length = sum(
            segment.stat().st_size if isinstance(segment, Path) else len(segment)
            for segment in segments
        )
        connection_type = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        connection = connection_type(parsed.hostname, parsed.port, timeout=self.timeout_sec)
        request_path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        try:
            connection.putrequest("POST", request_path)
            connection.putheader("Authorization", f"Token {self.token}")
            connection.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
            connection.putheader("Content-Length", str(content_length))
            connection.endheaders()
            for segment in segments:
                if isinstance(segment, Path):
                    with segment.open("rb") as handle:
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            connection.send(chunk)
                else:
                    connection.send(segment)
            response = connection.getresponse()
            raw = response.read()
            if response.status < 200 or response.status >= 300:
                detail = raw.decode("utf-8", errors="replace")
                raise CvatError(f"CVAT POST {url} failed ({response.status}): {detail[:1000]}")
            if not raw:
                return {"status": response.status}
            return json.loads(raw.decode("utf-8"))
        except (OSError, socket.timeout, http.client.HTTPException) as exc:
            raise CvatError(f"CVAT upload failed at {self.base_url}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise CvatError(f"CVAT returned malformed JSON for POST {url}") from exc
        finally:
            connection.close()

    def health(self) -> dict[str, Any]:
        value = self.request("GET", "/api/server/health", authenticate=False)
        if isinstance(value, dict) and int(value.get("status") or 0) == 200:
            return {"status": "ok", "http_status": 200}
        return value if isinstance(value, dict) else {"status": "ok"}

    def list_projects(self, name: str = "") -> list[dict[str, Any]]:
        query = f"?search={urllib.parse.quote(name)}" if name else ""
        value = self.request("GET", f"/api/projects{query}")
        if isinstance(value, dict) and isinstance(value.get("results"), list):
            return [item for item in value["results"] if isinstance(item, dict)]
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    def ensure_project(self, definition_path: Path = PROJECT_DEFINITION) -> dict[str, Any]:
        definition = _read_json(definition_path)
        name = str(definition.get("name") or "Mimir Contact Dataset v1")
        existing = next((item for item in self.list_projects(name) if item.get("name") == name), None)
        if existing:
            expected_labels = {
                str(label.get("name") or ""): {
                    str(attribute.get("name") or "")
                    for attribute in label.get("attributes", [])
                    if isinstance(attribute, dict)
                }
                for label in definition.get("labels", [])
                if isinstance(label, dict)
            }
            actual_labels = {
                str(label.get("name") or ""): {
                    str(attribute.get("name") or "")
                    for attribute in label.get("attributes", [])
                    if isinstance(attribute, dict)
                }
                for label in self.project_labels(int(existing["id"]))
            }
            if actual_labels != expected_labels:
                raise CvatError(
                    f"Existing CVAT project {existing['id']} does not match {definition.get('schema_version')}. "
                    "Create a new versioned project before importing tasks."
                )
            return {"created": False, "project": existing}
        labels = definition.get("labels") if isinstance(definition.get("labels"), list) else []
        project = self.request("POST", "/api/projects", {"name": name, "labels": labels})
        if not isinstance(project, dict) or not project.get("id"):
            raise CvatError("CVAT did not return a project id.")
        return {"created": True, "project": project}

    def create_video_task(
        self,
        project_id: int,
        name: str,
        media: list[Path],
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        readable = [path.resolve() for path in media if path.exists() and path.is_file()]
        if not readable:
            raise CvatError("A CVAT task requires at least one readable media file.")
        task_payload: dict[str, Any] = {
            "name": name[:256],
            "project_id": int(project_id),
            "segment_size": 0,
        }
        if metadata:
            task_payload["subset"] = str(metadata.get("split") or "")[:64]
        task = self.request("POST", "/api/tasks", task_payload)
        if not isinstance(task, dict) or not task.get("id"):
            raise CvatError("CVAT did not return a task id.")
        task_id = int(task["id"])
        file_parts = [(f"client_files[{index}]", path) for index, path in enumerate(readable)]
        try:
            response = self.request_multipart(
                f"/api/tasks/{task_id}/data",
                {"image_quality": 95, "use_cache": "true", "use_zip_chunks": "true"},
                file_parts,
            )
        except CvatError:
            # A task without uploaded media is not useful and causes confusing
            # annotation queues. Best-effort cleanup keeps retries predictable.
            try:
                self.request("DELETE", f"/api/tasks/{task_id}")
            except CvatError:
                pass
            raise
        return {
            "task_id": task_id,
            "name": task.get("name") or name,
            "media": [str(path) for path in readable],
            "upload_response": response,
        }

    def list_tasks(self, project_id: int) -> list[dict[str, Any]]:
        value = self.request("GET", f"/api/tasks?project_id={int(project_id)}&page_size=1000")
        if isinstance(value, dict) and isinstance(value.get("results"), list):
            return [item for item in value["results"] if isinstance(item, dict)]
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    def wait_for_task_data(self, task_id: int, timeout_sec: int = 300) -> dict[str, Any]:
        deadline = time.monotonic() + max(5, timeout_sec)
        while time.monotonic() < deadline:
            task = self.request("GET", f"/api/tasks/{int(task_id)}")
            if isinstance(task, dict):
                state = str(task.get("status") or task.get("state") or "").lower()
                size = task.get("size")
                if state in {"annotation", "validation", "completed"} or isinstance(size, int):
                    return task
            time.sleep(2)
        raise CvatError(f"CVAT task {task_id} did not become ready within {timeout_sec} seconds.")

    def task(self, task_id: int) -> dict[str, Any]:
        value = self.request("GET", f"/api/tasks/{int(task_id)}")
        if not isinstance(value, dict):
            raise CvatError(f"CVAT task {task_id} response is malformed.")
        return value

    def task_annotations(self, task_id: int) -> dict[str, Any]:
        value = self.request("GET", f"/api/tasks/{int(task_id)}/annotations")
        if not isinstance(value, dict):
            raise CvatError(f"CVAT annotations for task {task_id} are malformed.")
        return value

    def project_labels(self, project_id: int) -> list[dict[str, Any]]:
        value = self.request("GET", f"/api/labels?project_id={int(project_id)}&page_size=100")
        if isinstance(value, dict) and isinstance(value.get("results"), list):
            return [item for item in value["results"] if isinstance(item, dict)]
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def load_project_definition() -> dict[str, Any]:
    return _read_json(PROJECT_DEFINITION)
