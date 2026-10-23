"""MCP 层共享的领域常量与判定函数。

这里是 MCP 侧的**唯一事实源**：两个 server 都不再各自硬编码记录类型、
阈值。数值与 settings.py 保持一致，由 tests/test_core_parity.py 断言，
避免两边漂移。

刻意不 import settings：只读 server 在容器里独立部署，不应把整个应用
依赖（Flask、AI client 等）拖进去。
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# 记录类型
# ---------------------------------------------------------------------------

# 血糖类记录的 type 取值（这些类型的 value 字段才有血糖含义）
GLUCOSE_TYPES: tuple[str, ...] = (
    "CGM",
    "空腹",
    "餐前",
    "餐后",
    "餐后1小时",
    "餐后2小时",
    "睡前",
    "随机",
    "血糖",
    "运动后",
    "加餐",
)

# 统计/分析实际使用的子集（历史数据里稳定存在的几种）
GLUCOSE_ANALYSIS_TYPES: tuple[str, ...] = ("CGM", "空腹", "餐后", "睡前", "随机")

# 「最近一次血糖」用的类型（与历史数据写法保持一致）
GLUCOSE_LATEST_TYPES: tuple[str, ...] = ("CGM", "空腹", "餐后2小时", "睡前", "血糖")

# 血糖统计接受的测量类型（比分析子集宽，含加餐/运动后）
GLUCOSE_SUMMARY_TYPES: tuple[str, ...] = (
    "CGM",
    "空腹",
    "餐后2小时",
    "睡前",
    "血糖",
    "运动后",
    "加餐",
)

# 运动类记录的 type 取值
EXERCISE_TYPES: tuple[str, ...] = ("跑步", "走路", "健身", "运动", "晨跑前")

# 运动距离分层（km）：高 >= 3，中 1~3，低 < 1
EXERCISE_KM_HIGH: float = 3.0
EXERCISE_KM_LOW: float = 1.0

# ---------------------------------------------------------------------------
# 阈值（单位 mmol/L / mmHg）
# ---------------------------------------------------------------------------

# 目标范围内时间（TIR）区间：对齐 settings.GLUCOSE_TARGETS['default']
TIR_LOW: float = 3.9
TIR_HIGH: float = 10.0

# 低血糖 / 高血糖告警阈值
HYPOGLYCEMIA: float = 3.9
HYPERGLYCEMIA: float = 13.9

# 夜间高血糖（CGM 且凌晨 2-5 点）
NOCTURNAL_HIGH: float = 10.0
NOCTURNAL_HOURS: tuple[int, ...] = (2, 3, 4, 5)

# 血压判定
BP_HIGH: tuple[int, int] = (140, 90)
BP_WARN: tuple[int, int] = (130, 85)


def in_target_range(value: float) -> bool:
    """血糖值是否落在 TIR 目标区间内（闭区间）。"""
    return TIR_LOW <= value <= TIR_HIGH


def classify_bp(systolic: int, diastolic: int) -> str:
    """血压状态：偏高 / 警戒 / 正常。

    阈值取「达到即算」，与临床判定习惯一致（140/90 已属偏高）。
    """
    if systolic >= BP_HIGH[0] or diastolic >= BP_HIGH[1]:
        return "偏高"
    if systolic >= BP_WARN[0] or diastolic >= BP_WARN[1]:
        return "警戒"
    return "正常"


def is_high_bp(systolic: int, diastolic: int) -> bool:
    """是否达到高血压判定线（>=140/90）。"""
    return systolic >= BP_HIGH[0] or diastolic >= BP_HIGH[1]
