import codecs
import pytest
from studyqueues.queue_store import QueueStore, QueueError, ConflictError


def queue(tmp_path, text):
    path = tmp_path / "Русская очередь.md"
    path.write_bytes(text.encode("utf-8"))
    return QueueStore(path, tmp_path / "backups")


def test_insert_after_root_subtree_preserves_surrounding_markdown_and_conflict(tmp_path):
    text = ('\ufeff# Очередь\r\n- [ ] Первый <!-- task:a -->\r\n'
            '    - [x] Подпункт <!-- task:a1 -->\r\n'
            '\r\nПримечание [[заметка]]\r\n- [ ] Второй <!-- task:b -->\r\n')
    store = queue(tmp_path, text)
    ident = store.add('Новый', after_id='a')
    assert [t.id for t in store.tasks] == ['a', ident, 'b']
    assert store.by_id['a'].children[0].id == 'a1'
    assert store.path.read_bytes() == text.replace('\r\n\r\nПримечание', f'\r\n- [ ] Новый <!-- task:{ident} -->\r\n\r\nПримечание').encode('utf-8')
    assert next(store.backup_dir.glob('*.md')).read_bytes() == text.encode('utf-8')
    store.path.write_bytes(store.raw + 'Внешнее изменение\r\n'.encode('utf-8'))
    with pytest.raises(ConflictError):
        store.add('Не записывать', after_id='a')
    assert 'Не записывать' not in store.path.read_text(encoding='utf-8')


def test_preserve_document_bom_crlf_and_back_up(tmp_path):
    text = '\ufeff---\r\ntags: [учёба]\r\n---\r\n# Очередь\r\n\r\n- [ ] Один <!-- task:a -->\r\n\r\n```md\r\n- [ ] Не задача\r\n```\r\nИтог *текст* [[Ссылка]]\r\n'
    store = queue(tmp_path, text)
    assert len(store.tasks) == 1
    store.complete("a")
    assert store.path.read_bytes() == text.replace("- [ ] Один", "- [x] Один").encode("utf-8")
    assert next(store.backup_dir.glob("*.md")).read_bytes() == text.encode("utf-8")
    assert store.path.read_bytes().startswith(codecs.BOM_UTF8)


def test_duplicate_names_ids_and_exact_undo(tmp_path):
    store = queue(tmp_path, "- [ ] То же\n- [ ] То же\n")
    first, second = store.tasks
    action = store.complete(second.id)
    assert not store.by_id[first.id].done and store.by_id[second.id].done
    reopened = QueueStore(store.path, store.backup_dir)
    reopened.restore(action)
    assert [t.id for t in reopened.tasks] == [first.id, second.id]
    assert all(not t.done for t in reopened.tasks)


def test_reopen_child_then_parent_preserves_document_and_other_marks(tmp_path):
    text = ('\ufeff# Очередь\r\n- [x] Один <!-- task:a -->\r\n'
            '    - [x] Первый <!-- task:a1 -->\r\n'
            '    - [x] Второй <!-- task:a2 -->\r\n'
            '- [x] Другой <!-- task:b -->\r\nИтог [[заметка]]\r\n')
    store = queue(tmp_path, text)
    store.restore(store.reopening_for("a1"), forward=True)
    expected = text.replace('- [x] Один', '- [ ] Один').replace('- [x] Первый', '- [ ] Первый')
    assert store.path.read_bytes() == expected.encode('utf-8')
    assert store.by_id['a2'].done and store.by_id['b'].done
    store.restore(store.reopening_for("a"), forward=True)
    assert all(not t.done for t in [store.by_id['a'], *store.by_id['a'].children])
    assert store.by_id['b'].done


def test_reopen_preserves_external_changes(tmp_path):
    store = queue(tmp_path, '- [x] Один <!-- task:a -->\n')
    external = store.raw + '# Изменение редактора\n'.encode('utf-8')
    store.path.write_bytes(external)
    with pytest.raises(ConflictError):
        store.restore(store.reopening_for('a'), forward=True)
    assert store.path.read_bytes() == external


def test_delete_child_and_root_preserves_notes_bom_and_crlf(tmp_path):
    text = ('\ufeff# Очередь\r\n- [x] Родитель <!-- task:a -->\r\n'
            '\r\n    - [x] Первый <!-- task:a1 -->\r\n'
            '    - [x] Второй <!-- task:a2 -->\r\n'
            '\r\nЗаметка [[ссылка]]\r\n- [ ] Другая <!-- task:b -->\r\n')
    store = queue(tmp_path, text)
    store.delete('a1')
    expected = text.replace('    - [x] Первый <!-- task:a1 -->\r\n', '')
    assert store.path.read_bytes() == expected.encode('utf-8')
    assert store.by_id['a'].done and store.by_id['a2'].done
    store.delete('a')
    expected = expected.replace('- [x] Родитель <!-- task:a -->\r\n', '').replace('    - [x] Второй <!-- task:a2 -->\r\n', '')
    assert store.path.read_bytes() == expected.encode('utf-8')
    assert list(store.by_id) == ['b']
    assert len(list(store.backup_dir.glob('*.md'))) == 2


