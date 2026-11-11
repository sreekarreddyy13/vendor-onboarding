"""Generate synthetic vendor documents for the sample onboarding submissions.

All names, identifiers, and account details come from the sample JSON files.
The resulting documents are fictional test fixtures and have no legal effect.
"""

import json
import random
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageEnhance
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = PROJECT_ROOT / "data" / "samples"
DOCUMENT_DIR = PROJECT_ROOT / "data" / "documents"

# Each sample vendor uses a distinct layout, typeface, and accent palette.
DESIGNS = {
    "happy": {
        "layout": "banner",
        "font": "Helvetica",
        "bold": "Helvetica-Bold",
        "accent": colors.HexColor("#173b66"),
        "pale": colors.HexColor("#e9f0f8"),
    },
    "missing_bank_letter": {
        "layout": "certificate",
        "font": "Times-Roman",
        "bold": "Times-Bold",
        "accent": colors.HexColor("#93652e"),
        "pale": colors.HexColor("#f6efdf"),
    },
    "blocked_country": {
        "layout": "ledger",
        "font": "Courier",
        "bold": "Courier-Bold",
        "accent": colors.HexColor("#28675f"),
        "pale": colors.HexColor("#e4f0ed"),
    },
    "indian_vendor_us_ein": {
        "layout": "split",
        "font": "Times-Italic",
        "bold": "Times-BoldItalic",
        "accent": colors.HexColor("#624c81"),
        "pale": colors.HexColor("#eee9f5"),
    },
    "country_multiple_conflicts": {
        "layout": "split",
        "font": "Courier",
        "bold": "Courier-Bold",
        "accent": colors.HexColor("#8a583d"),
        "pale": colors.HexColor("#f5ece5"),
    },
    "name_suffix_match": {
        "layout": "banner",
        "font": "Times-Roman",
        "bold": "Times-Bold",
        "accent": colors.HexColor("#28736b"),
        "pale": colors.HexColor("#e5f2ef"),
    },
    "name_real_mismatch": {
        "layout": "certificate",
        "font": "Helvetica",
        "bold": "Helvetica-Bold",
        "accent": colors.HexColor("#8b4852"),
        "pale": colors.HexColor("#f4e9eb"),
    },
    "bank_account_collision": {
        "layout": "ledger",
        "font": "Helvetica-Oblique",
        "bold": "Helvetica-Bold",
        "accent": colors.HexColor("#9a3d43"),
        "pale": colors.HexColor("#f7e8e8"),
    },
    "bank_account_same_vendor": {
        "layout": "split",
        "font": "Courier",
        "bold": "Courier-Bold",
        "accent": colors.HexColor("#3f648d"),
        "pale": colors.HexColor("#e7eff7"),
    },
    "resubmission_missing_bank_letter": {
        "layout": "certificate",
        "font": "Times-Italic",
        "bold": "Times-BoldItalic",
        "accent": colors.HexColor("#8b683b"),
        "pale": colors.HexColor("#f4eedf"),
    },
    "resubmission_bank_letter_provided": {
        "layout": "ledger",
        "font": "Helvetica",
        "bold": "Helvetica-Bold",
        "accent": colors.HexColor("#356b55"),
        "pale": colors.HexColor("#e8f2eb"),
    },
}

TAX_DOCUMENT_TYPES = {
    "India": "gst_registration",
    "US": "ein_confirmation",
    "UK": "vat_registration",
}


