from pathlib import Path
import unittest

from playwright.sync_api import sync_playwright

from canvas_notes.capture import capture_player, discover

CANVAS = "https://canvas.example.test/courses/12/pages/week"
PLAYER = "https://demo.yuja.com/V/Video?v=123"
PAGE = """
<main>
<p>Video notes: <a href="/courses/12/files/20?wrap=1">Video notes.pdf</a></p>
<p>Class notes: <a href="/courses/12/files/21?wrap=1">Class notes.pdf</a></p>
<iframe title="Example lecture" src="%s"></iframe>
<hr>
<a href="/courses/12/files/22">Unpaired.pdf</a>
</main>
""" % PLAYER
PLAYER_DOM = """
<button onclick="document.querySelector('video').hidden=false;
document.querySelector('#toggle').hidden=false; this.hidden=true">Play</button>
<video hidden></video>
<button id="toggle" hidden onclick="document.querySelector('aside').hidden=false;
this.hidden=true">Show sidebar</button>
<aside hidden>
<button role="tab" onclick="document.querySelector('#captionList').hidden=false">Captions</button>
<button aria-label="Download transcript">Download</button>
<div id="captionList" hidden style="height:150px; overflow:auto; width:250px"></div>
</aside>
<script>
Object.defineProperty(document.querySelector('video'), 'duration', {get:()=>42});
const captions = document.querySelector('#captionList');
let count = 0;
function append(to) {
  for (; count < to; count++) {
    let timestamp = "00:00:" + String(count === 21 ? 20 : count).padStart(2,'0');
    const row = document.createElement('div');
    row.innerHTML = '<span>' + timestamp + '</span><p></p>';
    row.querySelector('p').textContent = count === 21 ? '  Repeat & α  ' : 'Cue number ' + count;
    captions.appendChild(row);
  }
}
append(20);
captions.addEventListener('scroll', () => {
  if (captions.scrollTop + captions.clientHeight >= captions.scrollHeight - 3)
    append(count === 20 ? 40 : 42);
});
</script>
"""


class BrowserTests(unittest.TestCase):
    def test_two_note_links_and_incremental_caption_rows(self):
        with sync_playwright() as driver:
            if not Path(driver.chromium.executable_path).exists():
                self.skipTest("Install Chromium with playwright install chromium")
            browser = driver.chromium.launch(headless=True)
            try:
                context = browser.new_context()
                context.route("**/*", lambda route: route.fulfill(
                    body={CANVAS: PAGE, PLAYER: PLAYER_DOM}.get(route.request.url, ""), content_type="text/html; charset=utf-8"
                ))
                page = context.new_page()
                page.goto(CANVAS)
                groups, unpaired = discover(page)
                self.assertEqual(len(groups), 1)
                self.assertEqual(len(groups[0]["notes"]), 2)
                self.assertEqual(len(unpaired), 1)
                data = capture_player(groups[0]["frame"], wait_ms=120, deadline_seconds=8)
                self.assertEqual(data["capture_report"]["status"], "full")
                self.assertEqual(data["capture_report"]["cue_count"], 42)
                self.assertEqual(data["cues"][21], {"timestamp": "00:00:20", "text": "  Repeat & α  "})
                self.assertEqual(data["capture_report"]["visible_download_options"], ["Download transcript"])
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
