"""Builds the .docx meeting report with charts."""

import io
import re
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from docx import Document  # noqa: E402
from docx.enum.text import WD_ALIGN_PARAGRAPH  # noqa: E402
from docx.shared import Inches, Pt, RGBColor  # noqa: E402

from .config import reports_dir  # noqa: E402

PALETTE = ["#2563eb", "#16a34a", "#f59e0b", "#dc2626", "#7c3aed", "#0891b2", "#db2777", "#65a30d"]


def _png(fig) -> io.BytesIO:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=170, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def _share_chart(totals: dict[str, float]):
    fig, ax = plt.subplots(figsize=(5, 3.6))
    names = list(totals)
    ax.pie([totals[n] for n in names], labels=names, colors=PALETTE[: len(names)], autopct="%1.0f%%",
           startangle=90, wedgeprops={"width": 0.45, "edgecolor": "white"})
    ax.set_title("Share of talk time")
    return _png(fig)


def _timeline_chart(talk: dict[str, dict[int, float]], minutes: float):
    n = max(1, int(minutes) + 1)
    fig, ax = plt.subplots(figsize=(6.5, 3.2))
    bottom = [0.0] * n
    for i, (spk, per) in enumerate(talk.items()):
        vals = [per.get(m, 0.0) / 60 for m in range(n)]
        ax.bar(range(n), vals, bottom=bottom, label=spk, color=PALETTE[i % len(PALETTE)], width=0.8)
        bottom = [b + v for b, v in zip(bottom, vals)]
    ax.set_xlabel("Minute of meeting")
    ax.set_ylabel("Minutes spoken")
    ax.set_title("Who spoke when")
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, fontsize=8)
    return _png(fig)


def _heading(doc, text: str) -> None:
    doc.add_heading(text, level=2)


def _bullets(doc, items) -> None:
    for it in items or []:
        doc.add_paragraph(str(it), style="List Bullet")


