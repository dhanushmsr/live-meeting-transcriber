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
    assert status == 200 and b"Online Doc" in page
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


def test_report_contains_new_sections(home):
    import docx
    path = report.build(
        settings=config.Settings(name="Dhanush"), title="Sync", platform="Zoom", started=datetime(2026, 10, 13, 9, 5),
        minutes=2.0, talk={"Ravi": {0: 30.0}}, languages=["English"], meetings_total=1, minutes_total=2.0,
        bookmarks=[{"t": 65, "text": "decision"}],
        transcript=[{"t": 5, "speaker": "Ravi", "text": "Dhanush, deck?", "translation": "", "mention": True}],
        analysis={"summary": "s", "chapters": [{"start": "00:00", "title": "Intro", "summary": "x"}],
                  "open_questions": ["Who pays?"], "follow_up_email": {"subject": "Recap", "body": "Hi\n\nThanks"}})
    text = "\n".join(p.text for p in docx.Document(path).paragraphs)
    for needle in ("Timeline", "Highlights", "Open questions", "Draft follow-up email", "[01:05] Bookmarked", "mentioned"):
        assert needle in text


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
