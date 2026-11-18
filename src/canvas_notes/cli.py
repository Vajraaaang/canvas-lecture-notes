"""Browser capture and offline replay commands."""
import argparse
import json
import sys
from pathlib import Path

from .captions import import_txt, transcript
from .capture import capture_tabs, resolve_note, save_manifest
from .packet import build_packet


def read_manifest(path):
    values = json.loads(path.read_text(encoding="utf-8"))
    if values.get("schema") != 1 or not isinstance(values.get("lectures"), list):
        raise ValueError("Use a schema-1 capture or replay manifest")
    for entry in values["lectures"]:
        if "transcript" in entry:
            source = (path.parent / entry["transcript"]).resolve()
            entry["original_transcript"] = str(source)
            entry["cues"] = import_txt(source.read_bytes().decode("utf-8"))
        if "cues" not in entry:
            raise ValueError("Each lecture needs cues or a transcript file")
        entry["pdfs"] = [str((path.parent / p).resolve()) for p in entry.get("pdfs", [])]
        if "original_transcript" in entry:
            entry["original_transcript"] = str((path.parent / entry["original_transcript"]).resolve())
        entry.setdefault("retrieved_at_utc", values.get("retrieved_at_utc"))
    return values


def bind_pdfs(manifest, notes_dir, mapping):
    files = sorted(notes_dir.glob("*.pdf")) if notes_dir else []
    overrides = json.loads(mapping.read_text(encoding="utf-8")) if mapping else {}
    known = {entry["id"] for entry in manifest["lectures"]}
    if set(overrides) - known:
        raise ValueError("PDF map contains unknown lecture IDs")
    for entry in manifest["lectures"]:
        if entry["id"] in overrides:
            paths = overrides[entry["id"]]
            if not isinstance(paths, list) or not paths or any(not isinstance(p, str) for p in paths):
                raise ValueError("Each PDF map entry must contain an array of PDF paths")
            entry["pdfs"] = [str((mapping.parent / p).resolve()) for p in paths]
        elif not entry.get("pdfs"):
            entry["pdfs"], entry["mapping_errors"] = [], []
            for note in entry.get("notes", []):
                try:
                    entry["pdfs"].append(str(resolve_note(note, files).resolve()))
                except ValueError as error:
                    entry["mapping_errors"].append(str(error))
        valid = []
        for pdf in dict.fromkeys(entry["pdfs"]):
            path = Path(pdf)
            if path.is_file() and path.suffix.lower() == ".pdf":
                valid.append(str(path))
            else:
                entry.setdefault("mapping_errors", []).append(f"Missing or invalid PDF: {path.name}")
        entry["pdfs"] = valid


def capture(args):
    data = capture_tabs(host=args.canvas_host.lower(), urls=args.canvas_url, profile=args.profile.resolve(),
                        title_filter=args.title, choose=args.choose)
    for lecture in data["lectures"]:
        lecture["retrieved_at_utc"] = data["retrieved_at_utc"]
    save_manifest(args.capture, data)
    if data["unpaired_note_links"]:
        print(f"{len(data['unpaired_note_links'])} unpaired PDF links recorded in the capture.", file=sys.stderr)
    caption_dir = args.capture.with_suffix(".captions")
    caption_dir.mkdir(exist_ok=False)
    for lecture in data["lectures"]:
        (caption_dir / f"{lecture['id']}.txt").write_bytes(transcript(lecture["cues"]).encode("utf-8"))
    return data


def print_packet(manifest, args):
    bind_pdfs(manifest, args.notes_dir, args.pdf_map)
    report = build_packet(args.output, manifest["lectures"], require_all=args.require_all_on_page)
    print(f"Wrote {report['printed_pages']} one-page lecture sheets in {args.output.resolve()}")
    for result in report["lectures"]:
        if result["problems"]:
            print(f"{result['id']}: " + "; ".join(result["problems"]), file=sys.stderr)
        elif result["caption_source"]["status"] != "full":
            print(f"{result['id']}: caption coverage is {result['caption_source']['status']}", file=sys.stderr)
    return 2 if any(r["problems"] or r["caption_source"]["status"] != "full" for r in report["lectures"]) else 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="canvas-notes")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("capture", "run"):
        command = commands.add_parser(name, help="Read captions from Canvas tabs in a dedicated Chromium window")
        command.add_argument("--canvas-host", required=True, help="Canvas hostname without https://")
        command.add_argument("--canvas-url", action="append", default=[], help="Canvas notes page URL, repeatable")
        command.add_argument("--profile", type=Path, default=Path(".browser-profile"), help="Dedicated local Chromium profile")
        command.add_argument("--title", help="Regex for embedded video titles")
        command.add_argument("--choose", action="store_true", help="Review proposed video/PDF pairs before capture")
        command.add_argument("--capture", type=Path, required=True, help="New output manifest JSON")
    build = commands.add_parser("build", help="Make printable pages from a saved capture or prior transcript export")
    build.add_argument("--capture", type=Path, required=True, help="Capture or replay manifest JSON")
    for command in (commands.choices["run"], build):
        command.add_argument("--notes-dir", type=Path, help="Folder of local PDFs named as in Canvas")
        command.add_argument("--pdf-map", type=Path, help="JSON map of lecture ID to array of local PDF paths")
        command.add_argument("--output", type=Path, required=True, help="New folder for the packet")
        command.add_argument("--require-all-on-page", action="store_true", help="Omit printable sheets when extracted units overflow or capture is partial")
    args = parser.parse_args(argv)
    try:
        if args.command != "build":
            if args.capture.exists() or args.capture.with_suffix(".captions").exists():
                raise ValueError("Choose a new capture filename")
            if args.command == "run" and args.output.exists():
                raise ValueError("Choose a new output directory")
            manifest = capture(args)
            if args.command == "capture":
                return 2 if any(x["capture_report"]["status"] != "full" for x in manifest["lectures"]) else 0
        else:
            manifest = read_manifest(args.capture)
        return print_packet(manifest, args)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"canvas-notes: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
