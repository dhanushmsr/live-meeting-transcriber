// Runs on the audio thread: forwards mono audio to the page in blocks of about 2048 samples.
class InkCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buf = new Float32Array(2048);
    this.n = 0;
  }

  process(inputs) {
    const ch = inputs[0];
    if (!ch || !ch.length) return true;
    const frames = ch[0].length;
    for (let i = 0; i < frames; i++) {
      let v = 0;
      for (let c = 0; c < ch.length; c++) v += ch[c][i];
      this.buf[this.n++] = v / ch.length;                    // mix down to mono
      if (this.n === this.buf.length) {
        this.port.postMessage(this.buf.slice(0));
        this.n = 0;
      }
    }
    return true;
  }
}
registerProcessor("ink-capture", InkCapture);
