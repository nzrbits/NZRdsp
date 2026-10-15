const NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
const IS_BLACK = [0, 1, 0, 1, 0, 0, 1, 0, 1, 0, 1, 0];

export function buildNoteRects(scheduled, durSec) {
  const rects = [];
  const pending = {};
  for (const ev of scheduled) {
    if (ev.type === 'on') {
      pending[ev.note] = ev.timeSec;
    } else if (ev.type === 'off' && pending[ev.note] !== undefined) {
      rects.push({ startSec: pending[ev.note], endSec: ev.timeSec, note: ev.note });
      delete pending[ev.note];
    }
  }
  for (const n in pending) {
    rects.push({ startSec: pending[n], endSec: durSec, note: parseInt(n) });
  }
  const notes = rects.map(r => r.note);
  const noteRange = notes.length > 0
    ? { lo: Math.min(...notes), hi: Math.max(...notes) }
    : { lo: 48, hi: 79 };
  return { rects, noteRange };
}

export function drawMidiRoll(canvas, rects, noteRange, durSec, bpm, cursorSec) {
  if (!canvas || rects.length === 0) return;

  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);

  const lo = noteRange.lo - 1;
  const hi = noteRange.hi + 1;
  const noteSpan = hi - lo + 1;
  const rowH = h / noteSpan;

  ctx.fillStyle = '#0e1016';
  ctx.fillRect(0, 0, w, h);

  for (let n = lo; n <= hi; n++) {
    const y = h - (n - lo + 1) * rowH;
    if (IS_BLACK[n % 12]) {
      ctx.fillStyle = 'rgba(255,255,255,0.02)';
      ctx.fillRect(0, y, w, rowH);
    }
    if (n % 12 === 0) {
      ctx.strokeStyle = 'rgba(255,255,255,0.08)';
      ctx.beginPath(); ctx.moveTo(0, y + rowH); ctx.lineTo(w, y + rowH); ctx.stroke();
    }
  }

  const beatSec = 60 / bpm;
  ctx.strokeStyle = 'rgba(255,255,255,0.04)';
  for (let t = 0; t < durSec; t += beatSec) {
    const x = (t / durSec) * w;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
  }
  ctx.strokeStyle = 'rgba(255,255,255,0.1)';
  for (let t = 0; t < durSec; t += beatSec * 4) {
    const x = (t / durSec) * w;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
  }

  const gap = Math.max(1, rowH * 0.1);
  for (const r of rects) {
    const x1 = (r.startSec / durSec) * w;
    const x2 = (r.endSec / durSec) * w;
    const y = h - (r.note - lo + 1) * rowH + gap / 2;
    const nw = Math.max(2, x2 - x1);
    const isActive = cursorSec !== undefined && cursorSec >= r.startSec && cursorSec < r.endSec;
    if (isActive) {
      ctx.fillStyle = '#2dd4a0';
      ctx.shadowColor = '#2dd4a0';
      ctx.shadowBlur = 6;
    } else {
      ctx.fillStyle = '#4a9eff';
      ctx.shadowBlur = 0;
    }
    ctx.beginPath();
    ctx.roundRect(x1, y, nw, rowH - gap, 2);
    ctx.fill();
    ctx.shadowBlur = 0;
  }

  if (cursorSec !== undefined && cursorSec >= 0) {
    const cx = (cursorSec / durSec) * w;
    ctx.strokeStyle = '#fff';
    ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.moveTo(cx, 0); ctx.lineTo(cx, h); ctx.stroke();
    ctx.lineWidth = 1;
  }

  ctx.font = '9px JetBrains Mono, monospace';
  ctx.fillStyle = 'rgba(255,255,255,0.25)';
  ctx.textBaseline = 'middle';
  for (let n = lo; n <= hi; n++) {
    if (n % 12 === 0 || n === lo) {
      const y = h - (n - lo + 0.5) * rowH;
      ctx.fillText(NOTE_NAMES[n % 12] + (Math.floor(n / 12) - 1), 3, y);
    }
  }
}
