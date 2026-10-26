"""Image worker: prepares an illustration for every plan section with Z-Image-Turbo, on the GB10.

As soon as a plan is loaded on the dashboard (a "prepare" event) or a talk starts, the small
LLM writes one image prompt per section and the diffusion model draws it (~14 s each at
1344x768). Files are cached by section text (base.prep_image), so a plan prepared once is
instant next time. The instant worker puts them on the section slides.

Diffusion can't be preempted by the priority gate, so while a talk is running each image is
started right after a coach verdict: that's the start of the gap before the next check-in.
"""

import asyncio
import logging
import os
import time
from pathlib import Path

import base64
import io

import httpx

from .base import COPILOT_URL, Talk, Worker, prep_image, section_key

log = logging.getLogger("worker")

MODEL_DIR = os.environ.get("ZIMAGE_DIR", str(Path.home() / "models" / "z-image-turbo"))
WIDTH, HEIGHT = 1344, 768  # 16:9-ish, multiples of 64

PROMPT = """Write ONE image prompt to illustrate a section of a talk on a presentation slide.

The image sits next to the section title, so it should make the idea concrete and memorable:
a scene, an object or a metaphor. Never a chart, diagram, screenshot or slide. No readable text,
letters, logos or captions anywhere. No faces of real people. Photographic or clean 3D render,
cinematic lighting, wide 16:9 composition, calm dark background that suits a dark slide.

Reply with ONLY JSON: {"prompt": "<40 to 70 words>"}"""


class Images(Worker):
    name = "image"
    every_s = 3600  # no live round; all work is section prep

    def __init__(self):
        super().__init__()
        self.pipe = None
        self.queue: list[dict] = []
        self.plan_sections: list[dict] = []  # the plan currently being prepared, for progress
        self.objective = ""
        self.wake = asyncio.Event()
        self.verdict = asyncio.Event()

    async def produce(self, talk: Talk) -> list:
        return []

    async def _main(self) -> None:
        asyncio.create_task(self._prep_loop())
        await super()._main()

    async def _event(self, ev: dict) -> None:
        await super()._event(ev)
        t = ev.get("type")
        if t in ("prepare", "start") and ev.get("plan"):
            self.plan_sections = ev["plan"].get("sections") or []
            self.objective = ev["plan"].get("objective", "")
            fresh = bool(ev.get("fresh"))
            has_deck = lambda s: prep_image(s).with_name(prep_image(s).name.replace("prep-", "deck-")).exists()
            todo = [s for s in self.plan_sections if not has_deck(s) and (fresh or not prep_image(s).exists())]
            for s in todo:
                s["_fresh"] = fresh
            self.queue = todo  # newest plan wins; anything already drawn is cached on disk
            self.wake.set()
            await self._progress("plan received" if todo else "all section images already prepared")
        elif t == "verdict":
            self.verdict.set()

    async def _progress(self, text: str) -> None:
        total = len(self.plan_sections)
        deck = lambda s: prep_image(s).with_name(prep_image(s).name.replace("prep-", "deck-")).exists()
        ready = sum(prep_image(s).exists() or deck(s) for s in self.plan_sections)  # the speaker's own slide counts
        try:
            await self.http.post(f"{COPILOT_URL}/prep/progress",
                                 json={"text": text, "ready": ready, "total": total})
        except Exception:
            pass

    def _load(self):
        import torch
        from diffusers import ZImagePipeline

        t0 = time.monotonic()
        pipe = ZImagePipeline.from_pretrained(MODEL_DIR, torch_dtype=torch.bfloat16, low_cpu_mem_usage=False)
        pipe.to("cuda")
        log.info("Z-Image loaded in %.0fs", time.monotonic() - t0)
        return pipe

    def _frame(self, latents, step: int, steps: int, sec: dict) -> None:
        """Decode the half-denoised latents to a small preview and send it to the speaker view."""
        import torch

        try:
            vae = self.pipe.vae
            with torch.inference_mode():
                # quarter-resolution latents decode ~16x faster; plenty for a preview
                lat = torch.nn.functional.interpolate(latents.float(), scale_factor=0.25, mode="bilinear")
                lat = lat.to(vae.dtype) / vae.config.scaling_factor + vae.config.shift_factor
                img = vae.decode(lat, return_dict=False)[0]
                img = self.pipe.image_processor.postprocess(img, output_type="pil")[0]
            img.thumbnail((448, 256))
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=70)
            httpx.post(f"{COPILOT_URL}/prep/frame", timeout=3, json={
                "key": section_key(sec), "step": step, "steps": steps, "title": sec["title"],
                "image": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()})
        except Exception as e:  # previews are decoration; never break the drawing
            log.info("preview frame skipped: %s", e)

    def _draw(self, prompt: str, path: Path, sec: dict | None = None) -> float:
        import torch

        steps = 9
        t0 = time.monotonic()

        def on_step(pipe, i, t, kw):
            if sec is not None and i < steps - 1 and i % 2 == 1:  # every other step keeps drawing fast
                self._frame(kw["latents"], i + 1, steps, sec)
            return kw

        with torch.inference_mode():
            image = self.pipe(prompt=prompt, height=HEIGHT, width=WIDTH, num_inference_steps=steps,
                              guidance_scale=0.0, callback_on_step_end=on_step,
                              generator=torch.Generator("cuda").manual_seed(int(time.time()))).images[0]
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp.jpg")
        image.save(tmp, quality=88)
        tmp.rename(path)  # appear atomically, so the instant worker never reads half a file
        return time.monotonic() - t0

    async def _prep_loop(self) -> None:
        self.pipe = await asyncio.to_thread(self._load)
        while True:
            if not self.queue:
                self.wake.clear()
                await self.wake.wait()
                continue
            sec = self.queue.pop(0)
            path = prep_image(sec)
            if path.exists() and not sec.get("_fresh"):
                continue
            try:
                d = await self.llm_json(
                    f"TALK OBJECTIVE: {self.objective}\n"
                    f"SECTION: {sec['title']}\nPOINTS:\n" + "\n".join(f"- {p}" for p in sec.get("points") or []),
                    PROMPT, model="small", max_tokens=200, temperature=0.6)
                prompt = str(d.get("prompt") or "").strip()
                if len(prompt) < 20:
                    prompt = f"A calm cinematic photograph that evokes: {sec['title']}. Dark background, no text."
                if self.talk.running:  # draw in the gap right after a coach check-in
                    self.verdict.clear()
                    try:
                        await asyncio.wait_for(self.verdict.wait(), timeout=10)
                    except TimeoutError:
                        pass
                secs = await asyncio.to_thread(self._draw, prompt + " No text, no letters.", path, sec)
                log.info("prepared '%s' in %.1fs", sec["title"], secs)
                await self._progress(f"drew {sec['title']} in {secs:.0f} s")
            except Exception:
                log.exception("prep failed for %s", sec.get("title"))


if __name__ == "__main__":
    Images().run()
