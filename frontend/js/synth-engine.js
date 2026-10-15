import { makeDistortionCurve } from './distortion.js';

export function midiToFreq(note) {
  return 440 * Math.pow(2, (note - 69) / 12);
}

export class SynthEngine {
  constructor() {
    this.actx = null;
    this.masterGain = null;
    this.analyser = null;
    this.scopeAnalyser = null;
    this.recDest = null;
    this.lfoNode = null;
    this.activeVoices = {};
  }

  get initialized() { return !!this.actx; }

  init(lfoShape = 'sine', lfoRate = 1) {
    if (this.actx) return;
    this.actx = new AudioContext();
    this.masterGain = this.actx.createGain();
    this.masterGain.gain.value = 0.7;
    this.analyser = this.actx.createAnalyser();
    this.analyser.fftSize = 2048;
    this.scopeAnalyser = this.actx.createAnalyser();
    this.scopeAnalyser.fftSize = 512;
    this.masterGain.connect(this.analyser);
    this.analyser.connect(this.scopeAnalyser);
    this.scopeAnalyser.connect(this.actx.destination);
    this.recDest = this.actx.createMediaStreamDestination();
    this.masterGain.connect(this.recDest);
    this.lfoNode = this.actx.createOscillator();
    this.lfoNode.type = lfoShape;
    this.lfoNode.frequency.value = lfoRate;
    this.lfoNode.start();
  }

  resume() {
    if (this.actx?.state === 'suspended') this.actx.resume();
  }

  setMasterVolume(v) { if (this.masterGain) this.masterGain.gain.value = v; }
  setLfoRate(v) { if (this.lfoNode) this.lfoNode.frequency.value = v; }
  setLfoShape(v) { if (this.lfoNode) this.lfoNode.type = v; }

