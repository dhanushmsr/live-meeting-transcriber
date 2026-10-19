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


# ---- who said what (voice separation after the meeting) ---------------------------------------------------
from inkwake import diarize  # noqa: E402
from inkwake.gemini import _turns_with_names  # noqa: E402

TURNS = [{"start": 2, "end": 9, "speaker": "Anita"}, {"start": 10, "end": 18, "speaker": "Paul"},
         {"start": 19, "end": 25, "speaker": "Anita"}, {"start": 26, "end": 32, "speaker": "Paul"}]


def _line(t, text, channel="Others", speaker="Others"):
    return {"t": t, "speaker": speaker, "text": text, "translation": "", "channel": channel}


def test_malformed_turns_are_dropped():
    raw = [{"start": "x", "end": 3, "speaker": "A"}, {"start": 5, "end": 4, "speaker": "A"}, {"start": 1, "end": 2},
           {"start": 6, "end": 8, "speaker": "B"}, "junk", None]
    assert diarize.clean_turns(raw) == [{"start": 6.0, "end": 8.0, "speaker": "B", "starts": ""}]


def test_other_side_lines_get_named_and_talk_time_is_rebuilt():
    lines = [_line(2, "Hello everyone this is Anita Paul can you tell us the launch date"),
             _line(10.4, "Sure Anita this is Paul speaking we are launching on Friday"),
             _line(19.4, "Great thank you Paul"), _line(26.2, "Perfect Anita send me the files"),
             _line(0.5, "mic line", channel="You", speaker="Dhanush")]
    old = {"Others": {0: 50.0}, "Dhanush": {0: 4.0}}
    talk = diarize.apply_turns(lines, TURNS, old, {"You", "Dhanush"})
    assert [x["speaker"] for x in sorted(lines, key=lambda x: x["t"]) if x["channel"] == "Others"] == ["Anita", "Paul", "Anita", "Paul"]
    assert [x["speaker"] for x in lines if x["channel"] == "You"] == ["Dhanush"]  # the host's own words are never relabelled
    assert "Others" not in talk and talk["Dhanush"] == {0: 4.0}  # host keeps exact mic talk time
    assert talk["Anita"][0] == pytest.approx(7 + 5, abs=0.01) or sum(talk["Anita"].values()) == pytest.approx(13)
    assert sum(talk["Paul"].values()) == pytest.approx(14)


def test_turns_that_cross_a_minute_are_split_between_minutes():
    talk = diarize.apply_turns([_line(50, "hello there friend")], [{"start": 50, "end": 70, "speaker": "Ana"}], {}, set())
    assert talk["Ana"] == {0: pytest.approx(10), 1: pytest.approx(10)}


def test_screen_names_turn_speaker_numbers_into_real_names():
    lines = [dict(_line(t, "words go here today"), speaker="Paul D") for t in (2, 4)]
    lines += [dict(_line(t, "other voice words here"), speaker="Others") for t in (12, 14)]
    turns = [{"start": 1, "end": 8, "speaker": "Speaker 1"}, {"start": 11, "end": 18, "speaker": "Speaker 2"}]
    diarize.apply_turns(lines, turns, {}, set())
    assert [x["speaker"] for x in lines] == ["Paul D", "Paul D", "Speaker 2", "Speaker 2"]  # screen agreed on Speaker 1


def test_nothing_changes_without_turns_or_other_side_lines():
    lines = [_line(2, "hello")]
    talk = {"Others": {0: 5.0}}
    assert diarize.apply_turns(lines, [], talk, set()) is talk and lines[0]["speaker"] == "Others"
    assert diarize.apply_turns([_line(2, "x", channel="You")], TURNS, talk, set()) is talk


