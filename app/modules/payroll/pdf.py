"""Payslip PDF rendering + payslip email (Steve's slice).

PDF: rendered on-demand with reportlab. A payslip whose status is not 'paid'
gets a visible diagonal "DRAFT / UNVALIDATED" watermark so HR can preview
before finalizing (prompt §3.5 edge case).

Email: bulk-send uses the standard library (`smtplib` + `email.mime`). SMTP
is configured purely via environment variables (no config.py changes — that
file is frozen/Eldo's):

    SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM

When SMTP_HOST is unset (typical local dev), a **console transport** is used:
the "email" is logged to stdout and treated as sent. This keeps the demo flow
and the test suite working end-to-end without an SMTP server. The per-recipient
error handling lives in the service layer (one bad address never aborts the
batch — prompt §3.5).
"""

import logging
import os
import smtplib
from datetime import datetime, timezone
from decimal import Decimal
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.models.enums import PayrunStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PDF rendering
# ---------------------------------------------------------------------------


def _money(value: Decimal) -> str:
    return f"{value:,.2f}"


def render_payslip_pdf(
    *,
    company_name: str,
    payrun_name: str,
    period_start,
    period_end,
    employee_name: str,
    employee_email: str,
    employee_type: str,
    department_name: str | None,
    worked_days: Decimal,
    gross_salary: Decimal,
    net_salary: Decimal,
    status: PayrunStatus,
    lines: list,
    warnings: list,
) -> bytes:
    """Render a single payslip to PDF bytes.

    `lines`    : iterable of (sequence, code, name, category, amount)
    `warnings` : iterable of (warning_type, message)
    """
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=f"Payslip — {employee_name}",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "PayslipTitle", parent=styles["Title"], fontSize=18, spaceAfter=2
    )
    sub_style = ParagraphStyle(
        "Subtitle", parent=styles["Normal"], alignment=1, textColor=colors.grey, fontSize=9
    )
    h2_style = ParagraphStyle(
        "H2", parent=styles["Heading2"], fontSize=12, spaceBefore=8, spaceAfter=4
    )
    warn_style = ParagraphStyle(
        "Warn", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#B00020")
    )

    watermark_text = ""
    if status != PayrunStatus.paid:
        watermark_text = "DRAFT / UNVALIDATED"

    def _watermark(cv: canvas.Canvas):
        if watermark_text:
            cv.saveState()
            cv.setFont("Helvetica-Bold", 48)
            cv.setFillColor(colors.HexColor("#DDDDDD"))
            cv.translate(A4[0] / 2.0, A4[1] / 2.0)
            cv.rotate(35)
            cv.drawCentredString(0, 0, watermark_text)
            cv.restoreState()

    story: list = []

    story.append(Paragraph(f"{company_name}", title_style))
    story.append(Paragraph("Payslip", sub_style))
    story.append(Paragraph(
        f"{payrun_name} &nbsp;|&nbsp; {period_start} — {period_end}", sub_style
    ))
    story.append(Spacer(1, 6))

    emp_data = [
        ["Employee", employee_name],
        ["Email", employee_email],
        ["Type", employee_type],
        ["Department", department_name or "—"],
        ["Worked days", str(worked_days)],
    ]
    emp_table = Table(emp_data, colWidths=[45 * mm, 100 * mm])
    emp_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F5F5F5")),
    ]))
    story.append(emp_table)
    story.append(Spacer(1, 6))

    story.append(Paragraph("Earnings & Deductions", h2_style))
    header = ["#", "Code", "Description", "Category", "Amount (INR)"]
    rows = [header]
    for seq, code, name, category, amount in lines:
        rows.append([str(seq), code, name, category, _money(amount)])
    line_table = Table(rows, colWidths=[10 * mm, 35 * mm, 55 * mm, 30 * mm, 30 * mm])
    line_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ALIGN", (4, 0), (4, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8E8E8")),
    ]))
    story.append(line_table)
    story.append(Spacer(1, 6))

    totals = Table(
        [
            ["Gross Salary", _money(gross_salary)],
            ["Net Salary (take-home)", _money(net_salary)],
        ],
        colWidths=[45 * mm, 100 * mm],
    )
    totals.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.black),
        ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#E8F0E8")),
    ]))
    story.append(totals)

    if warnings:
        story.append(Paragraph("Warnings", h2_style))
        for wtype, message in warnings:
            story.append(Paragraph(f"• [{wtype}] {message}", warn_style))

    story.append(Spacer(1, 10))
    story.append(Paragraph(
        f"Generated by PeoplePay360 at "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}. "
        "This document is auto-generated and does not require a signature.",
        sub_style,
    ))

    # Attach the watermark via the canvas callbacks.
    def _first_page(cv: canvas.Canvas, _doc):
        _watermark(cv)

    def _later_pages(cv: canvas.Canvas, _doc):
        _watermark(cv)

    doc.build(story, onFirstPage=_first_page, onLaterPages=_later_pages)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Email transport (SMTP if configured, console otherwise)
# ---------------------------------------------------------------------------


def _smtp_config() -> dict:
    return {
        "host": os.environ.get("SMTP_HOST"),
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "username": os.environ.get("SMTP_USERNAME"),
        "password": os.environ.get("SMTP_PASSWORD"),
        "from_addr": os.environ.get("SMTP_FROM", "payroll@peoplepay360.local"),
    }


def send_payslip_email(
    *,
    to_email: str,
    employee_name: str,
    payrun_name: str,
    period_start,
    period_end,
    pdf_bytes: bytes,
) -> None:
    """Send one payslip email. Raises on failure; the service layer catches
    per-recipient so one bad address never aborts the batch."""
    config = _smtp_config()
    subject = f"Payslip {period_start} — {period_end} ({payrun_name})"
    body = (
        f"Hi {employee_name},\n\n"
        f"Please find attached your payslip for the period {period_start} to "
        f"{period_end} ({payrun_name}).\n\n"
        "Regards,\nPeoplePay360 Payroll"
    )

    if not config["host"]:
        # Console transport — keeps local dev + tests working without SMTP.
        logger.info(
            "[console-email] To: %s | Subject: %s | PDF bytes: %d",
            to_email, subject, len(pdf_bytes),
        )
        return

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = config["from_addr"]
    msg["To"] = to_email
    msg.attach(MIMEText(body, "plain"))
    attachment = MIMEApplication(pdf_bytes, _subtype="pdf")
    attachment.add_header(
        "Content-Disposition", "attachment", filename="payslip.pdf"
    )
    msg.attach(attachment)

    with smtplib.SMTP(config["host"], config["port"], timeout=15) as server:
        server.starttls()
        if config["username"]:
            server.login(config["username"], config["password"])
        server.sendmail(config["from_addr"], [to_email], msg.as_string())