def build(*, settings, title, platform, started: datetime, minutes: float, transcript, talk, analysis,
          languages, meetings_total: int, minutes_total: float, participants=(), bookmarks=()) -> Path:
    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(11)

    h = doc.add_heading("Meeting Report", level=0)
    h.alignment = WD_ALIGN_PARAGRAPH.LEFT
    sub = doc.add_paragraph(f"{title or 'Meeting'}  |  {started:%A, %d %B %Y, %H:%M}")
    sub.runs[0].font.color.rgb = RGBColor(0x55, 0x55, 0x55)

    totals = {s: sum(per.values()) for s, per in talk.items() if sum(per.values()) > 0}
    rows = [
        ("Started by", settings.name or "-"),
        ("Platform", platform or "-"),
        ("Duration", f"{minutes:.1f} min"),
        ("Participants", ", ".join(participants) or (", ".join(totals) if totals else "-")),
        ("Languages", ", ".join(languages) or "-"),
        ("Overall tone", str(analysis.get("sentiment") or "-")),
        ("Your total meetings with Ink", str(meetings_total)),
        ("Your total meeting minutes", f"{minutes_total:.1f} min"),
    ]
    t = doc.add_table(rows=0, cols=2)
    t.style = "Light List Accent 1"
    for k, v in rows:
        c = t.add_row().cells
        c[0].text, c[1].text = k, v

    _heading(doc, "Summary")
    doc.add_paragraph(analysis.get("summary") or "-")

    if analysis.get("chapters"):
        _heading(doc, "Timeline")
        for ch in analysis["chapters"]:
            p = doc.add_paragraph(style="List Bullet")
            p.add_run(f"{ch.get('start', '')}  {ch.get('title', '')}").bold = True
            if ch.get("summary"):
                p.add_run(f" - {ch['summary']}")

    mentions = [x for x in transcript if x.get("mention")]
    if bookmarks or mentions:
        _heading(doc, "Highlights")
        for b in bookmarks:
            m, sec = divmod(int(b.get("t", 0)), 60)
            p = doc.add_paragraph(style="List Bullet")
            p.add_run(f"[{m:02d}:{sec:02d}] Bookmarked").bold = True
            if b.get("text"):
                p.add_run(f": {b['text']}")
        for x in mentions:
            m, sec = divmod(int(x.get("t", 0)), 60)
            p = doc.add_paragraph(style="List Bullet")
            p.add_run(f"[{m:02d}:{sec:02d}] You were mentioned by {x['speaker']}").bold = True
            p.add_run(f": {x.get('text') or x.get('translation', '')}")

    if analysis.get("topics"):
        _heading(doc, "What was discussed")
        for tp in analysis["topics"]:
            p = doc.add_paragraph(style="List Bullet")
            p.add_run(str(tp.get("title", "")) + ": ").bold = True
            p.add_run(str(tp.get("detail", "")))

    if analysis.get("key_points"):
        _heading(doc, "Key points")
        _bullets(doc, analysis["key_points"])
    if analysis.get("decisions"):
        _heading(doc, "Decisions")
        _bullets(doc, analysis["decisions"])
    if analysis.get("action_items"):
        _heading(doc, "Action items")
        at = doc.add_table(rows=1, cols=2)
        at.style = "Light Grid Accent 1"
        at.rows[0].cells[0].text, at.rows[0].cells[1].text = "Task", "Owner"
        for a in analysis["action_items"]:
            c = at.add_row().cells
            c[0].text, c[1].text = str(a.get("task", "")), str(a.get("owner") or "-")
    if analysis.get("follow_ups"):
        _heading(doc, "Suggested follow-ups")
        _bullets(doc, analysis["follow_ups"])

    if analysis.get("open_questions"):
        _heading(doc, "Open questions")
        _bullets(doc, analysis["open_questions"])
    email = analysis.get("follow_up_email") or {}
    if email.get("body"):
        _heading(doc, "Draft follow-up email")
        doc.add_paragraph().add_run(f"Subject: {email.get('subject', '')}").bold = True
        for para in str(email["body"]).split("\n"):
            doc.add_paragraph(para)

    if totals:
        _heading(doc, "Speaking analysis")
        doc.add_picture(_share_chart(totals), width=Inches(3.8))
        doc.add_picture(_timeline_chart(talk, minutes), width=Inches(6.2))
        tt = doc.add_table(rows=1, cols=3)
        tt.style = "Light Grid Accent 1"
        for i, label in enumerate(("Speaker", "Minutes", "Share")):
            tt.rows[0].cells[i].text = label
        whole = sum(totals.values())
        for spk, sec in sorted(totals.items(), key=lambda kv: -kv[1]):
            c = tt.add_row().cells
            c[0].text, c[1].text, c[2].text = spk, f"{sec / 60:.1f}", f"{sec / whole:.0%}"
        doc.add_paragraph("Remote speakers are named from the active-speaker highlight on the meeting screen; unknown moments are grouped as 'Others'.").runs[0].italic = True

    if transcript:
        doc.add_page_break()
        _heading(doc, "Full transcript")
        for x in transcript:
            p = doc.add_paragraph()
            p.add_run(f"[{int(x['t'] // 60):02d}:{int(x['t'] % 60):02d}] {x['speaker']}: ").bold = True
            p.add_run(x["text"])
            if x.get("translation"):
                r = p.add_run(f"\n    -> {x['translation']}")
                r.italic = True
                r.font.color.rgb = RGBColor(0x55, 0x55, 0x55)

    out = reports_dir(settings.name)
    out.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r'[<>:"/\\|?*\r\n]', "", title).strip(" .")[:80]
    stamp = f"{started:%Y-%m-%d %H-%M}"
    path = out / (f"{safe} - {stamp}.docx" if safe else f"{stamp}.docx")
    doc.save(path)
    return path
