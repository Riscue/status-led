"""Unix socket server for the daemon.

Bind, accept one client at a time (single-threaded daemon loop), receive
the full payload, and respond if needed. The orchestrator (daemon) decides
what to do with each payload — this module is a pure I/O adapter.

The accept-timeout is the heartbeat of the daemon loop: when no client
arrives within ACCEPT_TIMEOUT, accept_one returns None and the
orchestrator gets a chance to run housekeeping (transient TTL expiry).
"""
from __future__ import annotations

import logging
import os
import socket

ACCEPT_TIMEOUT: float = 1.0
CLIENT_TIMEOUT: float = 0.5
LISTEN_BACKLOG: int = 8
RECV_BUFFER: int = 256


def is_socket_live(path: str) -> bool:
    """Return True if a process is currently listening on the given Unix socket."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            s.connect(path)
        return True
    except (ConnectionRefusedError, FileNotFoundError, socket.timeout, OSError):
        return False


class DaemonSocket:
    """Unix socket server. Owns bind/listen/accept; yields client payloads."""

    def __init__(self,
                 path: str,
                 log: logging.Logger,
                 accept_timeout: float = ACCEPT_TIMEOUT,
                 client_timeout: float = CLIENT_TIMEOUT):
        self._path = path
        self._log = log
        self._accept_timeout = accept_timeout
        self._client_timeout = client_timeout
        self._listen_sock: socket.socket | None = None

    def setup(self) -> None:
        """Bind the socket, set perms, start listening.

        Raises RuntimeError if another daemon is already listening on the path.
        Stale socket files (from a crashed daemon) are unlinked first.
        """
        d = os.path.dirname(self._path)
        os.makedirs(d, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
        if os.path.exists(self._path):
            if is_socket_live(self._path):
                raise RuntimeError(
                    f"another daemon is already listening on {self._path}"
                )
            os.unlink(self._path)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(self._path)
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            pass
        s.listen(LISTEN_BACKLOG)
        s.settimeout(self._accept_timeout)
        self._listen_sock = s
        self._log.info("listening on %s", self._path)

    def accept_one(self) -> tuple[socket.socket, str] | None:
        """Accept one client and receive the full payload.

        Returns (client_socket, payload) on success, or None on accept-timeout
        (which the orchestrator uses as a housekeeping tick). The caller is
        responsible for closing the client socket — via respond() if replying,
        or close_client() otherwise.
        """
        if self._listen_sock is None:
            return None
        try:
            client, _ = self._listen_sock.accept()
        except socket.timeout:
            return None
        except OSError:
            return None
        client.settimeout(self._client_timeout)
        chunks = []
        while True:
            try:
                chunk = client.recv(RECV_BUFFER)
            except socket.timeout:
                break
            except OSError:
                break
            if not chunk:
                break
            chunks.append(chunk)
        payload = b"".join(chunks).decode("utf-8", errors="replace")
        return client, payload

    def respond(self, client: socket.socket, payload: str) -> None:
        """Send a response to a client and close the connection."""
        try:
            client.sendall((payload + "\n").encode("utf-8"))
        except OSError as e:
            self._log.debug("respond failed: %s", e)
        finally:
            self._close_quietly(client)

    def close_client(self, client: socket.socket) -> None:
        self._close_quietly(client)

    def close(self) -> None:
        if self._listen_sock is not None:
            try:
                self._listen_sock.close()
            except Exception:
                pass
            self._listen_sock = None
        try:
            if os.path.exists(self._path):
                os.unlink(self._path)
        except OSError as e:
            self._log.warning("could not unlink socket: %s", e)

    @staticmethod
    def _close_quietly(client: socket.socket) -> None:
        try:
            client.close()
        except Exception:
            pass
