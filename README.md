# C10K with Python threads

[`server.py`](server.py): one thread per connection, blocking sockets.

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
ulimit -n 65536 && epython server.py 8000

# terminal 2
ulimit -n 65536 && wrk -t$(( $(getconf _NPROCESSORS_ONLN) / 4 )) -c10000 -d30s --latency http://127.0.0.1:8000/hold


ulimit -n 65536 && wrk -t$(( $(getconf _NPROCESSORS_ONLN) / 4 )) -c10000 -d30s --latency http://127.0.0.1:8000/ping
```

`Socket errors: connect 0` means all 10,000 connections were open.
