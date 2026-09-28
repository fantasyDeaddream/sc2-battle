from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path
import os

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


GUIDE_DIR = Path(__file__).resolve().parent
OUTPUT_PATH = GUIDE_DIR / "operation_guide.pdf"


def register_font() -> str:
    windows_dir = Path(os.environ.get("WINDIR", "C:/Windows"))
    candidates = [
        windows_dir / "Fonts" / "msyh.ttc",
        windows_dir / "Fonts" / "msyh.ttf",
        windows_dir / "Fonts" / "simhei.ttf",
        windows_dir / "Fonts" / "simsun.ttc",
        windows_dir / "Fonts" / "simsun.ttf",
    ]
    for font_path in candidates:
        if not font_path.exists():
            continue
        pdfmetrics.registerFont(TTFont("GuideCN", str(font_path), subfontIndex=0))
        return "GuideCN"
    raise FileNotFoundError("未找到可用的中文字体，请检查 Windows 字体目录。")


def section_box(title: str, body: str) -> Table:
    table = Table([[Paragraph(f"<b>{escape(title)}</b>", STYLES["CardTitle"])], [Paragraph(escape(body), STYLES["CardBody"])]], colWidths=[170 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFF7EC")),
                ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#D96B2B")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#F2C38E")),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return table


def numbered_steps(steps: list[str]) -> list[Paragraph]:
    return [Paragraph(f"<b>{index}.</b> {escape(step)}", STYLES["Body"]) for index, step in enumerate(steps, 1)]


def build_story() -> list:
    sections = [
        {
            "title": "使用前准备",
            "summary": "首次使用前请先完成账号、游戏内昵称与功能入口确认。",
            "steps": [
                "进入平台后先确认已绑定游戏内昵称。平台的 replay 自动判定、天梯归档与赛事小局结果识别都依赖该昵称。",
                "顶部导航提供固定入口：赛事大厅、天梯匹配、操作指南、账号设置。建议先从赛事大厅和天梯匹配各打开一次，确认页面可正常加载。",
                "如果你是赛事创建者或管理员，先确认自己拥有创建赛事、管理赛事或天梯悬赏发布权限。",
            ],
            "shot": "推荐截图：登录页、顶部导航、账号设置中的昵称绑定区域。",
        },
        {
            "title": "赛事参与者",
            "summary": "适用于普通参赛选手，从报名到 BP、上传 replay 的完整路径。",
            "steps": [
                "进入赛事大厅，点击“查看并报名”打开目标赛事的信息页，确认赛制、签到时间、地图池、BP 模式与是否启用 replay 自动判定。",
                "在赛事信息页完成报名，并在签到开放后点击签到。若主办方尚未公布对阵表，只需等待赛事开始。",
                "对阵生成后进入“我的对阵”，点击“准备比赛”。若赛事启用了地图池 BP、随机地图 + 指挥官 BP、天梯 1v1 BP 或 EVO BP，请按页面提示逐步完成当前小局前的选择。",
                "开始比赛前，核对页面展示的当前小局地图、BP 结果与本方固定位置。天梯 1v1 BP 会在每个小局都重新执行“各选 3 位指挥官、互相 Ban、各自 Pick”。",
                "若赛事启用了 replay 自动判定，小局结束后在“我的对阵”上传本局 replay。系统会校验地图、开局时间和双方昵称，并累计系列赛比分。",
                "如果系统提示需要手动判定赢家或等待赛事管理员仲裁，请根据页面按钮完成手动选择，或联系管理员处理。",
            ],
            "shot": "推荐截图：赛事详情页、我的对阵页、BP 面板、replay 上传成功提示。",
        },
        {
            "title": "赛事创建者",
            "summary": "适用于创建比赛、设置赛制、开启 replay 与管理报名流程的用户。",
            "steps": [
                "在赛事大厅点击“创建比赛”，填写比赛时间、签到时间、赛制、赛段、地图池和共享赛事管理员。",
                "根据赛事需求选择 BP 模式：地图池 BP、随机地图 + 指挥官 BP、天梯 1v1 BP 或 EVO BP。若启用 EVO BP，必须同时开启 replay 自动判定。",
                "设置每轮 BP 时间。若使用随机地图 + 指挥官 BP，还可以额外指定共享池 / 独立池与 Ban 生效范围。",
                "创建完成后，在赛事信息页检查赛事描述、奖金信息、报名名单与共享管理员。必要时先隐藏对阵表，等确认名单后再开始并公布。",
                "比赛开始后，关注“我的对阵”和管理员观察面板中的 BP 状态、replay 上传与待仲裁小局。管理员可以在需要时仲裁 replay 或推进赛事流程。",
            ],
            "shot": "推荐截图：创建比赛表单、赛事信息页、管理员观察面板、共享管理员设置区。",
        },
        {
            "title": "天梯参与者",
            "summary": "适用于 1v1 / 2v2 天梯玩家，包括匹配、BP、聊天和 replay 归档。",
            "steps": [
                "进入天梯匹配页，先选择 1v1 或 2v2 模式。页面会展示当前在线人数、队列人数、活动比赛和聊天大厅。",
                "加入队列前确认自己的游戏内昵称已绑定，并按页面要求选择当前模式可用的指挥官或队友信息。匹配成功后，按照倒计时完成确认。",
                "进入比赛后，在当前比赛面板里完成 BP。1v1 天梯会进行随机地图 + Ban / Pick 指挥官流程；页面会通过 Ajax 局部刷新持续更新，不需要整页手动刷新。",
                "比赛过程中可使用临时聊天室与对手或队友沟通。比赛结束后上传 replay，系统会自动结算天梯分并把 replay 同时保存到双方用户名目录下。",
                "如需回看自己曾上传的 replay，可在后续的天梯悬赏提交页直接从自己的用户目录中选择已有文件。",
            ],
            "shot": "推荐截图：天梯大厅、当前比赛面板、临时聊天室、replay 上传结果提示。",
        },
        {
            "title": "天梯悬赏发布者",
            "summary": "适用于 sudo 超管或天梯管理员发布、审核、撤回悬赏，以及普通用户提交样本。",
            "steps": [
                "进入“天梯悬赏”页面。普通用户可在“上交悬赏”区域选择一个悬赏，并勾选自己用户名目录下已经保存的 replay 文件后提交。",
                "天梯管理员或 sudo 超管可看到“发布悬赏”面板，填写悬赏名、悬赏名额数、悬赏金额和悬赏内容后发布。",
                "发布后，管理员可在每个悬赏卡片中查看所有提交记录，下载指定用户的提交归档，并结合 replay 内容手动判断是否有效。",
                "若判定有效，悬赏进度会从例如 0/3 递增为 1/3；达到名额上限后，普通用户将不能继续提交该悬赏。",
                "若判定无效，必须填写无效原因；系统会把原因反馈给该用户。必要时管理员也可以撤回悬赏，并填写撤回说明。",
            ],
            "shot": "推荐截图：天梯悬赏主页、发布悬赏表单、提交审核卡片、归档下载入口。",
        },
    ]

    story: list = []
    story.append(Paragraph("SC2 Battle 平台操作指南", STYLES["Title"]))
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph("适用对象：赛事参与者、赛事创建者、天梯参与者、天梯悬赏发布者", STYLES["SubTitle"]))
    story.append(Spacer(1, 5 * mm))
    story.append(section_box("固定目录", "网页上的“操作指南”入口固定读取 guide_assets/operation_guide.pdf。你可以直接替换该文件，无需修改路由。"))
    story.append(Spacer(1, 4 * mm))
    story.append(section_box("关于截图", "当前版本先提供与页面文案严格对齐的步骤说明，并在每个章节列出推荐截图位。后续你可以替换本 PDF，或修改本目录脚本后重新生成带真实截图的版本。"))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", STYLES["Meta"]))
    story.append(Spacer(1, 8 * mm))

    for section in sections:
        story.append(Paragraph(section["title"], STYLES["Heading"]))
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph(section["summary"], STYLES["Intro"]))
        story.append(Spacer(1, 3 * mm))
        for item in numbered_steps(section["steps"]):
            story.append(item)
            story.append(Spacer(1, 1.8 * mm))
        story.append(Spacer(1, 2 * mm))
        story.append(section_box("推荐截图位", section["shot"]))
        story.append(Spacer(1, 6 * mm))

    story.append(section_box("维护建议", "若你想快速更新网站内展示内容，最省事的方式是直接用新的 PDF 覆盖本目录下的 operation_guide.pdf。若想继续沿用当前排版，可修改 generate_operation_guide.py 后重新生成。"))
    return story


