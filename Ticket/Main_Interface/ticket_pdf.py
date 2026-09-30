from io import BytesIO
from xml.sax.saxutils import escape

from django.conf import settings
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


GREEN = colors.HexColor("#006a4e")
INK = colors.HexColor("#172820")
LINE = colors.HexColor("#cfd8d3")


def create_ticket_pdf(ticket, schedule, nid_masked):
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title="RailwaySheba E-Ticket",
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="TicketTitle", parent=styles["Title"], fontName="Helvetica-Bold",
        fontSize=17, leading=21, textColor=colors.HexColor("#0a58ca"),
        alignment=TA_CENTER, spaceAfter=2,
    ))
    styles.add(ParagraphStyle(
        name="TicketSubtitle", parent=styles["Normal"], fontSize=10,
        textColor=colors.HexColor("#0a58ca"), alignment=TA_CENTER,
    ))
    styles.add(ParagraphStyle(
        name="TicketBody", parent=styles["BodyText"], fontSize=9,
        leading=13, textColor=INK,
    ))
    styles.add(ParagraphStyle(
        name="TicketCell", parent=styles["BodyText"], fontSize=8.5,
        leading=11, textColor=INK,
    ))
    styles.add(ParagraphStyle(
        name="TicketSection", parent=styles["Heading3"], fontSize=10,
        leading=13, textColor=colors.white, alignment=TA_LEFT,
    ))

    def paragraph(value, style="TicketCell"):
        return Paragraph(escape(str(value or "")), styles[style])

    verify_url = ticket["verify_url"]
    qr = QrCodeWidget(verify_url)
    bounds = qr.getBounds()
    qr_size = 58
    qr_drawing = Drawing(
        qr_size,
        qr_size,
        transform=[qr_size / bounds[2], 0, 0, qr_size / bounds[3], 0, 0],
    )
    qr_drawing.add(qr)

    logo_path = settings.BASE_DIR / "Main_Interface" / "static" / "image" / "RailwaySheba.png"
    logo = Image(str(logo_path), width=48, height=48) if logo_path.exists() else ""
    header = Table(
        [[
            logo,
            [paragraph("RAILWAYSHEBA E-TICKET", "TicketTitle"),
             paragraph("Bangladesh Railway", "TicketSubtitle")],
            qr_drawing,
        ]],
        colWidths=[55, 390, 60],
    )
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, 0), "CENTER"),
        ("ALIGN", (2, 0), (2, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))

    story = [header, Spacer(1, 7 * mm)]
    story.append(paragraph(
        f"Dear {ticket['passenger_name']}, your e-ticket booking is confirmed. "
        "Carry your NID or photo ID while travelling.",
        "TicketBody",
    ))
    story.append(Spacer(1, 4 * mm))

    def add_section(title, rows):
        section = Table([[paragraph(title, "TicketSection")]], colWidths=[505])
        section.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), GREEN),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        data = [[paragraph(label), paragraph(value)] for label, value in rows]
        table = Table(data, colWidths=[242, 263])
        table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), .5, LINE),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTNAME", (1, 0), (1, -1), "Helvetica-Bold"),
        ]))
        story.extend([section, table, Spacer(1, 4 * mm)])

    issue_time = timezone.localtime(ticket["issue_time"]).strftime("%d-%m-%Y %H:%M")
    departure_time = timezone.localtime(schedule.departure_time).strftime("%d-%m-%Y %H:%M")
    add_section("Journey Information", [
        ("Issue Date & Time", issue_time),
        ("Journey Date & Time", departure_time),
        ("Train Name & Number", f"{schedule.train.train_name.upper()} [{schedule.train.train_number}]"),
        ("From Station", schedule.source_station),
        ("To Station", schedule.destination_station),
        ("Class Name", ticket["class_name"]),
        ("Seat(s)", ticket["seats"]),
        ("No. of Seats", ticket["seat_count"]),
        ("No. of Adult Passenger(s)", ticket["seat_count"]),
        ("Total Fare", f"BDT {ticket['total_fare']:.2f}"),
    ])
    add_section("Passenger Information", [
        ("Passenger Name", ticket["passenger_name"]),
        ("Identification Type", "NID"),
        ("Identification Number", nid_masked),
        ("Mobile Number", ticket["phone_masked"]),
        ("Confirmation Number", ", ".join(ticket["confirmation_numbers"])),
    ])
    story.append(paragraph(
        "Please carry your NID or photo ID. A printed or digital copy of this ticket is valid. "
        "Scan the QR code to verify this ticket. Railway services: 131.",
        "TicketBody",
    ))
    story.append(Spacer(1, 5 * mm))
    story.append(paragraph("Wishing you a pleasant and safe journey - RailwaySheba", "TicketSubtitle"))
    document.build(story)
    return output.getvalue()