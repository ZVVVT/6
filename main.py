import sys

from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from app.main_window import MainWindow
from app.ui_style import get_app_stylesheet
from core.config_manager import ConfigManager


def main():
    app = QApplication(sys.argv)

    # 中文界面默认字体
    app.setFont(QFont("Microsoft YaHei", 10))

    # 启动阶段先读取项目根目录 config.ini，应用软件名称和 LOGO。
    # MainWindow 内部还会再应用一次，用于保存设置后的即时刷新。
    config_manager = ConfigManager()
    config_manager.ensure_default_config()

    app_name = config_manager.get_app_name()
    app.setApplicationName(app_name)

    logo_path = config_manager.get_app_logo_path()
    if logo_path.exists() and logo_path.is_file():
        icon = QIcon(str(logo_path))
        if not icon.isNull():
            app.setWindowIcon(icon)

    # 全局统一界面风格
    app.setStyleSheet(get_app_stylesheet())

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
