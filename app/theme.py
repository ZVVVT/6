"""
界面主题配置。

作用：
1. 所有颜色集中管理，避免在各页面写死颜色。
2. 后续可扩展医疗蓝、科研绿、深色主题等。
3. ui_style.py 只负责根据主题生成 QSS。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Dict


THEMES: Dict[str, Dict[str, str]] = {
    "medical_blue": {
        "name": "医疗蓝",

        # 主色
        "primary": "#1769E0",
        "primary_hover": "#0F5ED7",
        "primary_pressed": "#0B4DB5",
        "primary_light": "#EAF2FF",
        "primary_lighter": "#F3F7FF",
        "primary_border": "#BCD7FF",

        # 背景与卡片
        "background": "#F5F8FC",
        "background_alt": "#EEF4FB",
        "surface": "#FFFFFF",
        "surface_alt": "#F8FAFD",
        "surface_hover": "#F2F7FF",
        "border": "#DDE6F2",
        "border_light": "#E8EEF6",
        "divider": "#E4EBF5",

        # 文字
        "text_primary": "#1F2D3D",
        "text_secondary": "#5E6B7A",
        "text_muted": "#8A97A8",
        "text_inverse": "#FFFFFF",
        "title": "#102A43",

        # 状态色
        "success": "#16A34A",
        "success_bg": "#EAF8EF",
        "success_border": "#BDEACB",

        "warning": "#F59E0B",
        "warning_bg": "#FFF5E5",
        "warning_border": "#FAD89A",

        "danger": "#EF4444",
        "danger_bg": "#FDECEC",
        "danger_border": "#F6BFC0",

        "info": "#2563EB",
        "info_bg": "#EEF4FF",
        "info_border": "#C7D8FF",

        "purple": "#7C3AED",
        "purple_bg": "#F2ECFF",
        "purple_border": "#D8C8FF",

        # 表格
        "table_header_bg": "#EEF4FB",
        "table_alt_bg": "#F8FAFD",
        "table_grid": "#DDE6F2",
        "table_selected_bg": "#DCEBFF",

        # 阴影不直接用于 QSS，可供后续组件使用
        "shadow": "#D6DEE9",
    },

    "medical_green": {
        "name": "科研绿",

        "primary": "#059669",
        "primary_hover": "#047857",
        "primary_pressed": "#065F46",
        "primary_light": "#E8F8F1",
        "primary_lighter": "#F3FBF8",
        "primary_border": "#A8E6CF",

        "background": "#F6FBF8",
        "background_alt": "#EEF8F3",
        "surface": "#FFFFFF",
        "surface_alt": "#F8FCFA",
        "surface_hover": "#F0FAF5",
        "border": "#DCEBE3",
        "border_light": "#E8F2ED",
        "divider": "#DFEEE7",

        "text_primary": "#1F2D3D",
        "text_secondary": "#5E6B7A",
        "text_muted": "#8A97A8",
        "text_inverse": "#FFFFFF",
        "title": "#102A43",

        "success": "#16A34A",
        "success_bg": "#EAF8EF",
        "success_border": "#BDEACB",

        "warning": "#F59E0B",
        "warning_bg": "#FFF5E5",
        "warning_border": "#FAD89A",

        "danger": "#EF4444",
        "danger_bg": "#FDECEC",
        "danger_border": "#F6BFC0",

        "info": "#2563EB",
        "info_bg": "#EEF4FF",
        "info_border": "#C7D8FF",

        "purple": "#7C3AED",
        "purple_bg": "#F2ECFF",
        "purple_border": "#D8C8FF",

        "table_header_bg": "#EEF8F3",
        "table_alt_bg": "#F8FCFA",
        "table_grid": "#DCEBE3",
        "table_selected_bg": "#DFF7EC",

        "shadow": "#D6E5DC",
    },

    "dark_blue": {
        "name": "深色蓝",

        "primary": "#3B82F6",
        "primary_hover": "#60A5FA",
        "primary_pressed": "#2563EB",
        "primary_light": "#1E3A5F",
        "primary_lighter": "#172033",
        "primary_border": "#335D92",

        "background": "#111827",
        "background_alt": "#172033",
        "surface": "#1F2937",
        "surface_alt": "#243244",
        "surface_hover": "#2B3A50",
        "border": "#374151",
        "border_light": "#465266",
        "divider": "#374151",

        "text_primary": "#E5E7EB",
        "text_secondary": "#CBD5E1",
        "text_muted": "#94A3B8",
        "text_inverse": "#FFFFFF",
        "title": "#F8FAFC",

        "success": "#22C55E",
        "success_bg": "#12351F",
        "success_border": "#256D3E",

        "warning": "#F59E0B",
        "warning_bg": "#3A2A0B",
        "warning_border": "#7A5410",

        "danger": "#F87171",
        "danger_bg": "#3A1515",
        "danger_border": "#7A2E2E",

        "info": "#60A5FA",
        "info_bg": "#152B4A",
        "info_border": "#2F5F9D",

        "purple": "#A78BFA",
        "purple_bg": "#2E2350",
        "purple_border": "#5B45A0",

        "table_header_bg": "#243244",
        "table_alt_bg": "#1B2533",
        "table_grid": "#374151",
        "table_selected_bg": "#1E3A5F",

        "shadow": "#000000",
    },
}


DEFAULT_THEME_KEY = "medical_blue"


def get_theme(theme_key: str = DEFAULT_THEME_KEY) -> Dict[str, str]:
    """
    获取主题。

    参数：
        theme_key:
            medical_blue / medical_green / dark_blue

    返回：
        主题颜色字典。返回 deepcopy，避免外部修改 THEMES。
    """
    key = str(theme_key or "").strip()
    if key not in THEMES:
        key = DEFAULT_THEME_KEY
    return deepcopy(THEMES[key])


def get_theme_name(theme_key: str = DEFAULT_THEME_KEY) -> str:
    theme = get_theme(theme_key)
    return theme.get("name", theme_key)


def available_themes() -> Dict[str, str]:
    """
    返回可选主题列表。

    示例：
        {
            "medical_blue": "医疗蓝",
            "medical_green": "科研绿",
            "dark_blue": "深色蓝"
        }
    """
    return {key: value.get("name", key) for key, value in THEMES.items()}