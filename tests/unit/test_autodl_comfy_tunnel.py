from __future__ import annotations

import hashlib
import hmac
import io
import json
import socket
import stat
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import autodl_comfy_tunnel as tunnel


class ChunkedSource:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.offset = 0
        self.request_sizes: list[int] = []

    def recv(self, size: int) -> bytes:
        self.request_sizes.append(size)
        if self.offset == len(self.payload):
            return b""
        end = min(self.offset + size, len(self.payload))
        chunk = self.payload[self.offset : end]
        self.offset = end
        return chunk


class PartialWriter:
    def __init__(self, max_write: int) -> None:
        self.max_write = max_write
        self.payload = bytearray()
        self.write_sizes: list[int] = []

    def send(self, data: memoryview) -> int:
        count = min(len(data), self.max_write)
        self.payload.extend(data[:count])
        self.write_sizes.append(count)
        return count


def test_relay_preserves_large_payload_with_partial_writes_and_bounded_reads() -> None:
    payload = bytes(range(256)) * 8193
    source = ChunkedSource(payload)
    target = PartialWriter(max_write=8191)

    tunnel.relay(source, target)

    assert target.payload == payload
    assert max(source.request_sizes) == tunnel.COPY_CHUNK_BYTES
    assert max(target.write_sizes) <= 8191
    assert len(target.write_sizes) > len(source.request_sizes)


def test_ssh_transport_advertises_one_result_sized_receive_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeSocket:
        def setsockopt(self, level: int, option: int, value: int) -> None:
            captured["socket_option"] = (level, option, value)

    class FakeTransport:
        def __init__(self, sock: object, **kwargs: object) -> None:
            captured["sock"] = sock
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "paramiko", SimpleNamespace(Transport=FakeTransport))
    sock = FakeSocket()

    created = tunnel._high_throughput_transport(  # noqa: SLF001
        sock,
        gss_kex=False,
        gss_deleg_creds=True,
        disabled_algorithms=None,
    )

    assert isinstance(created, FakeTransport)
    assert captured["sock"] is sock
    assert captured["default_window_size"] == 16 * 1024 * 1024
    assert captured["default_max_packet_size"] == 64 * 1024
    assert captured["socket_option"] == (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    assert tunnel.COPY_CHUNK_BYTES == 1024 * 1024


def test_low_latency_socket_configuration_tolerates_socket_wrappers() -> None:
    tunnel._configure_low_latency_socket(object())  # noqa: SLF001


def test_low_latency_socket_configuration_tolerates_unsupported_option() -> None:
    class UnsupportedSocket:
        def setsockopt(self, _level: int, _option: int, _value: int) -> None:
            raise OSError("unsupported")

    tunnel._configure_low_latency_socket(UnsupportedSocket())  # noqa: SLF001


def test_ssh_endpoint_is_restricted_to_expected_provider_domains() -> None:
    assert tunnel._validated_ssh_endpoint(  # noqa: SLF001
        {"host": "connect.westd.seetacloud.com", "port": "28473"}
    ) == ("connect.westd.seetacloud.com", 28473)


def test_ssh_endpoint_rejects_suffix_confusion() -> None:
    try:
        tunnel._validated_ssh_endpoint(  # noqa: SLF001
            {"host": "connect.westd.seetacloud.com.attacker.invalid", "port": 22}
        )
    except RuntimeError as exc:
        assert "untrusted" in str(exc)
    else:
        raise AssertionError("untrusted SSH endpoint was accepted")


def test_provider_request_uses_the_existing_hmac_contract(monkeypatch: object) -> None:
    monkeypatch.setattr(tunnel.time, "time", lambda: 1_700_000_000)  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        tunnel.secrets, "token_urlsafe", lambda _size: "nonce_1234567890abcd"
    )
    path = "/internal/v1/providers/autodl/instances/app/pro-a1/ssh-credentials"
    secret = "s" * 48

    headers = tunnel._signed_headers(path, secret)  # noqa: SLF001

    digest = hashlib.sha256(b"").hexdigest()
    message = "\n".join(("POST", path, "1700000000", "nonce_1234567890abcd", digest))
    assert (
        headers["X-GPU-Signature"]
        == hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    )


def test_provider_url_rejects_non_internal_origin() -> None:
    try:
        tunnel._provider_url("http://provider-controller.attacker:8020", "pro-a1")  # noqa: SLF001
    except RuntimeError as exc:
        assert "internal service endpoint" in str(exc)
    else:
        raise AssertionError("external Provider Controller origin was accepted")