def _draw_template_header(
    page: canvas.Canvas,
    design: dict[str, object],
    title: str,
    vendor_name: str,
) -> None:
    """Apply one of several distinct document layouts."""
    accent = design["accent"]
    pale = design["pale"]
    font = str(design["font"])
    bold = str(design["bold"])
    layout = str(design["layout"])

    if layout == "banner":
        page.setFillColor(accent)
        page.rect(0, 665, 612, 127, fill=1, stroke=0)
        page.setFillColor(colors.white)
        page.setFont(bold, 10)
        page.drawString(54, 754, "MERIDIAN CLEARING  /  VENDOR RECORDS")
        page.setFont(bold, 21)
        page.drawString(54, 714, title)
        page.setFillColor(colors.HexColor("#c8d5e5"))
        page.setFont(font, 10)
        page.drawString(54, 690, vendor_name)
    elif layout == "certificate":
        page.setStrokeColor(accent)
        page.setLineWidth(2)
        page.rect(27, 27, 558, 738, fill=0, stroke=1)
        page.setStrokeColor(pale)
        page.setLineWidth(7)
        page.rect(39, 39, 534, 714, fill=0, stroke=1)
        page.setFillColor(accent)
        page.setFont(bold, 10)
        page.drawCentredString(306, 719, "NORTHGATE DOCUMENT SERVICES")
        page.setFont(bold, 20)
        page.drawCentredString(306, 671, title)
        page.setFont(font, 11)
        page.drawCentredString(306, 644, vendor_name)
    elif layout == "ledger":
        page.setFillColor(accent)
        page.rect(0, 0, 43, 792, fill=1, stroke=0)
        page.setFillColor(pale)
        page.rect(43, 654, 569, 138, fill=1, stroke=0)
        page.setFillColor(accent)
        page.setFont(bold, 10)
        page.drawString(69, 748, "CASPIAN REGISTRY  ::  RECORD COPY")
        page.setFont(bold, 18)
        page.drawString(69, 705, title)
        page.setFont(font, 10)
        page.drawString(69, 680, vendor_name[:75])
    else:  # split panel layout
        page.setFillColor(pale)
        page.rect(0, 0, 173, 792, fill=1, stroke=0)
        page.setFillColor(accent)
        page.rect(173, 665, 439, 127, fill=1, stroke=0)
        page.setFillColor(colors.white)
        page.setFont(bold, 9)
        page.drawString(203, 750, "FIELDSTONE  /  VERIFIED RECORDS")
        page.setFont(bold, 18)
        page.drawString(203, 711, title)
        page.setFillColor(accent)
        page.setFont(bold, 9)
        page.drawString(31, 733, "VENDOR FILE")
        page.setFillColor(colors.HexColor("#27384f"))
        page.setFont(font, 10)
        for line_number, line in enumerate(simpleSplit(vendor_name, font, 10, 128)):
            page.drawString(31, 701 - (line_number * 15), line)


def _draw_paragraph(
    page: canvas.Canvas,
    text: str,
    x: float,
    y: float,
    width: float,
    font: str,
    font_size: int = 11,
    leading: int = 16,
) -> float:
    """Draw wrapped text and return the next baseline position."""
    page.setFont(font, font_size)
    for line in simpleSplit(text, font, font_size, width):
        page.drawString(x, y, line)
        y -= leading
    return y


def _draw_label_value(
    page: canvas.Canvas,
    label: str,
    value: str,
    x: float,
    y: float,
    design: dict[str, object],
) -> float:
    """Draw a labeled value row using the vendor's selected typeface."""
    font = str(design["font"])
    bold = str(design["bold"])
    page.setFillColor(colors.HexColor("#6b7787"))
    page.setFont(bold, 9)
    page.drawString(x, y, label.upper())
    page.setFillColor(colors.HexColor("#17263d"))
    next_y = _draw_paragraph(page, value or "Not supplied", x, y - 22, 445, font, 12, 16)
    return next_y - 14


def _draw_footer(page: canvas.Canvas, design: dict[str, object], vendor_id: str) -> None:
    page.setStrokeColor(design["pale"])
    page.setLineWidth(1)
    page.line(56, 73, 556, 73)
    page.setFillColor(colors.HexColor("#7a8796"))
    page.setFont(str(design["font"]), 8)
    page.drawString(56, 56, "FICTIONAL SAMPLE  ·  NOT A LEGAL OR BANKING DOCUMENT")
    page.drawRightString(556, 56, f"REFERENCE {vendor_id.upper()}")


