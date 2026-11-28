import socket
import sys
import threading

import waitress

import django_server

HOST = "127.0.0.1"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
NUM_LOOPS = int(sys.argv[2]) if len(sys.argv) > 2 else 16
THREADS_PER_LOOP = int(sys.argv[3]) if len(sys.argv) > 3 else 1
BACKLOG = 1024


def make_reuseport_socket(host, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.setblocking(False)
    sock.bind((host, port))
    sock.listen(BACKLOG)
    return sock


def main():
    app = django_server.application
    servers = [
        waitress.create_server(
            app,
            sockets=[make_reuseport_socket(HOST, PORT)],
            threads=THREADS_PER_LOOP,
        )
        for _ in range(NUM_LOOPS)
    ]

    for srv in servers[1:]:
        threading.Thread(target=srv.run, daemon=True).start()
    servers[0].run()


if __name__ == "__main__":
    main()
