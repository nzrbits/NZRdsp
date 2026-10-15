export function makeDistortionCurve(mode, drive, scale = 10) {
  const n = 8192;
  const curve = new Float32Array(n);
  const d = 1 + drive * scale;
  for (let i = 0; i < n; i++) {
    const x = (i * 2) / n - 1;
    switch (mode) {
      case 'soft':
        curve[i] = (Math.PI + d) * x / (Math.PI + d * Math.abs(x));
        break;
      case 'tube': {
        const v = x * d;
        curve[i] = v > 0 ? 1 - Math.exp(-v) : -1 + Math.exp(v);
        break;
      }
      case 'hard':
        curve[i] = Math.max(-1, Math.min(1, x * d));
        break;
      case 'fold': {
        let v = x * d;
        while (Math.abs(v) > 1) v = v > 0 ? 2 - v : -2 - v;
        curve[i] = v;
        break;
      }
      case 'rectify':
        curve[i] = Math.max(-1, Math.min(1, Math.abs(x) * d));
        break;
      case 'bitcrush': {
        const bits = Math.max(2, 16 - Math.floor(drive * 14));
        const steps = Math.pow(2, bits);
        curve[i] = Math.max(-1, Math.min(1, Math.round(x * d * steps) / steps));
        break;
      }
    }
  }
  return curve;
}
