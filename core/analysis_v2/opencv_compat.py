"""在项目虚拟环境中加载现有基础 Python 的 OpenCV。

当前项目 .venv 关闭了 system-site-packages，但其基础 Python 已安装
opencv-python。优先正常导入；仅在 cv2 不可见时按 sys.base_prefix 动态加入
基础解释器的 site-packages，不写入或修改任何环境文件。
"""

from __future__ import annotations

import importlib
import site
import sys
from pathlib import Path


def _load_opencv():
    try:
        return importlib.import_module("cv2")
    except ModuleNotFoundError:
        base_site_packages = Path(sys.base_prefix) / "Lib" / "site-packages"
        if not base_site_packages.is_dir():
            raise
        site.addsitedir(str(base_site_packages))
        return importlib.import_module("cv2")


cv2 = _load_opencv()

__all__ = ["cv2"]
