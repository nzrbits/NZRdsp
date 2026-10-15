export function encodeWav24(rendered) {
  const ch = rendered.numberOfChannels;
  const sr = rendered.sampleRate;
  const len = rendered.length;
  const blockAlign = ch * 3;
  const dataSize = len * blockAlign;
  const buf = new ArrayBuffer(44 + dataSize);
  const v = new DataView(buf);
  const ws = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };

  ws(0, 'RIFF'); v.setUint32(4, 36 + dataSize, true); ws(8, 'WAVE');
  ws(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true);
  v.setUint16(22, ch, true); v.setUint32(24, sr, true);
  v.setUint32(28, sr * blockAlign, true); v.setUint16(32, blockAlign, true);
  v.setUint16(34, 24, true); ws(36, 'data'); v.setUint32(40, dataSize, true);

  const channels = [];
  for (let c = 0; c < ch; c++) channels.push(rendered.getChannelData(c));
  let off = 44;
  for (let i = 0; i < len; i++) {
    for (let c = 0; c < ch; c++) {
      const s = Math.max(-1, Math.min(1, channels[c][i]));
      const val = Math.round(s * 8388607);
      v.setUint8(off, val & 0xFF);
      v.setUint8(off + 1, (val >> 8) & 0xFF);
      v.setUint8(off + 2, (val >> 16) & 0xFF);
      off += 3;
    }
  }
  return buf;
}
