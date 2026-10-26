"""Feed a sample clip through LiveASR at real-time pace and print what comes back."""

import sys
import time
import urllib.request

import librosa

from copilot.asr import LiveASR

URL = "https://huggingface.co/datasets/Narsil/asr_dummy/resolve/main/1.flac"
path = "/tmp/asr_sample.flac"
urllib.request.urlretrieve(URL, path)

t0 = time.monotonic()
asr = LiveASR(lambda t: print(f"[{time.monotonic() - t0:5.1f}s] {t!r}", flush=True))
print(f"loaded in {time.monotonic() - t0:.1f}s, streaming latency {asr.latency_ms} ms", flush=True)
audio, _ = librosa.load(path, sr=asr.sr)
asr.start()
t0 = time.monotonic()
step = int(0.1 * asr.sr)
for i in range(0, len(audio), step):  # 100 ms frames, like the browser sends
    asr.feed(audio[i : i + step])
    time.sleep(0.1)
asr.feed(audio[: asr.sr * 2] * 0)  # 2 s of trailing silence to flush
time.sleep(4)
asr.stop()
print("done", file=sys.stderr)
