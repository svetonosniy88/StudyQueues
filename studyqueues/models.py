from dataclasses import dataclass, field


@dataclass
class Task:
    id: str
    text: str
    done: bool
    line: int
    parent_id: str | None = None
    children: list["Task"] = field(default_factory=list)

    @property
    def completed(self):
        return self.done or bool(self.children and all(t.done for t in self.children))


@dataclass
class Completion:
    changes: list[dict]

    @property
    def ids(self):
        return [c["id"] for c in self.changes]
