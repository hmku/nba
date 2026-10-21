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
// icon.svg is drawn on a 512x512 grid; its background is <rect id="bgrect" rx=...> (rounded for favicons/Android "any").
// Full-bleed square (no transparent corners): iOS/Android apply their own mask; transparency renders black on iOS.
const square = svg.replace(/(<rect id="bgrect"[^>]*?) rx="\d+"/, "$1");
if (square === svg) throw new Error("icon.svg: background <rect id=\"bgrect\" rx=...> not found");
// Maskable: full-bleed background; the ball (r=164 of 512, 64% of the width) sits inside the 80%-diameter
// safe-zone circle, so it is safe under any launcher mask.
const maskable = square;

const jobs = [
  ["icon-192.png", svg, 192],
  ["icon-512.png", svg, 512],
  ["icon-maskable-512.png", maskable, 512],
  ["apple-touch-icon.png", square, 180],
];
for (const [name, src, size] of jobs) {
  await sharp(Buffer.from(src), { density: (72 * size) / 512 })
    .resize(size, size)
    .png({ compressionLevel: 9 })
    .toFile(join(out, name));
  console.log(`wrote public/icons/${name} (${size}x${size})`);
}
