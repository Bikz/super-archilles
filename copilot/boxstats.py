"""What the GB10 is doing right now: GPU, memory, network out, internet, loaded models.

Feeds the box view on the GB10's own monitor, which doubles as the proof that the
talk is processed on this machine: the network-out counter sits near zero and
coaching keeps going when the internet is gone.
"""

import glob
import os
import re
import socket
import subprocess
import time
from pathlib import Path

import httpx

from .coach import LLM_URL

# physical uplinks only; tailscale0 rides on top of these so counting it would double up
UPLINK = re.compile(r"^(wl|en|eth)")


class BoxStats:
    def __init__(self):
        self._last_net: tuple[float, int, int] | None = None
        self._online = True
        self._online_checked = 0.0
        self._models: list[dict] | None = None
        self._models_checked = 0.0

    def snapshot(self) -> dict:
        out: dict = {"ts": time.time()}
        out.update(self._gpu())
        out.update(self._mem())
        out.update(self._net())
        out["online"] = self._internet()
        out["models"] = self._loaded_models()
        return out

    def _gpu(self) -> dict:
        try:
            q = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu,power.draw,temperature.gpu,clocks.sm",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=3,
            ).stdout.strip().split(", ")
            return {"gpu_util": float(q[0]), "power_w": float(q[1]),
                    "temp_c": float(q[2]), "sm_mhz": float(q[3])}
        except Exception:
            return {}

    def _mem(self) -> dict:
        mem = {
            line.split(":")[0]: int(line.split()[1])
            for line in Path("/proc/meminfo").read_text().splitlines()
        }
        gib = 1024 * 1024  # /proc/meminfo is in KiB; report GiB, which is what "128 GB" means
        return {
            "mem_used_gb": round((mem["MemTotal"] - mem["MemAvailable"]) / gib, 1),
            "mem_total_gb": round(mem["MemTotal"] / gib, 1),
        }

    def _net(self) -> dict:
        rx = tx = 0
        up = []
        for dev in os.listdir("/sys/class/net"):
            if not UPLINK.match(dev):
                continue
            base = f"/sys/class/net/{dev}"
            if Path(f"{base}/operstate").read_text().strip() == "up":
                up.append(dev)
            rx += int(Path(f"{base}/statistics/rx_bytes").read_text())
            tx += int(Path(f"{base}/statistics/tx_bytes").read_text())
        now = time.monotonic()
        rates = {"net_rx_bps": 0.0, "net_tx_bps": 0.0}
        if self._last_net:
            t, r0, t0 = self._last_net
            dt = max(now - t, 1e-3)
            rates = {"net_rx_bps": (rx - r0) / dt, "net_tx_bps": (tx - t0) / dt}
        self._last_net = (now, rx, tx)
        return {**rates, "uplinks": up}

    def _internet(self) -> bool:
        if time.monotonic() - self._online_checked > 3:
            self._online_checked = time.monotonic()
            try:
                socket.create_connection(("1.1.1.1", 443), timeout=1.5).close()
                self._online = True
            except OSError:
                self._online = False
        return self._online

    def _loaded_models(self) -> list[dict]:
        if self._models is not None and time.monotonic() - self._models_checked < 30:
            return self._models
        self._models_checked = time.monotonic()
        base = LLM_URL.removesuffix("/v1")
        # Behind the priority gate there can be several llama-servers; list each one.
        roles = {"Reasoning (LLM)": base}
        try:
            ups = httpx.get(f"{base}/gate/stats", timeout=2).json()["upstreams"]
            roles = {"Coach + director (main)": ups["main"]}
            if ups.get("small") and ups["small"] != ups["main"]:
                roles["Background workers (small)"] = ups["small"]
        except Exception:
            pass
        models = []
        for role, url in roles.items():
            try:
                path = httpx.get(f"{url}/props", timeout=2).json().get("model_path", "")
                shards = glob.glob(re.sub(r"-\d{5}-of-\d{5}\.gguf$", "-*-of-*.gguf", path)) or [path]
                extra = glob.glob(str(Path(path).parent / "mmproj-*.gguf")) + glob.glob(str(Path(path).parent / "*LiLiCorr*.gguf"))
                gb = sum(os.path.getsize(p) for p in shards + extra) / 1e9
                models.append({"role": role, "name": pretty(Path(path).name), "gb": round(gb, 1),
                               "where": url})
            except Exception:
                models.append({"role": role, "name": "not running", "gb": 0, "where": url})
        models.append({"role": "Speech to text", "name": "NVIDIA Nemotron 3.5 ASR streaming 0.6B",
                       "gb": 2.5, "where": "in-process, CUDA"})
        # The image model lives inside the image worker process, not behind an HTTP server.
        if subprocess.run(["pgrep", "-f", "^/home/[^ ]+/python -m copilot.workers.image"], capture_output=True).returncode == 0:
            models.append({"role": "Image generation", "name": "Z-Image-Turbo 6B (bf16)",
                           "gb": 20.0, "where": "image worker, CUDA"})
        self._models = models
        return models


def pretty(filename: str) -> str:
    name = re.sub(r"(-\d{5}-of-\d{5})?\.gguf$", "", filename).removeprefix("NVIDIA-")
    return name.replace("-UD-", " ").replace("-Q", " Q").replace("-IQ", " IQ").replace("-", " ")