class FakeRecoveryChannel:
    def __init__(self, status: int = 0) -> None:
        self.status = status
        self.command: str | None = None
        self.closed = False

    def settimeout(self, _timeout: float) -> None:
        return

    def exec_command(self, command: str) -> None:
        self.command = command

    def exit_status_ready(self) -> bool:
        return True

    def recv_exit_status(self) -> int:
        return self.status

    def close(self) -> None:
        self.closed = True


class FakeRecoveryTransport:
    def __init__(self, channel: FakeRecoveryChannel) -> None:
        self.channel = channel

    def is_active(self) -> bool:
        return True

    def open_session(self, *, timeout: int) -> FakeRecoveryChannel:
        assert timeout == 10
        return self.channel


class FakeRecoveryClient:
    def __init__(self, channel: FakeRecoveryChannel) -> None:
        self.transport = FakeRecoveryTransport(channel)

    def get_transport(self) -> FakeRecoveryTransport:
        return self.transport


class FakeDigestChannel:
    def __init__(self, payload: bytes, status: int = 0) -> None:
        self.payload = payload
        self.status = status
        self.command: str | None = None
        self.closed = False

    def settimeout(self, timeout: float) -> None:
        assert timeout == 10

    def exec_command(self, command: str) -> None:
        self.command = command

    def makefile(self, _mode: str) -> io.BytesIO:
        return io.BytesIO(self.payload)

    def makefile_stderr(self, _mode: str) -> io.BytesIO:
        return io.BytesIO(b"")

    def recv_exit_status(self) -> int:
        return self.status

    def close(self) -> None:
        self.closed = True


class FakeDigestTransport:
    def __init__(self, channel: FakeDigestChannel) -> None:
        self.channel = channel

    def open_session(self, *, timeout: int) -> FakeDigestChannel:
        assert timeout == 5
        return self.channel


class FakeCacheChannel(FakeDigestChannel):
    def settimeout(self, timeout: float) -> None:
        assert timeout == 30


def test_remote_input_digest_uses_fixed_root_and_returns_only_metadata() -> None:
    digest = "a" * 64
    channel = FakeDigestChannel(f"12345\n{digest} */root/ComfyUI/input/job-1/image.png\n".encode())

    path = tunnel._validated_comfy_input_path("image.png", "job-1")  # noqa: SLF001
    result = tunnel._remote_input_digest(  # type: ignore[arg-type]  # noqa: SLF001
        FakeDigestTransport(channel),
        path,
    )

    assert path == "/root/ComfyUI/input/job-1/image.png"
    assert result == (12345, digest)
    assert channel.command is not None
    assert "/usr/bin/stat" in channel.command
    assert "/usr/bin/sha256sum" in channel.command
    assert channel.closed is True


@pytest.mark.parametrize(
    ("filename", "subfolder"),
    [
        ("../image.png", "job-1"),
        ("image.png", "../job-1"),
        ("image.png", "/absolute"),
        ("image.png", "job-1//nested"),
    ],
)
def test_remote_input_digest_rejects_path_traversal(filename: str, subfolder: str) -> None:
    with pytest.raises(RuntimeError, match="invalid"):
        tunnel._validated_comfy_input_path(filename, subfolder)  # noqa: SLF001


def test_cache_request_is_tenant_scoped_and_materializes_atomically() -> None:
    namespace = "a" * 64
    digest = "b" * 64
    payload = {
        "namespace": namespace,
        "sha256": digest,
        "size_bytes": 12345,
        "filename": "image.png",
        "subfolder": "job-1",
    }
    validated = tunnel._validated_cache_request(payload)  # noqa: SLF001
    channel = FakeCacheChannel(b"")

    hit = tunnel._remote_input_cache(  # type: ignore[arg-type]  # noqa: SLF001
        FakeDigestTransport(channel),
        action="materialize",
        namespace=validated[0],
        digest=validated[1],
        size=validated[2],
        target_path=validated[3],
    )

    assert hit is True
    assert channel.command is not None
    assert f".gpu-control-cache-v1/{namespace}/{digest}" in channel.command
    assert "/root/ComfyUI/input/job-1/image.png" in channel.command
    assert "/bin/cp --reflink=auto" in channel.command
    assert "/bin/mv -f" in channel.command
    assert '"$destination" && trap - EXIT' in channel.command
    assert '"$destination"; trap - EXIT' not in channel.command
    assert channel.command.count("/usr/bin/sha256sum --check --status") == 1
    assert f"{digest}  /root/ComfyUI/input/job-1/.gpu-cache-" in channel.command
    assert f"{digest}  /root/ComfyUI/input/.gpu-control-cache-v1" not in channel.command
    assert "/usr/bin/touch" in channel.command
    assert channel.closed is True


