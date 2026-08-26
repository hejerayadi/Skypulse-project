#!/usr/bin/env node
/**
 * Fetch the sensor models from Sketchfab and optimise them for the web.
 *
 *   bash:       SKETCHFAB_API_TOKEN=... npm run models
 *   PowerShell: $env:SKETCHFAB_API_TOKEN = "..."; npm run models
 *
 * The token is free: log in, then copy it from
 * https://sketchfab.com/settings/password. Sketchfab's download endpoint is
 * authenticated even for CC-licensed models, which is why the GLBs are not
 * checked in already.
 *
 * Downloads are raw artist exports -- one of these is 2.9M triangles and tens
 * of megabytes, which would stall the nodes page on a phone. Everything here
 * after the download exists to get each model under a budget it can be served
 * at: decimate, then shrink textures, then quantize.
 *
 * Quantization rather than Draco or Meshopt is deliberate: KHR_mesh_quantization
 * is decoded by three.js's GLTFLoader with no extra decoder to ship or host, so
 * the runtime stays a plain `useGLTF(url)`. Draco would be ~15% smaller and
 * cost a WASM decoder on the critical path of this one page.
 *
 * Uses only Node built-ins plus `npx @gltf-transform/cli`.
 */

import { execFileSync, execSync } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { inflateRawSync } from "node:zlib";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const OUT_DIR = join(ROOT, "public", "models");
const TMP_DIR = join(ROOT, "node_modules", ".cache", "sketchfab");

/**
 * Keep the output names in step with the URLs in `src/lib/sensor-models.ts` --
 * that table is what the scene reads, this one is only how the files arrive.
 *
 * `ratio` is the fraction of triangles to keep. 1 means the export is already
 * small enough; anything lower is a model whose detail is wasted at the size it
 * is drawn on a node's deck.
 */
const MODELS = [
  {
    out: "sensor-temperature.glb",
    uid: "c46a658784034c679e4907bbddad0093",
    title: "DHT22 Temperature Sensor Module — YouniqueĪdeaStudio (CC BY)",
    ratio: 1,
  },
  {
    out: "sensor-humidity.glb",
    uid: "8f84a03824d64782858dccc916aeff9a",
    title: "LOYUNO-L — LOYTEC (CC BY)",
    // 2,889,545 triangles of building-controller detail for a prop that is ~40
    // pixels tall. Tune upward if it reads as mush at close zoom.
    ratio: 0.01,
  },
  {
    out: "sensor-pressure.glb",
    uid: "0bbf931dcd084be197be9688685cb942",
    title: "Pressure sensor — AndreiV (CC BY)",
    ratio: 1,
  },
  {
    out: "sensor-accelerometer.glb",
    uid: "527e510d5c1449d69f49fe2f50051c94",
    title: "Snowboard GPS Accelerometer logger — bernebeer (CC BY)",
    ratio: 1,
  },
  {
    out: "sensor-sound.glb",
    uid: "a4d302a674f242b3b9f32be18ac463e8",
    title: "Sound Sensor for Arduino — arieffitrah (CC BY)",
    ratio: 1,
  },
  {
    out: "sensor-heartbeat.glb",
    uid: "97f8ba87dadd4ec28d29ff36055735c6",
    title: "Heartbeat - Version 3 — Apex Trace (CC BY-NC-SA)",
    ratio: 1,
  },
];

/** A model over this is a bug in the pipeline, not a heavy model. */
const SIZE_BUDGET_MB = 3;

const token = process.env.SKETCHFAB_API_TOKEN;
if (!token) {
  console.error(
    "SKETCHFAB_API_TOKEN is not set.\n" +
      "Get one at https://sketchfab.com/settings/password, then:\n" +
      '  PowerShell:  $env:SKETCHFAB_API_TOKEN = "xxxx"; npm run models\n' +
      "  bash:        SKETCHFAB_API_TOKEN=xxxx npm run models",
  );
  process.exit(1);
}

