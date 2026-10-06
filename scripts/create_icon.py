"""Render the code-native SQ mark into a multi-resolution Windows icon."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import struct
from pathlib import Path
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QFont, QFontDatabase, QColor
from PySide6.QtWidgets import QApplication


def main():
    app = QApplication.instance() or QApplication([])
    font_file = Path("C:/Windows/Fonts/segoeuib.ttf")
    if font_file.exists():
        QFontDatabase.addApplicationFont(str(font_file))
    sizes = (16, 24, 32, 48, 64, 128, 256)
    images = []
    for size in sizes:
        image = QImage(size, size, QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.scale(size / 256, size / 256)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#3574f0"))
        painter.drawRoundedRect(QRectF(8, 8, 240, 240), 32, 32)
        painter.setPen(QColor("white"))
        font = QFont("Segoe UI")
        font.setPixelSize(120)
        font.setWeight(QFont.Bold)
        painter.setFont(font)
        painter.drawText(QRectF(8, 4, 240, 240), Qt.AlignCenter, "SQ")
        painter.end()
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.WriteOnly)
        image.save(buffer, "PNG")
        images.append(bytes(data))
    offset = 6 + len(images) * 16
    directory = bytearray(struct.pack("<HHH", 0, 1, len(images)))
    for size, data in zip(sizes, images):
        directory.extend(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    root = Path(__file__).resolve().parent.parent
    (root / "studyqueues" / "app.ico").write_bytes(bytes(directory) + b"".join(images))


if __name__ == "__main__":
    main()
