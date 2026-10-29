from html import escape
from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

REPORT_FONT = "STSong-Light"


def build_analysis_report(
    filename: str, result: dict[str, Any]
) -> bytes:
    if REPORT_FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(REPORT_FONT))

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        fontName=REPORT_FONT,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#17324d"),
    )
    heading_style = ParagraphStyle(
        "ReportHeading",
        parent=styles["Heading2"],
        fontName=REPORT_FONT,
        textColor=colors.HexColor("#245b84"),
        spaceBefore=12,
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "ReportBody",
        parent=styles["BodyText"],
        fontName=REPORT_FONT,
        leading=15,
    )

    content = [
        Paragraph("AI Resume Match Report", title_style),
        Spacer(1, 8),
        Paragraph(f"Resume: {escape(filename)}", body_style),
        Spacer(1, 8),
    ]
    if "match_score" in result:
        score = escape(str(result["match_score"]))
        score_table = Table([[Paragraph(f"Match Score: {score} / 100", heading_style)]])
        score_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#edf5fb")),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#b7d1e3")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ]
            )
        )
        content.extend([score_table, Spacer(1, 10)])

    sections = [
        ("matched_skills", "Matched Skills"),
        ("missing_skills", "Missing Skills"),
        ("strengths", "Strengths"),
        ("gaps", "Gaps"),
        ("resume_improvement_tips", "Resume Improvement Tips"),
        ("interview_questions", "Interview Questions"),
    ]
    for field, title in sections:
        values = result.get(field, [])
        if not isinstance(values, list) or not values:
            continue
        content.append(Paragraph(escape(title), heading_style))
        for value in values:
            content.append(Paragraph(f"- {escape(str(value))}", body_style))
            content.append(Spacer(1, 3))

    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=48,
        leftMargin=48,
        topMargin=48,
        bottomMargin=48,
        title="AI Resume Match Report",
    )
    document.build(content)
    return output.getvalue()
