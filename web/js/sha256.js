// SHA-256 of a Blob as lowercase hex.
// crypto.subtle (secure contexts: https or localhost) for files up to SUBTLE_MAX;
// otherwise an incremental pure-JS implementation reading the blob in slices, so a
// multi-GB video never has to sit in memory at once, and plain-http LAN testing works.

const SUBTLE_MAX = 192 * 1024 * 1024;
const SLICE = 8 * 1024 * 1024;

const hex = (buf) => Array.from(new Uint8Array(buf), (b) => b.toString(16).padStart(2, "0")).join("");

export async function sha256Blob(blob, onProgress) {
  const subtle = globalThis.crypto && globalThis.crypto.subtle;
  if (subtle && blob.size <= SUBTLE_MAX) {
    const buf = await blob.arrayBuffer();
    const out = await subtle.digest("SHA-256", buf);
    if (onProgress) onProgress(blob.size, blob.size);
    return hex(out);
  }
  const h = new Sha256();
  for (let off = 0; off < blob.size; off += SLICE) {
    const part = new Uint8Array(await blob.slice(off, off + SLICE).arrayBuffer());
    h.update(part);
    if (onProgress) onProgress(Math.min(off + SLICE, blob.size), blob.size);
    await new Promise((r) => setTimeout(r, 0)); // keep the UI responsive
  }
  return hex(h.digest());
}

const K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

export class Sha256 {
  constructor() {
    this.h = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
    this.buf = new Uint8Array(64);
    this.bufLen = 0;
    this.total = 0;
    this.w = new Uint32Array(64);
  }
  _block(d, o) {
    const w = this.w, h = this.h;
    for (let i = 0; i < 16; i++) w[i] = (d[o + 4 * i] << 24) | (d[o + 4 * i + 1] << 16) | (d[o + 4 * i + 2] << 8) | d[o + 4 * i + 3];
    for (let i = 16; i < 64; i++) {
      const a = w[i - 15], b = w[i - 2];
      const s0 = ((a >>> 7) | (a << 25)) ^ ((a >>> 18) | (a << 14)) ^ (a >>> 3);
      const s1 = ((b >>> 17) | (b << 15)) ^ ((b >>> 19) | (b << 13)) ^ (b >>> 10);
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
    }
    let a = h[0], b = h[1], c = h[2], dd = h[3], e = h[4], f = h[5], g = h[6], hh = h[7];
    for (let i = 0; i < 64; i++) {
      const S1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7));
      const ch = (e & f) ^ (~e & g);
      const t1 = (hh + S1 + ch + K[i] + w[i]) | 0;
      const S0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10));
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (S0 + maj) | 0;
      hh = g; g = f; f = e; e = (dd + t1) | 0; dd = c; c = b; b = a; a = (t1 + t2) | 0;
    }
    h[0] += a; h[1] += b; h[2] += c; h[3] += dd; h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
  }
  update(data) {
    let i = 0;
    this.total += data.length;
    if (this.bufLen) {
      while (this.bufLen < 64 && i < data.length) this.buf[this.bufLen++] = data[i++];
      if (this.bufLen === 64) { this._block(this.buf, 0); this.bufLen = 0; }
    }
    for (; i + 64 <= data.length; i += 64) this._block(data, i);
    while (i < data.length) this.buf[this.bufLen++] = data[i++];
    return this;
  }
  digest() {
    const bits = this.total * 8;
    const pad = new Uint8Array(((this.bufLen + 9 + 63) & ~63) - this.bufLen);
    pad[0] = 0x80;
    const hi = Math.floor(bits / 0x100000000), lo = bits >>> 0;
    const n = pad.length;
    pad[n - 8] = hi >>> 24; pad[n - 7] = hi >>> 16; pad[n - 6] = hi >>> 8; pad[n - 5] = hi;
    pad[n - 4] = lo >>> 24; pad[n - 3] = lo >>> 16; pad[n - 2] = lo >>> 8; pad[n - 1] = lo;
    this.update(pad);
    const out = new Uint8Array(32);
    for (let i = 0; i < 8; i++) {
      out[4 * i] = this.h[i] >>> 24; out[4 * i + 1] = this.h[i] >>> 16; out[4 * i + 2] = this.h[i] >>> 8; out[4 * i + 3] = this.h[i];
    }
    return out.buffer;
  }
}
