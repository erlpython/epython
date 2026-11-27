import json
import os
import selectors
import socket
import sys
import threading

HOST = "127.0.0.1"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
NUM_THREADS = int(sys.argv[2]) if len(sys.argv) > 2 else (os.cpu_count() or 4)
BACKLOG = 16384

# Sharded shared state: tests real shared-memory multithreading
# without bottlenecking all cores on a single global Lock.
NUM_SHARDS = 64
MAX_PER_SHARD = 200
SHARDS = [({}, threading.Lock()) for _ in range(NUM_SHARDS)]


class ConnState:
    __slots__ = ("in_buf", "out_buf", "close_after_write")

    def __init__(self):
        self.in_buf = bytearray()
        self.out_buf = bytearray()
        self.close_after_write = False


def pure_python_hash(text):
    """Pure-Python FNV-1a hash (forces bytecode execution; never releases GIL)."""
    h = 2166136261
    for ch in text:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return f"{h:08x}"


def build_response(status_line, body_dict, keep_alive=True):
    body = json.dumps(body_dict).encode("utf-8")
    conn_hdr = "keep-alive" if keep_alive else "close"
    return (
        f"HTTP/1.1 {status_line}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: {conn_hdr}\r\n"
        f"\r\n"
    ).encode("ascii") + body


def handle_request(method, path, body_bytes, keep_alive):
    if method == "POST" and path == "/items":
        try:
            payload = json.loads(body_bytes)
            key = str(payload["id"])
            value = str(payload["value"])
        except (ValueError, KeyError, TypeError):
            return build_response("400 Bad Request", {"error": "invalid json"}, keep_alive)

        digest = pure_python_hash(f"{key}:{value}")
        shard_dict, shard_lock = SHARDS[hash(key) % NUM_SHARDS]

        with shard_lock:
            if len(shard_dict) >= MAX_PER_SHARD:
                shard_dict.pop(next(iter(shard_dict)))
            item = {"id": key, "value": value, "hash": digest}
            shard_dict[key] = item

        return build_response("200 OK", {"status": "ok", "item": item}, keep_alive)

    return build_response("200 OK", {"status": "ok"}, keep_alive)


def parse_and_process(state):
    buf = state.in_buf
    while True:
        header_end = buf.find(b"\r\n\r\n")
        if header_end == -1:
            break

        header_block = bytes(buf[:header_end]).decode("latin-1")
        lines = header_block.split("\r\n")
        parts = lines[0].split(" ") if lines else []
        if len(parts) < 2:
            state.close_after_write = True
            break

        method, path = parts[0], parts[1]
        content_length = 0
        keep_alive = True

        for line in lines[1:]:
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            k = k.strip().lower()
            v = v.strip().lower()
            if k == "content-length":
                try:
                    content_length = int(v)
                except ValueError:
                    content_length = 0
            elif k == "connection" and v == "close":
                keep_alive = False

        total_len = header_end + 4 + content_length
        if len(buf) < total_len:
            break

        body_bytes = bytes(buf[header_end + 4 : total_len])
        del buf[:total_len]

        state.out_buf.extend(handle_request(method, path, body_bytes, keep_alive))
        if not keep_alive:
            state.close_after_write = True
            break


def worker_loop(worker_id, stop_event):
    sel = selectors.DefaultSelector()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.bind((HOST, PORT))
    sock.listen(BACKLOG)
    sock.setblocking(False)
    sel.register(sock, selectors.EVENT_READ, data=None)

    def close_conn(conn):
        try:
            sel.unregister(conn)
        except Exception:
            pass
        try:
            conn.close()
        except OSError:
            pass

    try:
        while not stop_event.is_set():
            for key, mask in sel.select(timeout=0.5):
                if key.data is None:
                    while True:
                        try:
                            conn, _ = sock.accept()
                        except (BlockingIOError, OSError):
                            break
                        conn.setblocking(False)
                        sel.register(conn, selectors.EVENT_READ, data=ConnState())
                else:
                    conn = key.fileobj
                    state = key.data
                    if mask & selectors.EVENT_READ:
                        try:
                            chunk = conn.recv(4096)
                            if not chunk:
                                close_conn(conn)
                                continue
                            state.in_buf.extend(chunk)
                            parse_and_process(state)
                        except (BlockingIOError, ConnectionResetError, OSError):
                            close_conn(conn)
                            continue

                    if state.out_buf:
                        try:
                            sent = conn.send(state.out_buf)
                            if sent > 0:
                                del state.out_buf[:sent]
                        except BlockingIOError:
                            pass
                        except (BrokenPipeError, ConnectionResetError, OSError):
                            close_conn(conn)
                            continue

                    if state.close_after_write and not state.out_buf:
                        close_conn(conn)
                        continue

                    new_events = selectors.EVENT_READ | (
                        selectors.EVENT_WRITE if state.out_buf else 0
                    )
                    if new_events != key.events:
                        sel.modify(conn, new_events, data=state)
    finally:
        sel.close()
        sock.close()


def main():
    stop_event = threading.Event()
    threads = []
    for i in range(NUM_THREADS):
        t = threading.Thread(target=worker_loop, args=(i, stop_event), daemon=True)
        t.start()
        threads.append(t)

    print(
        f"Listening on {HOST}:{PORT} with {NUM_THREADS} threads "
        f"({sys.implementation.name} {sys.version.split()[0]})"
    )
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        stop_event.set()


if __name__ == "__main__":
    main()