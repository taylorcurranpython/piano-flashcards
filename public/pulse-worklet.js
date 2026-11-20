'use strict';
// Clap onsets from the mic, timed in AudioContext time (the same clock the click is scheduled on).
// A clap is a sharp jump of the envelope well above the background level; one per refractory period.
class ClapDetector extends AudioWorkletProcessor {
  constructor() {
    super();
    this.env = 0; this.floor = 0.002; this.last = -1; this.refractory = 0.15;
    this.decay = Math.exp(-1 / (0.004 * sampleRate));  // ~4 ms envelope release
    this.slow = 1 / (0.5 * sampleRate);  // ~0.5 s background tracking
    this.port.onmessage = e => { if (e.data.refractory) this.refractory = e.data.refractory; };
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      const x = Math.abs(ch[i]);
      this.env = x > this.env ? x : this.env * this.decay;
      const thresh = Math.max(0.06, this.floor * 8), t = currentTime + i / sampleRate;
      if (this.env > thresh && t - this.last > this.refractory) { this.last = t; this.port.postMessage({t, level: this.env}); }
      else if (this.env < thresh) this.floor += (this.env - this.floor) * this.slow;
    }
    return true;
  }
}
registerProcessor('clap-detector', ClapDetector);
