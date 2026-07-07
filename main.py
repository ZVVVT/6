import re
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtGui import QFont, QFontDatabase, QIcon
from PySide6.QtWidgets import QApplication

from app.main_window import MainWindow
from app.ui_style import get_app_stylesheet
from core.config_manager import ConfigManager, get_application_root


DEFAULT_FONT_SIZE = 10


_SINGLE_INSTANCE_MUTEX_HANDLE = None


def acquire_single_instance_lock() -> bool:
    """限制 Windows 下同一用户会话内只启动一个软件实例。

    说明：
    - 打包后的 exe 被重复双击时，第二个进程会提示并立即退出；
    - 第一个进程退出后，系统会自动释放互斥锁；
    - 如果创建互斥锁失败，不阻止软件启动，避免异常环境下影响正常使用。
    """
    if not sys.platform.startswith("win"):
        return True

    try:
        import ctypes

        global _SINGLE_INSTANCE_MUTEX_HANDLE

        error_already_exists = 183
        mutex_name = r"Local\SpermProteinAnalyzer_SingleInstance_Mutex"

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32 = ctypes.WinDLL("user32", use_last_error=True)

        handle = kernel32.CreateMutexW(None, False, mutex_name)
        if not handle:
            return True

        last_error = ctypes.get_last_error()
        if last_error == error_already_exists:
            user32.MessageBoxW(
                None,
                "软件已在运行，请勿重复打开。",
                "提示",
                0x00000040,
            )
            kernel32.CloseHandle(handle)
            return False

        _SINGLE_INSTANCE_MUTEX_HANDLE = handle
        return True
    except Exception:
        return True



def project_root() -> Path:
    """返回软件运行根目录。源码运行时为项目根目录；打包后为 exe 所在目录。"""
    return get_application_root()


def resolve_project_path(root: Path, path_value) -> Path:
    """把配置里的相对路径解析为项目根目录下的绝对路径。

    空字符串和 "." 不代表项目目录，在字体配置中表示“系统默认字体”。
    """
    path_text = str(path_value or "").strip()
    if not path_text or path_text == ".":
        return Path("")
    path = Path(path_text)
    if path.is_absolute():
        return path
    return root / path


def load_custom_font_family(root: Path, config_manager: ConfigManager) -> Optional[str]:
    """
    尝试加载 config.ini 中配置的界面字体。

    规则：
    1. [AppInfo] font_path 为空时，不加载任何自定义字体，使用系统默认字体。
    2. font_path 不为空，但文件不存在 / 加载失败时，也使用系统默认字体。
    3. 只有字体文件存在且 Qt 成功加载时，才返回字体族名。
    """
    font_path_text = str(config_manager.get_app_font_path() or "").strip()
    if not font_path_text or font_path_text == ".":
        return None

    font_path = resolve_project_path(root, font_path_text)
    if not font_path.exists() or not font_path.is_file():
        return None

    font_id = QFontDatabase.addApplicationFont(str(font_path))
    if font_id == -1:
        return None

    families = QFontDatabase.applicationFontFamilies(font_id)
    if not families:
        return None

    return families[0]


def normalize_stylesheet_font(stylesheet: str, font_family: Optional[str]) -> str:
    """
    处理全局 QSS 中写死的 Microsoft YaHei。

    - 找到自定义字体时：把 Microsoft YaHei 替换为自定义字体。
    - 没有自定义字体时：删除这些 font-family 声明，让 Qt 使用系统默认字体。
    """
    if font_family:
        safe_font_family = font_family.replace('"', "")
        stylesheet = stylesheet.replace(
            'font-family: "Microsoft YaHei";',
            f'font-family: "{safe_font_family}";',
        )
        stylesheet = stylesheet.replace(
            "font-family: Microsoft YaHei;",
            f'font-family: "{safe_font_family}";',
        )
        return stylesheet

    return re.sub(
        r'\s*font-family\s*:\s*(?:"Microsoft YaHei"|Microsoft YaHei)\s*;\s*',
        '\n',
        stylesheet,
    )


def apply_app_font(app: QApplication, root: Path, config_manager: ConfigManager) -> Optional[str]:
    """
    应用界面字体。

    默认不强行指定字体，保留系统默认字体。
    只有 config.ini 里 font_path 指向有效字体文件时，才设置 QApplication 字体。
    """
    system_font = app.font()
    app.setProperty("system_font_family", system_font.family())
    app.setProperty("system_font_point_size", system_font.pointSize())

    custom_family = load_custom_font_family(root, config_manager)
    if custom_family:
        font_size = config_manager.get_app_font_size()
        app.setFont(QFont(custom_family, font_size))
        app.setProperty("app_font_custom", True)
        app.setProperty("app_font_family", custom_family)
        app.setProperty("app_font_size", font_size)
        return custom_family

    app.setProperty("app_font_custom", False)
    app.setProperty("app_font_family", app.font().family())
    app.setProperty("app_font_size", app.font().pointSize())
    return None


def apply_global_stylesheet(app: QApplication, custom_font_family: Optional[str]):
    stylesheet = get_app_stylesheet()
    app.setStyleSheet(normalize_stylesheet_font(stylesheet, custom_font_family))


def apply_app_branding(app: QApplication, root: Path, config_manager: ConfigManager):
    """启动阶段先应用软件名称和任务栏 LOGO。MainWindow 内部还会再应用一次。"""
    app_name = config_manager.get_app_name()
    app.setApplicationName(app_name)

    logo_path = resolve_project_path(root, config_manager.get_app_logo_path())
    if not logo_path.exists():
        logo_path = root / "assets" / "logo.png"

    if logo_path.exists() and logo_path.is_file():
        icon = QIcon(str(logo_path))
        if not icon.isNull():
            app.setWindowIcon(icon)


def main():
    if not acquire_single_instance_lock():
        sys.exit(0)

    app = QApplication(sys.argv)
    root = project_root()

    config_manager = ConfigManager(str(root / "config.ini"))
    config_manager.ensure_default_config()

    custom_font_family = apply_app_font(app, root, config_manager)
    apply_global_stylesheet(app, custom_font_family)
    apply_app_branding(app, root, config_manager)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