def draw_page(canvas, document):
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#D96B2B"))
    canvas.setLineWidth(1)
    canvas.line(18 * mm, 286 * mm, 192 * mm, 286 * mm)
    canvas.setFont("GuideCN", 9)
    canvas.setFillColor(colors.HexColor("#514236"))
    canvas.drawString(18 * mm, 289 * mm, "SC2 Battle 操作指南")
    canvas.drawRightString(192 * mm, 12 * mm, f"第 {document.page} 页")
    canvas.restoreState()


FONT_NAME = register_font()
base_styles = getSampleStyleSheet()
STYLES = {
    "Title": ParagraphStyle(
        "GuideTitle",
        parent=base_styles["Title"],
        fontName=FONT_NAME,
        fontSize=24,
        leading=30,
        textColor=colors.HexColor("#20170F"),
        alignment=TA_CENTER,
        spaceAfter=4,
    ),
    "SubTitle": ParagraphStyle(
        "GuideSubTitle",
        parent=base_styles["BodyText"],
        fontName=FONT_NAME,
        fontSize=11,
        leading=16,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#514236"),
    ),
    "Heading": ParagraphStyle(
        "GuideHeading",
        parent=base_styles["Heading2"],
        fontName=FONT_NAME,
        fontSize=17,
        leading=22,
        textColor=colors.HexColor("#21444F"),
        spaceBefore=4,
        spaceAfter=6,
    ),
    "Intro": ParagraphStyle(
        "GuideIntro",
        parent=base_styles["BodyText"],
        fontName=FONT_NAME,
        fontSize=10.5,
        leading=16,
        textColor=colors.HexColor("#514236"),
    ),
    "Body": ParagraphStyle(
        "GuideBody",
        parent=base_styles["BodyText"],
        fontName=FONT_NAME,
        fontSize=10,
        leading=15,
        textColor=colors.HexColor("#20170F"),
    ),
    "Meta": ParagraphStyle(
        "GuideMeta",
        parent=base_styles["BodyText"],
        fontName=FONT_NAME,
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#7A685A"),
        alignment=TA_CENTER,
    ),
    "CardTitle": ParagraphStyle(
        "GuideCardTitle",
        parent=base_styles["BodyText"],
        fontName=FONT_NAME,
        fontSize=10.5,
        leading=14,
        textColor=colors.HexColor("#20170F"),
    ),
    "CardBody": ParagraphStyle(
        "GuideCardBody",
        parent=base_styles["BodyText"],
        fontName=FONT_NAME,
        fontSize=9.5,
        leading=14,
        textColor=colors.HexColor("#514236"),
    ),
}


def main() -> None:
    GUIDE_DIR.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(
        str(OUTPUT_PATH),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=24 * mm,
        bottomMargin=18 * mm,
        title="SC2 Battle 平台操作指南",
        author="GitHub Copilot",
    )
    document.build(build_story(), onFirstPage=draw_page, onLaterPages=draw_page)
    print(f"Generated: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()