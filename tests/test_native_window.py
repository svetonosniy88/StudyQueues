from studyqueues.native_window import (hit_test, HTCLIENT, HTCAPTION, HTTOP,
                                      HTTOPLEFT, HTRIGHT, HTBOTTOMRIGHT)


def test_native_titlebar_excludes_controls_and_maximized_resize_edges():
    assert hit_test(100, 20, 1160, 820, 6, False, True) == HTCAPTION
    assert hit_test(1100, 20, 1160, 820, 6, False, True, True) == HTCLIENT
    assert hit_test(100, 2, 1160, 820, 6, False, True) == HTTOP
    assert hit_test(2, 2, 1160, 820, 6, False, True) == HTTOPLEFT
    assert hit_test(1159, 400, 1160, 820, 6, False, False) == HTRIGHT
    assert hit_test(1159, 819, 1160, 820, 6, False, False) == HTBOTTOMRIGHT
    assert hit_test(100, 2, 1160, 820, 6, True, True) == HTCAPTION
