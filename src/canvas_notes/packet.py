"""Extractive worksheet completion, one-page rendering, and source custody."""
import hashlib
import html
import json
import math
import re
import tempfile
from collections import Counter, deque
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph, Table, TableStyle

from .captions import assess, transcript

LETTER = (612, 792)
MARGIN = 18
GUTTER = 10
COLUMN = (LETTER[0] - MARGIN * 2 - GUTTER * 2) / 3
BODY = 8
STOP = set("about after again also and are because been but can class could did does example for from had has have here how into just like more notes now one only other our out over page say see sheet should slides than that the their them then there these they this those through too use used very was were what when where which while who will with would you your".split())


@dataclass(frozen=True)
class Block:
    label: str
    text: str
    refs: tuple[str, ...]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_file(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def extract_notes(pdfs, folder):
    units, inputs, files = [], [], {}
    for i, path in enumerate(pdfs, 1):
        data = path.read_bytes()
        name = f"note-{i:02}.pdf"
        (folder / name).write_bytes(data)
        files[name] = data
        entry = {"attachment": name, "original_name": path.name, "sha256": digest(data), "pages": []}
        inputs.append(entry)
        try:
            doc = PdfReader(BytesIO(data), strict=False)
            if not doc.pages:
                raise ValueError("PDF has no pages")
            for pn, page in enumerate(doc.pages, 1):
                try:
                    text = page.extract_text() or ""
                    status = "text extracted; inspect original for diagrams and handwriting" if text.strip() else "no text extracted"
                except Exception as error:
                    text, status = "", f"text extraction failed ({type(error).__name__})"
                entry["pages"].append({"page": pn, "text": text, "status": status})
                # Preserve the untouched extract above. Split its display text into selectable units.
                for ln, line in enumerate(text.splitlines(), 1):
                    if line.strip():
                        units.append(Block(f"Note {i}, p. {pn}", line.strip(), (f"N{i}.{pn}.{ln}",)))
        except Exception as error:
            entry["read_error"] = type(error).__name__
    return units, inputs, files


def terms(text):
    return set(re.findall(r"[^\W\d_][\w-]{2,}", text.lower())) - STOP


def fills(notes, cues):
    """Interleave note lines with exact nearby caption windows, then fill remaining space."""
    windows = []
    start = 0
    while start < len(cues):
        end = start
        length = len(cues[start]["text"])
        while end + 1 < len(cues) and end - start < 5 and length < 230:
            if length >= 95 and re.search(r"[.!?][\"”']?\s*$", cues[end]["text"]):
                break
            end += 1
            length += len(cues[end]["text"])
        stamp = cues[start]["timestamp"]
        if end != start:
            stamp += " – " + cues[end]["timestamp"]
        wording = " ".join(cue["text"] for cue in cues[start:end + 1])
        windows.append(Block(stamp, wording, tuple(f"C{i + 1}" for i in range(start, end + 1))))
        start = end + 1

    # Administrative text stays in the evidence and complete outline. Prefer
    # biological instruction or the worksheet topic when selecting the print page.
    admin = re.compile(
        r"succeeding in | bio sci \d|pollev|midterm |office hours|study strateg|learning assistant|"
        r"vote |by mail|announcements|take.*quiz|required read|let me|\bhttps?://",
        re.IGNORECASE,
    )
    window_words = [terms(block.text) for block in windows]
    frequencies = Counter(word for ws in window_words for word in ws)
    pending = set(range(len(windows)))
    pairs = []
    deferred_notes = []
    for note in notes:
        ws = terms(note.text)
        if not ws or not pending or admin.search(note.text) or (
            note.text[:1].islower() and not note.text.rstrip().endswith("?")
        ):
            deferred_notes.append((note,))
            continue
        best = max(sorted(pending), key=lambda j: sum(1 / math.sqrt(frequencies[w])
                                                       for w in ws & window_words[j])
                   - (0.8 if admin.search(windows[j].text) else 0))
        common = ws & window_words[best]
        if len(common) >= 2 or (len(ws) <= 3 and any(frequencies[w] <= 3 for w in common)):
            pairs.append((note, windows[best]))
            pending.remove(best)
        else:
            deferred_notes.append((note,))

    # Spread first picks across different PDFs/pages so long introductory sections
    # do not consume the whole page before later notes and video segments appear.
    by_page = {}
    def useful(group):
        # Leave slide numbers, dates and bare URLs available in the outline and
        # evidence, but give explanatory source lines first use of the page.
        text = group[0].text
        return (not admin.search(text) and bool(terms(text)) and
                (not text[:1].islower() or text.endswith("?")))

    groups = pairs + deferred_notes
    backlog = []
    for group in groups:
        if useful(group):
            by_page.setdefault(group[0].label, deque()).append(group)
        else:
            backlog.append(group)
    remaining = sorted(pending)
    video = deque()
    # Select across the timeline before revisiting closer neighboring windows.
    while remaining:
        interval = max(1, math.ceil(len(remaining) / 6))
        for j in range(0, len(remaining), interval):
            group = (windows[remaining[j]],)
            if admin.search(group[0].text):
                backlog.append(group)
            else:
                video.append(group)
        remaining = [index for j, index in enumerate(remaining) if j % interval]
    sources = deque(by_page.values())
    itinerary = []
    while sources or video:
        for _ in range(min(3, len(sources))):
            queue = sources.popleft()
            itinerary.append(queue.popleft())
            if queue:
                sources.append(queue)
        if video:
            itinerary.append(video.popleft())
    itinerary.extend(backlog)
    return itinerary, windows


def write_outline(path, title, itinerary):
    """Emit every extracted note line and every captured cue window once."""
    def source_order(group):
        ref = group[0].refs[0]
        if ref.startswith("N"):
            return (0, *(int(x) for x in ref[1:].split(".")))
        return (1, int(ref[1:]))

    with path.open("w", encoding="utf-8") as stream:
        stream.write(f"# {title}\n\nSee the original PDFs for all figures and layout. "
                     "Caption windows below include their cue IDs. The per-cue timestamped wording is in transcript.txt.\n")
        previous = None
        for group in sorted(itinerary, key=source_order):
            heading = group[0].label if group[0].refs[0].startswith("N") else "Additional caption passages"
            if heading != previous:
                stream.write(f"\n## {heading}\n\n")
                previous = heading
            for block in group:
                ref = ", ".join(block.refs)
                stream.write(f"**{block.label}** ({ref})  \n{block.text}\n\n")


def fonts():
    candidates = [
        ("/System/Library/Fonts/Supplemental/Arial Narrow.ttf",
         "/System/Library/Fonts/Supplemental/Arial Narrow Bold.ttf",
         "/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", None),
    ]
    for body, bold, handwriting in candidates:
        if Path(body).is_file() and Path(bold).is_file():
            pdfmetrics.registerFont(TTFont("Notes", body))
            pdfmetrics.registerFont(TTFont("NotesBold", bold))
            pdfmetrics.registerFont(TTFont("NotesTitle", handwriting if handwriting and Path(handwriting).is_file() else bold))
            pdfmetrics.registerFontFamily("Notes", normal="Notes", bold="NotesBold")
            return "Notes", "NotesTitle"
    return "Helvetica", "Helvetica-Bold"


def printable_text(value, font):
    chosen = pdfmetrics.getFont(font)
    glyphs = getattr(chosen.face, "charToGlyph", None)

    def supported(char):
        if char in "\n\r\t":
            return True
        if glyphs is not None:
            return ord(char) in glyphs
        try:
            char.encode("cp1252")
            return True
        except UnicodeEncodeError:
            return False

    return all(supported(char) for char in value)


def render_page(title, status, groups, files, out):
    font, heading = fonts()
    top = LETTER[1] - MARGIN
    style = ParagraphStyle("body", fontName=font, fontSize=BODY, leading=9.55,
                           textColor=HexColor("#162727"), splitLongWords=1)
    title_style = ParagraphStyle("title", fontName=heading, fontSize=16, leading=18,
                                 textColor=HexColor("#245053"), splitLongWords=1)
    if not printable_text(title, heading):
        heading = font
    title_style.fontName = heading
    title = title if printable_text(title, heading) else "Lecture notes"
    title_paragraph = Paragraph(html.escape(title), title_style)
    _, title_height = title_paragraph.wrap(LETTER[0] - 2 * MARGIN, 100)
    buf = BytesIO()
    canvas = Canvas(buf, pagesize=LETTER, pageCompression=1)
    title_paragraph.drawOn(canvas, MARGIN, top - title_height)
    top -= title_height + 6
    if status != "full":
        notice = Paragraph(f"<b>Caption coverage:</b> {html.escape(status)}. See coverage.json.", style)
        _, height = notice.wrap(LETTER[0] - 2 * MARGIN, 50)
        top -= height
        notice.drawOn(canvas, MARGIN, top)
        top -= 4
    for i in (1, 2):
        x = MARGIN + i * COLUMN + (i - .5) * GUTTER
        canvas.setStrokeColor(HexColor("#afc1c2"))
        canvas.setLineWidth(.35)
        canvas.line(x, MARGIN, x, top)
    positions = [top] * 3
    placed = []
    current_column = 0
    for group in groups:
        paragraphs = []
        for block in group:
            if not printable_text(block.text + block.label, font):
                continue
            label = html.escape(block.label)
            body = html.escape(block.text).replace("\n", "<br/>")
            cells = [x.strip() for x in block.text.strip("|").split("|")]
            if block.refs[0].startswith("N") and 2 <= len(cells) <= 3 and all(cells):
                widths = [COLUMN / len(cells)] * len(cells)
                values = [Paragraph(html.escape(cell), style) for cell in cells]
                paragraph = Table([[Paragraph(f"<b>{label}</b>", style), *[""] * (len(cells)-1)], values],
                                  colWidths=widths, style=TableStyle([
                                      ("SPAN", (0, 0), (-1, 0)),
                                      ("BACKGROUND", (0, 0), (-1, 0), HexColor("#edf4f4")),
                                      ("LINEBELOW", (0, 1), (-1, 1), .35, HexColor("#afc1c2")),
                                      ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                      ("TOPPADDING", (0, 0), (-1, -1), 1),
                                      ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                                  ]))
            else:
                paragraph = Paragraph(f"<b>{label}</b> {body}", style)
            _, height = paragraph.wrap(COLUMN, LETTER[1])
            paragraphs.append((paragraph, height, block))
        height = sum(h + 2 for _, h, _ in paragraphs) + 2
        candidates = [i for i in range(current_column, 3) if positions[i] - height >= MARGIN]
        if not paragraphs:
            continue
        if not candidates and len(paragraphs) > 1:
            # Allow a source line to fit even when its full matched caption window does not.
            paragraphs = paragraphs[:1]
            height = paragraphs[0][1] + 4
            candidates = [i for i in range(current_column, 3) if positions[i] - height >= MARGIN]
        if not candidates:
            continue
        col = current_column = candidates[0]
        x = MARGIN + col * (COLUMN + GUTTER)
        for paragraph, h, block in paragraphs:
            positions[col] -= h
            assert positions[col] >= MARGIN
            paragraph.drawOn(canvas, x, positions[col])
            positions[col] -= 2
            placed.extend(block.refs)
        positions[col] -= 2
    canvas.showPage()
    canvas.save()
    writer = PdfWriter()
    writer.append(PdfReader(BytesIO(buf.getvalue())))
    for name, data in files.items():
        writer.add_attachment(name, data)
    with out.open("wb") as stream:
        writer.write(stream)
    verify = PdfReader(out)
    if len(verify.pages) != 1 or any(verify.attachments[name] != [data] for name, data in files.items()):
        raise ValueError("PDF attachment or page-count verification failed")
    return placed


def _packet_lecture(root, record, require_all):
    lecture_id = record["id"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", lecture_id):
        raise ValueError("Use a simple, unique lecture ID")
    folder = root / lecture_id
    folder.mkdir()
    cues = record["cues"]
    caption_status = assess(cues, record.get("runtime_seconds"), record.get("stable_end", False),
                            record.get("expected_count"), record.get("capture_report", {}).get("visible_download_options", []))
    for reason in record.get("capture_report", {}).get("reasons", []):
        if reason not in caption_status["reasons"]:
            caption_status["reasons"].append(reason)
    raw = transcript(cues).encode("utf-8")
    (folder / "transcript.txt").write_bytes(raw)
    json_file(folder / "cues.json", cues)
    original = record.get("original_transcript")
    if original:
        (folder / "original-export.txt").write_bytes(Path(original).read_bytes())
    pdfs = [Path(path) for path in record.get("pdfs", [])]
    notes, sources, attachments = extract_notes(pdfs, folder)
    attachments["transcript.txt"] = raw
    attachments["cues.json"] = (folder / "cues.json").read_bytes()
    if original:
        attachments["original-export.txt"] = (folder / "original-export.txt").read_bytes()
    json_file(folder / "note-text.json", sources)
    attachments["note-text.json"] = (folder / "note-text.json").read_bytes()
    itinerary, _ = fills(notes, cues)
    write_outline(folder / "filled-outline.md", record["title"], itinerary)
    attachments["filled-outline.md"] = (folder / "filled-outline.md").read_bytes()
    out = folder / "filled-notes.pdf"
    problems = list(record.get("mapping_errors", []))
    if not pdfs:
        problems.append("No related note PDF resolved")
    if not cues:
        problems.append("Caption wording unavailable")
    if any(s.get("read_error") for s in sources):
        problems.append("One or more source PDFs could not be read")
    placed = set(render_page(record["title"], caption_status["status"], itinerary, attachments, out)) if not problems else set()
    missing_cues = [i for i in range(1, len(cues) + 1) if f"C{i}" not in placed]
    missing_notes = [block.refs[0] for block in notes if block.refs[0] not in placed]
    untxt = [f"N{i}.{p['page']}" for i, entry in enumerate(sources, 1) for p in entry["pages"] if not p["text"].strip()]
    coverage = {
        "title": record["title"], "id": lecture_id, "canvas_page": record.get("canvas_url"),
        "player_host": record.get("player_host"), "caption_track": record.get("caption_track", "imported"),
        "retrieved_at_utc": record.get("retrieved_at_utc"),
        "caption_source": caption_status, "transcript_sha256": digest(raw),
        "source_pdfs": [{**s, "pages": [{k: v for k, v in p.items() if k != "text"} for p in s["pages"]]}
                         for s in sources],
        "page_coverage": {
            "printed_cues": len(cues) - len(missing_cues), "unprinted_cues": len(missing_cues),
            "unprinted_cue_numbers": missing_cues,
            "printed_note_lines": len(notes) - len(missing_notes),
            "unprinted_note_line_ids": missing_notes, "pages_without_extractable_text": untxt,
            "printed_ids": sorted(placed),
            "visual_details": "Original PDFs remain attached. Extractive print selection does not certify visual or semantic completeness.",
        },
        "problems": problems,
    }
    if require_all and (missing_cues or missing_notes or untxt or caption_status["status"] != "full"):
        problems.append("Strict print coverage failed: incomplete captions, overflow or image-only note pages")
    if problems and out.exists():
        out.unlink()
    coverage["printed"] = out.exists()
    if not coverage["printed"]:
        coverage["page_coverage"].update(
            printed_cues=0, unprinted_cues=len(cues),
            unprinted_cue_numbers=list(range(1, len(cues) + 1)),
            printed_note_lines=0, unprinted_note_line_ids=[block.refs[0] for block in notes],
            printed_ids=[],
        )
    json_file(folder / "coverage.json", coverage)
    return coverage


def build_packet(output, records, *, require_all=False):
    output = Path(output)
    if output.exists():
        raise ValueError("Choose a new output directory")
    if not records:
        raise ValueError("Capture contains no lectures")
    ids = [r["id"].casefold() for r in records]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate lecture IDs")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".canvas-notes-", dir=output.parent) as staging:
        root = Path(staging) / "packet"
        root.mkdir()
        writer = PdfWriter()
        reports = []
        for record in records:
            coverage = _packet_lecture(root, record, require_all)
            reports.append({k: coverage[k] for k in ["id", "title", "printed", "caption_source", "page_coverage", "problems"]})
            if coverage["printed"]:
                writer.append(str(root / record["id"] / "filled-notes.pdf"))
        count = len(writer.pages)
        if count:
            with (root / "lecture-notes.pdf").open("wb") as stream:
                writer.write(stream)
            if len(PdfReader(root / "lecture-notes.pdf").pages) != count:
                raise ValueError("Combined PDF page count differs from verified per-video PDFs")
        result = {"printed_pages": count, "lectures": reports,
                  "evidence": "Each lecture folder retains exact displayed captions, source PDF bytes, and page-level text extracts."}
        json_file(root / "coverage.json", result)
        root.rename(output)
    return result
