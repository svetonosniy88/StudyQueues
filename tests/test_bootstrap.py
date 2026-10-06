from pathlib import Path
import json

from studyqueues.__main__ import default_queue_paths
from studyqueues.queue_store import QueueStore


def test_fresh_profile_creates_empty_portable_queues(tmp_path):
    profile = tmp_path / 'Profile with spaces'
    paths = default_queue_paths(profile)
    assert set(paths) == {'university', 'self_development'}
    for key, path in paths.items():
        assert path == profile / 'queues' / f'{key}.md'
        assert not QueueStore(path, profile / 'backups').tasks
    assert paths['university'].read_text(encoding='utf-8') == '# Университет\n'


def test_existing_queue_without_settings_is_never_replaced(tmp_path):
    target = tmp_path / 'queues' / 'university.md'
    target.parent.mkdir()
    original = b'\xef\xbb\xbf# Queue\r\n- [ ] Existing task\r\n'
    target.write_bytes(original)
    paths = default_queue_paths(tmp_path)
    assert target.read_bytes() == original
    assert paths['self_development'].exists()


def test_existing_profile_settings_history_session_and_external_queue_untouched(tmp_path):
    profile = tmp_path / 'profile'
    profile.mkdir()
    external = tmp_path / 'My notes.md'
    external.write_text('- [ ] My existing task\n', encoding='utf-8')
    (profile / 'settings.json').write_text(json.dumps({'paths': {'university': str(external)}, 'minutes': 35}), encoding='utf-8')
    (profile / 'sessions.json').write_bytes(b'{"sessions": []}')
    (profile / 'active_session.json').write_bytes(b'{"session": null}')
    before = {path: path.read_bytes() for path in [external, *profile.iterdir()]}
    paths = default_queue_paths(profile)
    assert all(path.read_bytes() == content for path, content in before.items())
    assert not (profile / 'queues').exists()
    assert all(not path.exists() for path in paths.values())


def test_corrupt_existing_settings_are_not_treated_as_a_fresh_profile(tmp_path):
    (tmp_path / 'settings.json').write_bytes(b'{broken')
    default_queue_paths(tmp_path)
    assert (tmp_path / 'settings.json').read_bytes() == b'{broken'
    assert not (tmp_path / 'queues').exists()
