"""IPC message types and serialization helpers for daemon communication."""

from __future__ import annotations

import msgspec as _msgspec

# ---------------------------------------------------------------------------
# Requests (tagged union via struct tag)
# ---------------------------------------------------------------------------


class HandshakeRequest(_msgspec.Struct, tag="handshake"):
    version: str


class IndexRequest(_msgspec.Struct, tag="index"):
    project_root: str


class SearchRequest(_msgspec.Struct, tag="search"):
    project_root: str
    query: str
    doc_ids: list[str] | None = None
    limit: int = 5
    offset: int = 0


class ProjectStatusRequest(_msgspec.Struct, tag="project_status"):
    project_root: str


class DaemonStatusRequest(_msgspec.Struct, tag="daemon_status"):
    pass


class RemoveProjectRequest(_msgspec.Struct, tag="remove_project"):
    project_root: str


class StopRequest(_msgspec.Struct, tag="stop"):
    pass


class DoctorRequest(_msgspec.Struct, tag="doctor"):
    project_root: str | None = None


class DaemonEnvRequest(_msgspec.Struct, tag="daemon_env"):
    pass


Request = (
    HandshakeRequest
    | IndexRequest
    | SearchRequest
    | ProjectStatusRequest
    | DaemonStatusRequest
    | RemoveProjectRequest
    | StopRequest
    | DoctorRequest
    | DaemonEnvRequest
)

# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class HandshakeResponse(_msgspec.Struct, tag="handshake"):
    ok: bool
    daemon_version: str
    global_settings_mtime_us: int | None = None
    warnings: list[str] = []


class IndexResponse(_msgspec.Struct, tag="index"):
    success: bool
    message: str | None = None
    file_count: int = 0
    total_lines: int = 0
    total_bytes: int = 0
    languages: dict[str, int] = _msgspec.field(default_factory=dict)


class IndexingProgress(_msgspec.Struct):
    """Indexing stats snapshot."""

    files_scanned: int
    files_processed: int
    files_unchanged: int
    files_error: int
    total_nodes: int


class IndexProgressUpdate(_msgspec.Struct, tag="index_progress"):
    """Streamed during indexing."""

    progress: IndexingProgress


class IndexWaitingNotice(_msgspec.Struct, tag="index_waiting"):
    """Sent when another indexing is already in progress."""

    pass


class SearchResult(_msgspec.Struct):
    file_path: str
    source_path: str | None = None
    doc_name: str | None = None
    node_title: str | None = None
    content: str
    start_line: int | None = None
    end_line: int | None = None
    score: float = 0.0


class SearchResponse(_msgspec.Struct, tag="search"):
    success: bool
    results: list[SearchResult] = []
    total_returned: int = 0
    offset: int = 0
    message: str | None = None
    confidence: float = 0.0


class ProjectStatusResponse(_msgspec.Struct, tag="project_status"):
    indexing: bool
    file_count: int
    total_lines: int
    total_bytes: int
    languages: dict[str, int]
    progress: IndexingProgress | None = None
    doc_id: str | None = None


class DaemonProjectInfo(_msgspec.Struct):
    project_root: str
    indexing: bool


class DaemonStatusResponse(_msgspec.Struct, tag="daemon_status"):
    version: str
    uptime_seconds: float
    projects: list[DaemonProjectInfo]


class RemoveProjectResponse(_msgspec.Struct, tag="remove_project"):
    ok: bool


class StopResponse(_msgspec.Struct, tag="stop"):
    ok: bool


class DoctorCheckResult(_msgspec.Struct):
    name: str
    ok: bool
    details: list[str]
    errors: list[str]


class DoctorResponse(_msgspec.Struct, tag="doctor"):
    result: DoctorCheckResult
    final: bool = False


class DbPathMappingEntry(_msgspec.Struct):
    source: str
    target: str


class DaemonEnvResponse(_msgspec.Struct, tag="daemon_env"):
    env_names: list[str]
    settings_env_names: list[str]
    path_mappings: list[DbPathMappingEntry] = []


class ErrorResponse(_msgspec.Struct, tag="error"):
    message: str


Response = (
    HandshakeResponse
    | IndexResponse
    | IndexProgressUpdate
    | IndexWaitingNotice
    | SearchResponse
    | ProjectStatusResponse
    | DaemonStatusResponse
    | RemoveProjectResponse
    | StopResponse
    | DoctorResponse
    | DaemonEnvResponse
    | ErrorResponse
)

IndexStreamResponse = IndexProgressUpdate | IndexWaitingNotice | IndexResponse | ErrorResponse
SearchStreamResponse = IndexWaitingNotice | SearchResponse | ErrorResponse
DoctorStreamResponse = DoctorResponse | ErrorResponse

# ---------------------------------------------------------------------------
# Encode / decode helpers (msgpack binary)
# ---------------------------------------------------------------------------

_request_encoder = _msgspec.msgpack.Encoder()
_request_decoder = _msgspec.msgpack.Decoder(Request)

_response_encoder = _msgspec.msgpack.Encoder()
_response_decoder = _msgspec.msgpack.Decoder(Response)


def encode_request(req: Request) -> bytes:
    return _request_encoder.encode(req)


def decode_request(data: bytes) -> Request:
    result: Request = _request_decoder.decode(data)
    return result


def encode_response(resp: Response) -> bytes:
    return _response_encoder.encode(resp)


def decode_response(data: bytes) -> Response:
    result: Response = _response_decoder.decode(data)
    return result
