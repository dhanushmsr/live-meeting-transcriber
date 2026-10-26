import test from "node:test";
import assert from "node:assert/strict";
import { applyTurns, cleanTurns, findPhrase, looksLikeRealName, nameMap, sliceWords } from "../core/diarize.js";
import { turnsWithNames } from "../core/gemini.js";

const TURNS = [
  { start: 2, end: 9, speaker: "Anita" }, { start: 10, end: 18, speaker: "Paul" },
  { start: 19, end: 25, speaker: "Anita" }, { start: 26, end: 32, speaker: "Paul" },
];
const line = (t, text, channel = "Others", speaker = "Others") => ({ t, speaker, text, translation: "", channel });
const sum = (o) => Object.values(o).reduce((a, b) => a + b, 0);

test("malformed turns are dropped", () => {
  const raw = [{ start: "x", end: 3, speaker: "A" }, { start: 5, end: 4, speaker: "A" }, { start: 1, end: 2 },
    { start: 6, end: 8, speaker: "B" }, "junk", null];
  assert.deepEqual(cleanTurns(raw), [{ start: 6, end: 8, speaker: "B", starts: "" }]);
});

test("other-side lines get named, the host is untouched, talk time is rebuilt", () => {
  const lines = [line(2, "Hello everyone this is Anita Paul can you tell us the launch date"),
    line(10.4, "Sure Anita this is Paul speaking we are launching on Friday"), line(19.4, "Great thank you Paul"),
    line(26.2, "Perfect Anita send me the files"), line(0.5, "mic line", "You", "Dhanush")];
  const talk = applyTurns(lines, TURNS, { Others: { 0: 50 }, Dhanush: { 0: 4 } }, new Set(["You", "Dhanush"]));
  const others = lines.filter((x) => x.channel === "Others").sort((a, b) => a.t - b.t);
  assert.deepEqual(others.map((x) => x.speaker), ["Anita", "Paul", "Anita", "Paul"]);
  assert.deepEqual(lines.filter((x) => x.channel === "You").map((x) => x.speaker), ["Dhanush"]);
  assert.equal(talk.Others, undefined);
  assert.deepEqual(talk.Dhanush, { 0: 4 });
  assert.ok(Math.abs(sum(talk.Anita) - 13) < 0.01 && Math.abs(sum(talk.Paul) - 14) < 0.01);
});

test("a turn that crosses a minute is split between the minutes", () => {
  const talk = applyTurns([line(50, "hello there friend")], [{ start: 50, end: 70, speaker: "Ana" }], {}, new Set());
  assert.ok(Math.abs(talk.Ana[0] - 10) < 1e-9 && Math.abs(talk.Ana[1] - 10) < 1e-9);
});

test("screen-agreed names turn speaker numbers into real names", () => {
  const lines = [2, 4].map((t) => ({ ...line(t, "words go here today"), speaker: "Paul D" }));
  lines.push(...[12, 14].map((t) => line(t, "other voice words here")));
  applyTurns(lines, [{ start: 1, end: 8, speaker: "Speaker 1" }, { start: 11, end: 18, speaker: "Speaker 2" }], {}, new Set());
  assert.deepEqual(lines.map((x) => x.speaker), ["Paul D", "Paul D", "Speaker 2", "Speaker 2"]);
});

test("nothing changes without turns or other-side lines", () => {
  const talk = { Others: { 0: 5 } };
  const lines = [line(2, "hello")];
  assert.equal(applyTurns(lines, [], talk, new Set()), talk);
  assert.equal(lines[0].speaker, "Others");
  assert.equal(applyTurns([line(2, "x", "You")], TURNS, talk, new Set()), talk);
});

