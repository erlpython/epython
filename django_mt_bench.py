import io
import sys
import threading
import time

import django_server

DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 1.5
THREAD_COUNTS = (1, 4, 16, 32, 48, 64)
PATHS = ("/json/", "/template/")

BASE_ENV = {
    "REQUEST_METHOD": "GET",
    "SCRIPT_NAME": "",
    "PATH_INFO": "/json/",
    "QUERY_STRING": "",
    "SERVER_NAME": "127.0.0.1",
    "SERVER_PORT": "8000",
    "SERVER_PROTOCOL": "HTTP/1.1",
    "HTTP_HOST": "127.0.0.1:8000",
    "wsgi.version": (1, 0),
    "wsgi.url_scheme": "http",
    "wsgi.input": io.BytesIO(b""),
    "wsgi.errors": io.StringIO(),
    "wsgi.multithread": True,
    "wsgi.multiprocess": False,
    "wsgi.run_once": False,
}


def start_response(_status, _headers, _exc_info=None):
    pass


def run_mt(app, path, num_threads, duration):
    stop = False
    counts = [0] * num_threads

    def worker(idx):
        env = dict(BASE_ENV, PATH_INFO=path)
        n = 0
        while not stop:
            list(app(dict(env), start_response))
            n += 1
        counts[idx] = n

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    time.sleep(duration)
    stop = True
    for t in threads:
        t.join()
    dt = time.perf_counter() - t0
    total = sum(counts)
    print(
        f"  {path:11s} {num_threads:2d}t : {total / dt:9.1f} req/s  "
        f"(per-thread: {total / dt / num_threads:7.1f}/s)",
        flush=True,
    )


def main():
    app = django_server.application
    for path in PATHS:
        for _ in range(100):
            list(app(dict(BASE_ENV, PATH_INFO=path), start_response))
    time.sleep(3.0)
    for path in PATHS:
        for _ in range(50):
            list(app(dict(BASE_ENV, PATH_INFO=path), start_response))

    for path in PATHS:
        for num_threads in THREAD_COUNTS:
            run_mt(app, path, num_threads, DURATION)


if __name__ == "__main__":
    main()
