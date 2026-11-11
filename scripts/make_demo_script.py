"""Create a phone-friendly, timed narration guide for the live demo."""

from __future__ import annotations

import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import portrait
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = PROJECT_ROOT / "DEMO_SCRIPT.pdf"
PAGE_SIZE = portrait((110 * mm, 200 * mm))


def register_phone_font() -> tuple[str, str]:
    """Use a Unicode TrueType font so punctuation renders safely."""
    font_pairs = [
        (Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/arialbd.ttf")),
        (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ),
        (
            Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
            Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
        ),
    ]
    for regular_path, bold_path in font_pairs:
        if regular_path.is_file() and bold_path.is_file():
            pdfmetrics.registerFont(TTFont("DemoSans", str(regular_path)))
            pdfmetrics.registerFont(TTFont("DemoSans-Bold", str(bold_path)))
            return "DemoSans", "DemoSans-Bold"
    raise RuntimeError(
        "A Unicode sans-serif font is required. Install Arial or DejaVu Sans, "
        "then regenerate DEMO_SCRIPT.pdf."
    )


def demo_segments() -> list[dict[str, object]]:
    """Return the timed, read-aloud narration and on-screen actions."""
    return [
        {
            "time": "0:00 to 0:20",
            "title": "Opening",
            "scenario": "No scenario yet; stay on Live Run.",
            "clicks": "Open Live Run and leave the form empty for this introduction.",
            "narration": "Before paying a new vendor, a team must verify its details and documents. I will show that process running live. AI reads documents and drafts emails; deterministic Python checks the rules and makes every decision.",
        },
        {
            "time": "0:20 to 1:10",
            "title": "Happy path",
            "scenario": 'Load "Happy path" (happy.json).',
            "clicks": "Choose Happy path in Load demo scenario, click Load, then click Submit for review.",
            "narration": "This is Saffron Peak, a sample Indian vendor. The form has a GSTIN, an IFSC code, matching company names, and three documents. Intake records the submission, and document reading extracts evidence. Completeness checks required details and files; format validation checks the country, IDs, bank code, and email. Cross-referencing compares names and bank country. Risk checks look for blocked countries, duplicate tax IDs, and reused accounts. All checks pass, so the vendor is APPROVED. No email is needed. The audit log saves the checks and reasons, and cached extraction helps the demo run quickly.",
        },
        {
            "time": "1:10 to 1:35",
            "title": "Name normalisation",
            "scenario": 'Load "Name normalisation" (name_suffix_match.json).',
            "clicks": "Choose Name normalisation, click Load, then Submit for review.",
            "narration": "The form says Sunrise Textiles Pvt Ltd, while the bank letter says Sunrise Textiles Private Limited. The system normalises case, punctuation, and legal suffixes before comparing names. These versions match, so the vendor is approved. The audit explains the normalisation, while a genuinely different name still fails the comparison.",
        },
        {
            "time": "1:35 to 2:00",
            "title": "Shared bank account",
            "scenario": 'Load "Shared bank account" (bank_account_collision.json).',
            "clicks": "Choose Shared bank account, click Load, then Submit for review.",
            "narration": "This submission uses an account already associated with another approved vendor. The risk check detects the reuse, so the decision is REJECTED. The internal audit explains the collision, but the vendor email does not reveal another vendor's identity.",
        },
        {
            "time": "2:00 to 2:45",
            "title": "Resubmission",
            "scenario": "Run both Willow Creek scenarios: initial pending attempt, then document supplied.",
            "clicks": "Choose Resubmission - initial pending attempt, Load, Submit for review. Then choose Resubmission - document supplied, Load, Submit for review.",
            "narration": "The first Willow Creek application is missing its bank letter. Completeness marks it PENDING, and communication drafts a request for the file. Now the same vendor resubmits with the letter attached. The matching tax ID links this run to the earlier attempt. The system reruns its checks and records what changed. Since the bank letter is now present and the other checks pass, the resubmission is APPROVED.",
        },
        {
            "time": "2:45 to 3:10",
            "title": "Country conflict",
            "scenario": 'Load "Country conflict" (indian_vendor_us_ein.json).',
            "clicks": "Choose Country conflict, click Load, then Submit for review.",
            "narration": "This vendor claims India but entered a US-style EIN where an Indian GSTIN is expected. The format check explains that conflict. Because one field could be a typo, the application is PENDING and the email asks for a correction. Uncertain evidence is not approved automatically.",
        },
        {
            "time": "3:10 to 3:40",
            "title": "Dashboard and audit trail",
            "scenario": "Open the dashboard after the runs above.",
            "clicks": "In the sidebar choose Dashboard. In Inspect a run, select the Willow Creek approved resubmission.",
            "narration": "The dashboard summarises run history and outcomes. These linked entries show Willow Creek's pending attempt beside its approved resubmission. The audit trail shows every check in order with timestamps, the original submission, and the saved email draft. The team can review what happened and why after the live run ends.",
        },
        {
            "time": "3:40 to 4:25",
            "title": "Design choices and next steps",
            "scenario": "Keep the Dashboard and audit trail visible.",
            "clicks": "No additional click; point to the audit details and linked runs.",
            "narration": "The pipeline separates each responsibility and records an explanation. RapidFuzz helps compare names; country rules and thresholds live in configuration; SQLite stores submissions and audit events. Gemini only reads documents and drafts emails. A cache and plain-template fallback keep the process usable if the API fails. The sample data and rules are illustrative, not legal verification. Next I would add reviewer sign-off, access controls, and policy-approved rules.",
        },
        {
            "time": "4:25 to 4:45",
            "title": "Closing",
            "scenario": "Finish on the Dashboard.",
            "clicks": "No additional click; close the recording on the dashboard.",
            "narration": "This demo showed a live approval, realistic edge cases, clear vendor follow-up, and an explainable audit trail. Thank you for watching.",
        },
    ]


def build_pdf() -> Path:
    regular_font, bold_font = register_phone_font()
    document = SimpleDocTemplate(
        str(OUTPUT_PATH),
        pagesize=PAGE_SIZE,
        rightMargin=9 * mm,
        leftMargin=9 * mm,
        topMargin=9 * mm,
        bottomMargin=9 * mm,
        title="Vendor Onboarding Demo Script",
        author="Vendor Onboarding Demo",
    )
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="PhoneTitle",
            parent=styles["Title"],
            fontName=bold_font,
            fontSize=19,
            leading=23,
            alignment=TA_LEFT,
            textColor=colors.black,
            spaceAfter=3 * mm,
        )
    )
    styles.add(
        ParagraphStyle(
            name="PhoneMeta",
            parent=styles["Normal"],
            fontName=regular_font,
            fontSize=13,
            leading=17,
            textColor=colors.black,
            spaceAfter=4 * mm,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SegmentHeading",
            parent=styles["Heading2"],
            fontName=bold_font,
            fontSize=14,
            leading=17,
            textColor=colors.black,
            spaceBefore=2 * mm,
            spaceAfter=1.5 * mm,
            keepWithNext=True,
        )
    )
    styles.add(
        ParagraphStyle(
            name="PhoneBody",
            parent=styles["BodyText"],
            fontName=regular_font,
            fontSize=13,
            leading=17,
            textColor=colors.black,
            spaceAfter=1.4 * mm,
            alignment=TA_LEFT,
        )
    )
    styles.add(
        ParagraphStyle(
            name="NarrationLabel",
            parent=styles["PhoneBody"],
            fontName=bold_font,
            spaceBefore=1 * mm,
            spaceAfter=1 * mm,
        )
    )
    story = [
        Paragraph("Vendor Onboarding Demo", styles["PhoneTitle"]),
        Paragraph("Total time: 4 minutes 45 seconds", styles["PhoneMeta"]),
    ]

    for segment in demo_segments():
        section = [
            Paragraph(
                f"{segment['time']}, {segment['title']}", styles["SegmentHeading"]
            ),
            Paragraph(f"<b>Load:</b> {segment['scenario']}", styles["PhoneBody"]),
            Paragraph(f"<b>Click:</b> {segment['clicks']}", styles["PhoneBody"]),
            Paragraph("Say:", styles["NarrationLabel"]),
            Paragraph(str(segment["narration"]), styles["PhoneBody"]),
        ]
        story.append(KeepTogether(section))
        story.append(Spacer(1, 2 * mm))

    story.extend(
        [
            PageBreak(),
            Paragraph("Before you press record", styles["PhoneTitle"]),
            Paragraph("Quick checklist", styles["PhoneMeta"]),
        ]
    )
    checklist = [
        "Run the preflight command and fix any reported failures.",
        "Close other apps and windows that should not appear in the recording.",
        "Turn off desktop and browser notifications.",
        "Set browser zoom to 110 to 125 percent.",
        "Check the microphone and make a short audio test.",
        "Keep the finished video under 5 minutes.",
    ]
    for item in checklist:
        story.append(Paragraph(f"[ ] {item}", styles["PhoneBody"]))
        story.append(Spacer(1, 2 * mm))

    document.build(story)
    return OUTPUT_PATH


def main() -> None:
    try:
        output = build_pdf()
    except Exception as error:
        print(f"Could not create DEMO_SCRIPT.pdf: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    print(f"Created {output.name}")


if __name__ == "__main__":
    main()
