import heapq
import multiprocessing
import os
import selectors
import signal
import socket
import sys
import time

PING_RESPONSE = (
    b"HTTP/1.1 200 OK\r\n"
    b"Content-Type: text/plain\r\n"
    b"Content-Length: 5\r\n"
    b"Connection: keep-alive\r\n"
    b"\r\n"
    b"PONG\n"
)

NOT_FOUND_RESPONSE = (
    b"HTTP/1.1 404 Not Found\r\n"
    b"Content-Length: 0\r\n"
    b"Connection: keep-alive\r\n"
    b"\r\n"
)


class ClientState:
    """Per-socket read/write state with __slots__ to minimize memory across 10k sockets."""

    __slots__ = ("sock", "in_buf", "out_buf", "writing")

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.in_buf = bytearray()
        self.out_buf = bytearray()
        self.writing = False


def run_worker(host: str, port: int):
    # Automatically picks EpollSelector on Linux, KqueueSelector on macOS/BSD
    sel = selectors.DefaultSelector()

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT") and sys.platform != "win32":
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)

    server_sock.bind((host, port))
    server_sock.listen(16384)
    server_sock.setblocking(False)

    # Register listener with data=None to distinguish accept events from client events
    sel.register(server_sock, selectors.EVENT_READ, data=None)

    active_conns = 0
    total_requests = 0
    timer_seq = 0
    timers = []  # Min-heap of (wake_monotonic_time, seq, ClientState, payload_bytes)
    running = True

    def stop_handler(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    def close_client(state: ClientState):
        nonlocal active_conns
        if state.sock.fileno() == -1:
            return
        try:
            sel.unregister(state.sock)
        except (KeyError, ValueError):
            pass
        try:
            state.sock.close()
        except OSError:
            pass
        state.in_buf.clear()
        state.out_buf.clear()
        active_conns -= 1

    def queue_response(state: ClientState, payload: bytes):
        if state.sock.fileno() == -1:
            return
        if not state.out_buf:
            # Fast path: attempt immediate non-blocking socket send
            try:
                sent = state.sock.send(payload)
                if sent == len(payload):
                    return
                state.out_buf.extend(payload[sent:])
            except BlockingIOError:
                state.out_buf.extend(payload)
            except OSError:
                close_client(state)
                return
        else:
            state.out_buf.extend(payload)

        # Subscribe to EVENT_WRITE only when kernel send buffer fills up (backpressure)
        if state.out_buf and not state.writing:
            state.writing = True
            sel.modify(state.sock, selectors.EVENT_READ | selectors.EVENT_WRITE, data=state)

    def accept_ready():
        nonlocal active_conns
        # Drain all pending connections in the kernel accept queue at once
        while True:
            try:
                conn, _ = server_sock.accept()
            except BlockingIOError:
                break
            except OSError:
                break

            conn.setblocking(False)
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            state = ClientState(conn)
            sel.register(conn, selectors.EVENT_READ, data=state)
            active_conns += 1

    def handle_client(state: ClientState, mask: int):
        nonlocal total_requests, timer_seq

        if mask & selectors.EVENT_READ:
            try:
                chunk = state.sock.recv(4096)
            except BlockingIOError:
                chunk = None
            except OSError:
                close_client(state)
                return

            if chunk == b"":
                close_client(state)
                return

            if chunk:
                state.in_buf.extend(chunk)
                while True:
                    hdr_end = state.in_buf.find(b"\r\n\r\n")
                    if hdr_end == -1:
                        if len(state.in_buf) > 8192:
                            close_client(state)
                        break

                    line_end = state.in_buf.find(b"\r\n", 0, hdr_end)
                    req_line = bytes(
                        state.in_buf[: line_end if line_end != -1 else hdr_end]
                    )
                    del state.in_buf[: hdr_end + 4]
                    total_requests += 1

                    if req_line.startswith(b"GET /ping "):
                        queue_response(state, PING_RESPONSE)
                    elif req_line.startswith(b"GET /hold "):
                        timer_seq += 1
                        heapq.heappush(
                            timers,
                            (time.monotonic() + 0.05, timer_seq, state, PING_RESPONSE),
                        )
                    elif req_line.startswith(b"GET /metrics "):
                        body = (
                            f"worker_pid: {os.getpid()}\n"
                            f"selector: {type(sel).__name__}\n"
                            f"active_connections: {active_conns}\n"
                            f"total_requests: {total_requests}\n"
                        ).encode("ascii")
                        resp = (
                            b"HTTP/1.1 200 OK\r\n"
                            b"Content-Type: text/plain\r\n"
                            b"Connection: keep-alive\r\n"
                            + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
                            + body
                        )
                        queue_response(state, resp)
                    else:
                        queue_response(state, NOT_FOUND_RESPONSE)

        if (mask & selectors.EVENT_WRITE) and state.out_buf and state.sock.fileno() != -1:
            try:
                sent = state.sock.send(state.out_buf)
                del state.out_buf[:sent]
            except BlockingIOError:
                pass
            except OSError:
                close_client(state)
                return

            if not state.out_buf and state.writing:
                state.writing = False
                sel.modify(state.sock, selectors.EVENT_READ, data=state)

    print(
        f"[Worker PID {os.getpid()}] {type(sel).__name__} listening on http://{host}:{port}"
    )

    try:
        while running:
            # Sleep only until the earliest /hold timer expires, or up to 1.0s
            timeout = 1.0
            if timers:
                timeout = max(0.0, timers[0][0] - time.monotonic())

            events = sel.select(timeout=timeout)
            for key, mask in events:
                if key.data is None:
                    accept_ready()
                else:
                    handle_client(key.data, mask)

            if timers:
                now = time.monotonic()
                while timers and timers[0][0] <= now:
                    _, _, delayed_state, payload = heapq.heappop(timers)
                    queue_response(delayed_state, payload)
    finally:
        sel.close()
        server_sock.close()


if __name__ == "__main__":
    HOST = "127.0.0.1"
    PORT = 8080
    WORKERS = (
        min(multiprocessing.cpu_count(), 4)
        if hasattr(socket, "SO_REUSEPORT") and sys.platform != "win32"
        else 1
    )

    print(f"Starting pure-selectors C10K server with {WORKERS} worker(s)...")
    procs = []
    for _ in range(WORKERS):
        p = multiprocessing.Process(target=run_worker, args=(HOST, PORT))
        p.start()
        procs.append(p)

    try:
        for p in procs:
            p.join()
    except KeyboardInterrupt:
        for p in procs:
            p.terminate()