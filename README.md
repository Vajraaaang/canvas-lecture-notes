# Canvas Lecture Notes

Turn a Canvas lecture section with YuJa video and related PDF worksheets into **one printable Letter page**, plus the full timestamped captions, filled outline, and original PDFs. Built for the Canvas layout where **Video notes** and **Class notes** are followed by the embedded player.

## Install and run

Use Python 3.11+ with [uv](https://docs.astral.sh/uv/getting-started/installation/) or install the project with `pip install -e .` in a virtual environment.

```sh
uv sync
uv run playwright install chromium
uv run canvas-notes run --canvas-host canvas.example.edu \
  --canvas-url https://canvas.example.edu/courses/123/pages/week-4-videos-and-notes \
  --choose --notes-dir "$HOME/Downloads" \
  --capture "$PWD/runs/week-4-capture.json" --output "$PWD/runs/week-4"
```

A dedicated Chromium window opens using a local project profile. Sign in normally, open the Canvas tabs you want to process, then press Enter in the terminal. Pass `--canvas-url` multiple times to open several pages, or omit it and open tabs in that window. Tabs in Chrome's default profile and Codex's in-app browser are separate sessions. `--title` accepts a video-title regex. `--choose` previews the discovered pairs and lets you select rows.

PDFs come from the local folder you supply. The command pairs Canvas course-file links with the following embedded player in document order. A divider or another embedded media section ends the current group. Exact filenames are preferred and download-label aliases are considered. Missing or ambiguous matches stop that lecture's printable page and appear in its coverage report. If the page uses a different grouping order or the files have different names, pass `--pdf-map` with a local mapping such as:

```json
{
  "lecture-001": ["/absolute/path/to/video-notes.pdf", "/absolute/path/to/class-notes.pdf"]
}
```

## Capture and replay independently

Use `canvas-notes capture` with the same browser arguments to save the capture manifest and one UTF-8 timestamped text file per video. The player opens the sidebar and its caption panel, reads displayed cue text and timestamps, and scrolls until the append-only list stabilizes at the bottom. It nudges the scroll position and checks again. If an embed yields no captions, the collector can retry in a direct tab using the actual YuJa player URL observed in that frame. Visible transcript-download options are reported. The player may require a login or a different layout.

```sh
uv run canvas-notes build --capture "$PWD/runs/week-4-capture.json" \
  --notes-dir "$HOME/Downloads" --output "$PWD/runs/week-4-replay"
```

You can also replay timestamped exports from a previous chat. Save a local schema-1 manifest with a list of lectures:

```json
{
  "schema": 1,
  "lectures": [
    {
      "id": "example-01",
      "title": "Example lecture",
      "runtime_seconds": 1372,
      "stable_end": true,
      "expected_count": 448,
      "transcript": "/absolute/path/to/example-transcript.txt",
      "pdfs": ["/absolute/path/to/example-worksheet.pdf"]
    }
  ]
}
```

Provide runtime and `stable_end` only when supported by your capture evidence. Use `false` if the end of the caption list was uncertain. You can supply a `cues` array with `timestamp` and `text` strings instead of `transcript`. This is the preferred interchange for exact row boundaries, including if the wording itself contains a line that looks like a timestamp marker. Original TXT exports are retained byte for byte as separate evidence. Paths in manifest/map files may be absolute or relative to those files.

## What you get

- `lecture-notes.pdf` merges the successfully rendered one-page sheets in selected order.
- Each lecture folder has a `filled-notes.pdf` with three narrow columns and bold lead-ins. A local handwritten heading font is used when available and the page has no running header or footer. Source rows with two or three pipe-delimited cells render as small comparison rows. The worksheet's source lines are paired with exact caption windows using lexical matching. The layout favors whole blocks and includes caption excerpts across the timeline. There is no model call.
- `filled-outline.md` retains every extracted worksheet line and every caption window, including those outside the printable page. Consult `transcript.txt` or `cues.json` for the exact timestamp and wording of each individual cue.
- Original PDFs, transcript, cue JSON and page-level text extracts are present in each folder and attached to each single-page PDF. Use the full PDF sources for illustrations, handwritten markings and layout. The combined PDF is for printing and gets its evidence from the companion lecture folders.
- `coverage.json` lists cue count, runtime, first and last cues, long intervals between cue starts, and short beginning/middle/end excerpts. It also lists each unprinted caption cue or worksheet line and identifies pages without extractable text. An unavailable player or PDF pairing is reported explicitly.

A lecture can have more content than fits legibly on one physical page. The printable PDF is an extractive study sheet, with full evidence in its attachments and companion files. To require that every captured cue and extracted worksheet line fit on the physical page, add `--require-all-on-page`. Strict mode withholds the printable sheet for overflow, uncertain caption coverage or pages with unavailable text. It still saves the sources. Even strict mode covers extracted text only. Original PDF images, layout and handwritten details remain in the PDF attachments.

`full` means the visible list stabilized and passed the runtime boundary/gap checks. It cannot establish speech during uncaptioned intervals, detect inaccuracies in the supplied captions, or recover visual-only video information. Long cue-start intervals are flagged conservatively. Exit status 2 means partial/unavailable captions or a sheet was withheld. Exit status 1 denotes a setup or processing error.

The browser profile, transcripts and notes stay on your machine. Keep captures and output under the ignored `runs/` directory. The repository has synthetic examples only.

## Verification

```sh
uv run python -m unittest discover -s tests -v
```

Browser integration tests use synthetic Canvas and YuJa URLs fulfilled entirely in-process. Install Chromium to exercise them. The checked replay command also works without a browser login.

For the actual player controls, see [YuJa's sidebar guide](https://support.yuja.com/hc/en-us/articles/360051140573-Using-the-Sidebar) and [transcript download guide](https://support.yuja.com/hc/en-us/articles/360048657534-Downloading-Notes-Resources-and-Transcripts-in-the-Lumina-Media-Player). See [Playwright's persistent browser context](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context) for browser-profile behavior.
