"""Resumable parallel HTTP downloader for big Hugging Face files.

usage: pdl.py <repo> <dest_dir> <file> [<file> ...]
Splits each file into 64 MB pieces fetched over N connections with Range requests,
records finished pieces in <file>.pieces so a restart only fetches what's missing.
"""

import concurrent.futures as cf
import os
import sys
import threading
import time

import httpx

PIECE = 64 * 1024 * 1024
CONNS = int(os.environ.get("CONNS", "16"))
repo, dest = sys.argv[1], sys.argv[2]
files = sys.argv[3:]
lock = threading.Lock()
done_bytes = 0


_api_sizes: dict[str, int] = {}


def size_of(client: httpx.Client, url: str) -> int:
    # Small non-LFS files don't send content-length on HEAD; the hub API knows every size.
    if not _api_sizes:
        r = client.get(f"https://huggingface.co/api/models/{repo}?blobs=true")
        r.raise_for_status()
        _api_sizes.update({f["rfilename"]: f.get("size") or 0 for f in r.json()["siblings"]})
    return _api_sizes[url.split("/resolve/main/", 1)[1]]


def fetch(client: httpx.Client, url: str, fd: int, start: int, end: int) -> None:
    global done_bytes
    for attempt in range(20):
        try:
            pos = start
            with client.stream("GET", url, headers={"Range": f"bytes={pos}-{end}"},
                               follow_redirects=True, timeout=httpx.Timeout(30, read=60)) as r:
                if r.status_code != 206:
                    raise RuntimeError(f"HTTP {r.status_code}")
                for chunk in r.iter_bytes(1 << 20):
                    os.pwrite(fd, chunk, pos)
                    pos += len(chunk)
                    with lock:
                        done_bytes += len(chunk)
            if pos != end + 1:
                raise RuntimeError("short read")
            return
        except Exception as e:  # noqa: BLE001
            with lock:
                done_bytes -= pos - start
            print(f"retry {start}: {e}", flush=True)
            time.sleep(min(30, 2 ** attempt))
    raise RuntimeError(f"gave up on piece {start}")


def main() -> None:
    os.makedirs(dest, exist_ok=True)
    client = httpx.Client(http2=False, limits=httpx.Limits(max_connections=CONNS * 2))
    jobs = []
    total = 0
    for name in files:
        url = f"https://huggingface.co/{repo}/resolve/main/{name}"
        out = os.path.join(dest, name)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        size = size_of(client, url)
        total += size
        if os.path.exists(out + ".done"):
            print(f"have {name}", flush=True)
            continue
        fd = os.open(out, os.O_RDWR | os.O_CREAT)
        os.ftruncate(fd, size)
        have = set()
        if os.path.exists(out + ".pieces"):
            have = {int(x) for x in open(out + ".pieces").read().split()}
        jobs.append((name, url, out, fd, size, have))

    global done_bytes
    done_bytes = sum(len(h) * PIECE for *_, h in jobs)
    t0, b0 = time.monotonic(), done_bytes
    remaining = sum(s for *_, s, _ in jobs)

    def report():
        while True:
            time.sleep(15)
            rate = (done_bytes - b0) / (time.monotonic() - t0) / 1e6
            print(f"{done_bytes / 1e9:6.1f} / {remaining / 1e9:.1f} GB  {rate:5.1f} MB/s", flush=True)

    threading.Thread(target=report, daemon=True).start()
    with cf.ThreadPoolExecutor(CONNS) as pool:
        futs = {}
        for name, url, out, fd, size, have in jobs:
            for start in range(0, size, PIECE):
                if start in have:
                    continue
                f = pool.submit(fetch, client, url, fd, start, min(start + PIECE, size) - 1)
                futs[f] = (out, start)
        for f in cf.as_completed(futs):
            f.result()
            out, start = futs[f]
            with lock, open(out + ".pieces", "a") as p:
                p.write(f"{start}\n")
    for name, url, out, fd, size, have in jobs:
        os.fsync(fd)
        os.close(fd)
        if os.path.exists(out + ".pieces"):
            os.remove(out + ".pieces")
        open(out + ".done", "w").close()
        print(f"done {name}", flush=True)


main()
