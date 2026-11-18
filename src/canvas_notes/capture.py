"""Canvas pairing and browser-side YuJa caption acquisition."""
import json
import re
from pathlib import Path
from datetime import datetime, timezone
from time import monotonic
from urllib.parse import urlparse, urljoin

from .captions import STAMP, assess, merge_snapshot

COURSE_PAGE = re.compile(r"^/courses/(\d+)(?:/pages(?:/[^/]+)?)?/?$")
YUJA = re.compile(r"(^|\.)yuja\.com$", re.I)

CANVAS_EVENTS = """() => {
    const main = document.querySelector('#wiki_page_show .user_content, #wiki_page_show, .ic-Layout-contentMain .user_content, main .user_content, main');
    if (!main) return [];
    const frames = [...document.querySelectorAll('iframe')];
    return [...main.querySelectorAll('a[href], iframe, hr')].flatMap(el => {
        if (!el.getClientRects().length) return [];
        if (el.tagName === 'HR') return [{kind:'boundary'}];
        if (el.tagName === 'IFRAME') return [{
            kind:'iframe', frame: frames.indexOf(el), src: el.src,
            title: el.getAttribute('title') || ''
        }];
        const names = [el.getAttribute('download'), el.getAttribute('title'),
                       el.getAttribute('aria-label'), el.textContent].map(x => x?.trim())
                       .filter(x => x && /\\.pdf$/i.test(x));
        if (!names.length) return [];
        const nearby = el.parentElement;
        if (nearby) for (const node of nearby.querySelectorAll('[aria-label],[title]')) {
            for (const key of ['title','aria-label']) {
                const name = node.getAttribute(key)?.match(/^(?:Alternative Formats for|Download) (.*\\.pdf)$/i);
                if (name) names.push(name[1]);
            }
        }
        return [{kind:'pdf', url:el.href, names:[...new Set(names)]}];
    });
}"""

# Match the rendered timestamp element first. Walk upward only until the row contains wording.
# Repeated timestamps are separate DOM rows. Use innerText of the wording element when available.
CUE_ROWS = """(pattern) => {
    const stamp = new RegExp('^' + pattern + '$');
    const nodes = [...document.querySelectorAll('body *')].filter(el =>
        !el.children.length && el.getClientRects().length && stamp.test(el.textContent.trim()));
    const lists = new Map();
    const used = new Set();
    for (const time of nodes) {
        let row = time.parentElement;
        for (let depth = 0; row && depth < 5; row = row.parentElement, depth++) {
            if (row.closest('video,button[aria-label*="seek" i]')) break;
            const times = nodes.filter(other => row.contains(other));
            if (times.length !== 1) break;
            const walk = document.createTreeWalker(row, NodeFilter.SHOW_TEXT);
            const words = [];
            let n;
            while (n = walk.nextNode()) {
                if (time.contains(n) || !n.parentElement.getClientRects().length ||
                    n.parentElement.closest('button:not(:scope), svg, script, style, [aria-hidden="true"]')) continue;
                if (n.nodeValue.trim()) words.push(n);
            }
            // A caption row may itself be a button. Fall back to its non-timestamp children.
            const candidates = [...row.children].filter(e => !e.contains(time) &&
                e.getClientRects().length && !e.matches('button,svg,[aria-hidden="true"]') &&
                e.innerText?.trim() && !stamp.test(e.innerText.trim()));
            function exactText(node) {
                if (node.nodeType === Node.TEXT_NODE) return node.nodeValue;
                if (node.nodeName === 'BR') return '\\n';
                return [...node.childNodes].map(exactText).join('');
            }
            let wording = candidates.length === 1 ? exactText(candidates[0]) : null;
            if (wording === null && words.length === 1 && !row.matches('button')) wording = words[0].nodeValue;
            if (wording === null || !wording.trim()) continue;
            let scroll = row.parentElement;
            while (scroll && scroll !== document.body &&
                   !/(auto|scroll)/.test(getComputedStyle(scroll).overflowY)) scroll = scroll.parentElement;
            if (!scroll || scroll === document.body) break;
            if (used.has(row)) break;
            used.add(row);
            if (!lists.has(scroll)) lists.set(scroll, []);
            lists.get(scroll).push({timestamp:time.textContent.trim(),text:wording});
            break;
        }
    }
    const valid = [...lists].filter(([,items]) => items.length);
    const marked = valid.filter(([el]) => /caption|transcript/i.test(
        [el.id,el.className,el.getAttribute('aria-label'),el.parentElement?.id,
         el.parentElement?.className,el.closest('[role=tabpanel]')?.getAttribute('aria-label')].join(' ')));
    const found = marked.length === 1 ? marked : valid;
    if (found.length !== 1) return {rows:[], ambiguous: found.length > 1};
    const [list,rows] = found[0];
    list.dataset.canvasNotesScroll = 'true';
    return {rows, atBottom:list.scrollTop + list.clientHeight >= list.scrollHeight - 3};
}"""


def is_yuja(url):
    parsed = urlparse(url)
    return parsed.scheme == "https" and bool(YUJA.search(parsed.hostname or ""))