def test_missing_or_corrupt_cache_is_a_safe_miss() -> None:
    channel = FakeCacheChannel(b"", status=1)

    hit = tunnel._remote_input_cache(  # type: ignore[arg-type]  # noqa: SLF001
        FakeDigestTransport(channel),
        action="materialize",
        namespace="a" * 64,
        digest="b" * 64,
        size=123,
        target_path="/root/ComfyUI/input/job-1/image.png",
    )

    assert hit is False


def test_cache_promotion_hashes_only_the_atomic_candidate_copy() -> None:
    channel = FakeCacheChannel(b"")

    promoted = tunnel._remote_input_cache(  # type: ignore[arg-type]  # noqa: SLF001
        FakeDigestTransport(channel),
        action="promote",
        namespace="a" * 64,
        digest="b" * 64,
        size=123,
        target_path="/root/ComfyUI/input/job-1/image.png",
    )

    assert promoted is True
    assert channel.command is not None
    assert channel.command.count("/usr/bin/sha256sum --check --status") == 1
    assert "/bin/cp --reflink=auto" in channel.command
    assert "/bin/mv -f" in channel.command
    assert '"$destination" && trap - EXIT' in channel.command


def test_cache_prune_is_bounded_to_tenant_root_with_ttl_count_and_bytes() -> None:
    channel = FakeCacheChannel(b"")

    tunnel._remote_cache_prune(  # type: ignore[arg-type]  # noqa: SLF001
        FakeDigestTransport(channel),
        "a" * 64,
    )

    assert channel.command is not None
    assert tunnel.REMOTE_COMFY_CACHE_ROOT in channel.command
    assert str(tunnel.CACHE_TTL_SECONDS) in channel.command
    assert str(tunnel.CACHE_MAX_OBJECTS_PER_TENANT) in channel.command
    assert str(tunnel.CACHE_MAX_BYTES_PER_TENANT) in channel.command
    assert str(tunnel.CACHE_MAX_OBJECTS_TOTAL) in channel.command
    assert str(tunnel.CACHE_MAX_BYTES_TOTAL) in channel.command
    assert "os.path.dirname(root) != parent" in channel.command
    assert "os.unlink" in channel.command
    assert channel.closed is True


