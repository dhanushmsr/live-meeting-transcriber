"""Fast checks that run in CI without a microphone, a screen or a Gemini key."""

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from inkwake import config, detector, localserver, report  # noqa: E402
from inkwake.session import Session  # noqa: E402


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Point every on-disk location at a temp folder so tests never touch the real ~/.inkwake."""
    monkeypatch.setattr(config, "HOME", tmp_path)
    monkeypatch.setattr(localserver, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(report, "reports_dir", lambda name: tmp_path / "out")
    return tmp_path


def fetch(url, **kw):
    req = urllib.request.Request(url, **kw)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


SAMPLE = {
    "title": "Weekly sync", "started_at": "2026-10-13T10:00:00", "duration_minutes": 3.5, "platform": "Zoom",
    "analysis": {"summary": "We agreed to launch Friday."},
    "transcript": [{"t": 3, "speaker": "Ravi", "text": "We launch Friday.", "translation": ""}],
    "talk": {"Ravi": {"0": 20}},
}


def test_viewer_page_is_packaged():
    assert localserver.VIEWER.exists() and localserver.VIEWER.stat().st_size > 10_000


def test_local_report_roundtrip(home):
    url = localserver.store(SAMPLE)
    assert url.startswith("http://localhost:") and "/r/" in url
    status, page = fetch(url)
    assert status == 200 and b"Meeting brief" in page
    status, body = fetch(url.replace("/r/", "/api/report/"))
    assert status == 200 and json.loads(body)["title"] == "Weekly sync"


def test_expired_report_is_deleted(home):
    url = localserver.store(SAMPLE)
    rid = url.rsplit("/", 1)[1]
    f = localserver.REPORTS / f"{rid}.json"
    old = time.time() - 73 * 3600
    os.utime(f, (old, old))
    status, _ = fetch(url.replace("/r/", "/api/report/"))
    assert status == 404 and not f.exists()


def test_unknown_and_malformed_ids_are_404(home):
    base = localserver.store(SAMPLE).rsplit("/r/", 1)[0]
    for bad in ("deadbeefdeadbeef", "..%2f..%2fconfig", "ZZZ"):
        assert fetch(f"{base}/api/report/{bad}")[0] == 404


def test_other_hosts_and_websites_are_refused(home):
    url = localserver.store(SAMPLE)
    api = url.replace("/r/", "/api/report/")
    assert fetch(api, headers={"Host": "evil.com"})[0] == 403
    localserver.set_asker(lambda rec, q: "ok")
    ask = url.rsplit("/r/", 1)[0] + "/api/ask"
    body = json.dumps({"id": url.rsplit("/", 1)[1], "question": "who?"}).encode()
    hdr = {"Content-Type": "application/json"}
    assert fetch(ask, data=body, headers=hdr)[0] == 200
    assert fetch(ask, data=body, headers={**hdr, "Origin": "https://evil.com"})[0] == 403
    assert fetch(ask, data=b"x" * 30_000, headers=hdr)[0] == 413
    localserver.set_asker(None)


@pytest.mark.parametrize("speaker,text,expected", [
    ("Ravi", "Dhanush, can you take this?", True),
    ("Ravi", "dhanush will handle it", True),
    ("Ravi", "Dhanushka joined", False),
    ("Dhanush M", "I am Dhanush speaking", False),  # your own words never alert you
    ("Ravi", "nothing relevant", False),
])
def test_name_mentions(speaker, text, expected):
    s = config.Settings(name="Dhanush M")
    assert Session(s)._mentions_me(speaker, text) is expected


def test_no_name_means_no_alerts():
    assert Session(config.Settings(name=""))._mentions_me("Ravi", "Dhanush?") is False


def test_meeting_title_from_window():
    assert detector._title_from("Weekly Sync - Google Chrome") == "Weekly Sync"
    assert detector._title_from("Zoom Meeting") == ""


def test_report_file_naming(home):
    kw = dict(settings=config.Settings(name="Dhanush"), platform="", started=datetime(2026, 10, 13, 9, 5), minutes=1.0,
              transcript=[], talk={}, analysis={}, languages=[], meetings_total=1, minutes_total=1.0)
    assert report.build(title="", **kw).name == "2026-10-13 09-05.docx"
    assert report.build(title='Q4: plan/review', **kw).name == "Q4 planreview - 2026-10-13 09-05.docx"


def _all_text(path) -> str:
    """Every paragraph and table cell, in the order a reader meets them."""
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    d = docx.Document(path)
    parts = []
    for child in d.element.body.iterchildren():
        tag = child.tag.split("}")[1]
        if tag == "p":
            parts.append(Paragraph(child, d).text)
        elif tag == "tbl":
            for row in Table(child, d).rows:
                parts += [c.text for c in row.cells]
    return "\n".join(parts)


def test_report_is_a_brief_with_the_answer_first(home):
    path = report.build(
        settings=config.Settings(name="Dhanush"), title="Sync", platform="Zoom", started=datetime(2026, 10, 13, 9, 5),
        minutes=2.0, talk={"Ravi": {0: 30.0}, "Priya": {0: 20.0}}, languages=["English"], meetings_total=1, minutes_total=2.0,
        bookmarks=[{"t": 65, "text": "decision"}],
        transcript=[{"t": 5, "speaker": "Ravi", "text": "Dhanush, deck?", "translation": "", "mention": True}],
        analysis={"tldr": "We launch Friday.", "summary": "Context line.", "decisions": ["Ship Friday"],
                  "action_items": [{"task": "Send the deck", "owner": "Dhanush", "due": "Wed"}], "takeaways": ["Budget is fixed"],
                  "chapters": [{"start": "00:00", "title": "Intro", "summary": "x"}], "open_questions": ["Who pays?"],
                  "follow_up_email": {"subject": "Recap", "body": "Hi\n\nThanks"}})
    text = _all_text(path)
    for needle in ("IN SHORT", "We launch Friday.", "Decisions", "Action items", "Send the deck", "Wed", "Worth remembering",
                   "Still open", "Timeline", "Highlights", "[Bookmarked]" if False else "decision", "mentioned", "Follow-up email",
                   "Who spoke", "Full transcript"):
        assert needle in text, needle
    assert text.index("IN SHORT") < text.index("Decisions") < text.index("Action items") < text.index("Full transcript")


def test_nothing_useless_is_printed(home):
    path = report.build(
        settings=config.Settings(name=""), title="", platform="", started=datetime(2026, 10, 13, 9, 5), minutes=2.0,
        talk={"Others": {0: 60.0}}, languages=[], meetings_total=1, minutes_total=2.0, participants=[], bookmarks=[],
        transcript=[{"t": 5, "speaker": "Others", "text": "Hello.", "translation": "Hello."}],
        analysis={"tldr": "Said hello.", "summary": "Said hello."})
    text = _all_text(path)
    assert "Started by" not in text and "Platform" not in text      # no empty "-" rows
    assert "Who spoke" not in text                                  # a lone "Others 100%" says nothing
    assert "Context" not in text                                    # summary identical to the one-liner is not repeated
    assert "In English" not in text                                 # a translation that repeats the line is dropped


def test_transcript_reads_as_turns_without_repeated_translations():
    lines = [{"t": 1, "speaker": "A", "text": "Hello there.", "translation": "Hello there."},
             {"t": 4, "speaker": "A", "text": "How are you?", "translation": "How are you?"},
             {"t": 40, "speaker": "B", "text": "Saya baik.", "translation": "I am fine."},
             {"t": 44, "speaker": "A", "text": "Great.", "translation": ""}]
    merged = report.merge_transcript(lines)
    assert [m["speaker"] for m in merged] == ["A", "B", "A"]
    assert merged[0]["text"] == "Hello there. How are you?" and merged[0]["translation"] == ""
    assert merged[1]["translation"] == "I am fine."   # a real translation is kept


def test_near_identical_translation_is_hidden_but_a_real_one_is_not():
    assert report.similar_text("Anyway, so if it today is a holiday", "Anyway, so if today is a holiday")
    assert not report.similar_text("Saya pikir kita buang waktu", "I think we are wasting time")


def test_bookmarks_get_the_words_spoken_near_them():
    tr = [{"t": 60, "text": "We must ship Friday.", "translation": ""}, {"t": 400, "text": "later", "translation": ""}]
    out = report.bookmark_context([{"t": 70, "text": ""}, {"t": 200, "text": ""}, {"t": 5, "text": "mine"}], tr)
    assert out[0]["text"] == "We must ship Friday."
    assert out[1]["text"] == ""          # nothing within 30 s: don't invent context
    assert out[2]["text"] == "mine"      # the user's own text is never overwritten


def test_speaker_chart_only_when_it_means_something():
    assert report.real_speakers({"Others": 60.0}) == {}
    assert report.real_speakers({"Dhanush": 30.0}) == {}
    assert report.real_speakers({"Dhanush": 30.0, "Others": 30.0})
    assert report.real_speakers({"Ravi": 30.0, "Priya": 10.0})


def _meeting(share, home, **extra):
    se = Session(config.Settings(name="Dhanush", share_online=share, cloud_report_url="https://reports.example", **extra))
    se.notice = type("N", (), {"emit": staticmethod(lambda *a: None)})()
    return se


def test_nothing_is_uploaded_unless_sharing_is_on(home, monkeypatch):
    se = _meeting(False, home)
    monkeypatch.setattr(se, "_upload_cloud", lambda payload: pytest.fail("uploaded without opt-in"))
    link = se._publish(title="t", started=datetime.now(), minutes=1, platform="", transcript=[], talk={},
                       analysis={}, participants=[], languages=[], bookmarks=[])
    assert link.startswith("http://localhost:")  # served locally


def test_sharing_on_uses_the_cloud_and_falls_back_to_local(home, monkeypatch):
    se = _meeting(True, home)
    monkeypatch.setattr(se, "_upload_cloud", lambda payload: "https://reports.example/r/abc")
    kw = dict(title="t", started=datetime.now(), minutes=1, platform="", transcript=[], talk={},
              analysis={}, participants=[], languages=[], bookmarks=[])
    assert se._publish(**kw) == "https://reports.example/r/abc"
    monkeypatch.setattr(se, "_upload_cloud", lambda payload: None)  # server down
    assert se._publish(**kw).startswith("http://localhost:")


def test_environment_override_is_not_persisted(home, monkeypatch):
    monkeypatch.setenv("INKWAKE_CLOUD_URL", "https://reports.example")
    monkeypatch.setattr(config, "CONFIG_FILE", home / "config.json")
    s = config.Settings.load()
    assert s.env_share and s.share_online is False
    s.save()
    assert json.loads((home / "config.json").read_text())["share_online"] is False


# ---- microphone handling -------------------------------------------------------------------------------------
import numpy as np  # noqa: E402

from inkwake import audio  # noqa: E402


def _tone(level, secs=1.0):
    t = np.arange(int(audio.SR * secs)) / audio.SR
    return (level * np.sqrt(2) * np.sin(2 * np.pi * 220 * t)).astype("float32")  # RMS == level


def test_quiet_laptop_mic_still_counts_as_speech():
    quiet_speech = _tone(0.006)  # a typical laptop mic: below the old fixed 0.012 threshold
    assert not audio.activity(quiet_speech).any()                                   # old behaviour missed it
    assert audio.activity(quiet_speech, audio.speech_threshold(0.0002)).all()       # adaptive threshold hears it


def test_threshold_rises_in_a_noisy_room_but_stays_bounded():
    assert audio.speech_threshold(0.0) == audio.MIN_SPEECH_RMS
    assert audio.speech_threshold(0.005) == pytest.approx(0.015)
    assert audio.speech_threshold(0.5) == audio.MAX_SPEECH_RMS


def test_dead_microphone_triggers_a_warning_once():
    from inkwake.live import DEAD_MIC_SECONDS, LiveStream
    notes = []
    st = LiveStream("You", None, None, config.Settings(), lambda *a: None, lambda *a: None, lambda *a: None, notes.append)
    for _ in range(int(DEAD_MIC_SECONDS / 0.1) + 5):
        st._take(np.zeros(int(audio.SR * 0.1), dtype="float32"))
    assert len(notes) == 1 and "can't hear your microphone" in notes[0]


def test_working_microphone_never_warns():
    from inkwake.live import LiveStream
    notes = []
    st = LiveStream("You", None, None, config.Settings(), lambda *a: None, lambda *a: None, lambda *a: None, notes.append)
    for _ in range(150):
        st._take(_tone(0.02, 0.1))
    assert notes == []


def test_the_other_side_of_the_call_is_not_flagged_as_a_dead_mic():
    from inkwake.live import LiveStream
    notes = []
    st = LiveStream("Others", None, None, config.Settings(), lambda *a: None, lambda *a: None, lambda *a: None, notes.append)
    for _ in range(150):
        st._take(np.zeros(int(audio.SR * 0.1), dtype="float32"))  # nobody talking is normal
    assert notes == []


def test_find_microphone_by_name():
    devices = [{"name": "Speakers", "max_input_channels": 0}, {"name": "Microphone Array (Intel)", "max_input_channels": 4},
               {"name": "USB Headset Mic", "max_input_channels": 1}]
    assert audio.find_microphone("headset", devices) == 2
    assert audio.find_microphone("", devices) is None
    assert audio.find_microphone("does not exist", devices) is None


def test_two_inks_never_share_a_port():
    """A second server must refuse a port that is taken, so links never reach the wrong Ink."""
    a = localserver._Server(("127.0.0.1", 0), localserver._Handler)
    try:
        port = a.server_address[1]
        with pytest.raises(OSError):
            localserver._Server(("127.0.0.1", port), localserver._Handler).server_close()
    finally:
        a.server_close()


# ---- who is speaking --------------------------------------------------------------------------------------
from inkwake.speakers import SpeakerWatcher  # noqa: E402


def _watcher(participants, me="Dhanush M"):
    w = SpeakerWatcher(None, config.Settings(name=me), lambda: True, lambda m: None)
    w.participants = set(participants)
    return w


def test_one_other_person_on_screen_gets_the_unattributed_words():
    assert _watcher(["Paul D", "Dhanush M"]).name_at(time.monotonic()) == "Paul D"
    assert _watcher(["Paul D", "You"]).name_at(time.monotonic()) == "Paul D"


def test_two_or_more_others_stay_unknown_until_the_highlight_is_read():
    assert _watcher(["Paul D", "Kannan Rangasamy", "Dhanush M"]).name_at(time.monotonic()) is None


def test_a_read_highlight_wins_over_the_fallback():
    w = _watcher(["Paul D", "Kannan Rangasamy", "Dhanush M"])
    w._append(time.monotonic() - 1, "Kannan Rangasamy")
    assert w.name_at(time.monotonic()) == "Kannan Rangasamy"


def test_ink_windows_are_excluded_from_screenshots_on_windows(monkeypatch):
    from inkwake.ui import capture
    calls = []
    monkeypatch.setattr(capture.sys, "platform", "linux")
    assert capture.hide_from_screen_capture(object()) is False  # harmless elsewhere