def clean_url(url):
    p = urlparse(url)
    return p._replace(query="", fragment="", netloc=p.hostname or "").geturl()


def course_page(url, host):
    p = urlparse(url)
    return p.scheme == "https" and p.hostname == host and COURSE_PAGE.fullmatch(p.path)


def group_events(events, canvas_url):
    """Pair consecutive Canvas file links with the next video in the same section."""
    canvas = urlparse(canvas_url)
    prefix = "/".join(canvas.path.split("/")[:3]) + "/files/"
    pending, unmatched, groups = {}, [], []
    for event in events:
        kind = event["kind"]
        if kind == "pdf":
            parsed = urlparse(urljoin(canvas_url, event["url"]))
            if parsed.scheme == "https" and parsed.hostname == canvas.hostname and parsed.path.startswith(prefix):
                names = pending.setdefault(clean_url(parsed.geturl()), [])
                names.extend(n for n in event["names"] if n not in names)
        elif kind == "player":
            groups.append({**event, "notes": [{"url": url, "names": names} for url, names in pending.items()]})
            pending.clear()
        elif kind in {"boundary", "other_media"}:
            unmatched.extend({"url": url, "names": names} for url, names in pending.items())
            pending.clear()
    unmatched.extend({"url": url, "names": names} for url, names in pending.items())
    return groups, unmatched


def resolve_note(note, files):
    def key(name):
        stem = re.sub(r"\s*\(\d+\)$", "", Path(name).stem.casefold())
        return " ".join(re.findall(r"\w+", stem))

    names = note["names"]
    exact = [p for p in files if p.name.casefold() in {n.casefold() for n in names}]
    candidates = exact or [p for p in files if key(p.name) in {key(n) for n in names}]
    if len(candidates) != 1:
        raise ValueError(f"{'Ambiguous' if candidates else 'Missing'} note PDF: {names[0]}. Supply an explicit map.")
    return candidates[0]


def _visible(root, role, name):
    return next((x for x in root.get_by_role(role, name=re.compile(name, re.I)).all()
                 if x.is_visible()), None)


def capture_player(root, *, wait_ms=800, deadline_seconds=120, max_probes=350):
    from playwright.sync_api import Error as BrowserError, TimeoutError as BrowserTimeout

    root.locator("body").wait_for(timeout=10_000)
    duration = root.evaluate("""() => {
        const v = document.querySelector('video');
        return v && Number.isFinite(v.duration) ? v.duration : null;
    }""")
    sidebar = _visible(root, "button", r"show sidebar|open sidebar|show transcript")
    if not sidebar:
        play = _visible(root, "button", r"^play(?: video)?$")
        if play:
            play.click(timeout=4000)
            try:
                root.locator("video").first.wait_for(state="attached", timeout=5000)
            except BrowserTimeout:
                pass
    # Pause playback without changing position or guessing seek times.
    root.evaluate("() => document.querySelectorAll('video, audio').forEach(v => v.pause())")
    if duration is None:
        try:
            root.wait_for_function("() => [...document.querySelectorAll('video')].some(v => Number.isFinite(v.duration))",
                                   timeout=4000)
        except BrowserTimeout:
            pass
        duration = root.evaluate("""() => [...document.querySelectorAll('video')]
            .find(v => Number.isFinite(v.duration))?.duration ?? null""")
    sidebar = _visible(root, "button", r"show sidebar|open sidebar|show transcript")
    if sidebar:
        sidebar.click(timeout=4000)
    # Prefer the panel tab. The player's CC toggle opens a separate menu.
    tab = _visible(root, "tab", r"caption|transcript")
    if not tab:
        panel = root.locator("[role=tabpanel], aside, [id*=sidebar i], [class*=sidebar i]")
        tab = _visible(panel, "button", r"^captions$|^transcript$|^cc$")
    if tab:
        tab.click(timeout=4000)
    options = []
    for role in ["button", "link"]:
        for el in root.get_by_role(role, name=re.compile(r"download.*(?:transcript|caption)|(?:transcript|caption).*download", re.I)).all():
            if el.is_visible():
                options.append(el.get_attribute("aria-label") or el.get_attribute("title") or el.inner_text())
    rows, stable, quiet, issue = [], False, 0, None
    start = monotonic()
    for probe in range(max_probes):
        if monotonic() - start > deadline_seconds:
            break
        try:
            current = root.evaluate(CUE_ROWS, STAMP)
            if current.get("ambiguous"):
                issue = "Multiple potential caption lists"
                break
            fresh = current["rows"]
            if not fresh:
                if rows or probe >= 8:
                    issue = "Caption rows no longer visible" if rows else "No readable caption rows in sidebar"
                    break
            else:
                grew = len(fresh) > len(rows)
                rows = merge_snapshot(rows, fresh)
                quiet = quiet + 1 if current.get("atBottom") and not grew else 0
                if quiet >= 3:
                    stable = True
                    break
                root.evaluate("""(nudge) => {
                    const list = document.querySelector('[data-canvas-notes-scroll=true]');
                    if (!list) return;
                    if (nudge) list.scrollTop = Math.max(0, list.scrollTop - 35);
                }""", quiet > 0)
                if quiet:
                    root.page.wait_for_timeout(150)
                root.evaluate("""() => {
                    const list = document.querySelector('[data-canvas-notes-scroll=true]');
                    if (list) list.scrollTop = list.scrollHeight;
                }""")
            root.page.wait_for_timeout(wait_ms)
        except (ValueError, BrowserError) as error:
            issue = str(error) if isinstance(error, ValueError) else "Player frame changed while reading caption list"
            break
    report = assess(rows, duration, stable, download_options=options)
    if issue:
        report["reasons"].append(issue)
    root.evaluate("() => document.querySelectorAll('video, audio').forEach(v => v.pause())")
    return {"cues": rows, "runtime_seconds": duration, "stable_end": stable, "capture_report": report}


