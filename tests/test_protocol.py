"""Tests for IPC message protocol."""

import msgspec

from vectorless_code.protocol import (
    DaemonStatusRequest,
    DaemonStatusResponse,
    DoctorRequest,
    HandshakeRequest,
    HandshakeResponse,
    IndexRequest,
    IndexResponse,
    ProjectStatusRequest,
    SearchRequest,
    SearchResponse,
    decode_request,
    decode_response,
    encode_request,
    encode_response,
)


def test_handshake_encode_decode() -> None:
    """Test handshake message encoding and decoding."""
    req = HandshakeRequest(version="0.1.1")
    data = encode_request(req)
    decoded = decode_request(data)

    assert isinstance(decoded, HandshakeRequest)
    assert decoded.version == "0.1.1"


def test_handshake_response_encode_decode() -> None:
    """Test handshake response encoding and decoding."""
    resp = HandshakeResponse(
        ok=True,
        daemon_version="0.1.1",
        global_settings_mtime_us=123456789,
        warnings=["test warning"],
    )
    data = encode_response(resp)
    decoded = decode_response(data)

    assert isinstance(decoded, HandshakeResponse)
    assert decoded.ok is True
    assert decoded.daemon_version == "0.1.1"
    assert decoded.global_settings_mtime_us == 123456789
    assert decoded.warnings == ["test warning"]


def test_index_request_encode_decode() -> None:
    """Test index request encoding and decoding."""
    req = IndexRequest(project_root="/path/to/project")
    data = encode_request(req)
    decoded = decode_request(data)

    assert isinstance(decoded, IndexRequest)
    assert decoded.project_root == "/path/to/project"


def test_index_response_encode_decode() -> None:
    """Test index response encoding and decoding."""
    resp = IndexResponse(
        success=True,
        file_count=10,
        total_lines=1000,
        total_bytes=50000,
        languages={"python": 8, "rust": 2},
    )
    data = encode_response(resp)
    decoded = decode_response(data)

    assert isinstance(decoded, IndexResponse)
    assert decoded.success is True
    assert decoded.file_count == 10
    assert decoded.total_lines == 1000
    assert decoded.total_bytes == 50000
    assert decoded.languages == {"python": 8, "rust": 2}


def test_search_request_encode_decode() -> None:
    """Test search request encoding and decoding."""
    req = SearchRequest(
        project_root="/path/to/project",
        query="test query",
        doc_ids=["doc1", "doc2"],
        limit=5,
        offset=0,
    )
    data = encode_request(req)
    decoded = decode_request(data)

    assert isinstance(decoded, SearchRequest)
    assert decoded.project_root == "/path/to/project"
    assert decoded.query == "test query"
    assert decoded.doc_ids == ["doc1", "doc2"]
    assert decoded.limit == 5
    assert decoded.offset == 0


def test_search_response_encode_decode() -> None:
    """Test search response encoding and decoding."""
    from vectorless_code.protocol import SearchResult

    resp = SearchResponse(
        success=True,
        results=[
            SearchResult(
                file_path="test.py",
                source_path="/path/to/test.py",
                doc_name="project",
                node_title="function",
                content="def test():\n    pass",
                score=0.95,
            )
        ],
        total_returned=1,
        offset=0,
        confidence=0.95,
    )
    data = encode_response(resp)
    decoded = decode_response(data)

    assert isinstance(decoded, SearchResponse)
    assert decoded.success is True
    assert len(decoded.results) == 1
    assert decoded.results[0].file_path == "test.py"
    assert decoded.results[0].score == 0.95
    assert decoded.confidence == 0.95


def test_doctor_request_encode_decode() -> None:
    """Test doctor request encoding and decoding."""
    req = DoctorRequest(project_root="/path/to/project")
    data = encode_request(req)
    decoded = decode_request(data)

    assert isinstance(decoded, DoctorRequest)
    assert decoded.project_root == "/path/to/project"


def test_daemon_status_request_encode_decode() -> None:
    """Test daemon status request encoding and decoding."""
    req = DaemonStatusRequest()
    data = encode_request(req)
    decoded = decode_request(data)

    assert isinstance(decoded, DaemonStatusRequest)


def test_daemon_status_response_encode_decode() -> None:
    """Test daemon status response encoding and decoding."""
    from vectorless_code.protocol import DaemonProjectInfo

    resp = DaemonStatusResponse(
        version="0.1.1",
        uptime_seconds=123.45,
        projects=[
            DaemonProjectInfo(project_root="/path/to/project", indexing=False)
        ],
    )
    data = encode_response(resp)
    decoded = decode_response(data)

    assert isinstance(decoded, DaemonStatusResponse)
    assert decoded.version == "0.1.1"
    assert decoded.uptime_seconds == 123.45
    assert len(decoded.projects) == 1
    assert decoded.projects[0].project_root == "/path/to/project"
