// Stage binary dependencies locally. No camera data or model requests leave the app at runtime.
import { mkdir, copyFile, readdir, access, writeFile, rename } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const root = fileURLToPath(new URL('../', import.meta.url));
const source = path.join(root, 'node_modules/@mediapipe/tasks-vision/wasm');
const destination = path.join(root, 'public/vision/wasm');
await mkdir(destination, { recursive: true });
for (const name of await readdir(source)) {
  if (/\.(js|wasm)$/.test(name)) await copyFile(path.join(source, name), path.join(destination, name));
}
const model = path.join(root, 'public/vision/blaze_face_short_range.tflite');
try { await access(model); }
catch {
  const response = await fetch('https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite');
  if (!response.ok) throw new Error(`Face model download failed: ${response.status}`);
  const data = new Uint8Array(await response.arrayBuffer());
  if (String.fromCharCode(...data.slice(4, 8)) !== 'TFL3') throw new Error('Downloaded asset is not a TensorFlow Lite model');
  await writeFile(model + '.tmp', data);
  await rename(model + '.tmp', model);
}
console.log('Local face detector assets ready.');
