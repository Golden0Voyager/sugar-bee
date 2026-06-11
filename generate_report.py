#!/usr/bin/env python3
"""为愚群生成血糖分析 PDF 报告"""

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import os

# ── 注册中文字体 ──
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
            # Try bold subfont
            try:
                pdfmetrics.registerFont(TTFont(name + "Bold", fp, subfontIndex=1))
                CN_FONT_BOLD = name + "Bold"
            except Exception:  # pragma: no cover（macOS 系统字体，不可移植）
                CN_FONT_BOLD = name  # pragma: no cover
            break
        except Exception:  # pragma: no cover
            continue  # pragma: no cover

# ── 颜色 ──
C_PRIMARY = HexColor("#1565c0")
C_ACCENT = HexColor("#e65100")
C_GREEN = HexColor("#2e7d32")
C_RED = HexColor("#c62828")
C_GRAY = HexColor("#616161")
C_LIGHT_BG = HexColor("#f5f5f5")
C_BLUE_BG = HexColor("#e3f2fd")
C_ORANGE_BG = HexColor("#fff3e0")
C_GREEN_BG = HexColor("#e8f5e9")
C_RED_BG = HexColor("#ffebee")

# ── 样式 ──
def make_styles():
    s = {}
    s["title"] = ParagraphStyle("title", fontName=CN_FONT_BOLD, fontSize=22,
                                 leading=30, alignment=TA_CENTER, textColor=C_PRIMARY,
                                 spaceAfter=4*mm)
    s["subtitle"] = ParagraphStyle("subtitle", fontName=CN_FONT, fontSize=11,
                                    leading=16, alignment=TA_CENTER, textColor=C_GRAY,
                                    spaceAfter=8*mm)
    s["h1"] = ParagraphStyle("h1", fontName=CN_FONT_BOLD, fontSize=16, leading=24,
                              textColor=C_PRIMARY, spaceBefore=8*mm, spaceAfter=3*mm)
    s["h2"] = ParagraphStyle("h2", fontName=CN_FONT_BOLD, fontSize=13, leading=20,
                              textColor=HexColor("#333333"), spaceBefore=5*mm, spaceAfter=2*mm)
    s["body"] = ParagraphStyle("body", fontName=CN_FONT, fontSize=11, leading=18,
                                textColor=HexColor("#333333"), spaceAfter=2*mm)
    s["body_indent"] = ParagraphStyle("body_indent", parent=s["body"], leftIndent=8*mm)
    s["tip"] = ParagraphStyle("tip", fontName=CN_FONT, fontSize=10.5, leading=17,
                               textColor=C_ACCENT, leftIndent=5*mm, spaceAfter=2*mm)
    s["good"] = ParagraphStyle("good", fontName=CN_FONT, fontSize=11, leading=18,
                                textColor=C_GREEN, leftIndent=5*mm, spaceAfter=2*mm)
    s["warn"] = ParagraphStyle("warn", fontName=CN_FONT, fontSize=11, leading=18,
                                textColor=C_RED, leftIndent=5*mm, spaceAfter=2*mm)
    s["footer"] = ParagraphStyle("footer", fontName=CN_FONT, fontSize=9,
                                  leading=14, alignment=TA_CENTER, textColor=C_GRAY)
    s["table_header"] = ParagraphStyle("th", fontName=CN_FONT_BOLD, fontSize=10,
                                        leading=14, alignment=TA_CENTER, textColor=HexColor("#ffffff"))
    s["table_cell"] = ParagraphStyle("tc", fontName=CN_FONT, fontSize=10,
                                      leading=14, alignment=TA_CENTER, textColor=HexColor("#333333"))
    s["table_cell_left"] = ParagraphStyle("tcl", fontName=CN_FONT, fontSize=10,
                                           leading=14, textColor=HexColor("#333333"))
    return s

