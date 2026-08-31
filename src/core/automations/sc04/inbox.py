from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from core.automations.sc04.contracts import DocumentInbox, IncomingDocument


class SyntheticDocumentInbox:
    """Deterministic private inbox used only with synthetic challenge data."""

    def list_attachments(self) -> tuple[IncomingDocument, ...]:
        invoice = (
            b"NOTA FISCAL DE SERVICOS\n"
            b"Cliente: Aurora Participacoes Demo\n"
            b"CNPJ: 12.345.678/0001-90\n"
            b"Numero: NF-DEMO-1042\n"
            b"Valor: R$ 1.250,00\n"
        )
        tax_payment = (
            b"DOCUMENTO DE ARRECADACAO SINTETICO\n"
            b"Contribuinte: Horizonte Comercio Demo\n"
            b"CNPJ: 98.765.432/0001-10\n"
            b"Competencia: 08/2026\n"
            b"Codigo: DARF-DEMO-2172\n"
        )
        ambiguous = (
            b"RELATORIO CONTABIL SINTETICO\n"
            b"Documento recebido sem identificador fiscal confiavel.\n"
            b"Necessita confirmacao humana do cliente e do tipo.\n"
        )
        return (
            IncomingDocument(
                source_reference="inbox:fiscal-demo:msg-001:attachment-001",
                filename="nota-fiscal-aurora.txt",
                declared_content_type="text/plain",
                content=invoice,
            ),
            IncomingDocument(
                source_reference="inbox:fiscal-demo:msg-002:attachment-001",
                filename="guia-horizonte.txt",
                declared_content_type="text/plain",
                content=tax_payment,
            ),
            IncomingDocument(
                source_reference="inbox:fiscal-demo:msg-003:attachment-001",
                filename="nota-fiscal-aurora-copia.txt",
                declared_content_type="text/plain",
                content=invoice,
            ),
            IncomingDocument(
                source_reference="inbox:fiscal-demo:msg-004:attachment-001",
                filename="relatorio-sem-cliente.txt",
                declared_content_type="text/plain",
                content=ambiguous,
            ),
            IncomingDocument(
                source_reference="inbox:fiscal-demo:msg-005:attachment-001",
                filename="extrato-lume-ocr.png",
                declared_content_type="image/png",
                content=_ocr_fixture(),
            ),
        )


def build_document_inbox() -> DocumentInbox:
    return SyntheticDocumentInbox()


def _ocr_fixture() -> bytes:
    image = Image.new("RGB", (1400, 520), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=42)
    draw.multiline_text(
        (80, 80),
        "EXTRATO FINANCEIRO SINTETICO\n"
        "LUME SERVICOS DEMO LTDA\n"
        "CNPJ 45.678.901/0001-22\n"
        "SALDO DEMONSTRATIVO R$ 8.420,00",
        fill="black",
        font=font,
        spacing=22,
    )
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