def test_delete_conflict_preserves_external_text(tmp_path):
    store = queue(tmp_path, '- [ ] Родитель <!-- task:a -->\n    - [ ] Подпункт <!-- task:a1 -->\n')
    external = store.raw + '# Чужая запись\n'.encode('utf-8')
    store.path.write_bytes(external)
    with pytest.raises(ConflictError):
        store.delete('a')
    assert store.path.read_bytes() == external and 'a1' in store.by_id


def test_consecutive_writes_with_same_timestamp_keep_each_backup(tmp_path, monkeypatch):
    from datetime import datetime
    class FixedClock:
        @staticmethod
        def now():
            return datetime(2026, 10, 5, 12, 0, 0)
    monkeypatch.setattr('studyqueues.queue_store.datetime', FixedClock)
    store = queue(tmp_path, '- [ ] Задача <!-- task:a -->\n')
    original = store.raw
    store.edit('a', 'Первое изменение')
    first = store.raw
    store.edit('a', 'Второе изменение')
    backups = list(store.backup_dir.glob('*.md'))
    assert len(backups) == 2
    assert {file.read_bytes() for file in backups} == {original, first}


def test_drag_reorders_subtrees_in_one_write_preserving_document(tmp_path, monkeypatch):
    text = ('\ufeff# Очередь\r\n- [ ] Один <!-- task:a -->\r\n'
            '    - [x] Готово <!-- task:a1 -->\r\n\r\n'
            '- [ ] То же <!-- task:b -->\r\n\r\n'
            '- [ ] То же <!-- task:c -->\r\n'
            '    - [ ] Осталось <!-- task:c1 -->\r\n\r\n'
            '- [x] Четвёртый <!-- task:d -->\r\nИтог [[заметка]]\r\n')
    store = queue(tmp_path, text)
    calls = []
    original = store._commit
    def commit(lines):
        calls.append(lines)
        original(lines)
    monkeypatch.setattr(store, "_commit", commit)
    store.move_before("c", "a")
    assert len(calls) == 1
    assert [t.id for t in store.tasks] == ["c", "a", "b", "d"]
    assert store.tasks[0].children[0].id == "c1"
    assert store.by_id["a1"].done
    payload = store.path.read_bytes()
    assert payload.startswith(codecs.BOM_UTF8)
    assert payload.endswith("Итог [[заметка]]\r\n".encode("utf-8"))
    assert payload.count(b"\r\n\r\n") == text.count("\r\n\r\n")
    store.move_before("c")
    assert [t.id for t in store.tasks] == ["a", "b", "d", "c"]
    assert QueueStore(store.path, store.backup_dir).tasks[-1].children[0].id == "c1"


def test_drag_without_final_newline_and_external_conflict(tmp_path):
    store = queue(tmp_path, "- [ ] Один <!-- task:a -->\n- [ ] Два <!-- task:b -->\n- [ ] Три <!-- task:c -->")
    store.move_before("c", "a")
    assert [t.id for t in store.tasks] == ["c", "a", "b"]
    original = store.path.read_bytes()
    external = original + "\n- [ ] Чужая задача\n".encode("utf-8")
    store.path.write_bytes(external)
    with pytest.raises(ConflictError):
        store.move_before("c")
    assert store.path.read_bytes() == external
    assert [t.id for t in store.tasks] == ["c", "a", "b"]


def test_drag_rejects_child_or_missing_destination(tmp_path):
    store = queue(tmp_path, "- [ ] Один <!-- task:a -->\n    - [ ] Подпункт <!-- task:a1 -->\n- [ ] Два <!-- task:b -->\n")
    original = store.path.read_bytes()
    for ident, before in (("a1", "b"), ("a", "missing"), ("missing", "a")):
        with pytest.raises(QueueError):
            store.move_before(ident, before)
        assert store.path.read_bytes() == original


def test_last_child_closes_parent_and_undo_reopens_only_changed(tmp_path):
    store = queue(tmp_path, "- [ ] ДЗ\n    - [X] 26\n    - [ ] 28\n")
    parent = store.tasks[0]
    action = store.complete(parent.children[1].id)
    assert len(action.changes) == 2
    assert store.by_id[parent.id].completed
    store.restore(action)
    assert not store.by_id[parent.id].done
    assert store.by_id[parent.children[0].id].done
    assert not store.by_id[parent.children[1].id].done


def test_parent_completion_skips_already_done_children(tmp_path):
    store = queue(tmp_path, "- [ ] ДЗ\n    - [x] Первый\n    - [ ] Второй\n")
    parent = store.tasks[0]
    action = store.complete(parent.id)
    assert len(action.changes) == 2
    assert all(t.done for t in store.by_id.values())
    store.restore(action)
    assert store.tasks[0].children[0].done


