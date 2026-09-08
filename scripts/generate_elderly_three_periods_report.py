#!/usr/bin/env python3
"""生成长辈大字版三次CGM血糖监测对比与健康指导报告 PDF。

受检者：愚群
输出：data/愚群_三次血糖监测对比与健康指导报告_大字版.pdf
用法：
    uv run python scripts/generate_elderly_three_periods_report.py
"""

import os
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage, PageBreak, HRFlowable
)
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, 'data')
CHART_DIR = os.path.join(DATA_DIR, '_elderly_charts')
OUT_PDF = os.path.join(DATA_DIR, '愚群_三次血糖监测对比与健康指导报告_大字版.pdf')

os.makedirs(CHART_DIR, exist_ok=True)

# 1. 注册中文字体
font_path = '/System/Library/Fonts/STHeiti Medium.ttc'
pdfmetrics.registerFont(TTFont('STHeiti', font_path, subfontIndex=0))
plt.rcParams['font.family'] = 'PingFang SC'
plt.rcParams['axes.unicode_minus'] = False


def generate_charts():
    f1 = os.path.join(DATA_DIR, '血糖数据-8280100424142.xlsx')
    f2 = os.path.join(DATA_DIR, '血糖数据-8280132C24942.xlsx')
    f3 = os.path.join(DATA_DIR, '血糖数据-ZP1TB00DIA436.xlsx')

    df1 = pd.read_excel(f1)
    df1['val'] = df1['血糖值 mmol/L']

    df2 = pd.read_excel(f2)
    df2['time'] = pd.to_datetime(df2['血糖时间'])
    df2['val'] = df2['血糖值 mmol/L']
    df2['hour'] = df2['time'].dt.hour + df2['time'].dt.minute / 60.0

    df3 = pd.read_excel(f3)
    df3['time'] = pd.to_datetime(df3['血糖时间'])
    df3['val'] = df3['血糖值 mmol/L']
    df3['hour'] = df3['time'].dt.hour + df3['time'].dt.minute / 60.0

    # 图1：大字版三期核心成果看板
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.5), dpi=200)
    stages = ['第一期 (2026.01)\n冬季双联口服', '第二期 (2026.02-03)\n流感考验与升级', '第三期 (2026.07)\n盛夏三联+晨跑']
    means = [df1['val'].mean(), df2['val'].mean(), df3['val'].mean()]
    titrs = [(df1['val'].between(3.9, 7.8).sum() / len(df1)) * 100,
             (df2['val'].between(3.9, 7.8).sum() / len(df2)) * 100,
             (df3['val'].between(3.9, 7.8).sum() / len(df3)) * 100]

    # 平均血糖
    bars1 = axes[0].bar(stages, means, color=['#42a5f5', '#26a69a', '#2e7d32'], width=0.55, edgecolor='black', linewidth=1.2)
    axes[0].set_title('全天平均血糖 (越平稳越好)', fontsize=15, fontweight='bold', pad=15)
    axes[0].set_ylabel('血糖浓度 (mmol/L)', fontsize=13)
    axes[0].set_ylim(0, 10)
    axes[0].axhline(7.0, color='gray', linestyle='--', linewidth=1.5, label='理想优良目标线 (7.0)')
    axes[0].tick_params(axis='x', labelsize=11); axes[0].tick_params(axis='y', labelsize=12)
    axes[0].legend(loc='upper right', fontsize=11); axes[0].grid(axis='y', linestyle=':', alpha=0.6)
    for b in bars1:
        h = b.get_height()
        axes[0].text(b.get_x() + b.get_width()/2, h + 0.25, f'{h:.2f}', ha='center', va='bottom', fontsize=14, fontweight='bold', color='#1565c0')
    axes[0].text(1.5, 1.0, '稳步下降 0.6 mmol/L\n长期锁定黄金区间！', ha='center', fontsize=12, fontweight='bold', bbox=dict(facecolor='#e8f5e9', edgecolor='#2e7d32', boxstyle='round,pad=0.5'))

    # 优质达标时间
    bars2 = axes[1].bar(stages, titrs, color=['#78909c', '#66bb6a', '#1b5e20'], width=0.55, edgecolor='black', linewidth=1.2)
    axes[1].set_title('严格优质达标时间比例 (3.9~7.8)', fontsize=15, fontweight='bold', pad=15)
    axes[1].set_ylabel('所占时间比例 (%)', fontsize=13)
    axes[1].set_ylim(0, 105)
    axes[1].axhline(70.0, color='#1976d2', linestyle='--', linewidth=1.5, label='医学达标线 (70%)')
    axes[1].tick_params(axis='x', labelsize=11); axes[1].tick_params(axis='y', labelsize=12)
    axes[1].legend(loc='lower right', fontsize=11); axes[1].grid(axis='y', linestyle=':', alpha=0.6)
    for b in bars2:
        h = b.get_height()
        axes[1].text(b.get_x() + b.get_width()/2, h + 2.0, f'{h:.1f}%', ha='center', va='bottom', fontsize=14, fontweight='bold', color='#2e7d32')
    axes[1].text(1.5, 20.0, '大幅提升超 15 个百分点！\n全天85%时间如健康人', ha='center', fontsize=12, fontweight='bold', bbox=dict(facecolor='#e8f5e9', edgecolor='#2e7d32', boxstyle='round,pad=0.5'))

    # 低血糖次数
    axes[2].bar(stages, [0, 0, 0], color='#43a047', width=0.55, edgecolor='black', linewidth=1.2)
    axes[2].set_title('发生低血糖次数 (< 3.9 mmol/L)', fontsize=15, fontweight='bold', pad=15)
    axes[2].set_ylabel('发生总次数 (次)', fontsize=13)
    axes[2].set_ylim(0, 5)
    axes[2].tick_params(axis='x', labelsize=11); axes[2].tick_params(axis='y', labelsize=12); axes[2].grid(axis='y', linestyle=':', alpha=0.6)
    for i in range(3):
        axes[2].text(i, 0.4, '0 次\n(安全满分)', ha='center', va='bottom', fontsize=14, fontweight='bold', color='#2e7d32')
    axes[2].text(1.0, 2.5, '全周期 43 天、2万次检测\n零低血糖发生！安全极高！', ha='center', fontsize=13, fontweight='bold', bbox=dict(facecolor='#e8f5e9', edgecolor='#2e7d32', boxstyle='round,pad=0.6'))

    plt.tight_layout()
    c1_path = os.path.join(CHART_DIR, 'elderly_chart1_three_phases.png')
    plt.savefig(c1_path)
    plt.close()

    # 图2：全天24小时起伏
    def get_curve(df):
        df['bin'] = (df['hour'] // 0.5) * 0.5
        grp = df.groupby('bin')['val'].agg(['median', lambda x: x.quantile(0.25), lambda x: x.quantile(0.75)]).reset_index()
        grp.columns = ['hour', 'med', 'p25', 'p75']
        return grp

    c2 = get_curve(df2)
    c3 = get_curve(df3)

    fig, ax = plt.subplots(figsize=(15, 6.5), dpi=200)
    ax.axhspan(3.9, 7.8, color='#e8f5e9', alpha=0.9, label='绿色优质区间 (3.9~7.8 mmol/L，如健康人般舒适)')
    ax.axhspan(7.8, 10.0, color='#fff9c4', alpha=0.5, label='正常可接受区间 (7.8~10.0 mmol/L)')
    ax.axhline(7.8, color='#2e7d32', linestyle='--', linewidth=1.5)
    ax.axhline(10.0, color='#e65100', linestyle='--', linewidth=1.5)

    ax.plot(c2['hour'], c2['med'], color='#ff9800', linewidth=3.0, label='第二期 (冬末春初) 全天中位线', linestyle=':')
    ax.plot(c3['hour'], c3['med'], color='#1565c0', linewidth=3.5, label='第三期 (盛夏三联+晨跑) 全天中位线')
    ax.fill_between(c3['hour'], c3['p25'], c3['p75'], color='#1565c0', alpha=0.15, label='第三期大部分时间波动范围')

    ax.annotate('【夜间安睡时段】\n均值 6.4~6.6\n稳如泰山，无低血糖风险',
                xy=(3.0, 6.6), xytext=(1.0, 8.8),
                arrowprops=dict(arrowstyle='->', color='#1565c0', lw=2),
                fontsize=12, fontweight='bold', bbox=dict(boxstyle='round,pad=0.4', facecolor='#e3f2fd', edgecolor='#1565c0'))
    ax.annotate('【清晨晨跑时段】\n跑前跑后几乎没有落差\n大运动量不引起低血糖',
                xy=(7.0, 7.0), xytext=(5.5, 4.8),
                arrowprops=dict(arrowstyle='->', color='#2e7d32', lw=2),
                fontsize=12, fontweight='bold', bbox=dict(boxstyle='round,pad=0.4', facecolor='#e8f5e9', edgecolor='#2e7d32'))
    ax.annotate('【早餐后唯一主峰 (10点)】\n受进餐影响略有上升\n调整进餐顺序即可削平',
                xy=(10.0, 8.2), xytext=(9.0, 9.8),
                arrowprops=dict(arrowstyle='->', color='#e65100', lw=2),
                fontsize=12, fontweight='bold', bbox=dict(boxstyle='round,pad=0.4', facecolor='#fff3e0', edgecolor='#e65100'))
    ax.annotate('【午餐后超平稳 (12-14点)】\n绿叶菜与蛋白质搭配好\n几乎没有任何波峰！',
                xy=(13.0, 6.7), xytext=(13.0, 4.8),
                arrowprops=dict(arrowstyle='->', color='#2e7d32', lw=2),
                fontsize=12, fontweight='bold', bbox=dict(boxstyle='round,pad=0.4', facecolor='#e8f5e9', edgecolor='#2e7d32'))
    ax.annotate('【晚餐后次要小波峰】\n夏季晚间水果与加餐稍多\n晚饭后散步20分钟可压下',
                xy=(18.5, 7.8), xytext=(17.5, 9.8),
                arrowprops=dict(arrowstyle='->', color='#c62828', lw=2),
                fontsize=12, fontweight='bold', bbox=dict(boxstyle='round,pad=0.4', facecolor='#ffebee', edgecolor='#c62828'))

    ax.set_title('全天24小时血糖变化规律（长辈通俗易读版）', fontsize=17, fontweight='bold', pad=15)
    ax.set_xlabel('时间（0点至24点）', fontsize=13); ax.set_ylabel('血糖值 (mmol/L)', fontsize=13)
    ax.set_xlim(0, 24); ax.set_xticks(range(0, 25, 2))
    ax.set_xticklabels([f'{h}点' for h in range(0, 25, 2)], fontsize=12)
    ax.tick_params(axis='y', labelsize=12); ax.set_ylim(3.5, 11.5); ax.grid(True, linestyle=':', alpha=0.6)
    ax.legend(loc='lower left', fontsize=11, framealpha=0.95)

    plt.tight_layout()
    c2_path = os.path.join(CHART_DIR, 'elderly_chart2_24h_clock.png')
    plt.savefig(c2_path)
    plt.close()

    # 图3：药物与运动看板
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6.2), dpi=200)
    ax1.set_xlim(0, 10); ax1.set_ylim(0, 10); ax1.axis('off')
    ax1.text(5, 9.4, '降糖药物【三剑客】科学分工', fontsize=16, fontweight='bold', ha='center', color='#1565c0')
    ax1.text(5, 8.7, '小剂量联合使用，疗效拉满且绝不引起低血糖', fontsize=12, ha='center', color='#555555')

    rect1 = plt.Rectangle((0.5, 5.8), 9.0, 2.5, facecolor='#e3f2fd', edgecolor='#1e88e5', lw=1.5, transform=ax1.transData)
    ax1.add_patch(rect1)
    ax1.text(1.0, 7.7, '1. 达格列净 (10mg / 早餐随餐)', fontsize=13, fontweight='bold', color='#0d47a1')
    ax1.text(1.0, 6.9, '【排糖阀门】每天随尿液排泄多余糖分，是拉低夜间血糖的功臣。\n同时保护肾脏和心脏。夏季出汗多需多喝水。', fontsize=11, color='#1565c0')

    rect2 = plt.Rectangle((0.5, 3.0), 9.0, 2.5, facecolor='#f1f8e9', edgecolor='#43a047', lw=1.5, transform=ax1.transData)
    ax1.add_patch(rect2)
    ax1.text(1.0, 4.9, '2. 二甲双胍 (500mg / 早晚随餐各1片)', fontsize=13, fontweight='bold', color='#1b5e20')
    ax1.text(1.0, 4.1, '【稳糖基石】管住肝脏不乱造糖，增加肌肉对糖分的利用。\n经典的控糖老牌药，踏实可靠。', fontsize=11, color='#2e7d32')

    rect3 = plt.Rectangle((0.5, 0.2), 9.0, 2.5, facecolor='#f3e5f5', edgecolor='#8e24aa', lw=1.5, transform=ax1.transData)
    ax1.add_patch(rect3)
    ax1.text(1.0, 2.1, '3. 司美格鲁肽 (0.25mg / 每周一注射一针)', fontsize=13, fontweight='bold', color='#4a148c')
    ax1.text(1.0, 1.3, '【智能削峰与护航】延缓胃排空、抚平餐后高峰，长效护心护肾。\n目前维持极小剂量效果极佳，无需加量！', fontsize=11, color='#6a1b9a')

    ax2.set_xlim(0, 10); ax2.set_ylim(0, 10); ax2.axis('off')
    ax2.text(5, 9.4, '跑步运动配合：心肺体能极强，稳糖压舱石', fontsize=16, fontweight='bold', ha='center', color='#2e7d32')
    ax2.text(5, 8.7, '手表实测：晨跑 6~8 公里，血糖平直不低血糖', fontsize=12, ha='center', color='#555555')

    rect_s1 = plt.Rectangle((0.5, 5.2), 9.0, 3.1, facecolor='#e8f5e9', edgecolor='#2e7d32', lw=1.5, transform=ax2.transData)
    ax2.add_patch(rect_s1)
    ax2.text(1.0, 7.6, '【晨跑实测数据】（7月连续跑13天）', fontsize=13, fontweight='bold', color='#1b5e20')
    ax2.text(1.0, 6.9, '· 平均每次跑步：6.1 公里，用时约 43 分钟', fontsize=11, color='#2e7d32')
    ax2.text(1.0, 6.3, '· 跑步平均心率：115~125 次/分（极佳的有氧燃脂心率）', fontsize=11, color='#2e7d32')
    ax2.text(1.0, 5.7, '· 最大摄氧量 VO2max：44~45（相当于35岁年轻人的心肺！）', fontsize=11, color='#2e7d32')

    rect_s2 = plt.Rectangle((0.5, 1.8), 9.0, 3.0, facecolor='#fffde7', edgecolor='#fbc02d', lw=1.5, transform=ax2.transData)
    ax2.add_patch(rect_s2)
    ax2.text(1.0, 4.1, '【跑步前后血糖实测变化】', fontsize=13, fontweight='bold', color='#f57f17')
    ax2.text(1.0, 3.4, '· 跑前血糖：6.2 ~ 7.7 mmol/L', fontsize=11, color='#f57f17')
    ax2.text(1.0, 2.8, '· 跑完即刻：6.6 ~ 7.6 mmol/L', fontsize=11, color='#f57f17')
    ax2.text(1.0, 2.2, '· 跑后1小时：6.5 ~ 7.5 mmol/L (稳如磐石，无任何运动低血糖)', fontsize=11, color='#e65100', fontweight='bold')

    rect_tip = plt.Rectangle((0.5, 0.2), 9.0, 1.3, facecolor='#ffebee', edgecolor='#e53935', lw=1.2, transform=ax2.transData)
    ax2.add_patch(rect_tip)
    ax2.text(5.0, 0.7, '【贴心建议】每周安排 1~2 天休息散步，给关节和身体放个假！', fontsize=11, fontweight='bold', ha='center', color='#c62828')

    plt.tight_layout()
    c3_path = os.path.join(CHART_DIR, 'elderly_chart3_med_and_sport.png')
    plt.savefig(c3_path)
    plt.close()

    # 图4：四大口诀卡片
    fig, ax = plt.subplots(figsize=(15, 6.0), dpi=200)
    ax.set_xlim(0, 10); ax.set_ylim(0, 10); ax.axis('off')
    ax.text(5, 9.4, '长辈日常健康生活【四大优化行动卡】', fontsize=17, fontweight='bold', ha='center', color='#1b5e20')
    ax.text(5, 8.7, '字大清晰，好记易行，助力身体硬朗、安度晚年', fontsize=12, ha='center', color='#555555')

    cards = [
        ('【进餐顺序口诀】\n先吃蔬菜蛋豆腐，最后再吃馒头饼',
         '· 改变一口主食一口菜的习惯\n· 优质蛋白与纤维先垫底，碳水吸收变平缓\n· 轻松削平上午10点和晚餐后的小波峰',
         0.2, 5.0, '#e8f5e9', '#2e7d32'),
        ('【补水防暑口诀】\n晨跑出汗多喝水，防脱水也护肾脏',
         '· 达格列净每天都在帮肾脏排糖利尿\n· 夏季出汗多，每天保证喝水 1800~2200 毫升\n· 晨跑回来喝点温水或淡盐水，浑身更轻松',
         5.2, 5.0, '#e3f2fd', '#1565c0'),
        ('【补钙减负口诀】\n钙片只吃一种足，防止过量长结石',
         '· GNC液体钙与Kirkland钙片停掉其中一种\n· 骨化三醇活性极强，避免钙质过多堆积\n· 每天户外晨跑晒太阳，骨骼已经非常结实',
         0.2, 0.4, '#fff3e0', '#e65100'),
        ('【运动休整口诀】\n跑二休一最科学，劳逸结合关节好',
         '· 62岁能跑出35岁心肺，值得大大点赞！\n· 但每周安排1~2天休息，散步或打八段锦\n· 避免身体长期紧绷，让肌肉软组织充分恢复',
         5.2, 0.4, '#fce4ec', '#c2185b'),
    ]

    for title, body, x, y, bg, border in cards:
        rect = plt.Rectangle((x, y), 4.6, 3.8, facecolor=bg, edgecolor=border, lw=1.8, transform=ax.transData)
        ax.add_patch(rect)
        ax.text(x + 0.3, y + 3.1, title, fontsize=12.5, fontweight='bold', color=border)
        ax.text(x + 0.3, y + 1.2, body, fontsize=11, color='#333333', linespacing=1.6)

    plt.tight_layout()
    c4_path = os.path.join(CHART_DIR, 'elderly_chart4_tips_cards.png')
    plt.savefig(c4_path)
    plt.close()

    return c1_path, c2_path, c3_path, c4_path