def make_table(headers, rows, col_widths, st):
    """构建带样式的表格"""
    header_row = [Paragraph(h, st["table_header"]) for h in headers]
    data = [header_row]
    for row in rows:
        data.append([Paragraph(str(c), st["table_cell"]) if i > 0
                      else Paragraph(str(c), st["table_cell_left"])
                      for i, c in enumerate(row)])

    t = Table(data, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), C_PRIMARY),
        ("TEXTCOLOR", (0, 0), (-1, 0), HexColor("#ffffff")),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, HexColor("#cccccc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [HexColor("#ffffff"), C_LIGHT_BG]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t

def build_pdf(output_path):
    doc = SimpleDocTemplate(output_path, pagesize=A4,
                            topMargin=20*mm, bottomMargin=20*mm,
                            leftMargin=18*mm, rightMargin=18*mm)
    st = make_styles()
    story = []
    W = doc.width  # usable width

    # ════════ 封面区 ════════
    story.append(Spacer(1, 15*mm))
    story.append(Paragraph("血糖管理与用药分析报告", st["title"]))
    story.append(Paragraph("愚群（爸爸）专属  |  2026年2月23日", st["subtitle"]))
    story.append(HRFlowable(width="80%", thickness=1, color=C_PRIMARY,
                             spaceAfter=5*mm, spaceBefore=2*mm, hAlign="CENTER"))

    # ════════ 一、您的基本情况 ════════
    story.append(Paragraph("一、您的基本情况", st["h1"]))
    story.append(Paragraph(
        "爸，先帮您整理一下基本信息：您今年62岁，身高170cm，最近体重68.6公斤，"
        "身体质量指数（BMI）23.7，属于<b>正常范围</b>。", st["body"]))
    story.append(Paragraph(
        "您最近两个月每天坚持跑步大约6公里，非常棒！这个运动量在同龄人中已经算很高了。"
        "体重也从两周前的70.25公斤降到了68.6公斤，说明运动和饮食控制都在起效果。", st["body"]))

    # ════════ 二、血糖表现 ════════
    story.append(Paragraph("二、您最近的血糖表现怎么样？", st["h1"]))
    story.append(Paragraph(
        "我们把您近两个月的血糖数据仔细看了一遍，发现可以分成<b>四个阶段</b>：", st["body"]))

    # 阶段表
    stage_data = [
        ["12月底~1月中旬", "控制很好", "空腹平均6.1\n餐后平均6.4", "正常饮食+每天跑步\n三种药都在吃"],
        ["1月20日~31日", "明显升高", "餐后平均8.0~8.5\n最高到过9.9", "春节前后饮食\n放开了"],
        ["2月7日~21日", "恢复理想", "空腹平均6.0~6.4\n餐后平均6.5~7.2", "恢复正常饮食\n继续跑步"],
        ["2月22日至今", "感染升高", "晚饭前9.4", "乙流感染+停药\n身体应激反应"],
    ]
    t = make_table(["时间段", "整体表现", "血糖数值", "原因分析"],
                    stage_data, [W*0.22, W*0.17, W*0.30, W*0.31], st)
    story.append(t)
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph("通俗地说：", st["h2"]))
    story.append(Paragraph(
        "您的血糖在正常吃饭、规律运动、按时吃药的情况下，控制得<b>相当好</b>，"
        "甚至有时候偏低了（2月7日餐后出现过4.8，正常人一般不会低于4.0）。", st["good"]))
    story.append(Paragraph(
        "春节那段时间升高，主要是吃得多了、食物油腻，这是正常的——节后恢复饮食，"
        "血糖马上就回来了，说明您的身体调节能力还不错。", st["body"]))
    story.append(Paragraph(
        "现在因为得了乙流，停了两种降糖药，血糖升到9.4，这是感染引起的「应激反应」，"
        "不用担心，<b>等感冒好了、恢复用药后，血糖会自己降下来</b>。", st["body"]))

    # ════════ 三、目前的药物 ════════
    story.append(Paragraph("三、您现在吃的三种降糖药", st["h1"]))
    story.append(Paragraph(
        "先帮您捋清楚这三种药各自干什么：", st["body"]))

    med_data = [
        ["司美格鲁肽\n（每周一打针）", "0.25毫克/周", "模拟肠道激素，让您\n吃饭后少饿、血糖少升",
         "这是最小的起步量\n还没加到正式治疗量"],
        ["达格列净\n（每天吃一次）", "10毫克/天", "让肾脏多排出一些糖\n从尿里排掉多余的糖分",
         "标准用量\n还能保护心脏和肾脏"],
        ["二甲双胍\n（每天三次）", "500毫克×3/天", "让身体对胰岛素\n更加敏感、更好用",
         "中等用量\n是最经典的降糖老药"],
    ]
    t = make_table(["药物名称", "您的用量", "通俗原理", "用量说明"],
                    med_data, [W*0.22, W*0.17, W*0.30, W*0.31], st)
    story.append(t)
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph(
        "简单说：您同时用了<b>三种不同机制</b>的降糖药，再加上每天6公里跑步——"
        "跑步本身就相当于一种「天然降糖药」，因为运动能帮助身体更好地利用血糖。", st["body"]))

    # ════════ 四、核心判断 ════════
    story.append(Paragraph("四、药量是不是合适？——关键判断", st["h1"]))

    story.append(Paragraph(
        "爸，说实话，从数据看，您目前的降糖力度<b>有点偏强了</b>。"
        "下面解释为什么这么说：", st["body"]))

    story.append(Paragraph("证据一：血糖有时偏低", st["h2"]))
    story.append(Paragraph(
        "2月7日您餐后血糖只有<b>4.8</b>——吃了饭血糖才4.8，说明药+运动把血糖压得太低了。"
        "虽然没到低血糖的危险线（4.0以下），但已经是一个<b>需要注意的信号</b>。"
        "如果哪天运动量大一些、或者吃少了，就可能真的低血糖，"
        "会头晕、出冷汗、手抖、心慌。", st["warn"]))

    story.append(Paragraph("证据二：司美格鲁肽才用了最小量", st["h2"]))
    story.append(Paragraph(
        "司美格鲁肽0.25毫克是「入门试用量」，正式治疗通常要加到0.5~1.0毫克。"
        "但您在这个最小量下血糖就已经控制得很好了——说明<b>另外两种药加上运动，"
        "已经够用了</b>，司美格鲁肽还没真正发力呢。", st["body"]))

    story.append(Paragraph("证据三：体重下降偏快", st["h2"]))
    story.append(Paragraph(
        "两周掉了1.65公斤。三种药里，司美格鲁肽会抑制食欲，达格列净会通过排糖减重，"
        "再加上每天跑步消耗——三重作用叠加，体重掉得快。"
        "对62岁的人来说，<b>掉太快容易丢肌肉</b>，不太好。", st["warn"]))

    # ════════ 五、建议 ════════
    story.append(Paragraph("五、用药调整建议", st["h1"]))
    story.append(Paragraph(
        "以下建议基于您的数据分析，<b>具体调整请和您的主治医生商量后再执行</b>。", st["tip"]))

    story.append(Paragraph("建议一：二甲双胍可以考虑减量", st["h2"]))
    story.append(Paragraph(
        "目前您每天吃三次（早中晚各500毫克），建议先试试<b>减掉中午那一次</b>，"
        "变成早晚各一次（每天1000毫克）。", st["body"]))
    story.append(Paragraph(
        "为什么减这个？因为您每天跑步6公里，运动本身就能起到和二甲双胍类似的作用"
        "——都是帮助身体更好地利用胰岛素。有了运动这个「天然药物」，"
        "二甲双胍的量就不需要那么大了。", st["body"]))
    story.append(Paragraph(
        "减了以后观察2~4周，如果空腹血糖还在7.0以下、餐后还在8.0以下，"
        "就说明减量是安全的。", st["good"]))

    story.append(Paragraph("建议二：达格列净继续吃，不要减", st["h2"]))
    story.append(Paragraph(
        "达格列净不只是降糖——它还能<b>保护心脏和肾脏</b>。"
        "您有高血压，正在吃降压药，达格列净对您的心血管有额外的好处，"
        "这个好处和降糖是分开的。所以这个药建议一直吃着。", st["body"]))
    story.append(Paragraph(
        "但是注意：<b>生病发烧、拉肚子、吃不下饭的时候要暂停</b>"
        "（您这次乙流停药就做对了），等病好了再恢复。", st["tip"]))

    story.append(Paragraph("建议三：司美格鲁肽不用加量", st["h2"]))
    story.append(Paragraph(
        "医生可能会建议您从0.25毫克加到0.5毫克——但从您的血糖数据看，"
        "<b>不加量也够了</b>。0.25毫克保持住就行。"
        "这样还能减缓体重下降的速度，保住肌肉。", st["body"]))

    # 总结表
    story.append(Spacer(1, 3*mm))
    summary_data = [
        ["二甲双胍", "每天3次 → 每天2次", "先减中午那顿", "观察2~4周"],
        ["达格列净", "10毫克/天不变", "保护心肾，别停", "生病时暂停"],
        ["司美格鲁肽", "0.25毫克/周不变", "不用加量", "维持现状即可"],
    ]
    t = make_table(["药物", "调整方案", "要点", "备注"],
                    summary_data, [W*0.22, W*0.28, W*0.25, W*0.25], st)
    story.append(KeepTogether([
        Paragraph("调整方案一览表", st["h2"]),
        t,
    ]))

    # ════════ 六、乙流期间 ════════
    story.append(Paragraph("六、乙流期间的注意事项", st["h1"]))
    story.append(Paragraph(
        "您现在正在感冒发烧，有几点要特别注意：", st["body"]))
    story.append(Paragraph(
        "<b>1. 达格列净和二甲双胍暂停是对的。</b>"
        "发烧拉肚子时身体容易脱水，这两种药在脱水状态下有风险"
        "（达格列净可能引起酮症酸中毒，二甲双胍可能引起乳酸酸中毒），"
        "所以停掉是正确的。", st["body_indent"]))
    story.append(Paragraph(
        "<b>2. 血糖升高不要慌。</b>"
        "感冒发烧时，身体会分泌「应激激素」（比如肾上腺素、皮质醇），"
        "这些激素会让血糖升高。这是身体在对抗病毒，不是糖尿病加重了。"
        "等感冒好了，血糖自然会降回来。", st["body_indent"]))
    story.append(Paragraph(
        "<b>3. 恢复用药的时机：</b>"
        "退烧48小时以上、能正常吃饭喝水后，先恢复达格列净；"
        "再过两天确认没问题了，再加回二甲双胍。不要一下子全加回来。", st["body_indent"]))
    story.append(Paragraph(
        "<b>4. 多喝水！</b>发烧出汗多，一定要多喝温水，"
        "防止脱水导致血糖进一步升高。", st["body_indent"]))

    # ════════ 七、运动建议 ════════
    story.append(Paragraph("七、关于运动的建议", st["h1"]))
    story.append(Paragraph(
        "爸，您每天跑6公里真的非常厉害，在糖尿病患者里属于运动量很大的。"
        "但有几点想提醒您：", st["body"]))
    story.append(Paragraph(
        "<b>1. 跑步前可以测一下血糖。</b>"
        "如果低于5.5，建议先吃一小块饼干或喝半杯牛奶再跑，"
        "防止运动中低血糖。", st["body_indent"]))
    story.append(Paragraph(
        "<b>2. 随身带一两颗糖。</b>"
        "万一跑步时感觉头晕、手抖、出冷汗，赶紧含一颗糖，坐下休息。"
        "这是低血糖的信号。", st["body_indent"]))
    story.append(Paragraph(
        "<b>3. 乙流期间不要跑步。</b>"
        "发烧时运动会加重心脏负担。等完全退烧、精神恢复后，"
        "先从散步开始，慢慢恢复跑量。", st["body_indent"]))

    # ════════ 总结 ════════
    story.append(Paragraph("总结", st["h1"]))
    story.append(Paragraph(
        "爸，您的血糖管理整体做得<b>很好</b>——规律运动、坚持测血糖、按时吃药，"
        "这些都很棒。目前的情况是药+运动的降糖力度加起来<b>稍微有点猛</b>，"
        "可以考虑把二甲双胍从一天三次减到一天两次，其他药不变。"
        "等乙流好了以后，跟医生商量一下这个减量方案，然后密切观察血糖变化就行。", st["body"]))
    story.append(Paragraph(
        "祝您早日康复！", st["good"]))

    # ── 页脚 ──
    story.append(Spacer(1, 10*mm))
    story.append(HRFlowable(width="60%", thickness=0.5, color=C_GRAY,
                             spaceAfter=3*mm, hAlign="CENTER"))
    story.append(Paragraph(
        "本报告由蜜蜂控糖系统自动生成，仅供参考，不构成医疗建议。<br/>"
        "用药调整请务必咨询主治医生。", st["footer"]))
    story.append(Paragraph("2026年2月23日", st["footer"]))

    doc.build(story)
    return output_path

if __name__ == "__main__":
    out = os.path.join(os.path.dirname(__file__), "愚群_血糖分析报告_20260223.pdf")
    build_pdf(out)
    print(f"PDF 已生成: {out}")
