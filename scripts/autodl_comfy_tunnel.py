#!/usr/bin/env python3
"""Expose one AutoDL ComfyUI loopback port to a private Docker network.

The process keeps one verified SSH transport warm and opens a direct-tcpip
channel per local connection. Provider credentials stay in memory and are
never written to logs.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import posixpath
import re
import secrets
import shlex
import signal
import socket
import socketserver
import stat
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING, Any, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

if TYPE_CHECKING:
    import paramiko

# ComfyUI results are usually PNG payloads between 2 MiB and 12 MiB.  Paramiko's
# 2 MiB receive window forces flow-control acknowledgements in the middle of a
# single result on a WAN link.  Keep enough credit for a normal result while
# retaining bounded memory/backpressure for concurrent channels.
SSH_CHANNEL_WINDOW_BYTES = 16 * 1024 * 1024
SSH_CHANNEL_MAX_PACKET_BYTES = 64 * 1024
COPY_CHUNK_BYTES = 1024 * 1024
MAX_CREDENTIAL_RESPONSE_BYTES = 1024 * 1024
MAX_HTTP_STATUS_BYTES = 4096
MAX_DIGEST_RESPONSE_BYTES = 4096
MAX_CACHE_REQUEST_BYTES = 4096
MAX_CACHED_INPUT_BYTES = 2_147_483_648
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
CACHE_MAX_OBJECTS_PER_TENANT = 2048
CACHE_MAX_BYTES_PER_TENANT = 8 * 1024 * 1024 * 1024
CACHE_MAX_OBJECTS_TOTAL = 8192
CACHE_MAX_BYTES_TOTAL = 32 * 1024 * 1024 * 1024
CACHE_PRUNE_MIN_INTERVAL_SECONDS = 10 * 60
INSTANCE_ID_PATTERN = re.compile(r"^pro-[A-Za-z0-9]+$")
COMFY_INPUT_COMPONENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SSH_PROXY_SUFFIXES = (".autodl.com", ".autodl.art", ".seetacloud.com")
REMOTE_COMFY_INPUT_ROOT = "/root/ComfyUI/input"
REMOTE_COMFY_CACHE_ROOT = f"{REMOTE_COMFY_INPUT_ROOT}/.gpu-control-cache-v1"
REMOTE_CACHE_PRUNE_PYTHON = "/root/miniconda3/bin/python"
REMOTE_START_SCRIPT_PATH = "/root/autodl-tmp/gpu-control/start-comfyui.sh"
REMOTE_START_SCRIPT_SHA256 = "2ce355754406f7cccf271668a11a907821620845774bf97a651f37d876f66d72"
REMOTE_START_SCRIPT_TEMP_PATH = (
    f"/root/autodl-tmp/gpu-control/.start-comfyui.{REMOTE_START_SCRIPT_SHA256}.tmp"
)
LOCAL_START_SCRIPT_PATH = Path("/usr/local/share/autodl_remote_start_comfyui.sh")
MAX_START_SCRIPT_BYTES = 64 * 1024
VERIFIED_REMOTE_START_COMMAND = (
    f"echo '{REMOTE_START_SCRIPT_SHA256}  {REMOTE_START_SCRIPT_PATH}' | "
    "/usr/bin/sha256sum --check --status - && "
    f"/bin/bash {REMOTE_START_SCRIPT_PATH}"
)
RECOVERY_COMMANDS = {
    "comfyui-6006-v1": VERIFIED_REMOTE_START_COMMAND,
}
RECOVERY_COMMAND_TIMEOUT_SECONDS = 70.0
_CACHE_PRUNE_LOCK = threading.Lock()
_CACHE_PRUNE_RUNNING: set[str] = set()
_CACHE_PRUNE_PENDING: set[str] = set()
_CACHE_PRUNE_LAST_STARTED_AT: float | None = None


class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        _req: Request,
        _fp: Any,
        _code: int,
        _msg: str,
        _headers: Any,
        _newurl: str,
    ) -> None:
        return None


class Readable(Protocol):
    def recv(self, size: int) -> bytes: ...


class Writable(Protocol):
    def send(self, data: memoryview) -> int: ...


def _load_hmac_secret(secret_file: Path) -> str:
    secret = secret_file.read_text(encoding="utf-8").strip()
    if not 32 <= len(secret) <= 4096 or any(character.isspace() for character in secret):
        raise RuntimeError("Provider Controller HMAC secret file is invalid")
    return secret


def _validated_instance_id(instance_id: str) -> str:
    if not INSTANCE_ID_PATTERN.fullmatch(instance_id):
        raise RuntimeError("AutoDL instance ID is invalid")
    return instance_id


def _validated_ssh_endpoint(info: dict[str, Any]) -> tuple[str, int]:
    host = str(info.get("host") or "").strip().lower().rstrip(".")
    if not host or not host.endswith(SSH_PROXY_SUFFIXES):
        raise RuntimeError("AutoDL returned an untrusted SSH proxy host")
    try:
        port = int(info["port"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("AutoDL returned an invalid SSH proxy port") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("AutoDL returned an invalid SSH proxy port")
    return host, port


def _provider_url(base_url: str, instance_id: str) -> tuple[str, str]:
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "provider-controller"
        or parsed.port != 8020
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("Provider Controller URL must be the internal service endpoint")
    path = (
        "/internal/v1/providers/autodl/instances/app/"
        f"{_validated_instance_id(instance_id)}/ssh-credentials"
    )
    return f"http://provider-controller:8020{path}", path


def _signed_headers(path: str, secret: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    nonce = secrets.token_urlsafe(24)
    body_digest = hashlib.sha256(b"").hexdigest()
    message = "\n".join(("POST", path, timestamp, nonce, body_digest))
    signature = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-GPU-Timestamp": timestamp,
        "X-GPU-Nonce": nonce,
        "X-GPU-Signature": signature,
    }


def credentials(base_url: str, secret_file: Path, instance_id: str) -> dict[str, Any]:
    url, path = _provider_url(base_url, instance_id)
    secret = _load_hmac_secret(secret_file)
    request = Request(  # noqa: S310 - the origin is a fixed internal service.
        url,
        data=b"",
        headers=_signed_headers(path, secret),
        method="POST",
    )
    try:
        with build_opener(NoRedirectHandler).open(request, timeout=30) as response:
            encoded = response.read(MAX_CREDENTIAL_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError("Provider Controller credential request failed") from exc
    if len(encoded) > MAX_CREDENTIAL_RESPONSE_BYTES:
        raise RuntimeError("Provider Controller credential response is too large")
    try:
        payload = json.loads(encoded)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError("Provider Controller credential response is invalid") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Provider Controller credential response is invalid")
    return cast(dict[str, Any], payload)


class TunnelState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._client: paramiko.SSHClient | None = None

    def replace(self, client: paramiko.SSHClient | None) -> None:
        with self._lock:
            previous = self._client
            self._client = client
        if previous is not None and previous is not client:
            previous.close()

    def transport(self) -> paramiko.Transport | None:
        with self._lock:
            if self._client is None:
                return None
            transport = self._client.get_transport()
            return transport if transport is not None and transport.is_active() else None


class ForwardServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        state: TunnelState,
        remote: tuple[str, int],
    ) -> None:
        self.tunnel_state = state
        self.remote = remote
        super().__init__(address, ForwardHandler)


def _send_all(target: Writable, data: bytes) -> None:
    """Write all data using bounded memory and blocking backpressure."""
    remaining = memoryview(data)
    try:
        while remaining:
            sent = target.send(remaining)
            if sent <= 0:
                raise ConnectionError("tunnel peer closed during write")
            remaining = remaining[sent:]
    finally:
        remaining.release()


def relay(source: Readable, target: Writable) -> None:
    """Copy one direction; blocking sends propagate receiver backpressure."""
    while True:
        data = source.recv(COPY_CHUNK_BYTES)
        if not data:
            return
        _send_all(target, data)


def _configure_low_latency_socket(sock: Any) -> None:
    """Disable Nagle where the object is a real TCP socket.

    The tunnel carries both multi-megabyte artifacts and tiny WebSocket
    terminal/control frames over the same long-lived SSH connection.  Nagle
    can hold the latter behind a delayed ACK after GPU execution has already
    completed, adding pure control-plane tail latency.  Socket-like test and
    proxy objects without ``setsockopt`` remain supported.
    """
    setter = getattr(sock, "setsockopt", None)
    if setter is None:
        return
    try:
        setter(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        # Some Paramiko-compatible socket wrappers don't expose TCP options.
        # Low-latency mode is an optimization; connection establishment must
        # remain available through those wrappers.
        return


def _high_throughput_transport(sock: Any, **kwargs: Any) -> Any:
    """Create Paramiko transport tuned for binary ComfyUI payloads over WAN."""
    import paramiko

    _configure_low_latency_socket(sock)
    return paramiko.Transport(
        sock,
        default_window_size=SSH_CHANNEL_WINDOW_BYTES,
        default_max_packet_size=SSH_CHANNEL_MAX_PACKET_BYTES,
        **kwargs,
    )


class ForwardHandler(socketserver.BaseRequestHandler):
    server: ForwardServer

    def handle(self) -> None:
        _configure_low_latency_socket(self.request)
        transport = self.server.tunnel_state.transport()
        if transport is None:
            return
        try:
            channel = transport.open_channel(
                "direct-tcpip",
                self.server.remote,
                cast(tuple[str, int], self.client_address),
                timeout=10,
            )
        except Exception:
            return
        if channel is None:
            return

        closed = threading.Event()

        def close_connection() -> None:
            if closed.is_set():
                return
            closed.set()
            channel.close()
            try:
                self.request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        def upload() -> None:
            try:
                relay(self.request, channel)
            except (ConnectionError, EOFError, OSError):
                pass
            finally:
                close_connection()

        upload_thread = threading.Thread(target=upload, daemon=True)
        upload_thread.start()
        try:
            relay(channel, self.request)
        except (ConnectionError, EOFError, OSError):
            pass
        finally:
            close_connection()
            upload_thread.join(timeout=2)


class HealthServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: TunnelState, remote: tuple[str, int]):
        self.tunnel_state = state
        self.remote = remote
        super().__init__(address, HealthHandler)


class HealthHandler(BaseHTTPRequestHandler):
    server: HealthServer

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API.
        request_url = urlsplit(self.path)
        if request_url.path == "/health/live":
            self._reply(HTTPStatus.OK, "live")
            return
        if request_url.path == "/health/ready" and self._remote_ready():
            self._reply(HTTPStatus.OK, "ready")
            return
        if request_url.path == "/health/ready":
            self._reply(HTTPStatus.SERVICE_UNAVAILABLE, "not ready")
            return
        if request_url.path == "/internal/v1/comfy-input-digest":
            self._input_digest(request_url.query)
            return
        self._reply(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API.
        request_url = urlsplit(self.path)
        actions = {
            "/internal/v1/comfy-input-cache/materialize": "materialize",
            "/internal/v1/comfy-input-cache/promote": "promote",
        }
        action = actions.get(request_url.path)
        if action is None or request_url.query:
            self._reply(HTTPStatus.NOT_FOUND, "not found")
            return
        self._input_cache(action)

    def _input_cache(self, action: str) -> None:
        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            content_length = -1
        if not 1 <= content_length <= MAX_CACHE_REQUEST_BYTES:
            self._reply_json(HTTPStatus.BAD_REQUEST, {"error": "invalid request body"})
            return
        try:
            payload = json.loads(self.rfile.read(content_length))
            namespace, digest, size, target_path = _validated_cache_request(payload)
        except (json.JSONDecodeError, UnicodeDecodeError, RuntimeError, TypeError, ValueError):
            self._reply_json(HTTPStatus.BAD_REQUEST, {"error": "invalid cache request"})
            return
        transport = self.server.tunnel_state.transport()
        if transport is None:
            self._reply_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "tunnel unavailable"})
            return
        try:
            cached = _remote_input_cache(
                transport,
                action=action,
                namespace=namespace,
                digest=digest,
                size=size,
                target_path=target_path,
            )
        except (OSError, RuntimeError, TimeoutError):
            self._reply_json(HTTPStatus.BAD_GATEWAY, {"error": "remote cache failed"})
            return
        if action == "materialize" and not cached:
            self._reply_json(HTTPStatus.NOT_FOUND, {"status": "miss"})
            return
        _schedule_remote_cache_prune(transport, namespace)
        response: dict[str, Any] = {
            "status": "hit" if action == "materialize" else "promoted"
        }
        if action == "materialize":
            # The remote command hashes the same-directory temporary copy
            # immediately before its atomic rename into the job path. Return
            # that proof so the Scheduler does not re-read the target through
            # a second SSH control request.
            response.update(
                {
                    "verified": True,
                    "size_bytes": size,
                    "sha256": digest,
                    "verification_method": "atomic_target_sha256",
                    "receipt_version": 2,
                }
            )
        else:
            # Promotion hashes the same-directory candidate immediately before
            # publishing it as the immutable cache object. This receipt lets a
            # cache miss use promotion as its upload integrity fence instead of
            # running a redundant digest command first.
            response.update(
                {
                    "verified": True,
                    "size_bytes": size,
                    "sha256": digest,
                    "verification_method": "atomic_cache_sha256",
                    "receipt_version": 2,
                }
            )
        self._reply_json(HTTPStatus.OK, response)

    def _input_digest(self, query: str) -> None:
        try:
            parameters = parse_qs(query, keep_blank_values=True, strict_parsing=True)
        except ValueError:
            self._reply_json(HTTPStatus.BAD_REQUEST, {"error": "invalid parameters"})
            return
        filenames = parameters.get("filename", [])
        subfolders = parameters.get("subfolder", [])
        if len(filenames) != 1 or len(subfolders) != 1:
            self._reply_json(HTTPStatus.BAD_REQUEST, {"error": "invalid parameters"})
            return
        try:
            relative_path = _validated_comfy_input_path(filenames[0], subfolders[0])
        except RuntimeError:
            self._reply_json(HTTPStatus.BAD_REQUEST, {"error": "invalid input path"})
            return
        transport = self.server.tunnel_state.transport()
        if transport is None:
            self._reply_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "tunnel unavailable"})
            return
        try:
            size, digest = _remote_input_digest(transport, relative_path)
        except (OSError, RuntimeError, TimeoutError):
            self._reply_json(HTTPStatus.BAD_GATEWAY, {"error": "remote digest failed"})
            return
        self._reply_json(
            HTTPStatus.OK,
            {"size_bytes": size, "sha256": digest, "method": "remote_sha256"},
        )

    def _remote_ready(self) -> bool:
        transport = self.server.tunnel_state.transport()
        if transport is None:
            return False
        return _remote_system_stats_ready(transport, self.server.remote)

    def _reply(self, status: HTTPStatus, text: str) -> None:
        body = text.encode("ascii")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=us-ascii")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _reply_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("ascii")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def _validated_comfy_input_path(filename: str, subfolder: str) -> str:
    """Resolve only job-scoped Comfy input paths; never accept traversal."""
    if not COMFY_INPUT_COMPONENT_PATTERN.fullmatch(filename):
        raise RuntimeError("Comfy input filename is invalid")
    components = [] if not subfolder else subfolder.split("/")
    if len(components) > 8 or any(
        not COMFY_INPUT_COMPONENT_PATTERN.fullmatch(component) for component in components
    ):
        raise RuntimeError("Comfy input subfolder is invalid")
    suffix = "/".join((*components, filename))
    return f"{REMOTE_COMFY_INPUT_ROOT}/{suffix}"


def _validated_cache_request(payload: object) -> tuple[str, str, int, str]:
    """Validate a tenant-scoped cache request before any SSH operation."""
    if not isinstance(payload, dict) or set(payload) != {
        "namespace",
        "sha256",
        "size_bytes",
        "filename",
        "subfolder",
    }:
        raise RuntimeError("cache request fields are invalid")
    namespace = payload.get("namespace")
    digest = payload.get("sha256")
    size = payload.get("size_bytes")
    filename = payload.get("filename")
    subfolder = payload.get("subfolder")
    if not isinstance(namespace, str) or not SHA256_PATTERN.fullmatch(namespace):
        raise RuntimeError("cache namespace is invalid")
    if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
        raise RuntimeError("cache digest is invalid")
    if type(size) is not int or not 1 <= size <= MAX_CACHED_INPUT_BYTES:
        raise RuntimeError("cache size is invalid")
    if not isinstance(filename, str) or not isinstance(subfolder, str):
        raise RuntimeError("cache target is invalid")
    target_path = _validated_comfy_input_path(filename, subfolder)
    return namespace, digest, size, target_path


def _remote_input_cache(
    transport: paramiko.Transport,
    *,
    action: str,
    namespace: str,
    digest: str,
    size: int,
    target_path: str,
) -> bool:
    """Materialize or promote one verified cache object entirely on the GPU host.

    Reflink/copy is deliberately preferred over a hardlink: a later Comfy
    overwrite of a job-scoped target must never mutate the immutable cache
    inode. Both directions use a same-directory temporary file and atomic
    rename, and every source plus temporary copy is checked before publication.
    """
    if action not in {"materialize", "promote"}:
        raise RuntimeError("cache action is invalid")
    if not SHA256_PATTERN.fullmatch(namespace) or not SHA256_PATTERN.fullmatch(digest):
        raise RuntimeError("cache identity is invalid")
    if not 1 <= size <= MAX_CACHED_INPUT_BYTES:
        raise RuntimeError("cache size is invalid")
    input_prefix = f"{REMOTE_COMFY_INPUT_ROOT}/"
    if not target_path.startswith(input_prefix):
        raise RuntimeError("cache target is invalid")
    relative_target = target_path.removeprefix(input_prefix)
    target_components = relative_target.split("/")
    expected_target = _validated_comfy_input_path(
        target_components[-1],
        "/".join(target_components[:-1]),
    )
    if target_path != expected_target:
        raise RuntimeError("cache target is invalid")
    cache_directory = f"{REMOTE_COMFY_CACHE_ROOT}/{namespace}"
    cache_path = f"{cache_directory}/{digest}"
    target_directory = posixpath.dirname(target_path)
    temporary_token = hashlib.sha256(target_path.encode("utf-8")).hexdigest()[:16]
    if action == "materialize":
        source_path = cache_path
        destination_path = target_path
        temporary_path = f"{target_directory}/.gpu-cache-{temporary_token}.tmp"
        destination_directory = target_directory
    else:
        source_path = target_path
        destination_path = cache_path
        temporary_path = f"{cache_directory}/.{digest}.{temporary_token}.tmp"
        destination_directory = cache_directory

    source = shlex.quote(source_path)
    destination = shlex.quote(destination_path)
    temporary = shlex.quote(temporary_path)
    destination_dir = shlex.quote(destination_directory)
    common_prefix = (
        "set -eu; umask 077; "
        f"source={source}; destination={destination}; temporary={temporary}; "
        "cleanup() { /bin/rm -f -- \"$temporary\"; }; trap cleanup EXIT; "
        f"/bin/mkdir -p -- {destination_dir}; "
        "/bin/rm -f -- \"$temporary\" && "
    )
    if action == "materialize":
        expected_temporary = shlex.quote(f"{digest}  {temporary_path}")
        integrity_step = (
            "[ \"$(/usr/bin/stat --printf='%s' -- \"$source\")\" = "
            f"{shlex.quote(str(size))} ] && "
            "/bin/cp --reflink=auto -- \"$source\" \"$temporary\" && "
            "[ \"$(/usr/bin/stat --printf='%s' -- \"$temporary\")\" = "
            f"{shlex.quote(str(size))} ] && "
            f"echo {expected_temporary} | /usr/bin/sha256sum --check --status - && "
            "/usr/bin/touch -- \"$source\" && "
        )
    else:
        expected_temporary = shlex.quote(f"{digest}  {temporary_path}")
        integrity_step = (
            "/bin/cp --reflink=auto -- \"$source\" \"$temporary\" && "
            "[ \"$(/usr/bin/stat --printf='%s' -- \"$temporary\")\" = "
            f"{shlex.quote(str(size))} ] && "
            f"echo {expected_temporary} | /usr/bin/sha256sum --check --status - && "
        )
    # Keep trap removal in the same AND-list as the copy, digest and atomic
    # rename. A semicolon here lets the successful ``trap - EXIT`` mask a
    # missing/corrupt source as exit status zero, yielding a false cache hit.
    command = (
        common_prefix
        + integrity_step
        + "/bin/mv -f -- \"$temporary\" \"$destination\" && trap - EXIT"
    )
    channel = transport.open_session(timeout=5)
    try:
        channel.settimeout(30)
        channel.exec_command(command)
        _read_bounded(channel.makefile("rb"), MAX_DIGEST_RESPONSE_BYTES)
        stderr = _read_bounded(channel.makefile_stderr("rb"), MAX_DIGEST_RESPONSE_BYTES)
        status = channel.recv_exit_status()
        if status == 0:
            return True
        if action == "materialize":
            return False
        raise RuntimeError(
            f"remote cache promotion failed with status {status}: "
            f"{stderr.decode('utf-8', errors='replace')[:256]}"
        )
    finally:
        channel.close()


def _remote_cache_prune(transport: paramiko.Transport, namespace: str) -> None:
    """Apply TTL plus per-tenant and global limits under the fixed cache root."""
    if not SHA256_PATTERN.fullmatch(namespace):
        raise RuntimeError("cache namespace is invalid")
    cache_directory = f"{REMOTE_COMFY_CACHE_ROOT}/{namespace}"
    script = """import os
