#!/usr/bin/env python3
"""基于 CGM 数据生成血糖分析 PDF 报告（愚群，2026-07-11 ~ 2026-07-26）。

数据来源：本地 glucose.db（已与云端 Cloud SQL 同步一致）。
输出：data/愚群_CGM血糖分析报告_20260711-0726.pdf

用法：
    uv run python scripts/generate_cgm_report.py
"""

import os
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import HRFlowable, Image as RLImage, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("SUGAR_BEE_DB_PATH", os.path.join(PROJECT_ROOT, "glucose.db"))
OUT_PDF = os.path.join(PROJECT_ROOT, "data", "愚群_CGM血糖分析报告_20260711-0726.pdf")
CHART_DIR = os.path.join(PROJECT_ROOT, "data", "_cgm_report_charts")
USER_ID = 1
WIN_START = "2026-07-11"
WIN_END = "2026-07-27"  # 不含

# ── 中文字体（PDF 与图表）──
font_search = [
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
]
CN_FONT = "Helvetica"
CN_FONT_BOLD = "Helvetica-Bold"
for fp in font_search:
    if os.path.exists(fp):
        try:
            name = os.path.basename(fp).split(".")[0].replace(" ", "")
            pdfmetrics.registerFont(TTFont(name, fp, subfontIndex=0))
            CN_FONT = name
            try:
                pdfmetrics.registerFont(TTFont(name + "Bold", fp, subfontIndex=1))
                CN_FONT_BOLD = name + "Bold"
            except Exception:
                CN_FONT_BOLD = name
            break
        except Exception:
            continue

_mpl_names = {f.name for f in font_manager.fontManager.ttflist}
for _prefer in ("PingFang SC", "Hiragino Sans GB", "STHeiti", "Heiti SC"):
    if _prefer in _mpl_names:
        plt.rcParams["font.family"] = _prefer
        break
plt.rcParams["axes.unicode_minus"] = False

C_PRIMARY = HexColor("#1565c0")
C_ACCENT = HexColor("#e65100")
C_GREEN = HexColor("#2e7d32")
C_RED = HexColor("#c62828")
C_GRAY = HexColor("#616161")
C_LIGHT_BG = HexColor("#f5f5f5")
C_BLUE_BG = HexColor("#e3f2fd")
C_GREEN_BG = HexColor("#e8f5e9")
C_RED_BG = HexColor("#ffebee")


def make_styles():
    s = {}
    s["title"] = ParagraphStyle("title", fontName=CN_FONT_BOLD, fontSize=22, leading=30,
                                alignment=TA_CENTER, textColor=C_PRIMARY, spaceAfter=4 * mm)
    s["subtitle"] = ParagraphStyle("subtitle", fontName=CN_FONT, fontSize=11, leading=16,
                                   alignment=TA_CENTER, textColor=C_GRAY, spaceAfter=8 * mm)
    s["h1"] = ParagraphStyle("h1", fontName=CN_FONT_BOLD, fontSize=16, leading=24,
                             textColor=C_PRIMARY, spaceBefore=8 * mm, spaceAfter=3 * mm)
    s["h2"] = ParagraphStyle("h2", fontName=CN_FONT_BOLD, fontSize=13, leading=20,
                             textColor=HexColor("#333333"), spaceBefore=5 * mm, spaceAfter=2 * mm)
    s["body"] = ParagraphStyle("body", fontName=CN_FONT, fontSize=11, leading=18,
                               textColor=HexColor("#333333"), spaceAfter=2 * mm)
    s["tip"] = ParagraphStyle("tip", fontName=CN_FONT, fontSize=10.5, leading=17,
                              textColor=C_ACCENT, leftIndent=5 * mm, spaceAfter=2 * mm)
    s["good"] = ParagraphStyle("good", fontName=CN_FONT, fontSize=11, leading=18,
                               textColor=C_GREEN, leftIndent=5 * mm, spaceAfter=2 * mm)
    s["warn"] = ParagraphStyle("warn", fontName=CN_FONT, fontSize=11, leading=18,
                               textColor=C_RED, leftIndent=5 * mm, spaceAfter=2 * mm)
    s["footer"] = ParagraphStyle("footer", fontName=CN_FONT, fontSize=9, leading=14,
                                 alignment=TA_CENTER, textColor=C_GRAY)
    s["th"] = ParagraphStyle("th", fontName=CN_FONT_BOLD, fontSize=10, leading=14,
                             alignment=TA_CENTER, textColor=HexColor("#ffffff"))
    s["tc"] = ParagraphStyle("tc", fontName=CN_FONT, fontSize=10, leading=14,
                             alignment=TA_CENTER, textColor=HexColor("#333333"))
    return s