def test_external_changes_are_preserved(tmp_path):
    store = queue(tmp_path, "- [ ] Моя задача\n")
    ident = store.tasks[0].id
    external = "- [ ] Моя задача\n- [ ] Добавил Кодекс\n"
    store.path.write_text(external, encoding="utf-8")
    with pytest.raises(ConflictError):
        store.complete(ident)
    assert store.path.read_text(encoding="utf-8") == external
    store.load()
    store.complete(store.tasks[0].id)
    assert store.tasks[1].text == "Добавил Кодекс"


@pytest.mark.parametrize("text", [
    "    - [ ] Сирота\n", "- [ ] Корень\n        - [ ] Глубоко\n",
    "- [ ] Корень\n  - [ ] Два пробела\n", "* [ ] Не согласовано\n",
    "- [ ] Один <!-- task:a -->\n- [ ] Другой <!-- task:a -->\n",
    "- [x] ДЗ\n    - [ ] Не сделано\n", "- [ ] \n",
])
def test_invalid_structure_never_rewrites(tmp_path, text):
    path = tmp_path / "invalid.md"
    path.write_bytes(text.encode())
    with pytest.raises(QueueError):
        QueueStore(path, tmp_path / "backups")
    assert path.read_bytes() == text.encode()


def test_failed_replace_keeps_source_and_no_temporary_file(tmp_path, monkeypatch):
    store = queue(tmp_path, "- [ ] Задача\n")
    original = store.path.read_bytes()
    def deny(*args):
        raise PermissionError("Нет доступа")
    monkeypatch.setattr("studyqueues.queue_store.os.replace", deny)
    with pytest.raises(QueueError, match="Нет доступа"):
        store.complete(store.tasks[0].id)
    assert store.path.read_bytes() == original
    assert not list(tmp_path.glob(".studyqueues-*"))


@pytest.mark.parametrize("text", ["- [ ] ДЗ", "- [x] ДЗ", "- [ ] ДЗ\n    - [ ] Старый"])
def test_add_child_to_file_without_final_newline(tmp_path, text):
    store = queue(tmp_path, text)
    parent_id = store.tasks[0].id
    store.add("Новый", parent_id)
    assert store.tasks[0].children[-1].text == "Новый"
    assert not store.tasks[0].done
    assert len(store.tasks[0].children) == (2 if "Старый" in text else 1)


def test_move_entire_subtree_add_at_end_and_edit(tmp_path):
    store = queue(tmp_path, "# Очередь\n- [ ] Первый\n    - [ ] Дочерний\n\n- [ ] Второй\n\nПримечание.\n")
    first, second = store.tasks
    store.move(first.id, 1)
    assert [t.id for t in store.tasks] == [second.id, first.id]
    assert store.tasks[1].children[0].text == "Дочерний"
    new = store.add("Третий")
    store.edit(new, "Изменённый третий")
    assert [t.text for t in store.tasks] == ["Второй", "Первый", "Изменённый третий"]
    assert store.path.read_text(encoding="utf-8").endswith("\nПримечание.\n")


def test_undo_rejects_changed_text(tmp_path):
    store = queue(tmp_path, "- [ ] Задача\n")
    ident = store.tasks[0].id
    action = store.complete(ident)
    store.edit(ident, "Другая формулировка")
    with pytest.raises(ConflictError):
        store.restore(action)
    assert store.by_id[ident].done


def test_prepend_keeps_frontmatter_bom_crlf_subtree_and_backup(tmp_path):
    original = b'\xef\xbb\xbf' + ('---\r\ntitle: Queue\r\n---\r\n# Очередь\r\n\r\n'
                '- [ ] Старый <!-- task:old -->\r\n'
                '    - [ ] Подпункт <!-- task:child -->\r\n\r\nПримечание.\r\n').encode('utf-8')
    path = tmp_path / 'queue.md'
    path.write_bytes(original)
    store = QueueStore(path, tmp_path / 'backups')
    first = store.add('Новый', at_start=True)
    second = store.add('Самый новый', at_start=True)
    assert [task.id for task in store.tasks] == [second, first, 'old']
    assert store.tasks[-1].children[0].id == 'child'
    updated = path.read_bytes()
    assert updated.startswith(original[:original.index(b'- [ ]')])
    assert updated.endswith(original[original.index(b'- [ ]'):])
    assert b'\n' not in updated.replace(b'\r\n', b'')
    assert any(file.read_bytes() == original for file in (tmp_path / 'backups').iterdir())


def test_prepend_empty_queue_keeps_intro_and_rejects_conflict(tmp_path):
    store = queue(tmp_path, '# Очередь без задач')
    first = store.add('Первая', at_start=True)
    assert store.path.read_text(encoding='utf-8').startswith('# Очередь без задач\n- [ ] Первая')
    external = store.path.read_bytes() + b'\nExternal note\n'
    store.path.write_bytes(external)
    with pytest.raises(ConflictError):
        store.add('Не записано', at_start=True)
    assert store.path.read_bytes() == external
    assert [t.id for t in store.tasks] == [first]
