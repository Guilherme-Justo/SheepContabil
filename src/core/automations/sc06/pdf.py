from __future__ import annotations

from collections import defaultdict
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from django.conf import settings
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    Flowable,
    Image,
    LongTable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from core.automations.models import SocietaryBriefing, SocietaryBriefingStatus
from core.automations.sc06.rules import format_answers

INK = colors.HexColor("#0B1A20")
PETROL = colors.HexColor("#10505F")
TURQUOISE = colors.HexColor("#1FA69A")
MIST = colors.HexColor("#EEF3F4")
GRAPHITE = colors.HexColor("#5A7078")
WHITE = colors.white


def build_briefing_pdf(briefing: SocietaryBriefing) -> bytes:
    if briefing.status != SocietaryBriefingStatus.COMPLETED:
        raise ValueError("O PDF só pode ser gerado para um briefing concluído.")

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=1.7 * cm,
        rightMargin=1.7 * cm,
        topMargin=1.6 * cm,
        bottomMargin=1.7 * cm,
        title=f"Briefing societário — {briefing.client_name}",
        author="SheepContabil",
        subject="Resumo consolidado do SC-06",
    )
    styles = _styles()
    story: list[Flowable] = []

    logo = _logo()
    if logo is not None:
        story.extend((logo, Spacer(1, 0.5 * cm)))

    story.append(Paragraph("SC-06 · BRIEFING SOCIETÁRIO", styles["eyebrow"]))
    story.append(Paragraph("Resumo consolidado", styles["title"]))
    story.append(
        Paragraph(
            "Documento gerado a partir da versão imutável do formulário usada no atendimento.",
            styles["subtitle"],
        )
    )
    story.append(Spacer(1, 0.55 * cm))
    story.append(_identity_table(briefing, styles))
    story.append(Spacer(1, 0.7 * cm))

    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for answer in format_answers(briefing.template_version.schema, briefing.answers):
        grouped[str(answer["section_title"])].append(answer)

    for section_title, answers in grouped.items():
        story.append(Paragraph(escape(section_title), styles["section"]))
        rows: list[list[Paragraph]] = []
        for answer in answers:
            rows.append(
                [
                    Paragraph(escape(str(answer["question_label"])), styles["question"]),
                    Paragraph(escape(str(answer["display_value"])), styles["answer"]),
                ]
            )
        story.append(_answer_table(rows))
        story.append(Spacer(1, 0.5 * cm))

    story.append(Spacer(1, 0.15 * cm))
    story.append(
        Paragraph(
            "Dados exclusivamente sintéticos · Evidência gerada pelo portal SheepContabil",
            styles["disclaimer"],
        )
    )
    document.build(
        story,
        onFirstPage=_draw_page,
        onLaterPages=_draw_page,
        canvasmaker=Canvas,
    )
    return buffer.getvalue()


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "eyebrow": ParagraphStyle(
            "Sc06Eyebrow",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10,
            textColor=TURQUOISE,
            spaceAfter=7,
        ),
        "title": ParagraphStyle(
            "Sc06Title",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=24,
            leading=28,
            textColor=INK,
            alignment=0,
            spaceAfter=7,
        ),
        "subtitle": ParagraphStyle(
            "Sc06Subtitle",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=14,
            textColor=GRAPHITE,
        ),
        "meta_label": ParagraphStyle(
            "Sc06MetaLabel",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=7,
            leading=9,
            textColor=GRAPHITE,
        ),
        "meta_value": ParagraphStyle(
            "Sc06MetaValue",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=12,
            textColor=INK,
        ),
        "section": ParagraphStyle(
            "Sc06Section",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=15,
            textColor=PETROL,
            keepWithNext=True,
            spaceAfter=7,
        ),
        "question": ParagraphStyle(
            "Sc06Question",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=11,
            textColor=PETROL,
        ),
        "answer": ParagraphStyle(
            "Sc06Answer",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=12,
            textColor=INK,
        ),
        "disclaimer": ParagraphStyle(
            "Sc06Disclaimer",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=7.5,
            leading=10,
            textColor=GRAPHITE,
            alignment=TA_RIGHT,
        ),
    }


def _logo() -> Image | None:
    path = Path(settings.SRC_DIR) / "static" / "brand" / "logo-dark.png"
    if not path.exists():
        return None
    image = Image(str(path))
    width = 4.4 * cm
    image.drawHeight = width * image.imageHeight / image.imageWidth
    image.drawWidth = width
    image.hAlign = "LEFT"
    return image


def _identity_table(
    briefing: SocietaryBriefing,
    styles: dict[str, ParagraphStyle],
) -> Table:
    completed_at = timezone.localtime(briefing.completed_at) if briefing.completed_at else None
    cells = (
        ("CLIENTE SINTÉTICO", briefing.client_name),
        ("CPF/CNPJ SINTÉTICO", briefing.client_document),
        ("VERSÃO DO TEMPLATE", f"v{briefing.template_version.version}"),
        (
            "CONCLUÍDO POR",
            briefing.completed_by.label if briefing.completed_by is not None else "—",
        ),
        ("CONCLUÍDO EM", completed_at.strftime("%d/%m/%Y %H:%M") if completed_at else "—"),
    )
    row = [
        [
            Paragraph(escape(label), styles["meta_label"]),
            Paragraph(escape(value), styles["meta_value"]),
        ]
        for label, value in cells
    ]
    table = Table(row, colWidths=(4.1 * cm, 12.5 * cm), hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), MIST),
                ("BACKGROUND", (1, 0), (1, -1), WHITE),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#D7E0E2")),
                ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D7E0E2")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return table


def _answer_table(rows: list[list[Paragraph]]) -> LongTable:
    table = LongTable(rows, colWidths=(6.2 * cm, 10.4 * cm), hAlign="LEFT", splitByRow=1)
    commands: list[Any] = [
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#D7E0E2")),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#D7E0E2")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]
    for index in range(len(rows)):
        commands.append(
            (
                "BACKGROUND",
                (0, index),
                (-1, index),
                WHITE if index % 2 == 0 else colors.HexColor("#F7F9F9"),
            )
        )
    table.setStyle(TableStyle(commands))
    return table


def _draw_page(canvas: Canvas, document: SimpleDocTemplate) -> None:
    canvas.saveState()
    page_width, _ = A4
    canvas.setStrokeColor(colors.HexColor("#D7E0E2"))
    canvas.setLineWidth(0.5)
    canvas.line(1.7 * cm, 1.25 * cm, page_width - 1.7 * cm, 1.25 * cm)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(GRAPHITE)
    canvas.drawString(1.7 * cm, 0.85 * cm, "SheepContabil · SC-06")
    canvas.drawRightString(
        page_width - 1.7 * cm,
        0.85 * cm,
        f"Página {document.page}",
    )
    canvas.restoreState()