def test_cache_prune_scheduler_is_single_flight_and_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocations: list[str] = []
    monotonic = iter((1_000.0, 1_001.0, 1_600.0))

    class ImmediateThread:
        def __init__(self, *, target: object, **_kwargs: object) -> None:
            self.target = target

        def start(self) -> None:
            assert callable(self.target)
            self.target()

    monkeypatch.setattr(tunnel, "_CACHE_PRUNE_RUNNING", set())
    monkeypatch.setattr(tunnel, "_CACHE_PRUNE_PENDING", set())
    monkeypatch.setattr(tunnel, "_CACHE_PRUNE_LAST_STARTED_AT", None)
    monkeypatch.setattr(tunnel.time, "monotonic", lambda: next(monotonic))
    monkeypatch.setattr(tunnel.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(
        tunnel,
        "_remote_cache_prune",
        lambda _transport, namespace: invocations.append(namespace),
    )

    transport = object()
    tunnel._schedule_remote_cache_prune(transport, "a" * 64)  # type: ignore[arg-type]  # noqa: SLF001
    tunnel._schedule_remote_cache_prune(transport, "b" * 64)  # type: ignore[arg-type]  # noqa: SLF001
    tunnel._schedule_remote_cache_prune(transport, "c" * 64)  # type: ignore[arg-type]  # noqa: SLF001

    assert invocations == ["a" * 64, "c" * 64]
    assert tunnel._CACHE_PRUNE_PENDING == set()  # noqa: SLF001


@pytest.mark.parametrize(
    "payload",
    [
        {
            "namespace": "../tenant",
            "sha256": "b" * 64,
            "size_bytes": 1,
            "filename": "image.png",
            "subfolder": "job-1",
        },
        {
            "namespace": "a" * 64,
            "sha256": "b" * 64,
            "size_bytes": 1,
            "filename": "image.png",
            "subfolder": "../job-1",
        },
        {
            "namespace": "a" * 64,
            "sha256": "b" * 64,
            "size_bytes": 0,
            "filename": "image.png",
            "subfolder": "job-1",
        },
    ],
)
def test_cache_request_rejects_cross_namespace_and_path_traversal(payload: object) -> None:
    with pytest.raises(RuntimeError, match="invalid"):
        tunnel._validated_cache_request(payload)  # noqa: SLF001


def test_recovery_profile_executes_only_the_fixed_allowlisted_command() -> None:
    channel = FakeRecoveryChannel()
    client = FakeRecoveryClient(channel)

    tunnel._execute_recovery_profile(  # type: ignore[arg-type]  # noqa: SLF001
        client, "comfyui-6006-v1"
    )

    assert channel.command == tunnel.VERIFIED_REMOTE_START_COMMAND
    assert tunnel.REMOTE_START_SCRIPT_SHA256 in channel.command
    assert "/usr/bin/sha256sum --check --status" in channel.command
    assert channel.closed is True


def test_recovery_profile_rejects_arbitrary_command_before_ssh_use() -> None:
    with pytest.raises(RuntimeError, match="not allowlisted"):
        tunnel._execute_recovery_profile(  # type: ignore[arg-type]  # noqa: SLF001
            object(), "comfyui-6006-v1; curl attacker.invalid"
        )


def test_remote_recovery_runs_once_then_waits_for_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readiness = iter((False, False, True))
    invocations: list[str] = []
    client = FakeRecoveryClient(FakeRecoveryChannel())
    monkeypatch.setattr(
        tunnel,
        "_remote_ready",
        lambda _client, _remote: next(readiness),
    )
    monkeypatch.setattr(
        tunnel,
        "_execute_recovery_profile",
        lambda _client, profile: invocations.append(profile),
    )

    ready = tunnel._prepare_remote(  # type: ignore[arg-type]  # noqa: SLF001
        client,
        ("127.0.0.1", 6006),
        "comfyui-6006-v1",
        threading.Event(),
        poll_seconds=0,
    )

    assert ready is True
    assert invocations == ["comfyui-6006-v1"]


def test_remote_recovery_does_not_restart_an_already_ready_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoked = False
    client = FakeRecoveryClient(FakeRecoveryChannel())
    monkeypatch.setattr(tunnel, "_remote_ready", lambda _client, _remote: True)

    def unexpected_recovery(_client: object, _profile: str) -> None:
        nonlocal invoked
        invoked = True

    monkeypatch.setattr(tunnel, "_execute_recovery_profile", unexpected_recovery)

    assert tunnel._prepare_remote(  # type: ignore[arg-type]  # noqa: SLF001
        client,
        ("127.0.0.1", 6006),
        "comfyui-6006-v1",
        threading.Event(),
        poll_seconds=0,
    )
    assert invoked is False


def test_recovery_command_timeout_uses_ready_fast_path(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeRecoveryClient(FakeRecoveryChannel())
    readiness = iter((False, True))
    monkeypatch.setattr(
        tunnel,
        "_remote_ready",
        lambda _client, _remote: next(readiness),
    )

    def timed_out(_client: object, _profile: str) -> None:
        raise TimeoutError("bounded recovery timeout")

    monkeypatch.setattr(tunnel, "_execute_recovery_profile", timed_out)

    assert tunnel._prepare_remote(  # type: ignore[arg-type]  # noqa: SLF001
        client,
        ("127.0.0.1", 6006),
        "comfyui-6006-v1",
        threading.Event(),
        poll_seconds=0,
    )
    event = json.loads(capsys.readouterr().out)
    assert event == {
        "event": "remote_recovery_command_timeout",
        "profile": "comfyui-6006-v1",
        "remote_port": 6006,
        "timeout_seconds": tunnel.RECOVERY_COMMAND_TIMEOUT_SECONDS,
        "remote_ready": True,
        "action": "continue_ready",
    }


def test_recovery_command_timeout_logs_and_reconnects_when_not_ready(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeRecoveryClient(FakeRecoveryChannel())
    monkeypatch.setattr(tunnel, "_remote_ready", lambda _client, _remote: False)

    def timed_out(_client: object, _profile: str) -> None:
        raise TimeoutError("bounded recovery timeout")

    monkeypatch.setattr(tunnel, "_execute_recovery_profile", timed_out)

    with pytest.raises(TimeoutError, match="bounded recovery timeout"):
        tunnel._prepare_remote(  # type: ignore[arg-type]  # noqa: SLF001
            client,
            ("127.0.0.1", 6006),
            "comfyui-6006-v1",
            threading.Event(),
            poll_seconds=0,
        )
    event = json.loads(capsys.readouterr().out)
    assert event["event"] == "remote_recovery_command_timeout"
    assert event["timeout_seconds"] == tunnel.RECOVERY_COMMAND_TIMEOUT_SECONDS
    assert event["remote_ready"] is False
    assert event["action"] == "reconnect"


def test_recovery_command_timeout_honors_shutdown_without_readiness_probe(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeRecoveryClient(FakeRecoveryChannel())
    readiness_probe_count = 0

    def unexpected_probe(_client: object, _remote: tuple[str, int]) -> bool:
        nonlocal readiness_probe_count
        readiness_probe_count += 1
        return False

    monkeypatch.setattr(tunnel, "_remote_ready", unexpected_probe)

    def timed_out(_client: object, _profile: str) -> None:
        raise TimeoutError("bounded recovery timeout")

    monkeypatch.setattr(tunnel, "_execute_recovery_profile", timed_out)
    stop = threading.Event()
    stop.set()

    assert not tunnel._prepare_remote(  # type: ignore[arg-type]  # noqa: SLF001
        client,
        ("127.0.0.1", 6006),
        "comfyui-6006-v1",
        stop,
        poll_seconds=0,
    )
    event = json.loads(capsys.readouterr().out)
    assert readiness_probe_count == 1
    assert event["remote_ready"] is False
    assert event["action"] == "stop"


class FakeHttpChannel:
    def __init__(self, response: bytes) -> None:
        self.response = bytearray(response)
        self.request = bytearray()
        self.closed = False

    def settimeout(self, timeout: int) -> None:
        assert timeout == 3

    def send(self, data: memoryview) -> int:
        self.request.extend(data)
        return len(data)

    def recv(self, size: int) -> bytes:
        chunk = bytes(self.response[:size])
        del self.response[:size]
        return chunk

    def close(self) -> None:
        self.closed = True


class FakeHttpTransport:
    def __init__(self, channel: FakeHttpChannel) -> None:
        self.channel = channel

    def open_channel(
        self,
        kind: str,
        remote: tuple[str, int],
        source: tuple[str, int],
        *,
        timeout: int,
    ) -> FakeHttpChannel:
        assert kind == "direct-tcpip"
        assert remote == ("127.0.0.1", 6006)
        assert source == ("127.0.0.1", 0)
        assert timeout == 3
        return self.channel


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}", True),
        (b"HTTP/1.1 503 Service Unavailable\r\n\r\n", False),
        (b"not http", False),
        (b"", False),
    ],
)
def test_remote_ready_requires_system_stats_http_200(
    response: bytes,
    expected: bool,
) -> None:
    channel = FakeHttpChannel(response)
    transport = FakeHttpTransport(channel)

    assert (
        tunnel._remote_system_stats_ready(  # type: ignore[arg-type]  # noqa: SLF001
            transport, ("127.0.0.1", 6006)
        )
        is expected
    )
    assert channel.request == (
        b"GET /system_stats HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
    )
    assert channel.closed is True


def test_remote_recovery_timeout_is_bounded_and_allows_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeRecoveryClient(FakeRecoveryChannel())
    invocations: list[str] = []
    monotonic = iter((100.0, 111.0))
    monkeypatch.setattr(tunnel, "_remote_ready", lambda _client, _remote: False)
    monkeypatch.setattr(
        tunnel,
        "_execute_recovery_profile",
        lambda _client, profile: invocations.append(profile),
    )
    monkeypatch.setattr(tunnel.time, "monotonic", lambda: next(monotonic))

    assert (
        tunnel._prepare_remote(  # type: ignore[arg-type]  # noqa: SLF001
            client,
            ("127.0.0.1", 6006),
            "comfyui-6006-v1",
            threading.Event(),
            poll_seconds=0,
            ready_timeout_seconds=10,
        )
        is False
    )
    assert invocations == ["comfyui-6006-v1"]


def test_remote_start_script_matches_pinned_hash_and_hardening_contract() -> None:
    script_path = Path("scripts/autodl_remote_start_comfyui.sh")
    payload = script_path.read_bytes()
    script = payload.decode("utf-8")

    assert hashlib.sha256(payload).hexdigest() == tunnel.REMOTE_START_SCRIPT_SHA256
    assert "/usr/bin/flock --exclusive --wait 30" in script
    assert "is_exact_comfy_process" in script
    assert "STARTING_GRACE_SECONDS=180" in script
    assert "STARTUP_CRASH_WINDOW_SECONDS=10" in script
    assert "2>&1 < /dev/null 9>&- &" in script
    assert "pkill" not in script
    assert "killall" not in script


class MemorySftpWriter(io.BytesIO):
    def __init__(self, sftp: MemorySftp, path: str) -> None:
        super().__init__()
        self.sftp = sftp
        self.path = path

    def close(self) -> None:
        if not self.closed:
            self.sftp.files[self.path] = self.getvalue()
            self.sftp.modes[self.path] = 0o600
        super().close()


class MemorySftp:
    def __init__(self, target: bytes | None = None) -> None:
        self.directories = {"/root", "/root/autodl-tmp", "/root/autodl-tmp/gpu-control"}
        self.files: dict[str, bytes] = {}
        self.modes: dict[str, int] = {}
        self.renames: list[tuple[str, str]] = []
        self.closed = False
        self.timeout: int | None = None
        if target is not None:
            self.files[tunnel.REMOTE_START_SCRIPT_PATH] = target
            self.modes[tunnel.REMOTE_START_SCRIPT_PATH] = 0o700

    def lstat(self, path: str) -> SimpleNamespace:
        if path in self.directories:
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o700)
        if path in self.files:
            return SimpleNamespace(st_mode=stat.S_IFREG | self.modes[path])
        raise OSError("missing")

    def get_channel(self) -> MemorySftp:
        return self

    def settimeout(self, timeout: int) -> None:
        self.timeout = timeout

    def mkdir(self, path: str, *, mode: int) -> None:
        assert mode == 0o700
        self.directories.add(path)

    def file(self, path: str, mode: str) -> io.BytesIO:
        if mode == "rb":
            return io.BytesIO(self.files[path])
        assert mode == "wb"
        return MemorySftpWriter(self, path)

    def chmod(self, path: str, mode: int) -> None:
        assert path in self.files
        self.modes[path] = mode

    def remove(self, path: str) -> None:
        del self.files[path]
        self.modes.pop(path, None)

    def posix_rename(self, source: str, target: str) -> None:
        self.renames.append((source, target))
        self.files[target] = self.files.pop(source)
        self.modes[target] = self.modes.pop(source)

    def close(self) -> None:
        self.closed = True