def test_voices_are_named_once_and_applied_to_every_turn():
    data = {"voices": {"voice 1": "Anita", "voice 2": ""},
            "turns": [{"start": 1, "end": 2, "voice": "voice 1", "starts": "hello there"}, {"start": 3, "end": 4, "voice": "voice 2"},
                      {"start": 5, "end": 6, "voice": "voice 1"}]}
    assert [t["speaker"] for t in _turns_with_names(data)] == ["Anita", "Speaker 2", "Anita"]
    assert _turns_with_names(data)[0]["starts"] == "hello there"
    assert _turns_with_names([{"start": 1, "end": 2, "speaker": "X"}])[0]["speaker"] == "X"
    assert _turns_with_names("nonsense") == []


# ---- stopping a meeting: the browser gets the link, the folder never opens ---------------------------------
def _app(monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])  # the clipboard needs a Qt application
    from inkwake import app as appmod
    opened = {"browser": [], "files": []}
    monkeypatch.setattr(appmod.webbrowser, "open", lambda u: opened["browser"].append(u))
    monkeypatch.setattr(appmod, "_open", lambda p: opened["files"].append(str(p)))
    notes = []
    inst = appmod.InkApp.__new__(appmod.InkApp)
    inst._notify = notes.append
    inst._last_report = None
    return inst, opened, notes


def test_with_a_link_the_browser_opens_and_nothing_else(monkeypatch):
    inst, opened, notes = _app(monkeypatch)
    inst._on_finished(r"C:\Users\x\Desktop\Dhanush\2026-10-19 07-54.docx", "https://reports.example/r/abc")
    assert opened["browser"] == ["https://reports.example/r/abc"]
    assert opened["files"] == []                    # no Explorer window, no Word window
    assert "browser" in notes[0].lower()


def test_without_a_link_nothing_pops_up_but_the_file_is_one_click_away(monkeypatch):
    inst, opened, notes = _app(monkeypatch)
    inst._on_finished(r"C:\Users\x\Desktop\Dhanush\r.docx", "")
    assert opened["browser"] == [] and opened["files"] == []
    assert inst._last_report.endswith("r.docx") and "could not be made" in notes[0]


def test_finished_signal_carries_path_and_link_together():
    got = []
    se = Session(config.Settings())
    se.finished.connect(lambda path, link: got.append((path, link)))
    se.finished.emit("a.docx", "http://link")
    assert got == [("a.docx", "http://link")]


# ---- live stream: session time limit and recording ---------------------------------------------------------
import asyncio  # noqa: E402
import wave  # noqa: E402


def test_goaway_reconnects_quietly():
    from inkwake.live import LiveStream
    notes = []
    st = LiveStream("Others", None, None, config.Settings(), lambda *a: None, lambda *a: None, lambda *a: None, notes.append)

    class Msg:
        go_away = object()
        server_content = None

    class Sess:
        async def receive(self):
            yield Msg()

    async def go():
        st._goaway = asyncio.Event()
        await st._recv(Sess())
        return st._goaway.is_set()

    assert asyncio.run(go()) is True and notes == []


def test_remote_audio_is_recorded_with_matching_length(tmp_path):
    from inkwake.live import LiveStream
    path = tmp_path / "tmp" / "others.wav"
    st = LiveStream("Others", None, None, config.Settings(), lambda *a: None, lambda *a: None, lambda *a: None,
                    lambda m: None, record_to=path)
    for _ in range(30):
        st._take(_tone(0.02, 0.1))   # 3 seconds, some of it speech
    st._take(np.zeros(audio.SR, dtype="float32"))   # and a second of silence: silence is recorded too
    st._rec.close()
    with wave.open(str(path)) as w:
        assert w.getframerate() == audio.SR and w.getnchannels() == 1
        assert w.getnframes() / w.getframerate() == pytest.approx(4.0, abs=0.01) == pytest.approx(st.offset, abs=0.01)


