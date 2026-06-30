import sys

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from app.main_window import MainWindow
from app.ui_style import get_app_stylesheet


def main():
    app = QApplication(sys.argv)

    # 中文界面默认字体
    app.setFont(QFont("Microsoft YaHei", 10))

    # 全局统一界面风格
    app.setStyleSheet(get_app_stylesheet())

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