  noteOn(midi, p) {
    if (this.activeVoices[midi]) return;
    const actx = this.actx;
    const freq = midiToFreq(midi);
    const now = actx.currentTime;
    const voice = { nodes: [] };

    const voiceGain = actx.createGain();
    voiceGain.gain.value = 0;
    voiceGain.gain.setValueAtTime(0, now);
    voiceGain.gain.linearRampToValueAtTime(1, now + p.env.a);
    voiceGain.gain.linearRampToValueAtTime(p.env.s, now + p.env.a + p.env.d);

    let filterNode = null;
    let driveNode = null;
    if (p.filter.on) {
      filterNode = actx.createBiquadFilter();
      filterNode.type = p.filter.type;
      filterNode.frequency.value = p.filter.cutoff;
      filterNode.Q.value = p.filter.resonance;

      if (Math.abs(p.filter.envAmt) > 0.01) {
        const base = p.filter.cutoff;
        const peak = base + p.filter.envAmt * 10000;
        filterNode.frequency.setValueAtTime(base, now);
        filterNode.frequency.linearRampToValueAtTime(Math.max(20, Math.min(18000, peak)), now + p.env.a);
        filterNode.frequency.linearRampToValueAtTime(Math.max(20, Math.min(18000, base + (peak - base) * p.env.s)), now + p.env.a + p.env.d);
      }

      const lfoFiltAmt = p.lfo.filt * 5000;
      if (lfoFiltAmt > 0) {
        const lfoFilterGain = actx.createGain();
        lfoFilterGain.gain.value = lfoFiltAmt;
        this.lfoNode.connect(lfoFilterGain);
        lfoFilterGain.connect(filterNode.frequency);
        voice.lfoFilterGain = lfoFilterGain;
      }

      if (p.filter.drive > 0.01) {
        driveNode = actx.createWaveShaper();
        driveNode.curve = makeDistortionCurve('soft', p.filter.drive);
        driveNode.oversample = '2x';
      }
    }

    let distNode = null;
    if (p.dist.on) {
      distNode = actx.createWaveShaper();
      distNode.curve = makeDistortionCurve(p.dist.mode, p.dist.drive);
      distNode.oversample = '2x';
    }

    function connectChain(source) {
      let current = source;
      if (driveNode) { current.connect(driveNode); current = driveNode; }
      if (filterNode) { current.connect(filterNode); current = filterNode; }
      if (distNode) { current.connect(distNode); current = distNode; }
      current.connect(voiceGain);
    }

    // OSC A
    const baseFreqA = freq * Math.pow(2, p.oscA.oct) * Math.pow(2, p.oscA.semi / 12) * Math.pow(2, p.oscA.fine / 1200);
    const oscAMix = actx.createGain();
    oscAMix.gain.value = p.oscA.level;

    for (let u = 0; u < p.oscA.unison; u++) {
      const osc = actx.createOscillator();
      osc.type = p.oscA.wave;
      const spread = p.oscA.unison > 1 ? ((u / (p.oscA.unison - 1)) - 0.5) * 2 : 0;
      osc.frequency.value = baseFreqA;
      osc.detune.value = spread * p.oscA.detune;

      const lfoPitchAmt = p.lfo.pitch * 200;
      if (lfoPitchAmt > 0) {
        const lpg = actx.createGain();
        lpg.gain.value = lfoPitchAmt;
        this.lfoNode.connect(lpg);
        lpg.connect(osc.detune);
        voice.nodes.push(lpg);
      }

      if (p.oscA.unison > 1) {
        const pan = actx.createStereoPanner();
        pan.pan.value = spread * p.oscA.width;
        osc.connect(pan);
        pan.connect(oscAMix);
        voice.nodes.push(pan);
      } else {
        osc.connect(oscAMix);
      }
      osc.start(now);
      voice.nodes.push(osc);
    }

    // OSC B + FM
    if (p.oscB.on) {
      const baseFreqB = freq * Math.pow(2, p.oscB.oct) * Math.pow(2, p.oscB.semi / 12) * Math.pow(2, p.oscB.fine / 1200);
      const oscB = actx.createOscillator();
      oscB.type = p.oscB.wave;
      oscB.frequency.value = baseFreqB;

      if (p.fm.on && p.fm.depth > 0) {
        const fmGain = actx.createGain();
        fmGain.gain.value = p.fm.depth;
        const lfoFMAmt = p.lfo.fm * p.fm.depth;
        if (lfoFMAmt > 0) {
          const lfg = actx.createGain();
          lfg.gain.value = lfoFMAmt;
          this.lfoNode.connect(lfg);
          lfg.connect(fmGain.gain);
          voice.nodes.push(lfg);
        }
        oscB.connect(fmGain);
        voice.nodes.forEach(n => {
          if (n instanceof OscillatorNode) fmGain.connect(n.frequency);
        });
        voice.nodes.push(fmGain);
      } else {
        const oscBMix = actx.createGain();
        oscBMix.gain.value = p.oscB.level;
        oscB.connect(oscBMix);
        connectChain(oscBMix);
        voice.nodes.push(oscBMix);
      }
      oscB.start(now);
      voice.nodes.push(oscB);
    }

    connectChain(oscAMix);
    voice.nodes.push(oscAMix);

    // Sub oscillator
    if (p.sub.level > 0) {
      const sub = actx.createOscillator();
      sub.type = p.sub.wave;
      sub.frequency.value = freq * Math.pow(2, p.sub.oct);
      const subGain = actx.createGain();
      subGain.gain.value = p.sub.level;
      sub.connect(subGain);
      connectChain(subGain);
      sub.start(now);
      voice.nodes.push(sub, subGain);
    }

    // Noise generator
    if (p.noise.level > 0) {
      const bufSize = actx.sampleRate * 2;
      const buf = actx.createBuffer(1, bufSize, actx.sampleRate);
      const data = buf.getChannelData(0);
      for (let i = 0; i < bufSize; i++) data[i] = Math.random() * 2 - 1;
      const noise = actx.createBufferSource();
      noise.buffer = buf;
      noise.loop = true;
      const noiseGain = actx.createGain();
      noiseGain.gain.value = p.noise.level;
      noise.connect(noiseGain);
      connectChain(noiseGain);
      noise.start(now);
      voice.nodes.push(noise, noiseGain);
    }

    if (filterNode) voice.nodes.push(filterNode);
    if (driveNode) voice.nodes.push(driveNode);
    if (distNode) voice.nodes.push(distNode);

    voiceGain.connect(this.masterGain);
    voice.voiceGain = voiceGain;
    voice.filterNode = filterNode;
    this.activeVoices[midi] = voice;
  }

  noteOff(midi, releaseSec = 0.1) {
    const voice = this.activeVoices[midi];
    if (!voice) return;
    const now = this.actx.currentTime;

    voice.voiceGain.gain.cancelScheduledValues(now);
    voice.voiceGain.gain.setValueAtTime(voice.voiceGain.gain.value, now);
    voice.voiceGain.gain.linearRampToValueAtTime(0, now + releaseSec);

    if (voice.lfoFilterGain) {
      try { this.lfoNode.disconnect(voice.lfoFilterGain); } catch {}
    }

    setTimeout(() => {
      voice.nodes.forEach(n => { try { if (n.stop) n.stop(); n.disconnect(); } catch {} });
      try { voice.voiceGain.disconnect(); } catch {}
      if (this.activeVoices[midi] === voice) delete this.activeVoices[midi];
    }, (releaseSec + 0.1) * 1000);
  }

  silenceAll() {
    for (const m in this.activeVoices) this.noteOff(parseInt(m), 0);
  }
}
