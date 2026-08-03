# -*- coding: utf-8 -*-
"""统一的蛋白分析结果校正服务。

设计边界：
1. 数据库、CSV、TIFF 和原始测量结果保持不变；
2. 当前病例最多允许一个“最新有效尾部结果”作为病例内部校正来源；
3. 内部尾部比例只作用于头部荧光强度；
4. 荧光强度整体系数、标定率整体系数只作用于最终展示和报告；
5. 可选让展示用共定位数按校正后的标定率与精子总数同步；
6. 最终荧光强度限制在等效 8-bit 的 0～255，最终标定率限制在 0%～100%；
7. 数据库中的原始精子数、共定位数和标定率始终保持不变。
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, Optional


class ResultAdjustmentService:
    """统一计算页面和报告需要的校正后结果。"""

    def __init__(self, database, config):
        self.database = database
        self.config = config

    # ------------------------------------------------------------------
    # 基础数值处理
    # ------------------------------------------------------------------
    @staticmethod
    def _finite_float(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except Exception:
            return None
        if not math.isfinite(number):
            return None
        return number

    @classmethod
    def _nonnegative_int(cls, value: Any) -> Optional[int]:
        number = cls._finite_float(value)
        if number is None or number < 0.0:
            return None
        return int(round(number))

    @staticmethod
    def _normalize_part(value: Any) -> str:
        text = str(value or "").strip().lower()
        if text in {"head", "tail"}:
            return text
        return ""

    @staticmethod
    def _record_id(row: Dict[str, Any]) -> int:
        try:
            return int(row.get("id", 0) or 0)
        except Exception:
            return 0

    def _normalize_protein_key(self, value: Any) -> str:
        try:
            return str(self.config.normalize_protein_key(value) or "").strip()
        except Exception:
            return str(value or "").strip().lower()

    def _default_tail_result(self, reason: str, *, conflict: bool = False) -> Dict[str, Any]:
        ratio = self.config.get_default_tail_rate_ratio()
        message = f"{str(reason or '').rstrip('。')}。当前使用默认尾部比例 {ratio:g}。"
        return {
            "rate_ratio": ratio,
            "raw_rate_percent": None,
            "applied_real_tail_rate": False,
            "used_default_tail_rate": True,
            "source_record_id": None,
            "source_protein_key": "",
            "source_protein_name": "",
            "multiple_tail_conflict": bool(conflict),
            "message": message,
        }

    # ------------------------------------------------------------------
    # 当前病例尾部内部比例
    # ------------------------------------------------------------------
    def get_case_tail_rate(self, case_id: Any) -> Dict[str, Any]:
        """返回当前病例唯一最新有效尾部结果产生的 0~1 比例。

        处理顺序：
        1. 只读取当前 case_id；
        2. 每个蛋白只保留最新一条 status=完成 的记录；
        3. 再检查这些最新记录中有几个 tail；
        4. 无 tail 或多 tail 均回退配置默认值；
        5. 唯一 tail 的 expression_rate 单位按数据库 0~100 转成 0~1。
        """
        if case_id in (None, ""):
            return self._default_tail_result(
                "当前病例缺少数据库 ID"
            )

        try:
            rows = list(self.database.get_protein_analysis_by_case(case_id) or [])
        except Exception as exc:
            return self._default_tail_result(
                f"读取当前病例尾部结果失败：{exc}"
            )

        latest_by_protein: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            status = str(row.get("status", "") or "").strip()
            if status != "完成":
                continue

            protein_key = self._normalize_protein_key(row.get("protein_name", ""))
            if not protein_key:
                continue

            old = latest_by_protein.get(protein_key)
            if old is None or self._record_id(row) > self._record_id(old):
                latest_by_protein[protein_key] = row

        tail_rows = [
            row
            for row in latest_by_protein.values()
            if self._normalize_part(row.get("protein_part")) == "tail"
        ]

        if not tail_rows:
            return self._default_tail_result(
                "当前病例尚无有效尾部结果"
            )

        if len(tail_rows) > 1:
            names = ", ".join(
                str(row.get("protein_name", "") or "-") for row in tail_rows
            )
            return self._default_tail_result(
                f"当前病例存在多个最新尾部结果（{names}），为避免误用已回退默认比例",
                conflict=True,
            )

        row = tail_rows[0]
        raw_rate = self._finite_float(row.get("expression_rate"))
        if raw_rate is None or not (0.0 <= raw_rate <= 100.0):
            return self._default_tail_result(
                "当前病例尾部标定率无效"
            )

        ratio = raw_rate / 100.0
        protein_name = str(row.get("protein_name", "") or "").strip()
        protein_key = self._normalize_protein_key(protein_name)
        return {
            "rate_ratio": ratio,
            "raw_rate_percent": raw_rate,
            "applied_real_tail_rate": True,
            "used_default_tail_rate": False,
            "source_record_id": self._record_id(row),
            "source_protein_key": protein_key,
            "source_protein_name": protein_name,
            "multiple_tail_conflict": False,
            "message": f"已应用当前病例尾部原始标定率：{raw_rate:.2f}%（内部比例 {ratio:.4f}）。",
        }

    # ------------------------------------------------------------------
    # 统一结果校正
    # ------------------------------------------------------------------
    def adjust_result(
        self,
        case_id: Any,
        protein_key: Any,
        protein_part: Any,
        raw_mean_intensity: Any,
        raw_expression_rate: Any,
        raw_total_sperm_count: Any = None,
        raw_positive_count: Any = None,
    ) -> Dict[str, Any]:
        raw_intensity = self._finite_float(raw_mean_intensity)
        raw_rate = self._finite_float(raw_expression_rate)
        total_sperm_count = self._nonnegative_int(raw_total_sperm_count)
        positive_count = self._nonnegative_int(raw_positive_count)
        part = self._normalize_part(protein_part)
        normalized_key = self._normalize_protein_key(protein_key)

        result = {
            "protein_key": normalized_key,
            "protein_part": part,
            "raw_mean_intensity": raw_intensity,
            "raw_expression_rate": raw_rate,
            "adjusted_mean_intensity": raw_intensity,
            "unclamped_mean_intensity": raw_intensity,
            "mean_intensity_was_capped": False,
            "adjusted_expression_rate": raw_rate,
            "unclamped_expression_rate": raw_rate,
            "expression_rate_was_capped": False,
            "raw_total_sperm_count": total_sperm_count,
            "raw_positive_count": positive_count,
            "adjusted_positive_count": positive_count,
            "positive_count_sync_enabled": False,
            "positive_count_was_adjusted": False,
            "case_tail_rate_ratio": 1.0,
            "applied_real_tail_rate": False,
            "used_default_tail_rate": False,
            "multiple_tail_conflict": False,
            "message": "结果校正未启用。",
        }

        if not self.config.is_result_adjustment_enabled():
            return result

        intensity_factor = self.config.get_fluorescence_result_factor()
        rate_factor = self.config.get_expression_rate_result_factor()

        # 先完成全部荧光强度乘法，随后统一限制到等效 8-bit 的 0～255。
        # 数据库中的原始值保持不变；这里只限制页面、报告和参考范围判断使用的最终值。
        unclamped_intensity = (
            None if raw_intensity is None else raw_intensity * intensity_factor
        )

        # 标定率属于百分比结果，最终展示值必须限制在 0%～100%。
        # 数据库中的原始值保持不变；这里只限制统一校正服务返回给页面和报告的结果。
        unclamped_rate = None if raw_rate is None else raw_rate * rate_factor
        adjusted_rate = (
            None
            if unclamped_rate is None
            else min(max(unclamped_rate, 0.0), 100.0)
        )

        sync_positive_count = (
            self.config.is_sync_positive_count_with_expression_rate()
        )
        adjusted_positive_count = positive_count
        positive_count_sync_applied = bool(
            sync_positive_count
            and total_sperm_count is not None
            and adjusted_rate is not None
        )
        if positive_count_sync_applied:
            # 计数必须与最终展示标定率一致。计数均为非负数，因此 floor(x + 0.5)
            # 表示常规四舍五入，避免 Python round() 的银行家舍入造成 0.5 偏差。
            calculated_count = int(
                math.floor(total_sperm_count * adjusted_rate / 100.0 + 0.5)
            )
            adjusted_positive_count = min(
                max(calculated_count, 0),
                total_sperm_count,
            )

        tail_info = {
            "rate_ratio": 1.0,
            "applied_real_tail_rate": False,
            "used_default_tail_rate": False,
            "multiple_tail_conflict": False,
            "message": "已应用最终结果整体系数。",
        }

        use_tail_rate = (
            self.config.is_use_case_tail_rate_for_head_intensity()
            and part == "head"
        )

        if use_tail_rate:
            tail_info = self.get_case_tail_rate(case_id)
            if unclamped_intensity is not None:
                unclamped_intensity *= float(tail_info.get("rate_ratio", 1.0) or 0.0)

        adjusted_intensity = (
            None
            if unclamped_intensity is None
            else min(max(unclamped_intensity, 0.0), 255.0)
        )

        adjustment_message = str(tail_info.get("message", "") or "")
        if positive_count_sync_applied:
            sync_message = "共定位数已按校正后标定率与精子总数同步。"
            adjustment_message = (
                f"{adjustment_message.rstrip('。')}。{sync_message}"
                if adjustment_message
                else sync_message
            )

        result.update(
            {
                "adjusted_mean_intensity": adjusted_intensity,
                "unclamped_mean_intensity": unclamped_intensity,
                "mean_intensity_was_capped": bool(
                    unclamped_intensity is not None
                    and adjusted_intensity != unclamped_intensity
                ),
                "adjusted_expression_rate": adjusted_rate,
                "unclamped_expression_rate": unclamped_rate,
                "expression_rate_was_capped": bool(
                    unclamped_rate is not None and adjusted_rate != unclamped_rate
                ),
                "adjusted_positive_count": adjusted_positive_count,
                "positive_count_sync_enabled": bool(sync_positive_count),
                "positive_count_was_adjusted": bool(
                    positive_count_sync_applied
                    and adjusted_positive_count != positive_count
                ),
                "case_tail_rate_ratio": float(tail_info.get("rate_ratio", 1.0) or 0.0),
                "applied_real_tail_rate": bool(tail_info.get("applied_real_tail_rate")),
                "used_default_tail_rate": bool(tail_info.get("used_default_tail_rate")),
                "multiple_tail_conflict": bool(tail_info.get("multiple_tail_conflict")),
                "message": adjustment_message,
            }
        )
        return result

    def adjust_row(
        self,
        case_id: Any,
        row: Dict[str, Any],
        *,
        protein_key: Any = "",
        protein_part: Any = "",
    ) -> Dict[str, Any]:
        """返回带 display_* 字段的行副本，不修改原始 row。"""
        copied = dict(row or {})
        resolved_key = protein_key or copied.get("protein_key") or copied.get("protein_name")
        resolved_part = protein_part or copied.get("protein_part")
        adjusted = self.adjust_result(
            case_id=case_id,
            protein_key=resolved_key,
            protein_part=resolved_part,
            raw_mean_intensity=copied.get("mean_intensity"),
            raw_expression_rate=copied.get("expression_rate"),
            raw_total_sperm_count=copied.get(
                "total_sperm_count",
                copied.get("sperm_count"),
            ),
            raw_positive_count=copied.get("positive_count"),
        )
        copied["display_mean_intensity"] = adjusted.get("adjusted_mean_intensity")
        copied["display_expression_rate"] = adjusted.get("adjusted_expression_rate")
        copied["display_positive_count"] = adjusted.get("adjusted_positive_count")
        copied["result_adjustment"] = adjusted
        return copied

    # ------------------------------------------------------------------
    # 统一格式化
    # ------------------------------------------------------------------
    def format_number(self, value: Any, *, suffix: str = "") -> str:
        number = self._finite_float(value)
        if number is None:
            return "--"
        decimals = self.config.get_result_display_decimals()
        return f"{number:.{decimals}f}{suffix}"

    def format_intensity(self, value: Any) -> str:
        return self.format_number(value)

    def format_expression_rate(self, value: Any) -> str:
        return self.format_number(value, suffix="%")
