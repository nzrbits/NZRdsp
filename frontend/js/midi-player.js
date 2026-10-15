export class MidiPlayer {
  #scheduled = [];
  #noteRects = [];
  #durSec = 0;
  #timers = [];
  #animId = null;
  #playing = false;
  #startMs = 0;
  #pausedAt = 0;
  #looping = false;
  #onNoteOn;
  #onNoteOff;
  #onFrame;
  #onEnd;
  #onStop;

  constructor({ onNoteOn, onNoteOff, onFrame, onEnd, onStop }) {
    this.#onNoteOn = onNoteOn;
    this.#onNoteOff = onNoteOff;
    this.#onFrame = onFrame;
    this.#onEnd = onEnd;
    this.#onStop = onStop;
  }

  load(scheduled, noteRects, durSec) {
    this.stop();
    this.#scheduled = scheduled;
    this.#noteRects = noteRects;
    this.#durSec = durSec;
  }

  get playing() { return this.#playing; }
  get pausedAt() { return this.#pausedAt; }
  get duration() { return this.#durSec; }
  get loaded() { return this.#scheduled.length > 0; }
  set looping(v) { this.#looping = v; }
  get looping() { return this.#looping; }

  #halt() {
    this.#playing = false;
    this.#timers.forEach(t => clearTimeout(t));
    this.#timers = [];
    if (this.#animId) { cancelAnimationFrame(this.#animId); this.#animId = null; }
    this.#onStop();
  }

  playFrom(startSec) {
    if (this.#scheduled.length === 0) return;
    this.#halt();
    this.#playing = true;
    this.#pausedAt = startSec;
    this.#startMs = performance.now() - startSec * 1000;

    for (const ev of this.#scheduled) {
      const delay = (ev.timeSec - startSec) * 1000;
      if (delay < -50) continue;
      const t = setTimeout(() => {
        if (!this.#playing) return;
        if (ev.type === 'on') this.#onNoteOn(ev.note);
        else this.#onNoteOff(ev.note);
      }, Math.max(0, delay));
      this.#timers.push(t);
    }

    for (const r of this.#noteRects) {
      if (r.startSec < startSec && r.endSec > startSec) {
        this.#onNoteOn(r.note);
      }
    }

    const remaining = (this.#durSec - startSec) * 1000;
    const endT = setTimeout(() => {
      this.#halt();
      this.#pausedAt = 0;
      this.#onEnd();
      if (this.#looping && this.#scheduled.length > 0) {
        setTimeout(() => this.playFrom(0), 200);
      }
    }, remaining);
    this.#timers.push(endT);

    const animate = () => {
      if (!this.#playing) return;
      const elapsed = (performance.now() - this.#startMs) / 1000;
      this.#onFrame(elapsed);
      this.#animId = requestAnimationFrame(animate);
    };
    animate();
  }

  pause() {
    if (!this.#playing) return this.#pausedAt;
    this.#pausedAt = (performance.now() - this.#startMs) / 1000;
    this.#halt();
    return this.#pausedAt;
  }

  stop() {
    this.#halt();
    this.#pausedAt = 0;
  }

  seekTo(sec) {
    this.#pausedAt = sec;
    this.#onFrame(sec);
    this.playFrom(sec);
  }
}
