"""Parse a markdown talk plan into timed sections.

Format:
    # Objective: what the audience must walk away with
    ## Hook (1.5 min)
    - point
    - point
    ## Demo (3 min) [must]
"""

import re
from dataclasses import dataclass, field

SECTION_RE = re.compile(r"^##\s+(.+?)\s*\((\d+(?:\.\d+)?)\s*min\)\s*(\[must\])?\s*$", re.I)


@dataclass
class Section:
    title: str
    minutes: float
    must: bool
    points: list[str] = field(default_factory=list)
    start_s: float = 0.0  # planned offset from talk start

    @property
    def end_s(self) -> float:
        return self.start_s + self.minutes * 60


@dataclass
class Plan:
    objective: str
    sections: list[Section]

    @property
    def total_s(self) -> float:
        return sum(s.minutes for s in self.sections) * 60

    def planned_section_at(self, elapsed_s: float) -> int:
        for i, s in enumerate(self.sections):
            if elapsed_s < s.end_s:
                return i
        return len(self.sections) - 1

    def as_prompt(self) -> str:
        lines = [f"OBJECTIVE: {self.objective}"]
        for i, s in enumerate(self.sections):
            tag = " [MUST COVER]" if s.must else ""
            lines.append(
                f"[{i}] {s.title}{tag}, {s.minutes:g} min "
                f"(planned {fmt(s.start_s)} to {fmt(s.end_s)})"
            )
            lines += [f"    - {p}" for p in s.points]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "objective": self.objective,
            "total_s": self.total_s,
            "sections": [
                {
                    "title": s.title,
                    "minutes": s.minutes,
                    "must": s.must,
                    "points": s.points,
                    "start_s": s.start_s,
                    "end_s": s.end_s,
                }
                for s in self.sections
            ],
        }


def fmt(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def parse_plan(md: str) -> Plan:
    objective = ""
    sections: list[Section] = []
    for raw in md.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("# ") and not line.startswith("## "):
            objective = re.sub(r"^#\s*(objective:)?\s*", "", line, flags=re.I)
        elif m := SECTION_RE.match(line):
            sections.append(Section(m[1], float(m[2]), bool(m[3])))
        elif line.startswith(("-", "*")) and sections:
            sections[-1].points.append(line.lstrip("-* ").strip())
    if not sections:
        raise ValueError("No sections found. Use headings like '## Intro (2 min)'.")
    t = 0.0
    for s in sections:
        s.start_s = t
        t = s.end_s
    return Plan(objective or "(none given)", sections)
