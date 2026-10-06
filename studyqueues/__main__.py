"""Desktop entry point; --demo never uses personal queues."""
import argparse
import os
from pathlib import Path
import shutil
import sys


def default_queue_paths(data_dir):
    """Bootstrap a fresh profile without inspecting or replacing existing data."""
    data_dir = Path(data_dir)
    paths = {key: data_dir / 'queues' / f'{key}.md' for key in ('university', 'self_development')}
    if not (data_dir / 'settings.json').exists():
        titles = {'university': 'Университет', 'self_development': 'Саморазвитие'}
        for key, target in paths.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with target.open('x', encoding='utf-8', newline='\n') as stream:
                    stream.write(f'# {titles[key]}\n')
            except FileExistsError:
                pass
    return paths


def main():
    parser = argparse.ArgumentParser(description="Учебные очереди")
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("LOCALAPPDATA", Path.home())) / "StudyQueues")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--screen", choices=("overview", "work", "stats"), default="overview")
    parser.add_argument("--width", type=int, default=1160)
    parser.add_argument("--height", type=int, default=820)
    args = parser.parse_args()
    if args.demo:
        args.data_dir = args.data_dir / "demo"
    if args.screenshot:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QLockFile, QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox
    from PySide6.QtGui import QIcon
    from .ui import Window, apply_theme

    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("StudyQueues.Desktop")
    app = QApplication(sys.argv[:1])
    app.setApplicationName("StudyQueues")
    app.setWindowIcon(QIcon(str(Path(__file__).with_name("app.ico"))))
    apply_theme(app)
    args.data_dir.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(args.data_dir / "application.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        message = "Учебные очереди уже открыты для этой папки данных. Переключитесь на существующее окно."
        if args.screenshot:
            print(message, file=sys.stderr)
        else:
            QMessageBox.information(None, "Учебные очереди", message)
        return 3
    root = Path(__file__).resolve().parent.parent
    paths = {}
    if args.demo:
        paths = {}
        for key in ("university", "self_development"):
            target = args.data_dir / f"{key}.md"
            if not target.exists():
                shutil.copyfile(root / "examples" / f"{key}.md", target)
            paths[key] = target
    else:
        paths = default_queue_paths(args.data_dir)
    try:
        window = Window(args.data_dir, paths, force_paths=args.demo)
    except Exception as error:
        message = f"Не удалось открыть приложение. Файлы не заменены.\n{error}"
        if args.screenshot:
            print(message, file=sys.stderr)
        else:
            QMessageBox.critical(None, "Учебные очереди", message)
        return 1
    window.resize(args.width, args.height)
    window.show()
    if args.screen == "work":
        window.duration.setValue(25)
        window.start_session()
    elif args.screen == "stats":
        window.show_stats()
    if args.screenshot:
        def capture():
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            saved = window.grab().save(str(args.screenshot))
            window.close()
            app.exit(0 if saved else 2)
        QTimer.singleShot(350, capture)
    result = app.exec()
    lock.unlock()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