def _player_for(frame):
    if is_yuja(frame.url):
        return frame
    return next((p for child in frame.child_frames if (p := _player_for(child))), None)


def discover(page):
    """Discover loaded YuJa frames by Canvas iframe document position."""
    raw = page.evaluate(CANVAS_EVENTS)
    handles = page.locator("iframe")
    events = []
    for event in raw:
        if event["kind"] != "iframe":
            events.append(event)
            continue
        handle = handles.nth(event["frame"]).element_handle(timeout=3000)
        frame = handle.content_frame() if handle else None
        player = _player_for(frame) if frame else None
        if player:
            events.append({"kind": "player", "frame": player, "title": event["title"] or player.title()})
        elif "/external_tools/retrieve" in event["src"]:
            events.append({"kind": "player", "frame": None, "title": event["title"] or "Unresolved Canvas player"})
        else:
            events.append({"kind": "other_media"})
    return group_events(events, page.url)


def capture_tabs(*, host, urls, profile, title_filter=None, choose=False):
    from playwright.sync_api import sync_playwright, Error as BrowserError

    with sync_playwright() as driver:
        context = driver.chromium.launch_persistent_context(str(profile), headless=False)
        try:
            for url in urls:
                if not course_page(url, host):
                    raise ValueError("Use HTTPS Canvas course page URLs on the specified host")
                context.new_page().goto(url, wait_until="domcontentloaded")
            input("Sign in normally in the browser window. Open the Canvas notes tabs and wait for the players, then press Enter here. ")
            groups, orphans = [], []
            seen_pages = set()
            for page in context.pages:
                if not course_page(page.url, host) or clean_url(page.url) in seen_pages:
                    continue
                seen_pages.add(clean_url(page.url))
                links, unmatched = discover(page)
                groups.extend({**item, "canvas_url": clean_url(page.url)} for item in links)
                orphans.extend(unmatched)
            if not groups:
                raise ValueError("No matching video sections discovered. Verify Canvas tabs and embedded players are loaded.")
            if title_filter:
                groups = [g for g in groups if re.search(title_filter, g["title"], re.I)]
                if not groups:
                    raise ValueError("Title filter matched no discovered videos")
            if choose:
                for i, item in enumerate(groups, start=1):
                    notes = ", ".join(n["names"][0] for n in item["notes"])
                    print(f"{i}. {item['title']} — {notes or 'no related PDF detected'}")
                answer = input("Enter comma-separated row numbers, or Enter to capture all: ").strip()
                if answer:
                    choices = sorted(set(int(i) for i in answer.split(",")))
                    if any(i < 1 or i > len(groups) for i in choices):
                        raise ValueError("Selection out of range")
                    groups = [groups[i - 1] for i in choices]
            manifest = []
            for i, item in enumerate(groups, start=1):
                player = item.pop("frame")
                item.pop("kind")
                fallback = player.url if player else None
                report = {"cues": [], "runtime_seconds": None, "stable_end": False}
                if player:
                    try:
                        report = capture_player(player)
                    except BrowserError:
                        pass
                    if not report["cues"] and fallback and is_yuja(fallback):
                        direct = context.new_page()
                        try:
                            direct.goto(fallback, wait_until="domcontentloaded")
                            if is_yuja(direct.url):
                                direct_report = capture_player(direct.main_frame)
                                if direct_report["cues"] or not report.get("capture_report"):
                                    report = direct_report
                                elif direct_report.get("capture_report"):
                                    details = report["capture_report"]
                                    details["visible_download_options"] = list(dict.fromkeys(
                                        details["visible_download_options"] +
                                        direct_report["capture_report"]["visible_download_options"]))
                        except BrowserError:
                            pass
                        finally:
                            direct.close()
                report.setdefault("capture_report", assess(report["cues"], report["runtime_seconds"], report["stable_end"]))
                if player is None:
                    report["capture_report"]["reasons"].append("Canvas external-tool frame did not resolve to a visible YuJa player")
                manifest.append({"id": f"lecture-{i:03}", **item, **report,
                                 "player_host": urlparse(fallback).hostname if fallback else None,
                                 "caption_track": "displayed sidebar track"})
                print(f"{i}. {item['title']} — {report['capture_report']['cue_count']} cues ({report['capture_report']['status']})")
        finally:
            context.close()
    return {"schema": 1, "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            "lectures": manifest, "unpaired_note_links": orphans}


def save_manifest(path, manifest):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