/**
 * Run the glTF-Transform CLI.
 *
 * Windows makes this awkward twice over. Node has refused to spawn a `.cmd`
 * without a shell since the 2024 argument-injection fix -- `npx.cmd` alone dies
 * with EINVAL -- and the moment a shell is involved Node stops quoting
 * arguments, so paths containing spaces (this repository's own directory has
 * them) have to be quoted here. `cmd.exe` also reads `^` as an escape, which is
 * why the version is pinned to a major rather than written as a `^4` range.
 */
const WINDOWS = process.platform === "win32";

const shellQuote = (arg) => (/[\s&|<>^()"]/.test(arg) ? `"${arg.replace(/"/g, '\\"')}"` : arg);

function gltfTransform(args) {
  const argv = ["--yes", "@gltf-transform/cli@4", ...args];

  if (!WINDOWS) {
    execFileSync("npx", argv, { stdio: "inherit" });
    return;
  }

  // Quoted and joined by hand rather than handed to `execFileSync` with
  // `shell: true`, which warns (DEP0190) that it concatenates without escaping.
  // Concatenating is what we want; doing it here is what makes it safe.
  execSync([`npx.cmd`, ...argv].map(shellQuote).join(" "), { stdio: "inherit" });
}

const mb = (bytes) => `${(bytes / 1e6).toFixed(2)} MB`;

/**
 * Extract a zip using only `node:zlib`.
 *
 * Shelling out to `tar` looked simpler until you notice which `tar` you get:
 * Windows and macOS ship bsdtar, which reads zips, but a Git Bash or MSYS shell
 * puts GNU tar first on PATH, and GNU tar cannot. Rather than have the script
 * work or not depending on the terminal it was launched from, this walks the
 * central directory itself -- the format is fixed and this is ~50 lines.
 */
function unzip(archive, dest) {
  const buffer = readFileSync(archive);

  // End-of-central-directory record, scanned backwards past any trailing comment.
  let eocd = -1;
  for (let i = buffer.length - 22; i >= 0 && i > buffer.length - 22 - 0xffff; i--) {
    if (buffer.readUInt32LE(i) === 0x06054b50) {
      eocd = i;
      break;
    }
  }
  if (eocd < 0) throw new Error("not a zip archive");

  const entries = buffer.readUInt16LE(eocd + 10);
  let offset = buffer.readUInt32LE(eocd + 16);
  if (offset === 0xffffffff) throw new Error("zip64 archive — extract it by hand");

  for (let n = 0; n < entries; n++) {
    if (buffer.readUInt32LE(offset) !== 0x02014b50) throw new Error("corrupt central directory");

    const method = buffer.readUInt16LE(offset + 10);
    const compressedSize = buffer.readUInt32LE(offset + 20);
    const nameLength = buffer.readUInt16LE(offset + 28);
    const extraLength = buffer.readUInt16LE(offset + 30);
    const commentLength = buffer.readUInt16LE(offset + 32);
    const localOffset = buffer.readUInt32LE(offset + 42);
    const name = buffer.toString("utf8", offset + 46, offset + 46 + nameLength);
    offset += 46 + nameLength + extraLength + commentLength;

    if (name.endsWith("/")) continue;

    // Entry paths come from a third party, so refuse anything that escapes the
    // work directory rather than trusting the archive.
    const target = join(dest, name.replace(/\\/g, "/"));
    if (!resolve(target).startsWith(resolve(dest))) throw new Error(`unsafe entry path: ${name}`);

    // The local header repeats name and extra with its own lengths, which need
    // not match the central directory's.
    const start =
      localOffset +
      30 +
      buffer.readUInt16LE(localOffset + 26) +
      buffer.readUInt16LE(localOffset + 28);
    const raw = buffer.subarray(start, start + compressedSize);

    if (method !== 0 && method !== 8) throw new Error(`unsupported compression method ${method}`);

    mkdirSync(dirname(target), { recursive: true });
    writeFileSync(target, method === 0 ? raw : inflateRawSync(raw));
  }
}

/**
 * Optimise a model, degrading rather than failing.
 *
 * Texture work goes through sharp/libvips, which rejects images in colour
 * spaces it does not model -- the DHT22's textures declare a 32-bit
 * interpretation and blow the whole pass up. That is a texture problem, not a
 * reason to ship no model, so the geometry passes run again without it.
 *
 * `--texture-compress false` is explicit because `optimize` otherwise defaults
 * to `auto`, which calls straight back into sharp.
 *
 * @returns what happened to the textures, for the log line.
 */
function optimize(source, out) {
  // Simplification, where used, already ran; repeating it here would decimate twice.
  const base = ["optimize", source, out, "--compress", "quantize", "--simplify", "false"];

  try {
    gltfTransform([...base, "--texture-compress", "webp", "--texture-size", "1024"]);
    return "webp textures";
  } catch {
    console.warn("  texture compression failed — retrying with the originals");
    gltfTransform([...base, "--texture-compress", "false"]);
    return "original textures";
  }
}

/** Artist exports nest the payload arbitrarily deep; find the scene wherever it is. */
function findScene(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      const found = findScene(path);
      if (found) return found;
    } else if (/\.(gltf|glb)$/i.test(entry.name)) {
      return path;
    }
  }
  return null;
}

async function fetchModel(model) {
  console.log(`\n=== ${model.out} — ${model.title}`);

  const meta = await fetch(`https://api.sketchfab.com/v3/models/${model.uid}/download`, {
    headers: { Authorization: `Token ${token}` },
  });

  if (!meta.ok) {
    // 403 here almost always means the model is not licensed for download
    // (Sketchfab Standard), which no token can unlock.
    throw new Error(`download request failed: ${meta.status} ${await meta.text()}`);
  }

  const { gltf } = await meta.json();
  if (!gltf?.url) throw new Error("response carried no glTF archive");

  const work = join(TMP_DIR, model.uid);
  rmSync(work, { recursive: true, force: true });
  mkdirSync(work, { recursive: true });

  const archive = join(work, "model.zip");
  const payload = await fetch(gltf.url);
  if (!payload.ok) throw new Error(`archive fetch failed: ${payload.status}`);
  writeFileSync(archive, Buffer.from(await payload.arrayBuffer()));
  console.log(`  downloaded ${mb(statSync(archive).size)}`);

  unzip(archive, work);

  const scene = findScene(work);
  if (!scene) throw new Error("no .gltf or .glb inside the archive");

  const out = join(OUT_DIR, model.out);

  // Decimation runs on its own so the ratio is explicit per model; `optimize`
  // only offers an error tolerance, which is the wrong control when the goal is
  // "cut this to 1% and accept whatever it looks like".
  let source = scene;
  if (model.ratio < 1) {
    source = join(work, "simplified.glb");
    gltfTransform(["simplify", scene, source, "--ratio", String(model.ratio), "--error", "0.01"]);
  }

  const textures = optimize(source, out);

  rmSync(work, { recursive: true, force: true });

  const size = statSync(out).size;
  const over = size > SIZE_BUDGET_MB * 1e6;
  console.log(`  ${over ? "!!" : "ok"} ${model.out} — ${mb(size)}, ${textures}`);
  return { ...model, size, over };
}

mkdirSync(OUT_DIR, { recursive: true });
mkdirSync(TMP_DIR, { recursive: true });

const results = [];
for (const model of MODELS) {
  if (existsSync(join(OUT_DIR, model.out)) && !process.argv.includes("--force")) {
    console.log(`skip ${model.out} (already fetched — pass --force to redo)`);
    continue;
  }
  try {
    results.push(await fetchModel(model));
  } catch (error) {
    // One unavailable model must not block the other five: the scene falls back
    // to a primitive for whichever file is missing.
    console.error(`  FAILED ${model.out}: ${error.message}`);
  }
}

const total = results.reduce((sum, r) => sum + r.size, 0);
console.log(`\n${results.length}/${MODELS.length} fetched, ${mb(total)} total`);

if (results.some((r) => r.over)) {
  console.warn(`Some models exceed the ${SIZE_BUDGET_MB} MB budget — lower their ratio.`);
}
