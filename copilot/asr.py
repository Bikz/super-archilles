"""Live speech-to-text on the GB10 with NVIDIA nemotron-3.5-asr-streaming-0.6b.

Audio arrives as 16 kHz float32 frames (from the browser mic). In streaming mode
the model runs one long cache-aware `generate` call fed by a generator that
blocks on the growing audio buffer, so each 80 ms frame is encoded exactly once.
`chunked` mode is a fallback that transcribes independent 4 s windows.
"""

import os
import threading
import time
from collections.abc import Callable

import numpy as np
import torch
from transformers import AutoModelForRNNT, AutoProcessor, TextIteratorStreamer

MODEL_ID = "nvidia/nemotron-3.5-asr-streaming-0.6b"
LANGUAGE = os.environ.get("ASR_LANGUAGE", "en-US")
MODE = os.environ.get("ASR_MODE", "streaming")
DEVICE = os.environ.get("ASR_DEVICE", "cuda" if torch.cuda.is_available() else "cpu")
CHUNK_S = 4.0


class LiveASR:
    def __init__(self, on_text: Callable[[str], None]):
        self.on_text = on_text
        self.processor = AutoProcessor.from_pretrained(MODEL_ID)
        self.model = AutoModelForRNNT.from_pretrained(MODEL_ID).to(DEVICE).eval()
        self.sr = self.processor.feature_extractor.sampling_rate
        self.processor.set_num_lookahead_tokens(6)
        self._lock = threading.Lock()
        self._chunks: list[np.ndarray] = []
        self._audio = np.zeros(0, dtype=np.float32)
        self._running = False
        self._threads: list[threading.Thread] = []

    @property
    def latency_ms(self) -> float:
        return self.processor.streaming_latency_ms

    def feed(self, pcm: np.ndarray) -> None:
        with self._lock:
            self._chunks.append(pcm.astype(np.float32, copy=False))

    def _available(self) -> np.ndarray:
        with self._lock:
            if self._chunks:
                self._audio = np.concatenate([self._audio, *self._chunks])
                self._chunks.clear()
            return self._audio

    def _wait_for(self, n: int) -> np.ndarray | None:
        while self._running:
            audio = self._available()
            if audio.shape[0] >= n:
                return audio
            time.sleep(0.02)
        return None

    def start(self) -> None:
        self.stop()
        with self._lock:
            self._chunks.clear()
            self._audio = np.zeros(0, dtype=np.float32)
        self._running = True
        target = self._run_streaming if MODE == "streaming" else self._run_chunked
        self._threads = [threading.Thread(target=target, daemon=True)]
        self._threads[0].start()

    def stop(self) -> None:
        self._running = False
        for t in self._threads:
            t.join(timeout=5)
        self._threads = []

    def _inputs(self, audio: np.ndarray, first: bool):
        inputs = self.processor(
            audio,
            sampling_rate=self.sr,
            is_streaming=True,
            is_first_audio_chunk=first,
            language=LANGUAGE,
            return_tensors="pt",
        )
        return inputs.to(self.model.device, dtype=self.model.dtype)

    def _run_streaming(self) -> None:
        p = self.processor
        while self._running:  # restart the stream if generate ever returns
            audio = self._wait_for(p.num_samples_first_audio_chunk)
            if audio is None:
                return
            # each restart begins from the newest audio, not from the talk start
            base = audio.shape[0] - p.num_samples_first_audio_chunk
            first = self._inputs(audio[base : base + p.num_samples_first_audio_chunk], True)

            def features():
                yield first.input_features[:, : p.num_mel_frames_first_audio_chunk, :]
                hop, n_fft = p.feature_extractor.hop_length, p.feature_extractor.n_fft
                mel_idx = p.num_mel_frames_first_audio_chunk
                while True:
                    start = base + mel_idx * hop - n_fft // 2
                    end = start + p.num_samples_per_audio_chunk
                    buf = self._wait_for(end)
                    if buf is None:
                        return
                    yield self._inputs(buf[start:end], False).input_features
                    mel_idx += p.num_mel_frames_per_audio_chunk

            streamer = TextIteratorStreamer(p.tokenizer, skip_special_tokens=True)
            gen = threading.Thread(
                target=self._generate,
                kwargs={**first, "input_features": features(), "streamer": streamer},
                daemon=True,
            )
            gen.start()
            for text in streamer:
                if text.strip():
                    self.on_text(text)
            gen.join()

    def _generate(self, **kwargs) -> None:
        with torch.inference_mode():
            self.model.generate(**kwargs, max_new_tokens=1_000_000)

    def _run_chunked(self) -> None:
        n = int(CHUNK_S * self.sr)
        pos = 0
        while (audio := self._wait_for(pos + n)) is not None:
            window = audio[pos : pos + n]
            pos += n
            inputs = self.processor(
                window, sampling_rate=self.sr, language=LANGUAGE, return_tensors="pt"
            ).to(self.model.device, dtype=self.model.dtype)
            with torch.inference_mode():
                out = self.model.generate(**inputs)
            text = self.processor.batch_decode(out, skip_special_tokens=True)[0]
            if text.strip():
                self.on_text(" " + text.strip())
