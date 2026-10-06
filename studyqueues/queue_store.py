"""Small, preserving editor for a deliberately restricted Markdown task format."""
from pathlib import Path
from datetime import datetime
import hashlib
import os
import re
import tempfile
from uuid import uuid4

from .models import Task, Completion


class QueueError(Exception):
    pass


class ConflictError(QueueError):
    pass


TASK = re.compile(r"^([ \t]*)- \[([ xX])\] (.*?)(\r?\n)?$")
IDENTITY = re.compile(r"\s*<!-- task:([A-Za-z0-9_-]+) -->\s*$")


class QueueStore:
    def __init__(self, path, backup_dir):
        self.path = Path(path)
        self.backup_dir = Path(backup_dir)
        self.raw = b""
        self.lines = []
        self.tasks = []
        self.by_id = {}
        self.bom = False
        self.newline = "\n"
        self.load()

    def load(self):
        try:
            raw = self.path.read_bytes()
            if raw != self.raw:
                self._parse(raw)
        except (OSError, UnicodeError) as e:
            raise QueueError(f"Не удалось прочитать {self.path.name}: {e}") from e

    def _parse(self, raw):
        text = raw.decode("utf-8-sig")
        lines = text.splitlines(keepends=True)
        tasks, by_id = [], {}
        fence = None
        frontmatter = bool(lines and lines[0].strip() == "---")
        parent = None
        for index, line in enumerate(lines):
            stripped = line.lstrip()
            if frontmatter:
                if index > 0 and line.strip() == "---":
                    frontmatter = False
                continue
            fenced = re.match(r"^(`{3,}|~{3,})", stripped)
            if fenced:
                parent = None
                mark = fenced.group(1)
                if fence is None:
                    fence = (mark[0], len(mark))
                elif mark[0] == fence[0] and len(mark) >= fence[1]:
                    fence = None
                continue
            if fence:
                continue
            match = TASK.match(line)
            if not match:
                if re.match(r"^[ \t]*[-*+] \[", line):
                    raise QueueError(f"Строка {index + 1}: используйте '- [ ] текст'.")
                if line.strip():
                    parent = None
                continue
            indent, mark, body, _ = match.groups()
            if indent not in ("", "    "):
                raise QueueError(f"Строка {index + 1}: разрешены только задача и один уровень подпунктов (4 пробела).")
            identity = IDENTITY.search(body)
            task_id = identity.group(1) if identity else uuid4().hex
            content = body[:identity.start()].rstrip() if identity else body.rstrip()
            if not content.strip():
                raise QueueError(f"Строка {index + 1}: задача не может быть пустой.")
            if task_id in by_id:
                raise QueueError(f"Строка {index + 1}: повторяющийся ID задачи.")
            if indent and parent is None:
                raise QueueError(f"Строка {index + 1}: у подпункта нет родительской задачи.")
            item = Task(task_id, content, mark.lower() == "x", index, parent.id if indent else None)
            by_id[item.id] = item
            if indent:
                parent.children.append(item)
            else:
                tasks.append(item)
                parent = item
        if frontmatter or fence:
            raise QueueError("В заметке не закрыт frontmatter или блок кода. Исправьте разметку и обновите очередь.")
        for item in tasks:
            if item.done and any(not c.done for c in item.children):
                raise QueueError(f"У выполненной задачи «{item.text}» есть невыполненные подпункты. Исправьте отметки и обновите очередь.")
        self.raw, self.lines, self.tasks, self.by_id = raw, lines, tasks, by_id
        self.bom = raw.startswith(b"\xef\xbb\xbf")
        self.newline = "\r\n" if "\r\n" in text else "\n"

    @property
    def active(self):
        return [t for t in self.tasks if not t.completed]

    @property
    def fingerprint(self):
        return hashlib.sha256(self.raw).hexdigest()

    def _prepared(self):
        lines = self.lines.copy()
        for item in self.by_id.values():
            line = lines[item.line]
            if not IDENTITY.search(line.rstrip("\r\n")):
                suffix = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
                lines[item.line] = line.rstrip("\r\n") + f" <!-- task:{item.id} -->" + suffix
        return lines

    def _commit(self, lines):
        """Detect external edits, back up original bytes, atomically replace our file."""
        try:
            if self.path.read_bytes() != self.raw:
                raise ConflictError("Очередь изменилась в другом приложении. Нажмите «Обновить» и повторите действие.")
            payload = ("\ufeff" if self.bom else "") + "".join(lines)
            encoded = payload.encode("utf-8")
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            digest = hashlib.sha256(str(self.path.resolve()).encode()).hexdigest()[:8]
            # Windows clock resolution can give consecutive commits the same stamp.
            # A unique suffix and exclusive creation prevent overwriting a backup.
            backup = self.backup_dir / f"{self.path.stem}-{digest}-{stamp}-{uuid4().hex}.md"
            with backup.open("xb") as stream:
                stream.write(self.raw)
            fd, temp = tempfile.mkstemp(prefix=".studyqueues-", dir=self.path.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                if self.path.read_bytes() != self.raw:
                    raise ConflictError("Файл изменился перед сохранением. Обновите очередь.")
                os.replace(temp, self.path)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
            self._parse(encoded)
        except OSError as e:
            raise QueueError(f"Не удалось сохранить очередь: {e}") from e

    def edit(self, task_id, text):
        text = self._valid_text(text)
        item = self.by_id[task_id]
        lines = self._prepared()
        lines[item.line] = self._line(item, text=text)
        self._commit(lines)

    def delete(self, task_id):
        """Remove task lines only; retain notes, blank lines, BOM and newlines."""
        item = self.by_id[task_id]
        removed = {t.line for t in [item, *item.children]}
        self._commit([line for i, line in enumerate(self._prepared()) if i not in removed])

    def add(self, text, parent_id=None, *, after_id=None, at_start=False):
        text = self._valid_text(text)
        if at_start and (parent_id is not None or after_id is not None):
            raise QueueError("Вставка в начало доступна только для новой корневой задачи.")
        if after_id is not None and (parent_id is not None or after_id not in self.by_id or self.by_id[after_id].parent_id):
            raise QueueError("Позиция добавления должна быть корневой задачей.")
        lines = self._prepared()
        ident = uuid4().hex
        if parent_id:
            parent = self.by_id[parent_id]
            if parent.parent_id:
                raise QueueError("Вложение в подпункт не поддерживается.")
            pos = parent.children[-1].line + 1 if parent.children else parent.line + 1
            if pos and not lines[pos - 1].endswith("\n"):
                lines[pos - 1] += self.newline
            lines.insert(pos, f"    - [ ] {text} <!-- task:{ident} -->{self.newline}")
            if parent.done:
                lines[parent.line] = self._line(parent, done=False)
                if not lines[parent.line].endswith("\n"):
                    lines[parent.line] += self.newline
        else:
            anchor = self.by_id[after_id] if after_id is not None else self.tasks[-1] if self.tasks else None
            pos = anchor.children[-1].line + 1 if anchor and anchor.children else anchor.line + 1 if anchor else len(lines)
            if at_start and self.tasks:
                pos = self.tasks[0].line
            if pos and not lines[pos - 1].endswith("\n"):
                lines[pos - 1] += self.newline
            lines.insert(pos, f"- [ ] {text} <!-- task:{ident} -->{self.newline}")
        self._commit(lines)
        return ident

    @staticmethod
    def _valid_text(text):
        text = text.strip()
        if not text or "\n" in text or "\r" in text or "<!-- task:" in text:
            raise QueueError("Введите непустую задачу одной строкой.")
        return text

    def _line(self, task, text=None, done=None):
        old = self.lines[task.line]
        suffix = "\r\n" if old.endswith("\r\n") else "\n" if old.endswith("\n") else ""
        content = task.text if text is None else text
        checked = task.done if done is None else done
        return f"{'    ' if task.parent_id else ''}- [{'x' if checked else ' '}] {content} <!-- task:{task.id} -->{suffix}"

    def completion_for(self, task_id):
        item = self.by_id[task_id]
        if item.completed:
            raise QueueError("Эта задача уже выполнена.")
        targets = [item] + item.children
        if item.parent_id:
            parent = self.by_id[item.parent_id]
            if all(c.done or c.id == item.id for c in parent.children):
                targets.append(parent)
        changes = [{"id": t.id, "text": t.text, "before": t.done, "after": True,
                    "parent_id": t.parent_id} for t in targets if not t.done]
        return Completion(changes)

    def complete(self, task_id):
        completion = self.completion_for(task_id)
        self.restore(completion, forward=True)
        return completion

    def reopening_for(self, task_id):
        """Open a whole task, or one child together with its parent, atomically."""
        item = self.by_id[task_id]
        targets = [item] + item.children
        if item.parent_id:
            targets.append(self.by_id[item.parent_id])
        return Completion([{"id": t.id, "text": t.text, "before": True, "after": False,
                            "parent_id": t.parent_id} for t in targets if t.done])

    def restore(self, completion, forward=False):
        lines = self._prepared()
        for change in completion.changes:
            task = self.by_id.get(change["id"])
            expected = change["before"] if forward else change["after"]
            if not task or task.text != change["text"] or task.done != expected:
                raise ConflictError("Задача изменилась. Обновите очередь; прежняя отмена больше недоступна.")
            lines[task.line] = self._line(task, done=change["after"] if forward else change["before"])
        self._commit(lines)

    def move(self, task_id, direction):
        item = self.by_id[task_id]
        if item.parent_id:
            raise QueueError("Перемещайте задачу вместе с её подпунктами.")
        index = self.tasks.index(item)
        dest = index + direction
        if not 0 <= dest < len(self.tasks):
            return
        low, high = sorted((index, dest))
        if high != low + 1:
            raise QueueError("Можно переместить на одну позицию.")
        lines = self._prepared()
        a, b = self.tasks[low], self.tasks[high]
        end_a = a.children[-1].line + 1 if a.children else a.line + 1
        end_b = b.children[-1].line + 1 if b.children else b.line + 1
        first, gap, second = lines[a.line:end_a], lines[end_a:b.line], lines[b.line:end_b]
        if first and not first[-1].endswith("\n"):
            first[-1] += self.newline
        if second and not second[-1].endswith("\n"):
            second[-1] += self.newline
        lines[a.line:end_b] = second + gap + first
        self._commit(lines)

    def move_before(self, task_id, before_id=None):
        """Reorder entire task blocks in one write, keeping non-task text in its slots."""
        item = self.by_id.get(task_id)
        if item is None or item.parent_id:
            raise QueueError("Перемещайте задачу вместе с её подпунктами.")
        if before_id == task_id:
            return
        if before_id is not None and not any(t.id == before_id for t in self.tasks):
            raise QueueError("Место вставки изменилось. Обновите очередь.")
        order = [t.id for t in self.tasks if t.id != task_id]
        order.insert(order.index(before_id) if before_id is not None else len(order), task_id)
        if order == [t.id for t in self.tasks]:
            return
        lines = self._prepared()
        slots = [(t.line, t.children[-1].line + 1 if t.children else t.line + 1) for t in self.tasks]
        blocks = {t.id: lines[start:end] for t, (start, end) in zip(self.tasks, slots)}
        for ident, (start, end) in reversed(list(zip(order, slots))):
            block = blocks[ident].copy()
            if end < len(lines) and not block[-1].endswith("\n"):
                block[-1] += self.newline
            lines[start:end] = block
        self._commit(lines)
