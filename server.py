import socket
import sys
import threading
import time

RESPONSE = (b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 13\r\n"
            b"Connection: keep-alive\r\n\r\nHello, World!")


def handle(c):
    buf = b""
    try:
        while True:
            b = c.recv(65536)
            if not b:
                break
            buf += b
            n = buf.count(b"\r\n\r\n")
            if n:
                buf = buf[buf.rfind(b"\r\n\r\n") + 4:]
                c.sendall(RESPONSE * n)
    except OSError:
        pass
    finally:
        c.close()


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    host = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    s.listen(65535)
    print("ready on %s:%d" % (host, port), flush=True)
    reported = False
    while True:
        try:
            c, _ = s.accept()
        except OSError as e:  # EMFILE: out of file descriptors (raise ulimit -n)
            if not reported:
                reported = True
                print("accept: %s" % e, flush=True)
            time.sleep(0.01)
            continue
        try:
            threading.Thread(target=handle, args=(c,), daemon=True).start()
        except RuntimeError as e:  # can't start new thread
            if not reported:
                reported = True
                print("thread limit: %s (%d threads)" % (e, threading.active_count()), flush=True)
            c.close()


if __name__ == "__main__":
    main()
