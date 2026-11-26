import hashlib
import json
import selectors
import socket
import sys

HOST = "127.0.0.1"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
BACKLOG = 16384
MAX_STORE_SIZE = 10000

sel = selectors.DefaultSelector()
store = {}


class ConnState:
    __slots__ = ("in_buf", "out_buf", "close_after_write")

    def __init__(self):
        self.in_buf = bytearray()
        self.out_buf = bytearray()
        self.close_after_write = False


def build_response(status_line, body_dict, keep_alive=True):
    body = json.dumps(body_dict).encode("utf-8")
    conn_hdr = b"keep-alive" if keep_alive else b"close"
    return (
        f"HTTP/1.1 {status_line}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: {conn_hdr.decode()}\r\n"
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

        # Light pure-Python + stdlib CPU work (hashing + bounded dict mutation)
        digest = hashlib.sha256(f"{key}:{value}".encode("utf-8")).hexdigest()[:16]
        if len(store) >= MAX_STORE_SIZE:
            store.pop(next(iter(store)))
        store[key] = {"id": key, "value": value, "hash": digest}

        return build_response("200 OK", {"status": "ok", "item": store[key]}, keep_alive)

    if method == "GET" and path.startswith("/items/"):
        key = path[7:]
        item = store.get(key)
        if item is None:
            return build_response("404 Not Found", {"error": "not found"}, keep_alive)
        return build_response("200 OK", item, keep_alive)

    return build_response("200 OK", {"status": "ok", "count": len(store)}, keep_alive)


def parse_and_process(state):
    buf = state.in_buf
    while True:
        header_end = buf.find(b"\r\n\r\n")
        if header_end == -1:
            break

        header_block = bytes(buf[:header_end]).decode("latin-1")
        lines = header_block.split("\r\n")
        if not lines:
            state.close_after_write = True
            break

        parts = lines[0].split(" ")
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
            # Wait for the rest of the HTTP body
            break

        body_bytes = bytes(buf[header_end + 4 : total_len])
        del buf[:total_len]

        resp = handle_request(method, path, body_bytes, keep_alive)
        state.out_buf.extend(resp)

        if not keep_alive:
            state.close_after_write = True
            break


def close_conn(conn):
    try:
        sel.unregister(conn)
    except Exception:
        pass
    try:
        conn.close()
    except OSError:
        pass


def accept(sock):
    while True:
        try:
            conn, _ = sock.accept()
        except (BlockingIOError, OSError):
            break
        conn.setblocking(False)
        sel.register(conn, selectors.EVENT_READ, data=ConnState())


def handle_io(key, mask):
    conn = key.fileobj
    state = key.data

    if mask & selectors.EVENT_READ:
        try:
            chunk = conn.recv(4096)
            if not chunk:
                close_conn(conn)
                return
            state.in_buf.extend(chunk)
            parse_and_process(state)
        except (BlockingIOError, ConnectionResetError, OSError):
            close_conn(conn)
            return

    if state.out_buf:
        try:
            sent = conn.send(state.out_buf)
            if sent > 0:
                del state.out_buf[:sent]
        except BlockingIOError:
            pass
        except (BrokenPipeError, ConnectionResetError, OSError):
            close_conn(conn)
            return

    if state.close_after_write and not state.out_buf:
        close_conn(conn)
        return

    # Only watch EVENT_WRITE when there is unsent data in out_buf
    new_events = selectors.EVENT_READ | (selectors.EVENT_WRITE if state.out_buf else 0)
    if new_events != key.events:
        sel.modify(conn, new_events, data=state)


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.bind((HOST, PORT))
    sock.listen(BACKLOG)
    sock.setblocking(False)

    sel.register(sock, selectors.EVENT_READ, data=None)
    print(f"Listening on {HOST}:{PORT} ({sys.implementation.name} {sys.version.split()[0]})")

    try:
        while True:
            for key, mask in sel.select():
                if key.data is None:
                    accept(key.fileobj)
                else:
                    handle_io(key, mask)
    except KeyboardInterrupt:
        pass
    finally:
        sel.close()
        sock.close()


if __name__ == "__main__":
    main()
