from datetime import datetime, timedelta, time
from uuid import uuid4
import time as clock_module


def local_now():
    return datetime.now().astimezone()


class Session:
    def __init__(self, queue, path, minutes, clock=clock_module.monotonic, now=local_now):
        self.id = uuid4().hex
        self.queue = queue
        self.path = str(path)
        self.planned_seconds = float(minutes * 60)
        self.active_seconds = 0.0
        self.started_at = now().isoformat()
        self.ended_at = None
        self.intervals = []
        self.completed = {}
        self.last_completion = None
        self.pending = None
        self.view_task_id = None
        self.view_task_position = None
        self.at_queue_end = False
        self.running = True
        self.clock, self.now = clock, now
        self.last_tick = clock()
        self.last_wall = now()
        self._merge = False

    def tick(self):
        mono, wall = self.clock(), self.now()
        delta = mono - self.last_tick
        slept = False
        if self.running:
            if delta > 10 or (wall - self.last_wall).total_seconds() > 10:
                self.running = False
                self._merge = False
                slept = True
            elif delta > 0:
                self.active_seconds += delta
                start = self.last_wall
                # Correct duration follows monotonic time even after wall-clock edits.
                if wall <= start:
                    start = wall - timedelta(seconds=delta)
                    self._merge = False
                interval = {"start": start.isoformat(), "end": wall.isoformat(), "seconds": delta}
                if self._merge and self.intervals and self.intervals[-1]["end"] == interval["start"]:
                    self.intervals[-1]["end"] = interval["end"]
                    self.intervals[-1]["seconds"] += delta
                else:
                    self.intervals.append(interval)
                self._merge = True
        self.last_tick, self.last_wall = mono, wall
        return slept

    def pause(self):
        self.tick()
        self.running = False
        self._merge = False

    def resume(self):
        if self.ended_at:
            raise ValueError("Занятие уже завершено")
        self.last_tick, self.last_wall = self.clock(), self.now()
        self.running = True
        self._merge = False

    def extend(self, minutes):
        if minutes <= 0:
            raise ValueError("Время должно быть положительным")
        self.tick()
        self.planned_seconds += minutes * 60

    @property
    def remaining(self):
        return self.planned_seconds - self.active_seconds

    def register(self, completion):
        self.last_completion = completion
        for c in completion.changes:
            self.completed[c["id"]] = {"text": c["text"], "parent_id": c["parent_id"]}
        self.pending = None

    def undo_registered(self):
        if self.last_completion:
            for c in self.last_completion.changes:
                self.completed.pop(c["id"], None)
        self.last_completion = None

    @property
    def counts(self):
        roots = sum(c["parent_id"] is None for c in self.completed.values())
        return roots, len(self.completed) - roots

    def finish(self):
        self.pause()
        self.ended_at = self.now().isoformat()
        self.last_completion = None

    def to_dict(self):
        return {k: getattr(self, k) for k in ("id", "queue", "path", "planned_seconds", "active_seconds",
                "started_at", "ended_at", "intervals", "completed", "pending", "view_task_id", "view_task_position", "at_queue_end")}

    @classmethod
    def restore(cls, record, clock=clock_module.monotonic, now=local_now):
        from .state_store import StateStore
        StateStore.validate_session(record)
        instance = cls(record["queue"], record["path"], record["planned_seconds"] / 60, clock, now)
        for key in instance.to_dict():
            if key in record:
                setattr(instance, key, record[key])
        instance.running = False
        return instance


def daily_seconds(records):
    """Allocate monotonic durations proportionally across local calendar midnights."""
    totals = {}
    for record in records:
        queue = record["queue"]
        for interval in record.get("intervals", []):
            start, end = datetime.fromisoformat(interval["start"]), datetime.fromisoformat(interval["end"])
            span = (end - start).total_seconds()
            if span <= 0:
                key = (start.date().isoformat(), queue)
                totals[key] = totals.get(key, 0) + interval["seconds"]
                continue
            current = start
            while current < end:
                midnight = datetime.combine(current.date() + timedelta(days=1), time.min, current.tzinfo)
                stop = min(end, midnight)
                seconds = interval["seconds"] * (stop - current).total_seconds() / span
                key = (current.date().isoformat(), queue)
                totals[key] = totals.get(key, 0) + seconds
                current = stop
    return totals
