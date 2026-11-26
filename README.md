# C10K bench

[`server.py`](server.py): 4-worker `multiprocessing` + `selectors` (`EpollSelector` / `KqueueSelector`) non-blocking HTTP server on `127.0.0.1:8080` (`/ping`, `/hold` 50ms timer, `/metrics`).

## Setup

macOS:

```sh
brew install wrk
sudo sysctl -w kern.ipc.somaxconn=16384
```

Linux:

```sh
sudo apt install wrk
sudo sysctl -w fs.file-max=2000000 net.core.somaxconn=65535 net.ipv4.tcp_max_syn_backlog=65535
sudo sysctl -w net.ipv4.ip_local_port_range="1024 65535" net.ipv4.tcp_tw_reuse=1
sudo sysctl -w kernel.threads-max=4194304 kernel.pid_max=4194304 vm.max_map_count=1048576
```

## Run

```sh
# terminal 1
ulimit -n 65536 && epython server.py

# terminal 2
ulimit -n 65536 && wrk -t8 -c10000 -d30s --timeout 5s --latency http://127.0.0.1:8080/ping
ulimit -n 65536 && wrk -t8 -c10000 -d30s --timeout 5s --latency http://127.0.0.1:8080/hold
```

No `Socket errors:` line means all 10,000 connections completed cleanly with 0 errors.

## Results

Sequential 30s runs (`wrk -t8 -c10000 -d30s --timeout 5s --latency`, 4 workers, 0 socket errors, 0 non-2xx responses):

### Machine

| Spec | Value |
|---|---|
| **CPU** | AMD EPYC 7B13 (1 socket, 32 cores / 64 threads, x86_64) |
| **Cache** | 1 MiB L1d, 1 MiB L1i, 16 MiB L2, 128 MiB L3 |
| **Memory** | 117 GiB RAM |
| **OS / Kernel** | Debian GNU/Linux, Linux 7.1.6 (`x86_64`, `SMP PREEMPT_DYNAMIC`) |
| **Runtimes** | `EPython 0.1.0` (Erlang/OTP 29, ERTS 17.0.4), `CPython 3.13.18`, `CPython 3.13.6t` (free-threaded), `RustPython 0.6.0` (`rustc 1.100.0`) |
| **Kernel TCP** | `somaxconn=4096`, `tcp_max_syn_backlog=4096`, `ip_local_port_range=1024 65535`, `tcp_tw_reuse=1` |

### `/ping` (keep-alive echo)

| Interpreter | Req/s | Avg | p50 | p99 | Max | Steady CPU/req (user / sys / total) |
|---|--:|--:|--:|--:|--:|--:|
| **CPython 3.13.18 (GIL)** | 201,127 | 49.16ms | 47.15ms | 73.99ms | 145.43ms | 3.76 / 16.12 / 19.88 µs |
| **CPython 3.13.6t (FT)** | 190,347 | 51.81ms | 49.45ms | 84.84ms | 156.45ms | 4.66 / 16.34 / 21.01 µs |
| **EPython 0.1.0** | 179,953 | 58.87ms | 49.62ms | 289.21ms | 619.38ms | 3.87 / 16.18 / 20.05 µs |
| **RustPython 0.6.0** | 131,374 | 75.29ms | 72.01ms | 110.44ms | 191.65ms | 12.16 / 18.26 / 30.42 µs |

### `/hold` (50ms min-heap timer per request)

| Interpreter | Req/s | Avg | p50 | p99 | Max | Steady CPU/req (user / sys / total) |
|---|--:|--:|--:|--:|--:|--:|
| **CPython 3.13.18 (GIL)** | 146,768 | 67.39ms | 65.41ms | 93.80ms | 138.32ms | 5.26 / 21.99 / 27.25 µs |
| **CPython 3.13.6t (FT)** | 141,142 | 70.07ms | 67.98ms | 97.01ms | 128.75ms | 6.32 / 22.00 / 28.33 µs |
| **EPython 0.1.0** | 125,030 | 84.16ms | 73.07ms | 408.86ms | 712.16ms | 7.09 / 23.91 / 31.00 µs |
| **RustPython 0.6.0** | 99,546 | 99.41ms | 94.26ms | 155.02ms | 213.40ms | 16.57 / 23.54 / 40.12 µs |
