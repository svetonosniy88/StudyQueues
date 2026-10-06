"""A bounded overview of roots; the Markdown queue itself is never truncated."""
from dataclasses import dataclass

PAGE_SIZE = 15


@dataclass
class QueueWindow:
    anchor: str | None = None
    start: int = 0
    history_page: int = 0

    def resolve(self, tasks, show_done=False):
        pending = [task for task in tasks if not task.completed]
        positions = {task.id: index for index, task in enumerate(tasks)}
        if self.anchor in positions:
            position = positions[self.anchor]
            # A completed anchor moves forward, filling the vacated space.
            self.start = next((i for i, task in enumerate(pending)
                               if positions[task.id] >= position), len(pending))
        self.start = min(self.start, max(0, len(pending) - 1))
        upcoming = pending[self.start:self.start + PAGE_SIZE]
        self.anchor = upcoming[0].id if upcoming and self.start else None
        end = self.start + len(upcoming)
        boundary = positions[pending[end].id] if end < len(pending) else len(tasks)
        past = [task for task in tasks[:boundary] if task.completed]
        self.history_page = min(self.history_page, max(0, (len(past) - 1) // PAGE_SIZE))
        history_end = len(past) - self.history_page * PAGE_SIZE
        history = past[max(0, history_end - PAGE_SIZE):history_end] if show_done else []
        visible = sorted([*upcoming, *history], key=lambda task: positions[task.id])
        return visible, pending, past

    def move(self, tasks, direction):
        _, pending, _ = self.resolve(tasks)
        self.start = max(0, min(self.start + direction * PAGE_SIZE,
                               ((len(pending) - 1) // PAGE_SIZE) * PAGE_SIZE))
        self.anchor = pending[self.start].id if pending and self.start else None
        self.history_page = 0