class MemorySftpClient:
    def __init__(self, sftp: MemorySftp) -> None:
        self.sftp = sftp

    def open_sftp(self) -> MemorySftp:
        return self.sftp


def test_remote_start_script_sync_is_checksum_verified_and_atomic() -> None:
    local_path = Path("scripts/autodl_remote_start_comfyui.sh")
    payload = local_path.read_bytes()
    sftp = MemorySftp(target=b"old script")

    tunnel._sync_remote_start_script(  # type: ignore[arg-type]  # noqa: SLF001
        MemorySftpClient(sftp), local_path
    )

    assert sftp.files[tunnel.REMOTE_START_SCRIPT_PATH] == payload
    assert tunnel.REMOTE_START_SCRIPT_TEMP_PATH not in sftp.files
    assert sftp.modes[tunnel.REMOTE_START_SCRIPT_PATH] == 0o700
    assert sftp.timeout == 30
    assert sftp.renames == [(tunnel.REMOTE_START_SCRIPT_TEMP_PATH, tunnel.REMOTE_START_SCRIPT_PATH)]
    assert sftp.closed is True


def test_matching_remote_start_script_is_verified_without_rewrite() -> None:
    local_path = Path("scripts/autodl_remote_start_comfyui.sh")
    sftp = MemorySftp(target=local_path.read_bytes())

    tunnel._sync_remote_start_script(  # type: ignore[arg-type]  # noqa: SLF001
        MemorySftpClient(sftp), local_path
    )

    assert sftp.renames == []
    assert sftp.modes[tunnel.REMOTE_START_SCRIPT_PATH] == 0o700
    assert sftp.timeout == 30
    assert sftp.closed is True
