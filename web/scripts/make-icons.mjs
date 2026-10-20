// Dev-time PWA icon generator: renders PNGs from src/app/icon.svg into public/icons/.
// Run: `node scripts/make-icons.mjs` (uses `sharp`, already installed as a Next.js dependency; not a runtime dep).
// Re-run and commit the PNGs whenever icon.svg changes.
import { readFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const out = join(root, "public", "icons");
mkdirSync(out, { recursive: true });

const svg = readFileSync(join(root, "src", "app", "icon.svg"), "utf8");
// Inner glyph of icon.svg (everything after the background <rect .../>), drawn on a 32x32 grid.
const glyph = svg.replace(/^.*?<rect[^>]*\/>/s, "").replace(/<\/svg>\s*$/, "");
const BG = "#8b97ff";

// Full-bleed square (no transparent corners): iOS/Android apply their own mask; transparency renders black on iOS.
const square = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" fill="${BG}"/>${glyph}</svg>`;
// Maskable: full-bleed background; the glyph's outer ring spans ~63% of the width (r≈10.1 of 32), i.e. inside the
// 80%-diameter safe-zone circle, so the unscaled glyph is safe under any launcher mask.
const maskable = square;

const jobs = [
  ["icon-192.png", svg, 192],
  ["icon-512.png", svg, 512],
  ["icon-maskable-512.png", maskable, 512],
  ["apple-touch-icon.png", square, 180],
];
for (const [name, src, size] of jobs) {
  await sharp(Buffer.from(src), { density: (72 * size) / 32 })
    .resize(size, size)
    .png({ compressionLevel: 9 })
    .toFile(join(out, name));
  console.log(`wrote public/icons/${name} (${size}x${size})`);
}