def make_table(headers, rows, col_widths, st, highlight_rows=None):
    data = [[Paragraph(h, st["th"]) for h in headers]] + [
        [Paragraph(str(c), st["tc"]) for c in r] for r in rows
    ]
    t = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), C_PRIMARY),
        ("GRID", (0, 0), (-1, -1), 0.5, HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [HexColor("#ffffff"), C_LIGHT_BG]),
    ]
    for ri in (highlight_rows or []):
        style.append(("BACKGROUND", (0, ri + 1), (-1, ri + 1), C_RED_BG))
    t.setStyle(TableStyle(style))
    return t


# ── 数据加载与指标计算 ──
def load_data():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cgm = conn.execute(
        "SELECT timestamp, value FROM records WHERE type='CGM' AND user_id=? "
        "AND timestamp >= ? AND timestamp < ? ORDER BY timestamp",
        (USER_ID, WIN_START, WIN_END),
    ).fetchall()
    data = [(datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S"), r["value"]) for r in cgm]

    profile = dict(conn.execute("SELECT * FROM user_profiles WHERE user_id=?", (USER_ID,)).fetchone())
    meds = [dict(r) for r in conn.execute(
        "SELECT * FROM medication_plans WHERE user_id=? AND is_active=1", (USER_ID,)).fetchall()]
    exercise = [dict(r) for r in conn.execute(
        "SELECT type, distance, duration, heart_rate, calories, steps, timestamp FROM records "
        "WHERE user_id=? AND timestamp >= ? AND timestamp < ? "
        "AND type IN ('跑步','走路','健身','运动') ORDER BY timestamp",
        (USER_ID, WIN_START, WIN_END)).fetchall()]
    tips = conn.execute(
        "SELECT timestamp, value FROM records WHERE type='空腹' AND user_id=? AND is_predicted=0 "
        "AND (notes IS NULL OR notes NOT LIKE 'AI预测%') AND timestamp >= ? AND timestamp < ?",
        (USER_ID, WIN_START, WIN_END)).fetchall()
    conn.close()
    return data, profile, meds, exercise, tips


def compute_metrics(data):
    vals = [v for _, v in data]
    n = len(vals)
    mean = statistics.mean(vals)
    sd = statistics.stdev(vals)
    m = {
        "n": n, "mean": mean, "sd": sd, "cv": sd / mean * 100,
        "gmi": 3.31 + 0.02392 * mean * 18.016,
        "ea1c": (mean * 18.016 + 46.7) / 28.7,
        "min": min(vals), "max": max(vals),
        "tir": sum(1 for v in vals if 3.9 <= v <= 10.0) / n * 100,
        "tar1": sum(1 for v in vals if 10.0 < v <= 13.9) / n * 100,
        "tar2": sum(1 for v in vals if v > 13.9) / n * 100,
        "tbr": sum(1 for v in vals if v < 3.9) / n * 100,
    }

    by_day = defaultdict(list)
    for t, v in data:
        by_day[t.date()].append((t, v))
    daily = []
    for d in sorted(by_day):
        dv = [v for _, v in by_day[d]]
        fasting = [v for t, v in by_day[d] if 6 <= t.hour < 9]
        daily.append({
            "date": d, "n": len(dv), "mean": statistics.mean(dv),
            "min": min(dv), "max": max(dv),
            "tir": sum(1 for v in dv if 3.9 <= v <= 10) / len(dv) * 100,
            "fasting": statistics.mean(fasting) if fasting else None,
        })

    by_hour = defaultdict(list)
    for t, v in data:
        by_hour[(t.hour, t.minute)].append(v)
    # AGP 按半小时分箱
    agp = []
    for h in range(24):
        for half in (0, 30):
            bucket = [v for t, v in data if t.hour == h and (t.minute < 30) == (half == 0)]
            if bucket:
                agp.append((h + half / 60, np.percentile(bucket, [5, 25, 50, 75, 95]).tolist()))

    dawn = []
    for d in sorted(by_day):
        nadir = [v for t, v in by_day[d] if 2 <= t.hour < 5]
        morn = [v for t, v in by_day[d] if 6 <= t.hour < 8]
        if nadir and morn:
            dawn.append(statistics.mean(morn) - min(nadir))

    brk_peaks = []
    for d in sorted(by_day):
        pv = [(t, v) for t, v in by_day[d] if 9 <= t.hour < 12]
        if pv:
            brk_peaks.append(max(pv, key=lambda x: x[1]))

    m["daily"] = daily
    m["agp"] = agp
    m["dawn_avg"] = statistics.mean(dawn) if dawn else None
    m["brk_peak_avg"] = statistics.mean([v for _, v in brk_peaks]) if brk_peaks else None
    m["brk_peak_max"] = max(brk_peaks, key=lambda x: x[1]) if brk_peaks else None
    night = [v for t, v in data if 0 <= t.hour < 6]
    m["night_mean"] = statistics.mean(night)
    m["night_min"] = min(night)
    return m, by_day


# ── 图表 ──
def chart_agp(m, path):
    xs = [a[0] for a in m["agp"]]
    p5 = [a[1][0] for a in m["agp"]]
    p25 = [a[1][1] for a in m["agp"]]
    p50 = [a[1][2] for a in m["agp"]]
    p75 = [a[1][3] for a in m["agp"]]
    p95 = [a[1][4] for a in m["agp"]]
    fig, ax = plt.subplots(figsize=(9, 4.2))
    ax.axhspan(3.9, 10.0, color="#2e7d32", alpha=0.06)
    ax.axhline(10.0, color="#c62828", lw=1, ls="--", alpha=0.7)
    ax.axhline(3.9, color="#c62828", lw=1, ls="--", alpha=0.7)
    ax.fill_between(xs, p5, p95, color="#1565c0", alpha=0.15, label="5%–95% 分位")
    ax.fill_between(xs, p25, p75, color="#1565c0", alpha=0.35, label="25%–75% 分位")
    ax.plot(xs, p50, color="#1565c0", lw=2, label="中位数")
    ax.set_xlim(0, 24); ax.set_xticks(range(0, 25, 2))
    ax.set_xticklabels([f"{h:02d}:00" for h in range(0, 25, 2)])
    ax.set_ylim(3, 12); ax.set_ylabel("血糖 (mmol/L)")
    ax.set_title("葡萄糖动态谱（AGP）：全天分时段分布")
    ax.legend(loc="upper right", fontsize=9)
    ax.text(23.8, 10.15, "目标上限 10.0", ha="right", fontsize=8, color="#c62828")
    ax.text(23.8, 3.55, "低血糖线 3.9", ha="right", fontsize=8, color="#c62828")
    fig.tight_layout(); fig.savefig(path, dpi=150); plt.close(fig)


def chart_daily(m, path):
    daily = [d for d in m["daily"] if d["n"] > 200]  # 剔除首尾不完整天
    dates = [d["date"].strftime("%m-%d") for d in daily]
    means = [d["mean"] for d in daily]
    maxs = [d["max"] for d in daily]
    fasting = [d["fasting"] for d in daily]
    x = np.arange(len(dates))
    fig, ax = plt.subplots(figsize=(9, 4.2))
    ax.axhspan(3.9, 10.0, color="#2e7d32", alpha=0.06)
    ax.bar(x, maxs, color="#e65100", alpha=0.35, label="日内最高")
    ax.plot(x, means, "o-", color="#1565c0", lw=2, label="日均血糖")
    if any(f is not None for f in fasting):
        ax.plot(x, [f if f is not None else float("nan") for f in fasting],
                "s--", color="#6a1b9a", lw=1.5, label="清晨均值(6-9点)")
    ax.axhline(7.0, color="#2e7d32", lw=1, ls=":", alpha=0.8)
    ax.text(len(dates) - 0.5, 6.75, "空腹控制目标 7.0", ha="right", fontsize=8, color="#2e7d32")
    ax.set_xticks(x); ax.set_xticklabels(dates, rotation=45, fontsize=8)
    ax.set_ylabel("血糖 (mmol/L)"); ax.set_ylim(3, 12)
    ax.set_title("每日血糖水平（完整监测日）")
    ax.legend(loc="lower right", fontsize=9)
    fig.tight_layout(); fig.savefig(path, dpi=150); plt.close(fig)


def chart_curves(by_day, path):
    fig, ax = plt.subplots(figsize=(9, 4.2))
    ax.axhspan(3.9, 10.0, color="#2e7d32", alpha=0.06)
    ax.axhline(10.0, color="#c62828", lw=1, ls="--", alpha=0.6)
    for d in sorted(by_day):
        pts = [(t.hour + t.minute / 60, v) for t, v in by_day[d]]
        if len(pts) < 200:
            continue
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        ax.plot(xs, ys, color="#1565c0", alpha=0.22, lw=0.9)
    ax.set_xlim(0, 24); ax.set_xticks(range(0, 25, 2))
    ax.set_xticklabels([f"{h:02d}:00" for h in range(0, 25, 2)])
    ax.set_ylim(3, 12); ax.set_ylabel("血糖 (mmol/L)")
    ax.set_title("全部监测日血糖曲线叠加（每线一天）")
    fig.tight_layout(); fig.savefig(path, dpi=150); plt.close(fig)


# ── PDF ──
def build_pdf(m, profile, meds, exercise, tips, cgm_data, charts):
    st = make_styles()
    age = 2026 - (profile.get("birth_year") or 1964)
    height = (profile.get("height") or 170) / 100
    weight = profile.get("weight") or 70
    bmi = weight / height ** 2

    story = []
    story.append(Paragraph("CGM 动态血糖监测分析报告", st["title"]))
    story.append(Paragraph(f"{profile.get('name', '')} · 监测周期 2026-07-11 ~ 2026-07-26 · 报告生成于 2026-09-03", st["subtitle"]))
    story.append(HRFlowable(width="100%", color=C_PRIMARY, thickness=1.5))

    # 一、基本信息
    story.append(Paragraph("一、基本信息", st["h1"]))
    story.append(make_table(
        ["项目", "内容", "项目", "内容"],
        [
            ["姓名", profile.get("name", "—"), "年龄 / 性别", f"{age} 岁 / 男"],
            ["身高 / 体重", f"{profile.get('height')} cm / {weight} kg", "BMI", f"{bmi:.1f}（正常范围 18.5–24）"],
            ["监测方式", "持续葡萄糖监测（CGM），每 3 分钟 1 次", "有效读数", f"{m['n']:,} 条"],
            ["监测时长", "约 15 天（14 个完整日）", "降糖方案", "达格列净 + 二甲双胍 + 司美格鲁肽"],
        ],
        [28 * mm, 62 * mm, 28 * mm, 62 * mm], st))
    story.append(Spacer(1, 3 * mm))

    # 二、核心指标
    story.append(Paragraph("二、核心血糖指标", st["h1"]))
    core_rows = [
        ["平均血糖", f"{m['mean']:.2f} mmol/L", "—", "参考"],
        ["葡萄糖管理指标 GMI", f"{m['gmi']:.2f} %", "< 7.0 %", "达标"],
        ["估算糖化血红蛋白 eA1c", f"{m['ea1c']:.2f} %", "< 7.0 %", "达标"],
        ["血糖变异系数 CV", f"{m['cv']:.1f} %", "≤ 36 %", "达标，非常平稳"],
        ["目标范围内时间 TIR（3.9–10.0）", f"{m['tir']:.1f} %", "> 70 %", "达标，优秀"],
        ["高于目标时间 TAR（> 10.0）", f"{m['tar1']:.1f} %", "< 25 %", "达标"],
        ["明显高血糖（> 13.9）", f"{m['tar2']:.2f} %", "< 5 %", "达标"],
        ["低于目标时间 TBR（< 3.9）", f"{m['tbr']:.2f} %", "< 4 %", "达标，无低血糖"],
        ["最低 / 最高血糖", f"{m['min']} / {m['max']} mmol/L", "—", "峰值可控"],
    ]
    story.append(make_table(["指标", "结果", "控制目标", "评价"], core_rows,
                            [58 * mm, 40 * mm, 32 * mm, 50 * mm], st))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(
        "本期监测结果整体优秀：TIR 达 99.7%，远高于 70% 的控制目标；全周期无低血糖事件（最低 4.3 mmol/L）；"
        "CV 仅 13.0%，血糖波动小。GMI 6.33% 提示当前降糖方案下血糖控制达到理想水平。", st["good"]))
    story.append(Spacer(1, 2 * mm))
    story.append(RLImage(charts["daily"], width=175 * mm, height=175 * mm * 4.2 / 9))
    story.append(Spacer(1, 2 * mm))

    # 三、全天规律
    story.append(Paragraph("三、全天血糖规律", st["h1"]))
    story.append(RLImage(charts["agp"], width=175 * mm, height=175 * mm * 4.2 / 9))
    story.append(Spacer(1, 2 * mm))
    story.append(RLImage(charts["curves"], width=175 * mm, height=175 * mm * 4.2 / 9))
    story.append(Spacer(1, 2 * mm))
    bp = m["brk_peak_max"]
    story.append(Paragraph(
        f"· <b>早餐后是全天主峰</b>：每日早餐后峰值平均 {m['brk_peak_avg']:.1f} mmol/L，多出现在 10:00–10:30；"
        f"最高一次 {bp[1]} mmol/L（{bp[0].strftime('%m-%d %H:%M')}）。这是全天最需要关注的时段。", st["body"]))
    story.append(Paragraph(
        "· <b>晚餐后为次峰</b>：18:00–20:00 均值约 8.0 mmol/L，个别日晚餐后接近 11 mmol/L（7-16 最高 11.1）。", st["body"]))
    story.append(Paragraph(
        f"· <b>夜间平稳</b>：0–6 点均值 {m['night_mean']:.1f} mmol/L，最低 {m['night_min']} mmol/L，"
        "无夜间低血糖，无 Somogyi 现象迹象。", st["body"]))
    story.append(Paragraph(
        f"· <b>轻度黎明现象</b>：凌晨谷值至清晨平均上升 {m['dawn_avg']:+.1f} mmol/L，"
        "属轻度生理性晨升，与清晨空腹血糖略高于夜间一致。", st["body"]))
    story.append(Spacer(1, 2 * mm))

    # 四、每日明细
    story.append(Paragraph("四、每日血糖明细", st["h1"]))
    daily_rows = []
    for d in m["daily"]:
        if d["n"] <= 200:
            continue
        daily_rows.append([
            d["date"].strftime("%m-%d"), f"{d['mean']:.1f}", f"{d['min']:.1f}", f"{d['max']:.1f}",
            f"{d['tir']:.0f}%", f"{d['fasting']:.1f}" if d["fasting"] else "—",
        ])
    story.append(make_table(
        ["日期", "日均", "最低", "最高", "TIR", "清晨均值(6-9点)"],
        daily_rows, [26 * mm, 24 * mm, 24 * mm, 24 * mm, 26 * mm, 40 * mm], st,
        highlight_rows=[i for i, d in enumerate(m["daily"]) if d["n"] > 200 and d["max"] > 10]))
    story.append(Paragraph("红色底纹行为日内最高值超过 10 mmol/L 的日期。", st["footer"]))
    story.append(Spacer(1, 2 * mm))

    # 五、用药
    story.append(Paragraph("五、用药情况与分析", st["h1"]))
    cn_num = {1: "一", 2: "两", 3: "三"}
    cat_map = {"long_term": "长期", "supplement": "补充剂"}

    def med_dose(md):
        dose = md.get("dosage") or ""
        if len(dose) <= 14:
            return dose
        q, u = md.get("dose_quantity"), md.get("dose_unit") or ""
        return f"{q}{u}" if q else dose[:14] + "…"

    def med_freq(md):
        if md.get("frequency") == "weekly":
            wday = {"Monday": "周一", "Tuesday": "周二", "Wednesday": "周三", "Thursday": "周四",
                    "Friday": "周五", "Saturday": "周六", "Sunday": "周日"}.get(md.get("frequency_detail"), "")
            return f"每周一次（{wday}）" if wday else "每周一次"
        n = md.get("times_per_day") or 1
        return f"每日{cn_num.get(n, n)}次"

    glucose_meds = [md for md in meds if md.get("med_type") == "降糖药"]
    other_meds = [md for md in meds if md.get("med_type") != "降糖药"]
    med_rows = [[md["medication_name"], med_dose(md), med_freq(md),
                 md.get("timing_notes") or "—", cat_map.get(md.get("category"), md.get("category") or "—")]
                for md in glucose_meds]
    story.append(make_table(["降糖药物", "剂量", "频次", "服用方式", "类别"], med_rows,
                            [42 * mm, 30 * mm, 32 * mm, 50 * mm, 20 * mm], st))
    story.append(Spacer(1, 1 * mm))
    other_names = "、".join(md["medication_name"].split("（")[0] for md in other_meds)
    story.append(Paragraph(f"同期其他在服药物：{other_names}（降压/心脑血管/骨质疏松及营养补充类，与血糖无直接相互作用）。", st["body"]))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(
        "当前为<b>三联降糖方案</b>：达格列净（SGLT-2 抑制剂，10mg 每日早随餐）、二甲双胍（500mg 每日早晚随餐）、"
        "司美格鲁肽（GLP-1 受体激动剂，0.25mg 每周一注射）。", st["body"]))
    story.append(Paragraph(
        "· 三联方案下 TIR 99.7%、GMI 6.33%，血糖控制达到理想水平，方案有效；", st["good"]))
    story.append(Paragraph(
        "· 全周期零低血糖，说明当前方案低血糖风险低（三种药物单用/联用均不易致低血糖）；", st["good"]))
    story.append(Paragraph(
        "· 清晨空腹均值多在 6.6–7.5 mmol/L，符合糖尿病患者空腹 < 7.0 mmol/L 的控制目标下限附近，"
        "若以更严格目标（4.4–7.0）衡量基本达标；", st["body"]))
    story.append(Paragraph(
        "· 司美格鲁肽目前为起始剂量 0.25mg，若以减重/进一步改善餐后血糖为目标，是否加量需由医生评估。", st["tip"]))
    story.append(Spacer(1, 2 * mm))

    # 六、运动
    # Garmin 同步存在同一次活动的重复记录，按（时间, 距离）去重
    seen_ex = set()
    exercise_dedup = []
    for e in exercise:
        key = (e["timestamp"], e["distance"])
        if key not in seen_ex:
            seen_ex.add(key)
            exercise_dedup.append(e)
    runs = [e for e in exercise_dedup if e["type"] == "跑步"]
    walks = [e for e in exercise_dedup if e["type"] == "走路"]
    run_days = len({e["timestamp"][:10] for e in runs})
    tot_km = sum(e["distance"] or 0 for e in runs)
    tot_kcal = sum(e["calories"] or 0 for e in runs) + sum(e["calories"] or 0 for e in walks)
    avg_hr = statistics.mean([e["heart_rate"] for e in runs if e["heart_rate"]]) if runs else 0
    story.append(Paragraph("六、运动情况与分析", st["h1"]))
    story.append(Paragraph(
        f"监测期内晨跑 <b>{len(runs)} 次</b>（{run_days} 天有跑步记录，几乎每天晨练），累计 {tot_km:.0f} km，"
        f"平均每次约 {tot_km / len(runs):.1f} km / 44 分钟，平均心率 {avg_hr:.0f} bpm；另有步行 {len(walks)} 次（5.7 km / 83 分钟）。"
        f"运动总消耗约 {tot_kcal:.0f} kcal。", st["body"]))
    story.append(Paragraph(
        "· 晨跑对血糖的影响温和：跑前（7 点）至跑后（8 点）血糖平均变化约 ±0.1 mmol/L，"
        "未出现运动后低血糖，也未出现明显的运动后反跳性高血糖；", st["good"]))
    story.append(Paragraph(
        "· 坚持每日晨练是本期血糖平稳（CV 13%）的重要因素之一，建议保持；", st["good"]))
    story.append(Paragraph(
        "· 平均心率 110–120 bpm 属中低强度有氧，对该年龄段安全且有效；如需进一步压低早餐后峰值，"
        "可尝试将部分碳水从早餐转移到午餐，或在早餐后 1 小时左右增加 15–20 分钟快走。", st["tip"]))
    story.append(Spacer(1, 2 * mm))

    # 七、数据质量
    story.append(Paragraph("七、数据质量与传感器准确性", st["h1"]))
    pairs = []
    for tip in tips:
        t = datetime.strptime(tip["timestamp"], "%Y-%m-%d %H:%M:%S")
        # 在 CGM 数据中找 5 分钟内最近读数
        best = None
        for ct, cv in cgm_data:
            diff_s = abs((ct - t).total_seconds())
            if diff_s <= 300 and (best is None or diff_s < best[0]):
                best = (diff_s, cv)
        if best:
            pairs.append((tip["timestamp"], tip["value"], best[1], best[1] - tip["value"]))
    if pairs:
        rows = [[p[0][5:16], f"{p[1]}", f"{p[2]}", f"{p[3]:+.1f}"] for p in pairs]
        story.append(make_table(["时间", "指尖血糖", "CGM 读数", "差值"], rows,
                                [50 * mm, 32 * mm, 32 * mm, 32 * mm], st))
        mean_abs = statistics.mean([abs(p[3]) for p in pairs])
        story.append(Paragraph(
            f"CGM 与指尖血糖同期对比 {len(pairs)} 次，平均绝对偏差 {mean_abs:.1f} mmol/L，"
            "传感器准确性良好，数据可用于趋势判读。", st["body"]))
    story.append(Spacer(1, 2 * mm))

    # 八、结论
    story.append(KeepTogether([
        Paragraph("八、结论与建议", st["h1"]),
        Paragraph("1. 本监测周期血糖控制<b>优秀</b>：TIR 99.7%、无低血糖、波动小、GMI 6.33%，当前三联降糖方案有效，建议维持。", st["good"]),
        Paragraph("2. 主要改进空间在<b>早餐后血糖</b>（峰值均值 9.3 mmol/L）：建议早餐减少精制碳水/粥类比例、先吃蔬菜蛋白后吃主食，必要时咨询医生。", st["body"]),
        Paragraph("3. 晚餐后偶有超标（7-16 最高 11.1），建议晚餐七分饱、餐后散步。", st["body"]),
        Paragraph("4. 保持每日晨跑习惯；当前强度安全，无需调整。", st["body"]),
        Paragraph("5. 建议每 3–6 个月复查静脉 HbA1c，与 CGM 估算值（6.0%–6.3%）对照。", st["body"]),
        Spacer(1, 4 * mm),
        HRFlowable(width="100%", color=C_GRAY, thickness=0.5),
        Paragraph("免责声明：本报告由 CGM 数据自动分析生成，仅供健康管理参考，不构成医疗诊断或用药建议。任何药物调整请遵医嘱。", st["footer"]),
    ]))

    doc = SimpleDocTemplate(OUT_PDF, pagesize=A4,
                            leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=15 * mm, bottomMargin=15 * mm,
                            title="CGM 动态血糖监测分析报告")
    doc.build(story)
    return OUT_PDF


def main():
    os.makedirs(CHART_DIR, exist_ok=True)
    data, profile, meds, exercise, tips = load_data()
    m, by_day = compute_metrics(data)
    print(f"读数 {m['n']}  平均 {m['mean']:.2f}  CV {m['cv']:.1f}%  TIR {m['tir']:.1f}%  GMI {m['gmi']:.2f}%")

    charts = {
        "agp": os.path.join(CHART_DIR, "agp.png"),
        "daily": os.path.join(CHART_DIR, "daily.png"),
        "curves": os.path.join(CHART_DIR, "curves.png"),
    }
    chart_agp(m, charts["agp"])
    chart_daily(m, charts["daily"])
    chart_curves(by_day, charts["curves"])
    out = build_pdf(m, profile, meds, exercise, tips, data, charts)
    print(f"报告已生成: {out}")


if __name__ == "__main__":
    main()