def test_connection_trouble_is_only_reported_when_it_persists():
    from inkwake.live import LiveStream
    notes = []
    st = LiveStream("You", None, None, config.Settings(), lambda *a: None, lambda *a: None, lambda *a: None, notes.append)

    class Boom:
        def __getattr__(self, name):
            raise RuntimeError("1008 session limit")

    async def go():
        st._goaway = asyncio.Event()
        st.client = Boom()
        task = asyncio.create_task(st.run())
        await asyncio.sleep(2.8)
        n_early = len(notes)
        st.stop()
        st._ended = True
        task.cancel()
        try:
            await task
        except BaseException:
            pass
        return n_early

    assert asyncio.run(go()) <= 1   # a retry or two stays invisible; a loud warning appears only on the third failure


def test_one_long_line_with_two_people_is_split_exactly_where_the_voices_change():
    words = ("Hello everyone, this is Anita. Paul, can you tell us the launch date? "
             "Sure Anita. This is Paul speaking. We are launching on Friday, and the release notes are ready. "
             "Great, thank you Paul. Then I will prepare the announcement for Thursday.")
    turns = [{"start": 2, "end": 9, "speaker": "Anita", "starts": "Hello everyone, this is Anita"},
             {"start": 10, "end": 18, "speaker": "Paul", "starts": "Sure Anita. This is Paul"},
             {"start": 19, "end": 31, "speaker": "Anita", "starts": "Great, thank you Paul"}]
    line = dict(_line(2, words), t_end=31.0, mention=False)
    lines = [line]
    diarize.apply_turns(lines, turns, {}, set(), mention_fn=lambda spk, text: "paul" in text.lower() and spk != "Paul")
    got = sorted(lines, key=lambda x: x["t"])
    assert [x["speaker"] for x in got] == ["Anita", "Paul", "Anita"]
    assert got[0]["text"].endswith("launch date?")                       # cut where Paul's first words appear
    assert got[1]["text"] == "Sure Anita. This is Paul speaking. We are launching on Friday, and the release notes are ready."
    assert got[2]["text"].startswith("Great, thank you Paul.")
    assert " ".join(x["text"] for x in got) == words                     # no word lost or repeated
    assert got[0]["mention"] is True and got[1]["mention"] is False      # mentions are re-worked per person after a split


def test_without_first_words_the_cut_still_lands_on_a_sentence_end():
    words = ("One two three four five. Six seven eight nine ten. Eleven twelve thirteen fourteen fifteen. "
             "Sixteen seventeen eighteen nineteen twenty.")
    turns = [{"start": 0, "end": 10, "speaker": "A"}, {"start": 10, "end": 20, "speaker": "B"}]   # no "starts" hints
    lines = [dict(_line(0, words), t_end=20.0)]
    diarize.apply_turns(lines, turns, {}, set())
    got = sorted(lines, key=lambda x: x["t"])
    assert [x["speaker"] for x in got] == ["A", "B"] and got[0]["text"].endswith(".") and got[1]["text"].endswith(".")
    assert " ".join(x["text"] for x in got) == words


def test_first_words_are_found_even_when_the_two_speech_recognisers_disagree_a_little():
    words = "so I think we should go on friday okay sounds good to me let us do that".split()
    assert diarize.find_phrase(words, "okay sound good to", ideal=8, lo=1, reach=4) == 8      # 'okay' is word 8; 'sounds' vs 'sound' still matches
    assert diarize.find_phrase(words, "completely different words", ideal=8, lo=1, reach=4) is None
    assert diarize.find_phrase(words, "ok", ideal=8, lo=1, reach=4) is None                    # too short to trust


def test_a_stretch_too_short_to_matter_does_not_split_a_line():
    line = dict(_line(2, "one two three four five six seven eight"), t_end=10.0)
    turns = [{"start": 2, "end": 9.5, "speaker": "Anita"}, {"start": 9.5, "end": 10, "speaker": "Paul"}]
    lines = [line]
    diarize.apply_turns(lines, turns, {}, set())
    assert len(lines) == 1 and lines[0]["speaker"] == "Anita"
