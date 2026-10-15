export const DEFAULT_BPM = 120;

export function parseMidiFile(arrayBuf) {
  const bytes = new Uint8Array(arrayBuf);
  let pos = 0;
  const u8 = () => bytes[pos++];
  const u16 = () => { const v = (bytes[pos] << 8) | bytes[pos + 1]; pos += 2; return v; };
  const u32 = () => { const v = (bytes[pos] << 24) | (bytes[pos + 1] << 16) | (bytes[pos + 2] << 8) | bytes[pos + 3]; pos += 4; return v >>> 0; };
  const read = n => { const s = bytes.subarray(pos, pos + n); pos += n; return s; };
  const varLen = () => {
    let v = 0;
    for (let i = 0; i < 4; i++) {
      const b = u8();
      v = (v << 7) | (b & 0x7F);
      if (!(b & 0x80)) return v;
    }
    return v;
  };

  const hdrId = String.fromCharCode(...read(4));
  if (hdrId !== 'MThd') throw new Error('Not a MIDI file');
  const hdrLen = u32();
  u16(); // format
  const numTracks = u16();
  const division = u16();
  if (hdrLen > 6) pos += hdrLen - 6;

  const allEvents = [];
  let bpm = DEFAULT_BPM;

  for (let t = 0; t < numTracks; t++) {
    const trkId = String.fromCharCode(...read(4));
    const trkLen = u32();
    if (trkId !== 'MTrk') { pos += trkLen; continue; }

    const trkEnd = pos + trkLen;
    let ticks = 0;
    let runningStatus = 0;

    while (pos < trkEnd) {
      ticks += varLen();
      let byte1 = u8();

      if (byte1 < 0x80) {
        pos--;
        byte1 = runningStatus;
      } else {
        if (byte1 < 0xF0) runningStatus = byte1;
      }

      const cmd = byte1 & 0xF0;

      if (cmd === 0x90) {
        const note = u8(), vel = u8();
        allEvents.push({ ticks, type: vel > 0 ? 'on' : 'off', note, vel });
      } else if (cmd === 0x80) {
        const note = u8(); u8();
        allEvents.push({ ticks, type: 'off', note, vel: 0 });
      } else if (cmd === 0xA0 || cmd === 0xB0 || cmd === 0xE0) {
        u8(); u8();
      } else if (cmd === 0xC0 || cmd === 0xD0) {
        u8();
      } else if (byte1 === 0xFF) {
        const metaType = u8();
        const metaLen = varLen();
        if (metaType === 0x51 && metaLen === 3) {
          const microSPQ = (bytes[pos] << 16) | (bytes[pos + 1] << 8) | bytes[pos + 2];
          bpm = Math.round(60000000 / microSPQ);
        }
        pos += metaLen;
      } else if (byte1 === 0xF0) {
        while (pos < trkEnd && bytes[pos - 1] !== 0xF7) pos++;
      } else if (byte1 === 0xF7) {
        const sysLen = varLen();
        pos += sysLen;
      }
    }
    pos = trkEnd;
  }

  allEvents.sort((a, b) => a.ticks - b.ticks || (a.type === 'off' ? -1 : 1));
  return { division, bpm, events: allEvents };
}