test("voices are named once and applied to every turn of that voice", () => {
  const data = {
    voices: { "voice 1": "Anita", "voice 2": "" },
    turns: [{ start: 1, end: 2, voice: "voice 1", starts: "hello there" }, { start: 3, end: 4, voice: "voice 2" }, { start: 5, end: 6, voice: "voice 1" }],
  };
  const t = turnsWithNames(data);
  assert.deepEqual(t.map((x) => x.speaker), ["Anita", "Speaker 2", "Anita"]);
  assert.equal(t[0].starts, "hello there");
  assert.equal(turnsWithNames([{ start: 1, end: 2, speaker: "X" }])[0].speaker, "X");
  assert.deepEqual(turnsWithNames("nonsense"), []);
});

test("one long line with two people is split exactly where the voices change", () => {
  const words = "Hello everyone, this is Anita. Paul, can you tell us the launch date? " +
    "Sure Anita. This is Paul speaking. We are launching on Friday, and the release notes are ready. " +
    "Great, thank you Paul. Then I will prepare the announcement for Thursday.";
  const turns = [
    { start: 2, end: 9, speaker: "Anita", starts: "Hello everyone, this is Anita" },
    { start: 10, end: 18, speaker: "Paul", starts: "Sure Anita. This is Paul" },
    { start: 19, end: 31, speaker: "Anita", starts: "Great, thank you Paul" },
  ];
  const lines = [{ ...line(2, words), t_end: 31, mention: false }];
  applyTurns(lines, turns, {}, new Set(), (spk, text) => text.toLowerCase().includes("paul") && spk !== "Paul");
  const got = lines.sort((a, b) => a.t - b.t);
  assert.deepEqual(got.map((x) => x.speaker), ["Anita", "Paul", "Anita"]);
  assert.ok(got[0].text.endsWith("launch date?"));
  assert.equal(got[1].text, "Sure Anita. This is Paul speaking. We are launching on Friday, and the release notes are ready.");
  assert.ok(got[2].text.startsWith("Great, thank you Paul."));
  assert.equal(got.map((x) => x.text).join(" "), words);            // no word lost or repeated
  assert.equal(got[0].mention, true); assert.equal(got[1].mention, false);
});

test("without first-words hints the cut still lands on a sentence end", () => {
  const words = "One two three four five. Six seven eight nine ten. Eleven twelve thirteen fourteen fifteen. Sixteen seventeen eighteen nineteen twenty.";
  const lines = [{ ...line(0, words), t_end: 20 }];
  applyTurns(lines, [{ start: 0, end: 10, speaker: "A" }, { start: 10, end: 20, speaker: "B" }], {}, new Set());
  const got = lines.sort((a, b) => a.t - b.t);
  assert.deepEqual(got.map((x) => x.speaker), ["A", "B"]);
  assert.ok(got.every((x) => x.text.endsWith(".")));
  assert.equal(got.map((x) => x.text).join(" "), words);
});

test("a stretch too short to matter does not split a line", () => {
  const lines = [{ ...line(2, "one two three four five six seven eight"), t_end: 10 }];
  applyTurns(lines, [{ start: 2, end: 9.5, speaker: "Anita" }, { start: 9.5, end: 10, speaker: "Paul" }], {}, new Set());
  assert.equal(lines.length, 1); assert.equal(lines[0].speaker, "Anita");
});

test("first words are found even when two recognisers disagree a little", () => {
  const words = "so I think we should go on friday okay sounds good to me let us do that".split(" ");
  assert.equal(findPhrase(words, "okay sound good to", 8, 1, 4), 8);
  assert.equal(findPhrase(words, "completely different words", 8, 1, 4), null);
  assert.equal(findPhrase(words, "ok", 8, 1, 4), null);
  assert.deepEqual(sliceWords("a b c d".split(" "), [0.5, 0.5]), ["a b", "c d"]);
});

test("real names versus speaker numbers", () => {
  assert.ok(looksLikeRealName("Anita") && !looksLikeRealName("Speaker 2") && !looksLikeRealName("voice 1"));
  assert.deepEqual(nameMap([{ diar: "S1", screen: "Others" }]), {});
});