import re
import sys
import time

root = os.path.realpath(sys.argv[1])
parent = os.path.realpath(sys.argv[2])
ttl = int(sys.argv[3])
max_objects = int(sys.argv[4])
max_bytes = int(sys.argv[5])
max_objects_total = int(sys.argv[6])
max_bytes_total = int(sys.argv[7])
if os.path.dirname(root) != parent or not re.fullmatch(r"[0-9a-f]{64}", os.path.basename(root)):
    raise SystemExit(2)
if not os.path.isdir(parent):
    raise SystemExit(0)
now = time.time()
all_objects = []
for tenant in os.scandir(parent):
    if not tenant.is_dir(follow_symlinks=False) or not re.fullmatch(r"[0-9a-f]{64}", tenant.name):
        continue
    objects = []
    for entry in os.scandir(tenant.path):
        try:
            info = entry.stat(follow_symlinks=False)
        except FileNotFoundError:
            continue
        if entry.is_file(follow_symlinks=False) and re.fullmatch(r"[0-9a-f]{64}", entry.name):
            if now - info.st_mtime > ttl:
                try:
                    os.unlink(entry.path)
                except FileNotFoundError:
                    pass
            else:
                objects.append((info.st_mtime_ns, info.st_size, entry.path))
        elif (
            entry.is_file(follow_symlinks=False)
            and entry.name.startswith(".")
            and entry.name.endswith(".tmp")
            and now - info.st_mtime > 3600
        ):
            try:
                os.unlink(entry.path)
            except FileNotFoundError:
                pass
    objects.sort(reverse=True)
    kept_objects = 0
    kept_bytes = 0
    for modified, size, path in objects:
        if kept_objects < max_objects and kept_bytes + size <= max_bytes:
            kept_objects += 1
            kept_bytes += size
            all_objects.append((modified, size, path))
            continue
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
all_objects.sort(reverse=True)
kept_objects = 0
kept_bytes = 0
for _, size, path in all_objects:
    if kept_objects < max_objects_total and kept_bytes + size <= max_bytes_total:
        kept_objects += 1
        kept_bytes += size
        continue
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
"""
    command = " ".join(
        (
            shlex.quote(REMOTE_CACHE_PRUNE_PYTHON),
            "-c",
            shlex.quote(script),
            shlex.quote(cache_directory),
            shlex.quote(REMOTE_COMFY_CACHE_ROOT),
            str(CACHE_TTL_SECONDS),
            str(CACHE_MAX_OBJECTS_PER_TENANT),
            str(CACHE_MAX_BYTES_PER_TENANT),
            str(CACHE_MAX_OBJECTS_TOTAL),
            str(CACHE_MAX_BYTES_TOTAL),
        )
    )
    channel = transport.open_session(timeout=5)
    try:
        channel.settimeout(30)
        channel.exec_command(command)
        _read_bounded(channel.makefile("rb"), MAX_DIGEST_RESPONSE_BYTES)
        stderr = _read_bounded(channel.makefile_stderr("rb"), MAX_DIGEST_RESPONSE_BYTES)
        status = channel.recv_exit_status()
        if status != 0:
            raise RuntimeError(
                f"remote cache prune failed with status {status}: "
                f"{stderr.decode('utf-8', errors='replace')[:256]}"
            )
    finally:
        channel.close()


def _schedule_remote_cache_prune(transport: paramiko.Transport, namespace: str) -> None:
    """Run one non-blocking global prune at most once per cooldown interval."""
    global _CACHE_PRUNE_LAST_STARTED_AT

    prune_key = "global"
    now = time.monotonic()
    with _CACHE_PRUNE_LOCK:
        _CACHE_PRUNE_PENDING.add(namespace)
        if prune_key in _CACHE_PRUNE_RUNNING:
            return
        if (
            _CACHE_PRUNE_LAST_STARTED_AT is not None
            and now - _CACHE_PRUNE_LAST_STARTED_AT < CACHE_PRUNE_MIN_INTERVAL_SECONDS
        ):
            return
        _CACHE_PRUNE_RUNNING.add(prune_key)
        _CACHE_PRUNE_PENDING.clear()
        _CACHE_PRUNE_LAST_STARTED_AT = now

    def prune() -> None:
        try:
            _remote_cache_prune(transport, namespace)
        except (OSError, RuntimeError, TimeoutError):
            # Reuse remains an optimization. A later promotion retries pruning;
            # cache maintenance must never turn a verified job into a failure.
            pass
        finally:
            with _CACHE_PRUNE_LOCK:
                _CACHE_PRUNE_RUNNING.discard(prune_key)

    threading.Thread(target=prune, daemon=True, name="autodl-cache-prune").start()


def _remote_input_digest(
    transport: paramiko.Transport,
    path: str,
) -> tuple[int, str]:
    """Calculate size and SHA-256 on the GPU host without reading file bytes over WAN."""
    channel = transport.open_session(timeout=5)
    try:
        channel.settimeout(10)
        quoted_path = shlex.quote(path)
        channel.exec_command(
            "LC_ALL=C /usr/bin/stat --printf='%s\\n' -- "
            f"{quoted_path} && /usr/bin/sha256sum --binary -- {quoted_path}"
        )
        stdout = _read_bounded(channel.makefile("rb"), MAX_DIGEST_RESPONSE_BYTES)
        stderr = _read_bounded(channel.makefile_stderr("rb"), MAX_DIGEST_RESPONSE_BYTES)
        status = channel.recv_exit_status()
        if status != 0:
            raise RuntimeError(
                f"remote digest command failed with status {status}: "
                f"{stderr.decode('utf-8', errors='replace')[:256]}"
            )
        lines = stdout.decode("ascii").splitlines()
        if len(lines) != 2 or not lines[0].isdigit():
            raise RuntimeError("remote digest response is invalid")
        digest = lines[1].split(maxsplit=1)[0].lower()
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise RuntimeError("remote digest response is invalid")
        return int(lines[0]), digest
    finally:
        channel.close()


def connect(
    provider_controller_url: str,
    hmac_secret_file: Path,
    known_hosts_file: Path,
    instance_id: str,
) -> paramiko.SSHClient:
    import paramiko

    info = credentials(provider_controller_url, hmac_secret_file, instance_id)
    host, port = _validated_ssh_endpoint(info)
    username = str(info.get("username") or "")
    if username != "root":
        raise RuntimeError("Provider Controller returned an invalid SSH username")
    password = str(info.get("root_password") or "")
    if not password:
        password = str(info.get("password") or "")
    if not password:
        raise RuntimeError("Provider Controller SSH credentials are unavailable")
    client = paramiko.SSHClient()
    client.load_host_keys(str(known_hosts_file))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        client.connect(
            host,
            port=port,
            username=username,
            password=password,
            timeout=20,
            banner_timeout=20,
            auth_timeout=20,
            allow_agent=False,
            look_for_keys=False,
            transport_factory=_high_throughput_transport,
        )
        transport = client.get_transport()
        if transport is None:
            raise RuntimeError("AutoDL SSH transport is unavailable")
        transport.set_keepalive(15)
    except Exception:
        client.close()
        raise
    return client


def _recovery_command(profile: str) -> str:
    """Resolve a recovery profile without accepting any remote command input."""
    try:
        return RECOVERY_COMMANDS[profile]
    except KeyError as exc:
        raise RuntimeError("AutoDL recovery profile is not allowlisted") from exc


def _active_transport(client: paramiko.SSHClient) -> paramiko.Transport | None:
    transport = client.get_transport()
    return transport if transport is not None and transport.is_active() else None


def _remote_system_stats_ready(
    transport: paramiko.Transport,
    remote: tuple[str, int],
) -> bool:
    """Require an HTTP 200 from ComfyUI through the SSH direct channel."""
    channel = None
    try:
        channel = transport.open_channel(
            "direct-tcpip",
            remote,
            ("127.0.0.1", 0),
            timeout=3,
        )
        if channel is None:
            return False
        channel.settimeout(3)
        _send_all(
            channel,
            b"GET /system_stats HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n",
        )
        response = bytearray()
        while b"\n" not in response and len(response) < MAX_HTTP_STATUS_BYTES:
            chunk = channel.recv(min(1024, MAX_HTTP_STATUS_BYTES - len(response)))
            if not chunk:
                break
            response.extend(chunk)
        status_line = bytes(response).split(b"\n", 1)[0].rstrip(b"\r")
        return status_line.startswith((b"HTTP/1.0 200 ", b"HTTP/1.1 200 "))
    except Exception:
        return False
    finally:
        if channel is not None:
            channel.close()


def _remote_ready(client: paramiko.SSHClient, remote: tuple[str, int]) -> bool:
    transport = _active_transport(client)
    return False if transport is None else _remote_system_stats_ready(transport, remote)


def _execute_recovery_profile(client: paramiko.SSHClient, profile: str) -> None:
    """Execute exactly one compile-time allowlisted command on the remote host."""
    command = _recovery_command(profile)
    transport = _active_transport(client)
    if transport is None:
        raise RuntimeError("AutoDL SSH transport is unavailable for recovery")
    channel = transport.open_session(timeout=10)
    try:
        channel.settimeout(RECOVERY_COMMAND_TIMEOUT_SECONDS)
        channel.exec_command(command)
        deadline = time.monotonic() + RECOVERY_COMMAND_TIMEOUT_SECONDS
        while not channel.exit_status_ready():
            if time.monotonic() >= deadline:
                raise TimeoutError("AutoDL recovery profile timed out")
            time.sleep(0.1)
        status = channel.recv_exit_status()
        if status != 0:
            raise RuntimeError(f"AutoDL recovery profile failed with status {status}")
    finally:
        channel.close()


def _read_bounded(source: Any, maximum: int) -> bytes:
    payload = bytearray()
    while len(payload) <= maximum:
        chunk = source.read(min(8192, maximum + 1 - len(payload)))
        if not chunk:
            return bytes(payload)
        payload.extend(chunk)
    raise RuntimeError("AutoDL remote start script exceeds the size limit")


def _script_digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _remote_script_digest(sftp: Any, path: str) -> str | None:
    try:
        attributes = sftp.lstat(path)
        if not stat.S_ISREG(attributes.st_mode):
            return None
        with sftp.file(path, "rb") as source:
            return _script_digest(_read_bounded(source, MAX_START_SCRIPT_BYTES))
    except OSError:
        return None


def _ensure_remote_start_directory(sftp: Any) -> None:
    for directory in ("/root/autodl-tmp", "/root/autodl-tmp/gpu-control"):
        try:
            attributes = sftp.lstat(directory)
        except OSError:
            sftp.mkdir(directory, mode=0o700)
            attributes = sftp.lstat(directory)
        if not stat.S_ISDIR(attributes.st_mode):
            raise RuntimeError("AutoDL remote start script directory is invalid")


def _sync_remote_start_script(
    client: paramiko.SSHClient,
    local_path: Path = LOCAL_START_SCRIPT_PATH,
) -> None:
    """Atomically install the checksum-pinned recovery script over SFTP."""
    payload = local_path.read_bytes()
    if not payload or len(payload) > MAX_START_SCRIPT_BYTES:
        raise RuntimeError("Bundled AutoDL remote start script is invalid")
    if not hmac.compare_digest(_script_digest(payload), REMOTE_START_SCRIPT_SHA256):
        raise RuntimeError("Bundled AutoDL remote start script checksum mismatch")

    sftp = client.open_sftp()
    try:
        sftp.get_channel().settimeout(30)
        _ensure_remote_start_directory(sftp)
        if hmac.compare_digest(
            _remote_script_digest(sftp, REMOTE_START_SCRIPT_PATH) or "",
            REMOTE_START_SCRIPT_SHA256,
        ):
            sftp.chmod(REMOTE_START_SCRIPT_PATH, 0o700)
            return

        try:
            temp_attributes = sftp.lstat(REMOTE_START_SCRIPT_TEMP_PATH)
        except OSError:
            temp_attributes = None
        if temp_attributes is not None:
            if not stat.S_ISREG(temp_attributes.st_mode):
                raise RuntimeError("AutoDL remote start script temporary path is invalid")
            sftp.remove(REMOTE_START_SCRIPT_TEMP_PATH)

        with sftp.file(REMOTE_START_SCRIPT_TEMP_PATH, "wb") as target:
            target.write(payload)
            target.flush()
        sftp.chmod(REMOTE_START_SCRIPT_TEMP_PATH, 0o700)
        if not hmac.compare_digest(
            _remote_script_digest(sftp, REMOTE_START_SCRIPT_TEMP_PATH) or "",
            REMOTE_START_SCRIPT_SHA256,
        ):
            raise RuntimeError("Uploaded AutoDL remote start script checksum mismatch")
        try:
            sftp.posix_rename(REMOTE_START_SCRIPT_TEMP_PATH, REMOTE_START_SCRIPT_PATH)
        except OSError as exc:
            raise RuntimeError("AutoDL server does not support atomic script replacement") from exc
        sftp.chmod(REMOTE_START_SCRIPT_PATH, 0o700)
        if not hmac.compare_digest(
            _remote_script_digest(sftp, REMOTE_START_SCRIPT_PATH) or "",
            REMOTE_START_SCRIPT_SHA256,
        ):
            raise RuntimeError("Installed AutoDL remote start script checksum mismatch")
    finally:
        sftp.close()


def _prepare_remote(
    client: paramiko.SSHClient,
    remote: tuple[str, int],
    recovery_profile: str,
    stop: threading.Event,
    *,
    poll_seconds: float = 2.0,
    ready_timeout_seconds: float = 180.0,
) -> bool:
    """Start an absent service once, then wait while this SSH session stays alive."""
    if _remote_ready(client, remote):
        return True

    # The profile, path and command are all local constants. In particular, no
    # provider response or CLI value is interpolated into a shell command.
    try:
        _execute_recovery_profile(client, recovery_profile)
    except TimeoutError:
        # A correctly detached service can become healthy even when the SSH
        # command channel does not report completion.  Probe the real ComfyUI
        # endpoint once before discarding a usable transport and paying the
        # reconnect backoff.  Keep the failed case fail-closed.
        stop_requested = stop.is_set()
        ready_after_timeout = False
        if not stop_requested and _active_transport(client) is not None:
            ready_after_timeout = _remote_ready(client, remote)
        action = (
            "stop" if stop_requested else "continue_ready" if ready_after_timeout else "reconnect"
        )
        print(
            json.dumps(
                {
                    "event": "remote_recovery_command_timeout",
                    "profile": recovery_profile,
                    "remote_port": remote[1],
                    "timeout_seconds": RECOVERY_COMMAND_TIMEOUT_SECONDS,
                    "remote_ready": ready_after_timeout,
                    "action": action,
                }
            ),
            flush=True,
        )
        if ready_after_timeout:
            return True
        if stop_requested:
            return False
        raise
    print(
        json.dumps(
            {
                "event": "remote_recovery_invoked",
                "profile": recovery_profile,
                "remote_port": remote[1],
            }
        ),
        flush=True,
    )
    deadline = time.monotonic() + ready_timeout_seconds
    while not stop.is_set():
        if _active_transport(client) is None:
            return False
        if _remote_ready(client, remote):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            print(
                json.dumps(
                    {
                        "event": "remote_ready_timeout",
                        "profile": recovery_profile,
                        "remote_port": remote[1],
                        "timeout_seconds": ready_timeout_seconds,
                    }
                ),
                flush=True,
            )
            return False
        if stop.wait(min(poll_seconds, remaining)):
            return False
    return False


def _positive_port(value: int, label: str) -> int:
    if not 1 <= value <= 65535:
        raise RuntimeError(f"{label} must be between 1 and 65535")
    return value


def _serve(server: socketserver.BaseServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--bind-host", default="0.0.0.0")  # noqa: S104
    parser.add_argument("--bind-port", type=int, default=16006)
    parser.add_argument("--remote-port", type=int, default=6006)
    parser.add_argument("--health-host", default="127.0.0.1")
    parser.add_argument("--health-port", type=int, default=18080)
    parser.add_argument("--reconnect-max-seconds", type=float, default=60.0)
    parser.add_argument("--remote-ready-timeout-seconds", type=float, default=180.0)
    parser.add_argument(
        "--recovery-profile",
        choices=tuple(RECOVERY_COMMANDS),
        default=None,
    )
    parser.add_argument(
        "--provider-controller-url",
        default="http://provider-controller:8020",
    )
    parser.add_argument(
        "--hmac-secret-file",
        type=Path,
        default=Path("/run/secrets/providers/provider-controller-hmac"),
    )
    parser.add_argument(
        "--known-hosts-file",
        type=Path,
        default=Path("/run/secrets/providers/autodl-known-hosts"),
    )
    args = parser.parse_args()
    instance_id = _validated_instance_id(args.instance_id)
    bind_port = _positive_port(args.bind_port, "bind port")
    remote_port = _positive_port(args.remote_port, "remote port")
    health_port = _positive_port(args.health_port, "health port")
    if args.reconnect_max_seconds < 1:
        raise RuntimeError("reconnect maximum must be at least one second")
    if not 10 <= args.remote_ready_timeout_seconds <= 1800:
        raise RuntimeError("remote ready timeout must be between 10 and 1800 seconds")
    if args.recovery_profile is not None:
        _recovery_command(args.recovery_profile)

    state = TunnelState()
    remote = ("127.0.0.1", remote_port)
    forward_server = ForwardServer((args.bind_host, bind_port), state, remote)
    health_server = HealthServer((args.health_host, health_port), state, remote)
    _serve(forward_server)
    _serve(health_server)
    stop = threading.Event()

    def request_stop(_signum: int, _frame: FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    print(
        json.dumps(
            {
                "event": "tunnel_listening",
                "bind": f"{args.bind_host}:{bind_port}",
                "instance_id": instance_id,
            }
        ),
        flush=True,
    )
    reconnect_delay = 1.0
    try:
        while not stop.is_set():
            if state.transport() is None:
                candidate = None
                try:
                    candidate = connect(
                        args.provider_controller_url,
                        args.hmac_secret_file,
                        args.known_hosts_file,
                        instance_id,
                    )
                    print(json.dumps({"event": "ssh_connected"}), flush=True)
                    _sync_remote_start_script(candidate)
                    print(
                        json.dumps(
                            {
                                "event": "remote_start_script_ready",
                                "sha256": REMOTE_START_SCRIPT_SHA256,
                            }
                        ),
                        flush=True,
                    )
                    if args.recovery_profile is not None and not _prepare_remote(
                        candidate,
                        remote,
                        args.recovery_profile,
                        stop,
                        ready_timeout_seconds=args.remote_ready_timeout_seconds,
                    ):
                        candidate.close()
                        candidate = None
                        state.replace(None)
                        if stop.is_set():
                            break
                        print(
                            json.dumps(
                                {
                                    "event": "remote_port_wait_reconnect",
                                    "retry_seconds": reconnect_delay,
                                }
                            ),
                            flush=True,
                        )
                        if stop.wait(reconnect_delay):
                            break
                        reconnect_delay = min(reconnect_delay * 2, args.reconnect_max_seconds)
                        continue
                    state.replace(candidate)
                    candidate = None
                    reconnect_delay = 1.0
                    print(
                        json.dumps(
                            {
                                "event": "tunnel_ready",
                                "remote_port": remote_port,
                            }
                        ),
                        flush=True,
                    )
                except Exception as exc:
                    if candidate is not None:
                        candidate.close()
                    state.replace(None)
                    print(
                        json.dumps(
                            {
                                "event": "ssh_connect_wait",
                                "error_type": type(exc).__name__,
                                "retry_seconds": reconnect_delay,
                            }
                        ),
                        flush=True,
                    )
                    if stop.wait(reconnect_delay):
                        break
                    reconnect_delay = min(reconnect_delay * 2, args.reconnect_max_seconds)
                    continue
            stop.wait(2)
    finally:
        forward_server.shutdown()
        health_server.shutdown()
        forward_server.server_close()
        health_server.server_close()
        state.replace(None)


if __name__ == "__main__":
    main()
