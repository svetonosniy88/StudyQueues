from studyqueues.models import Task
from studyqueues.queue_window import QueueWindow


def tasks(count, done=False):
    return [Task(str(i), str(i), done, i) for i in range(count)]


def test_first_window_fills_vacated_slots_and_does_not_drop_tasks():
    queue = tasks(40)
    window = QueueWindow()
    assert [t.id for t in window.resolve(queue)[0]] == [str(i) for i in range(15)]
    for task in queue[:3]:
        task.done = True
    visible, _, _ = window.resolve(queue)
    assert [t.id for t in visible] == [str(i) for i in range(3, 18)]
    assert len(queue) == 40


def test_browsed_window_keeps_anchor_when_earlier_tasks_change():
    queue = tasks(40)
    window = QueueWindow()
    window.move(queue, 1)
    queue[0].done = True
    assert window.resolve(queue)[0][0].id == '15'
    queue[15].done = True
    assert window.resolve(queue)[0][0].id == '16'
    window.move(queue, -1)
    assert window.resolve(queue)[0][0].id == '1'


def test_history_windows_reach_every_completed_task_and_allow_reopening():
    queue = tasks(46, done=True)
    window = QueueWindow()
    seen = set()
    for page in range(4):
        window.history_page = page
        visible, _, _ = window.resolve(queue, True)
        assert len(visible) <= 15
        seen.update(t.id for t in visible)
    assert seen == {t.id for t in queue}
    queue[4].done = False
    visible, pending, _ = window.resolve(queue, True)
    assert queue[4] in visible and pending == [queue[4]]


def test_completed_backlog_and_pending_window_are_both_bounded():
    queue = tasks(120)
    for task in queue[:60]:
        task.done = True
    visible, pending, past = QueueWindow().resolve(queue, True)
    assert [t.id for t in visible] == [str(i) for i in range(45, 75)]
    assert len(pending) == len(past) == 60
