import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader
from reportlab.pdfgen.canvas import Canvas

from canvas_notes.captions import assess, transcript, merge_snapshot
from canvas_notes.packet import build_packet
from canvas_notes.capture import group_events, resolve_note


class CaptionTests(unittest.TestCase):
    def test_verbatim_repeated_unicode_and_precision(self):
        cues = [{"timestamp": "01:02:03.004", "text": "  α\nATP & H₂O  "}] * 2
        self.assertEqual(transcript(cues), "[01:02:03.004]   α\nATP & H₂O  \n" * 2)
        self.assertEqual(assess(cues, 3726, True)["cue_count"], 2)

    def test_partial_and_unavailable(self):
        rows = [{"timestamp": "00:00:00", "text": "Start"},
                {"timestamp": "00:09:32", "text": "Bye"}]
        result = assess(rows, 681, True)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["uncaptioned_tail_seconds"], 109)
        self.assertEqual(len(result["cue_start_gaps"]), 1)
        self.assertEqual(assess([], None, False)["status"], "unavailable")
        self.assertEqual(assess(rows, 574, False)["status"], "partial")

    def test_discontinuous_snapshot_is_rejected(self):
        a, b = {"timestamp": "00:00", "text": "a"}, {"timestamp": "00:01", "text": "b"}
        self.assertEqual(merge_snapshot([a], [a, b]), [a, b])
        with self.assertRaises(ValueError):
            merge_snapshot([a, b], [b])


class PairingTests(unittest.TestCase):
    def test_only_course_pdfs_before_each_video(self):
        stream = [
            {"kind": "pdf", "url": "https://canvas.invalid/courses/1/files/123?wrap=1", "names": ["A.pdf"]},
            {"kind": "player", "frame": 1, "title": "A"},
            {"kind": "pdf", "url": "https://canvas.invalid/courses/1/files/456", "names": ["B.pdf"]},
            {"kind": "player", "frame": 2, "title": "B"},
        ]
        grouped, unmatched = group_events(stream, "https://canvas.invalid/courses/1/pages/week")
        self.assertEqual([v["notes"][0]["names"][0] for v in grouped], ["A.pdf", "B.pdf"])
        self.assertFalse(unmatched)

    def test_collision_requires_override(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name in ["A (1).pdf", "A (2).pdf"]:
                (root / name).touch()
            with self.assertRaises(ValueError):
                resolve_note({"names": ["A.pdf"]}, list(root.iterdir()))


class PacketTests(unittest.TestCase):
    def test_entire_sources_survive_one_page_overflow(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            notes = root / "Worksheet.pdf"
            can = Canvas(str(notes))
            can.drawString(50, 700, "Cell energy:")
            can.showPage()
            can.drawString(50, 700, "Second original page")
            can.save()
            cues = [{"timestamp": f"00:{i//60:02}:{i%60:02}", "text": "Cell energy and ATP. " * 12}
                    for i in range(180)]
            result = build_packet(root / "run", [{"title": "Demo", "id": "demo", "cues": cues,
                                   "runtime_seconds": 180, "stable_end": True,
                                   "pdfs": [str(notes)]}])
            bundle = root / "run" / "demo"
            pdf = PdfReader(bundle / "filled-notes.pdf")
            self.assertEqual(len(pdf.pages), 1)
            self.assertEqual(pdf.attachments["note-01.pdf"][0], notes.read_bytes())
            self.assertEqual(pdf.attachments["transcript.txt"][0], transcript(cues).encode())
            self.assertEqual((bundle / "transcript.txt").read_bytes(), transcript(cues).encode())
            report = json.loads((bundle / "coverage.json").read_text())
            self.assertGreater(report["page_coverage"]["unprinted_cues"], 0)
            self.assertEqual(report["source_pdfs"][0]["sha256"], hashlib.sha256(notes.read_bytes()).hexdigest())
            self.assertEqual(len(PdfReader(root / "run" / "lecture-notes.pdf").pages), 1)
            self.assertEqual(result["printed_pages"], 1)
            strict = build_packet(root / "strict", [{"title": "Demo", "id": "demo", "cues": cues,
                                    "runtime_seconds": 180, "stable_end": True,
                                    "pdfs": [str(notes)]}], require_all=True)
            self.assertEqual(strict["printed_pages"], 0)
            self.assertTrue((root / "strict" / "demo" / "transcript.txt").exists())


if __name__ == "__main__":
    unittest.main()
