"""Tiny stand-in for the copilot's debrief endpoints, to check copilot/static/debrief.html offline.

    python3 scripts/debrief_mock.py [port]     then open http://127.0.0.1:8099/debrief

Serves two synthetic "pitch" reports (the newer has every debrief field; the older is the
"vs last run") plus an old-format report. keep/drop and POST /plans only touch memory.
"""

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "copilot"))
from plan import parse_plan  # noqa: E402

PLAN = parse_plan((ROOT / "plans" / "pitch.md").read_text()).to_dict() if (ROOT / "plans" / "pitch.md").exists() else None


def pitch(sid, started, times, scores, nudges, library):
    secs = PLAN["sections"]
    return {
        "id": sid, "name": "pitch", "started_at": started, "duration_s": sum(times), "planned_s": PLAN["total_s"],
        "plan": PLAN, "nudges": nudges, "covered": [], "library": library,
        "transcript": "hi everyone",
        "transcript_timed": [[2.1, "Hi everyone. Every speaker drifts and runs over."], [31.0, "Who pays for this?"],
                             [80.4, "Let me show you the live demo."], [190.2, "On the GB10, 128 gigabytes."]],
        "timeline": [{"t": 15.0, "section": 0, "status": "on_track", "reason": "Hook landing", "nudge": None},
                     {"t": 45.0, "section": 1, "status": "drifting", "reason": "Long tangent on pricing",
                      "nudge": "Finish the problem. Demo next."},
                     {"t": 120.0, "section": 2, "status": "behind", "reason": "Demo running long", "nudge": None},
                     {"t": 200.0, "section": 3, "status": "wrapping_up", "reason": "Hardware story", "nudge": None}],
        "section_time_s": times,
        "coverage": {"points": [{"section": si, "point": pi, "score": scores[si][pi],
                                 "evidence": f"we said something about {p[:30]}" if scores[si][pi] else ""}
                                for si, s in enumerate(secs) for pi, p in enumerate(s["points"])],
                     "summary": "Thin on the close: the pilot ask never landed."},
        "cards": [{"id": "c1", "kind": "number", "title": "128 GB unified memory", "source": "plan", "status": "shown",
                   "at_s": 192.0, "reason": "number spoken"},
                  {"id": "c2", "kind": "section", "title": "Live demo", "source": "prep", "status": "dismissed",
                   "at_s": 80.0, "reason": None}],
        "debrief": "Done. 4:31 against 4:00 planned.\nNext time: shorter problem section.",
    }


REPORTS = {}
if PLAN:
    REPORTS["20261025-170000-pitch"] = pitch(
        "20261025-170000-pitch", "2026-10-25 16:56", [28.0, 74.0, 112.0, 60.0, 0.0],
        [[3, 2], [3, 1], [2, 2], [3, 1], [0, 0]],
        [[45.0, "Finish the problem. Demo next."], [150.0, "Two minutes left, go to the GB10 story."]],
        [{"title": "Cloud latency vs local", "explanation": "3 seconds per answer in the cloud vs under 1 here.", "at_s": 52.0},
         {"title": "Wifi off demo", "explanation": "Pull the cable and it keeps working.", "at_s": 140.0, "keep": True}])
    REPORTS["20261025-160000-pitch"] = pitch(
        "20261025-160000-pitch", "2026-10-25 15:56", [35.0, 60.0, 95.0, 50.0, 30.0],
        [[2, 2], [2, 1], [2, 1], [2, 1], [1, 0]], [[60.0, "Demo now."]], [])
REPORTS["20261025-150000-old talk"] = {
    "name": "old talk", "started_at": "2026-10-25 14:58", "duration_s": 30.9, "planned_s": 240.0, "plan": PLAN,
    "nudges": [[22.4, "Finish the problem. Demo next."]], "covered": PLAN["sections"][0]["points"] if PLAN else [],
    "library": [], "transcript": "Hi everyone.", "debrief": "Done. 0:30 against 4:00 planned."}


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = unquote(self.path.split("?")[0])
        if path == "/debrief":
            return self._send(200, (ROOT / "copilot/static/debrief.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/sessions":
            return self._send(200, [{"id": k, "name": r["name"], "started_at": r["started_at"], "duration_s": r["duration_s"],
                                     "planned_s": r["planned_s"], "nudges": len(r["nudges"])}
                                    for k, r in sorted(REPORTS.items(), reverse=True)])
        if path.startswith("/sessions/") and path[10:] in REPORTS:
            return self._send(200, REPORTS[path[10:]])
        self._send(404, {"detail": "not found"})

    def do_POST(self):
        path = unquote(self.path)
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if m := re.match(r"^/sessions/(.+)/library/(\d+)$", path):
            r = REPORTS.get(m[1])
            if not r or int(m[2]) >= len(r["library"]):
                return self._send(404, {"detail": "no such library entry"})
            r["library"][int(m[2])]["keep"] = bool(body.get("keep"))
            return self._send(200, r["library"][int(m[2])])
        if path == "/plans":
            try:
                parse_plan(body["markdown"])
            except ValueError as e:
                return self._send(400, {"detail": str(e)})
            print("--- saved plan", body["name"], "\n" + body["markdown"])
            return self._send(200, {"ok": True, "name": re.sub(r"[^a-z0-9-]+", "-", body["name"].lower()).strip("-")})
        self._send(404, {"detail": "not found"})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    print(f"http://127.0.0.1:{port}/debrief")
    HTTPServer(("127.0.0.1", port), H).serve_forever()
