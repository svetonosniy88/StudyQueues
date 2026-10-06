from datetime import datetime
from pathlib import Path
import json
import os
import tempfile
import math


class StateError(Exception):
    pass


class StateStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.backups = self.directory / "backups"

    def read(self, name, default):
        file = self.directory / name
        if not file.exists():
            return default
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("version") != 1:
                raise ValueError("неподдерживаемый формат")
            return data
        except (OSError, ValueError) as e:
            raise StateError(f"Не удалось прочитать {name}. Исходный файл сохранён: {e}") from e

    def write(self, name, data):
        file = self.directory / name
        data = {**data, "version": 1}
        temp = None
        try:
            fd, temp = tempfile.mkstemp(prefix=".state-", dir=self.directory)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            if file.exists():
                self.backups.mkdir(exist_ok=True)
                backup = self.backups / f"{name}.{datetime.now():%Y%m%d-%H%M%S-%f}.bak"
                backup.write_bytes(file.read_bytes())
                # Bound JSON checkpoint backups; Markdown originals remain untouched.
                prior = sorted(self.backups.glob(f"{name}.*.bak"))
                for old in prior[:-12]:
                    old.unlink()
            os.replace(temp, file)
        except OSError as e:
            raise StateError(f"Не удалось сохранить {name}: {e}") from e
        finally:
            if temp and os.path.exists(temp):
                os.unlink(temp)

    @staticmethod
    def validate_settings(data):
        paths = data.get("paths")
        minutes = data.get("minutes")
        if not isinstance(paths, dict) or not all(isinstance(k, str) and isinstance(v, str) and v for k, v in paths.items()) or type(minutes) is not int or not 0 <= minutes <= 480:
            raise StateError("Некорректные настройки. Исходный settings.json сохранён.")

    @staticmethod
    def validate_session(record):
        try:
            if not isinstance(record, dict) or record["queue"] not in ("university", "self_development"):
                raise ValueError("неизвестная очередь")
            if type(record.get("favorite", False)) is not bool:
                raise ValueError("некорректная отметка избранного")
            for key in ("id", "path", "started_at"):
                if not isinstance(record[key], str) or not record[key]:
                    raise ValueError(f"пустое поле {key}")
            for key in ("started_at", "ended_at"):
                if key == "ended_at" and record[key] is None:
                    continue
                if datetime.fromisoformat(record[key]).tzinfo is None:
                    raise ValueError("дата без часового пояса")
            for key in ("planned_seconds", "active_seconds"):
                value = record[key]
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                    raise ValueError("некорректная длительность")
            if not isinstance(record["intervals"], list) or not isinstance(record["completed"], dict):
                raise ValueError("некорректные интервалы или результаты")
            for interval in record["intervals"]:
                if not isinstance(interval, dict):
                    raise ValueError("некорректный интервал")
                for key in ("start", "end"):
                    if datetime.fromisoformat(interval[key]).tzinfo is None:
                        raise ValueError("интервал без часового пояса")
                seconds = interval["seconds"]
                if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
                    raise ValueError("некорректная длительность интервала")
            if not math.isclose(sum(i["seconds"] for i in record["intervals"]), record["active_seconds"], abs_tol=0.01):
                raise ValueError("интервалы не соответствуют накопленному времени")
            for ident, value in record["completed"].items():
                if not isinstance(ident, str) or not isinstance(value, dict) or not isinstance(value["text"], str) or (value["parent_id"] is not None and not isinstance(value["parent_id"], str)):
                    raise ValueError("некорректный результат задачи")
            pending = record.get("pending")
            if record.get("view_task_id") is not None and not isinstance(record["view_task_id"], str):
                raise ValueError("некорректная просматриваемая задача")
            position = record.get("view_task_position")
            if position is not None and (not isinstance(position, dict) or type(position.get("index")) is not int or position["index"] < 0 or not isinstance(position.get("fingerprint"), str)):
                raise ValueError("некорректное положение в очереди")
            if pending is not None:
                if not isinstance(pending, dict) or pending["kind"] not in ("complete", "undo", "reopen") or not isinstance(pending["changes"], list) or not pending["changes"]:
                    raise ValueError("некорректное незавершённое действие")
                for change in pending["changes"]:
                    expected_after = pending["kind"] != "reopen"
                    if not isinstance(change, dict) or not isinstance(change["id"], str) or not isinstance(change["text"], str) or type(change["before"]) is not bool or change["after"] is not expected_after or (pending["kind"] == "reopen" and change["before"] is not True) or (change["parent_id"] is not None and not isinstance(change["parent_id"], str)):
                        raise ValueError("некорректное изменение задачи")
        except (KeyError, TypeError, ValueError, OverflowError) as e:
            raise StateError(f"Некорректные данные занятия; исходный файл сохранён: {e}") from e

    def history(self):
        data = self.read("sessions.json", {"version": 1, "sessions": []})
        if not isinstance(data.get("sessions"), list) or not all(isinstance(s, dict) for s in data["sessions"]):
            raise StateError("Некорректная история занятий; файл не изменён.")
        for session in data["sessions"]:
            self.validate_session(session)
        return data["sessions"]

    def save_session(self, session):
        self.validate_session(session)
        history = self.history()
        prior = next((s for s in history if s["id"] == session["id"]), None)
        if prior is not None:
            session = {**session, "favorite": prior.get("favorite", False)}
        history = [s for s in history if s.get("id") != session["id"]]
        history.append(session)
        self.write("sessions.json", {"sessions": history})

    def delete_session(self, ident):
        history = self.history()
        if not any(s["id"] == ident for s in history):
            raise StateError("Занятие уже отсутствует в истории. Обновите результаты.")
        self.write("sessions.json", {"sessions": [s for s in history if s["id"] != ident]})

    def set_favorite(self, ident, favorite):
        if type(favorite) is not bool:
            raise StateError("Некорректная отметка избранного.")
        history = self.history()
        if not any(s["id"] == ident for s in history):
            raise StateError("Занятие уже отсутствует в истории. Обновите результаты.")
        updated = [{**s, "favorite": favorite} if s["id"] == ident else s for s in history]
        self.write("sessions.json", {"sessions": updated})