def create_document(
    destination: Path,
    sample: dict[str, object],
    sample_id: str,
    document_kind: str,
) -> None:
    """Create one realistic-looking, but clearly fictional, PDF test fixture."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    design = DESIGNS[sample_id]
    company_name = str(sample.get("company_name", "Unnamed Example Vendor"))
    country = str(sample.get("country", "Unspecified"))
    tax_id = str(sample.get("tax_id", ""))
    page = canvas.Canvas(str(destination), pagesize=letter, pageCompression=1)
    page.setTitle(f"Synthetic {document_kind.replace('_', ' ').title()} - {company_name}")
    page.setAuthor("Vendor Onboarding Test Data Generator")

    if document_kind == "tax_registration_certificate":
        title = "Tax Registration Certificate"
        _draw_template_header(page, design, title, company_name)
        y = 606 if design["layout"] != "certificate" else 583
        page.setFillColor(colors.HexColor("#27384f"))
        y = _draw_paragraph(
            page,
            f"This certificate records a fictional tax registration for {company_name}.",
            72,
            y,
            470,
            str(design["font"]),
            12,
            19,
        ) - 22
        y = _draw_label_value(page, "Registered entity", company_name, 78, y, design)
        y = _draw_label_value(page, "Tax identifier", tax_id, 78, y, design)
        y = _draw_label_value(page, "Registration jurisdiction", country, 78, y, design)
        y = _draw_label_value(page, "Record reference", f"TAX-{sample_id.upper()}-2025", 78, y, design)
        _draw_label_value(page, "Record date", "15 January 2025", 78, y, design)
    elif document_kind == "bank_confirmation_letter":
        title = "Bank Account Confirmation"
        _draw_template_header(page, design, title, company_name)
        x = 72 if design["layout"] != "certificate" else 82
        y = 605 if design["layout"] != "certificate" else 586
        y = _draw_paragraph(
            page,
            "To whom it may concern: the account details below are reproduced from the vendor's synthetic onboarding submission for test purposes.",
            x,
            y,
            470,
            str(design["font"]),
            11,
            18,
        ) - 28
        y = _draw_label_value(
            page, "Account holder", str(sample.get("bank_account_holder_name") or company_name), x, y, design
        )
        y = _draw_label_value(page, "Account number", str(sample.get("bank_account_number", "")), x, y, design)
        y = _draw_label_value(page, "Bank code", str(sample.get("bank_code", "")), x, y, design)
        y = _draw_label_value(page, "Bank country", str(sample.get("bank_country", country)), x, y, design)
        _draw_label_value(page, "Confirmation reference", f"BANK-{sample_id.upper()}-041", x, y, design)
    else:
        title = "Certificate of Incorporation"
        _draw_template_header(page, design, title, company_name)
        y = 602 if design["layout"] != "certificate" else 580
        page.setFillColor(colors.HexColor("#27384f"))
        y = _draw_paragraph(
            page,
            "The registry records the following fictional entity as incorporated for vendor onboarding test purposes.",
            72,
            y,
            470,
            str(design["font"]),
            11,
            18,
        ) - 28
        y = _draw_label_value(page, "Legal entity name", company_name, 78, y, design)
        y = _draw_label_value(page, "Place of registration", country, 78, y, design)
        y = _draw_label_value(page, "Registry reference", f"INC-{sample_id.upper()}-2025", 78, y, design)
        _draw_label_value(page, "Incorporation date", "08 March 2021", 78, y, design)

    _draw_footer(page, design, sample_id)
    page.showPage()
    page.save()


def create_scanned_png(source_pdf: Path, destination_png: Path) -> None:
    """Render a generated PDF page and add subtle scan rotation and grain."""
    pdf = pdfium.PdfDocument(str(source_pdf))
    rendered_page = pdf[0].render(scale=2.0)
    clean_image = rendered_page.to_pil().convert("RGB")
    pdf.close()

    rotated = clean_image.rotate(
        0.55,
        resample=Image.Resampling.BICUBIC,
        expand=True,
        fillcolor=(248, 247, 242),
    )
    gray_background = Image.new("L", (120, 160))
    random_source = random.Random(2025)
    gray_background.putdata([random_source.randint(112, 144) for _ in range(120 * 160)])
    noise = gray_background.resize(rotated.size, Image.Resampling.BILINEAR).convert("RGB")
    scanned = Image.blend(rotated, noise, alpha=0.035)
    scanned = ImageEnhance.Contrast(scanned).enhance(0.97)
    destination_png.parent.mkdir(parents=True, exist_ok=True)
    scanned.save(destination_png, format="PNG", optimize=True)


def _document_type_for_tax(country: str) -> str:
    return TAX_DOCUMENT_TYPES.get(country, "tax_registration_certificate")


def main() -> None:
    """Generate the PDFs and write their relative paths into each sample JSON."""
    DOCUMENT_DIR.mkdir(parents=True, exist_ok=True)
    sample_files = sorted(SAMPLE_DIR.glob("*.json"))
    if not sample_files:
        raise FileNotFoundError(f"No sample JSON files found in {SAMPLE_DIR}")

    scanned_tax_pdf: Path | None = None
    scanned_sample: dict[str, object] | None = None
    scanned_sample_path: Path | None = None

    for sample_path in sample_files:
        sample_id = sample_path.stem
        if sample_id not in DESIGNS:
            raise ValueError(f"Add a visual design for sample {sample_id!r} in DESIGNS.")
        sample = json.loads(sample_path.read_text(encoding="utf-8"))
        country = str(sample.get("country", ""))
        tax_type = _document_type_for_tax(country)
        filenames = {
            "certificate_of_incorporation": f"{sample_id}_incorporation_certificate.pdf",
            tax_type: f"{sample_id}_tax_registration_certificate.pdf",
            "bank_letter": f"{sample_id}_bank_confirmation_letter.pdf",
        }

        for kind, filename in (
            ("certificate_of_incorporation", filenames["certificate_of_incorporation"]),
            ("tax_registration_certificate", filenames[tax_type]),
            ("bank_confirmation_letter", filenames["bank_letter"]),
        ):
            create_document(DOCUMENT_DIR / filename, sample, sample_id, kind)

        submitted_types = [
            "certificate_of_incorporation",
            tax_type,
            "bank_letter",
        ]
        # This fixture deliberately omits the bank letter from the submitted packet.
        if sample_id in {"missing_bank_letter", "resubmission_missing_bank_letter"}:
            submitted_types.remove("bank_letter")
        sample["documents"] = submitted_types
        sample["document_files"] = {
            kind: f"data/documents/{filenames[kind]}" for kind in submitted_types
        }
        sample.pop("scanned_document_file", None)

        if sample_id == "happy":
            scanned_tax_pdf = DOCUMENT_DIR / filenames[tax_type]
            scanned_sample = sample
            scanned_sample_path = sample_path

        sample_path.write_text(json.dumps(sample, indent=2) + "\n", encoding="utf-8")

    if scanned_tax_pdf and scanned_sample is not None and scanned_sample_path:
        scan_path = DOCUMENT_DIR / "happy_tax_registration_scanned.png"
        create_scanned_png(scanned_tax_pdf, scan_path)
        scanned_sample["scanned_document_file"] = "data/documents/happy_tax_registration_scanned.png"
        scanned_sample_path.write_text(
            json.dumps(scanned_sample, indent=2) + "\n", encoding="utf-8"
        )

    print(f"Generated sample PDFs and scan image in {DOCUMENT_DIR}")


if __name__ == "__main__":
    main()
