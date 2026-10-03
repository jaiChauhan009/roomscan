// Shrink a phone JPEG before upload: the engine works at 960 px and the capture checker calls
// anything under 1600 px a chat-app copy, so 2048 px on the long side loses nothing it uses,
// and a 12 MP photo drops from ~2.5 MB to ~0.5 MB (uploads 4-5x faster on a slow uplink).
// The EXIF block (focal length, which sets the scale of the depth) is copied over, with the
// orientation reset to 1 because the browser has already turned the pixels upright.
// Anything unexpected (HEIC, PNG, a decode failure, no gain) returns the original file.

export const SHRINK_LONG = 2048;
const MIN_BYTES = 1200 * 1024;
const QUALITY = 0.9;

function isJpeg(f) {
  return /^image\/jpe?g$/i.test(f.type) || /\.jpe?g$/i.test(f.name);
}

/** The APP1 "Exif" segment (marker included) of a JPEG, or null. */
export function exifSegment(buf) {
  const b = new Uint8Array(buf);
  if (b[0] !== 0xff || b[1] !== 0xd8) return null;
  let i = 2;
  while (i + 4 <= b.length && b[i] === 0xff) {
    const marker = b[i + 1];
    if (marker === 0xda || marker === 0xd9) break; // start of scan / end of image
    const len = (b[i + 2] << 8) | b[i + 3];
    if (marker === 0xe1 && b[i + 4] === 0x45 && b[i + 5] === 0x78 && b[i + 6] === 0x69 && b[i + 7] === 0x66) {
      return b.slice(i, i + 2 + len);
    }
    i += 2 + len;
  }
  return null;
}

/** Set the IFD0 orientation tag (0x0112) of an APP1 Exif segment to 1, in place. */
export function resetOrientation(seg) {
  const t = 10; // FF E1, length (2), "Exif\0\0" (6): the TIFF header starts here
  const le = seg[t] === 0x49; // "II" little endian, "MM" big endian
  const dv = new DataView(seg.buffer, seg.byteOffset, seg.byteLength);
  const ifd0 = t + dv.getUint32(t + 4, le);
  const n = dv.getUint16(ifd0, le);
  for (let k = 0; k < n; k++) {
    const e = ifd0 + 2 + 12 * k;
    if (e + 12 > seg.length) break;
    if (dv.getUint16(e, le) === 0x0112) { dv.setUint16(e + 8, 1, le); break; }
  }
  return seg;
}

/** A new JPEG blob: SOI, the Exif segment, then the encoder's output after its SOI. */
export function withExif(jpegBuf, seg) {
  const out = new Uint8Array(jpegBuf);
  const res = new Uint8Array(out.length + seg.length);
  res.set(out.subarray(0, 2), 0);
  res.set(seg, 2);
  res.set(out.subarray(2), 2 + seg.length);
  return res;
}

async function encode(bmp, w, h) {
  if (typeof OffscreenCanvas !== "undefined") {
    const c = new OffscreenCanvas(w, h);
    c.getContext("2d").drawImage(bmp, 0, 0, w, h);
    return c.convertToBlob({ type: "image/jpeg", quality: QUALITY });
  }
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  c.getContext("2d").drawImage(bmp, 0, 0, w, h);
  return new Promise((res) => c.toBlob(res, "image/jpeg", QUALITY));
}

export async function shrinkPhoto(file) {
  try {
    if (!isJpeg(file) || file.size < MIN_BYTES || typeof createImageBitmap !== "function") return file;
    const buf = await file.arrayBuffer();
    const seg = exifSegment(buf);
    const bmp = await createImageBitmap(file); // EXIF orientation applied (browser default)
    const long = Math.max(bmp.width, bmp.height);
    if (long <= SHRINK_LONG) { bmp.close && bmp.close(); return file; }
    const s = SHRINK_LONG / long;
    const w = Math.round(bmp.width * s), h = Math.round(bmp.height * s);
    const blob = await encode(bmp, w, h);
    bmp.close && bmp.close();
    if (!blob) return file;
    const out = seg ? withExif(await blob.arrayBuffer(), resetOrientation(seg.slice())) : new Uint8Array(await blob.arrayBuffer());
    if (out.length >= file.size * 0.8) return file;
    return new File([out], file.name, { type: "image/jpeg", lastModified: file.lastModified });
  } catch {
    return file;
  }
}