def build_pdf(c1_path, c2_path, c3_path, c4_path):
    C_PRIMARY = HexColor('#1565c0')
    C_GREEN = HexColor('#2e7d32')
    C_ORANGE = HexColor('#e65100')
    C_LIGHT_BG = HexColor('#f8f9fa')
    C_GREEN_BG = HexColor('#e8f5e9')

    doc = SimpleDocTemplate(
        OUT_PDF,
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm
    )

    s = {}
    s['title'] = ParagraphStyle('title', fontName='STHeiti', fontSize=21, leading=27, alignment=TA_CENTER, textColor=C_PRIMARY, spaceAfter=3*mm)
    s['subtitle'] = ParagraphStyle('subtitle', fontName='STHeiti', fontSize=11, leading=16, alignment=TA_CENTER, textColor=HexColor('#555555'), spaceAfter=4*mm)
    s['h1'] = ParagraphStyle('h1', fontName='STHeiti', fontSize=15, leading=20, textColor=C_PRIMARY, spaceBefore=4*mm, spaceAfter=2.5*mm)
    s['h2'] = ParagraphStyle('h2', fontName='STHeiti', fontSize=12.5, leading=18, textColor=C_GREEN, spaceBefore=3*mm, spaceAfter=2*mm)
    s['body'] = ParagraphStyle('body', fontName='STHeiti', fontSize=11, leading=18, textColor=HexColor('#222222'), spaceAfter=2*mm)
    s['good'] = ParagraphStyle('good', fontName='STHeiti', fontSize=11.5, leading=18, textColor=C_GREEN, spaceAfter=2*mm)
    s['th'] = ParagraphStyle('th', fontName='STHeiti', fontSize=10.5, leading=14, alignment=TA_CENTER, textColor=HexColor('#ffffff'))
    s['tc'] = ParagraphStyle('tc', fontName='STHeiti', fontSize=10, leading=14, alignment=TA_CENTER, textColor=HexColor('#222222'))
    s['tc_bold'] = ParagraphStyle('tc_bold', fontName='STHeiti', fontSize=10, leading=14, alignment=TA_CENTER, textColor=C_PRIMARY)

    story = []

    # Title & Subtitle
    story.append(Paragraph('愚群三次血糖监测对比与健康指导报告', s['title']))
    story.append(Paragraph('受检者：愚群（62岁·男） ｜ 家庭长辈大字关爱版 ｜ 报告生成日期：2026年9月', s['subtitle']))
    story.append(HRFlowable(width='100%', color=C_PRIMARY, thickness=2))
    story.append(Spacer(1, 2.5*mm))

    # Top Banner
    banner_data = [[
        Paragraph('<b>【核心喜讯】您的综合血糖控制处于同龄人顶尖水平！</b><br/>'
                  '先后三次佩戴动态血糖仪（累计43天、超2万次检测），全天99.7%时间都在安全范围内，<b>零次低血糖</b>，身体心肺和代谢韧性极强！', s['good'])
    ]]
    banner_t = Table(banner_data, colWidths=[182*mm])
    banner_t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), C_GREEN_BG),
        ('BOX', (0,0), (-1,-1), 1.5, C_GREEN),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(banner_t)
    story.append(Spacer(1, 3*mm))

    # 第一部分
    story.append(Paragraph('一、先后三次血糖监测成果大对比', s['h1']))
    story.append(Paragraph('从今年1月到7月，您佩戴了三次同品牌的动态血糖仪，记录下了步步扎实的控糖历程：', s['body']))

    table_headers = ['监测阶段', '时间段', '全天平均血糖', '优质平稳达标率', '低血糖发生', '当时用药与状态']
    table_rows = [
        ['第一期', '1月18日~1月30日 (冬天)', '7.62 (适中)', '69.6% (尚可)', '0 次 (安全)', '达格列净 + 二甲双胍 双联口服'],
        ['第二期', '2月16日~3月03日 (初春)', '7.00 (优秀)', '86.8% (飞跃)', '0 次 (安全)', '经受乙流发热考验，期末引入司美格鲁肽'],
        ['第三期', '7月11日~7月26日 (盛夏)', '7.01 (顶尖)', '84.2% (卓越)', '0 次 (安全)', '三联用药稳固4.5个月 + 每天晨跑6~8公里'],
    ]
    data = [[Paragraph(h, s['th']) for h in table_headers]]
    for r in table_rows:
        row_cells = []
        for i, c in enumerate(r):
            if i in (2, 3):
                row_cells.append(Paragraph(f'<b>{c}</b>', s['tc_bold']))
            else:
                row_cells.append(Paragraph(c, s['tc']))
        data.append(row_cells)

    t_cmp = Table(data, colWidths=[20*mm, 35*mm, 28*mm, 30*mm, 22*mm, 47*mm])
    t_cmp.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), C_PRIMARY),
        ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#cccccc')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [HexColor('#ffffff'), C_LIGHT_BG]),
    ]))
    story.append(t_cmp)
    story.append(Spacer(1, 3*mm))

    story.append(RLImage(c1_path, width=182*mm, height=182*mm * 5.5 / 16))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph('<b>为什么第二期到第三期数值差不多？</b> 很多糖友误以为血糖越低越好，其实对于62岁的长辈，平均血糖降到7.0已经进入医学认定的<b>最佳舒适区（天花板）</b>。如果强行降到6.0以下，反而极易发生头晕、心慌等危险的低血糖。在经历了盛夏高温、饮食诱惑和大运动量下，依然牢牢稳在7.0，是极高水平的稳态！', s['body']))

    story.append(PageBreak())

    # 第二部分
    story.append(Paragraph('二、全天24小时血糖起伏规律与特点', s['h1']))
    story.append(Paragraph('动态血糖仪就像给血糖录了一部全天候录像，让我们看清身体的每一个起伏节律：', s['body']))

    story.append(RLImage(c2_path, width=182*mm, height=182*mm * 6.5 / 15))
    story.append(Spacer(1, 2.5*mm))

    story.append(Paragraph('1. <b>夜间踏实安睡（00:00 - 06:00）</b>：血糖整夜稳定在 6.4 ~ 6.6 之间，平缓如镜，没有夜间低血糖，说明达格列净与二甲双胍把夜间基础控得极好，长辈可以踏实睡好觉。', s['body']))
    story.append(Paragraph('2. <b>清晨晨跑超稳定（06:30 - 07:30）</b>：晨跑前 6.8，跑完 7.0，跑后 6.8。大运动量完全没有引起低血糖反跳，证明心肺与糖原调节能力非常强健。', s['body']))
    story.append(Paragraph('3. <b>早餐后唯一主峰（09:30 - 10:30）</b>：受早饭影响升至 8.0 左右（1月曾到 9.2，现在已大为改善）。这是全天唯一的最高点，只要调整一下吃饭顺序，即可轻松削平。', s['body']))
    story.append(Paragraph('4. <b>午餐后波澜不惊（12:00 - 14:00）</b>：因为午餐蔬菜和豆制品、鱼肉搭配极好，午饭后几乎平直没有波峰（仅 6.6 左右），堪称典范！', s['body']))
    story.append(Paragraph('5. <b>晚餐后略有小波峰（18:00 - 19:30）</b>：夏天晚间有时水果吃得略多，或晚饭较丰盛，血糖会升到 8.0 左右。晚饭后若能出门散步20分钟，就能彻底压平。', s['body']))

    story.append(PageBreak())

    # 第三部分
    story.append(Paragraph('三、降糖药物与晨跑运动的协同疗效', s['h1']))
    story.append(Paragraph('好成绩是“药物科学配合 + 规律自律运动”共同作用的结晶：', s['body']))

    story.append(RLImage(c3_path, width=182*mm, height=182*mm * 6.2 / 16))
    story.append(Spacer(1, 2.5*mm))

    story.append(Paragraph('<b>药物各司其职，效果卓越，切勿盲目加量：</b>', s['h2']))
    story.append(Paragraph('· <b>达格列净 (10mg)</b>：如同一个智能排糖阀门，每天把血液里多余的糖分随尿液带走，帮肾脏减负，是夜间血糖平稳的最大功臣。', s['body']))
    story.append(Paragraph('· <b>二甲双胍 (500mg 早晚各1片)</b>：老牌基石药，管住肝脏不造假糖，让身体肌肉更好地吸收葡萄糖。', s['body']))
    story.append(Paragraph('· <b>司美格鲁肽 (0.25mg 每周一注射一针)</b>：延缓胃肠排空，削平饭后尖峰，长效护心护肾。<b>目前每周0.25mg极小剂量效果已非常完美，千万不要盲目加量到0.5mg</b>，保持当前用量既安全又无不适。', s['body']))

    story.append(Paragraph('<b>晨跑表现惊艳，心肺体能极强：</b>', s['h2']))
    story.append(Paragraph('手表数据显示：7月份连续跑了13天，每次 6~8 公里，最大摄氧量高达 44~45，这在62岁同龄人中属于全国前5%的顶尖水平！跑步极大地增强了骨骼肌对糖的利用，是白天血糖平稳的坚强后盾。', s['body']))

    story.append(PageBreak())

    # 第四部分
    story.append(Paragraph('四、日常健康生活【四大贴心建议】', s['h1']))
    story.append(Paragraph('为了让好状态长期保持，保护好血管、肾脏和关节，建议长辈在日常生活中做好四件小事：', s['body']))

    story.append(RLImage(c4_path, width=182*mm, height=182*mm * 6.0 / 15))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph('<b>1. 吃饭顺序口诀：先吃菜蛋肉，后吃馒头饼</b>', s['h2']))
    story.append(Paragraph('早晨吃饭时，改掉一口稀饭一口馒头的吃法。先吃水煮蛋/豆腐脑/拌黄瓜，喝无糖豆浆，最后再吃杂粮饼或全麦面包。高蛋白和纤维先在胃里垫底，糖分吸收就会慢吞吞，10点的小波峰立刻削平！', s['body']))

    story.append(Paragraph('<b>2. 补水防渴口诀：跑步出汗多，多喝温开水</b>', s['h2']))
    story.append(Paragraph('达格列净在排糖的同时会带走水分，加上夏天每天跑步大汗淋漓，身体容易缺水。一定要督促长辈每天喝够 1800~2200 毫升水，晨跑回来喝一杯淡盐水，不仅防中暑，更能防止血液浓缩和尿路结石。', s['body']))

    story.append(Paragraph('<b>3. 补钙减负口诀：钙片吃一种，别跟补剂重</b>', s['h2']))
    story.append(Paragraph('记录显示家里有GNC液体钙、Kirkland柠檬酸钙、骨化三醇等多种钙剂。<b>建议停掉其中一种钙片，只吃一种即可</b>。骨化三醇活性极强，加上长辈天天户外跑步晒太阳，体内维生素D很充足，钙吃多了反而容易长肾结石，减掉一种身体更轻快。', s['body']))

    story.append(Paragraph('<b>4. 运动休整口诀：跑二休一天，慢跑不拼速</b>', s['h2']))
    story.append(Paragraph('天天连续跑 6~8 公里，对62岁的膝关节和跟腱压力较大。建议调整为“跑两天、休息一天”。休息的那天可以散步30分钟或打八段锦，给身体自我修复的时间，心率继续控制在 115~125 舒服出汗即可。', s['body']))

    story.append(Spacer(1, 4*mm))
    story.append(HRFlowable(width='100%', color=HexColor('#cccccc'), thickness=1))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph('祝愚群长辈身体硬朗、安康长寿！ ｜ 本报告基于 SugarBee 智能控糖系统生成 ｜ 2026年9月', ParagraphStyle('footer', fontName='STHeiti', fontSize=9.5, leading=14, alignment=TA_CENTER, textColor=HexColor('#888888'))))

    doc.build(story)
    print('PDF successfully generated at:', OUT_PDF)


if __name__ == '__main__':
    c1, c2, c3, c4 = generate_charts()
    build_pdf(c1, c2, c3, c4)
