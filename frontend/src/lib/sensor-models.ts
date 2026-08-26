import type { NodeStatus } from "@/lib/nodes";

/**
 * Which physical sensors each node carries, and the 3D model that stands in for
 * each one on its pad.
 *
 * The station used to draw a node as a coloured primitive whose shape encoded
 * its role. That told you four nodes were different but not *how*, so this
 * table swaps the primitives for the actual hardware. The primitives survive as
 * the fallback -- see `shape` -- because a model that is still streaming, or
 * whose GLB is not in the repo, must not leave a hole in the scene.
 */

export type SensorKind =
  "temperature" | "humidity" | "pressure" | "accelerometer" | "sound" | "heartbeat";

/** Drawn while a model streams in, and instead of it when its GLB is absent. */
export type SensorShape = "box" | "octahedron" | "cylinder" | "sphere";

export type ModelCredit = {
  title: string;
  author: string;
  authorUrl: string;
  modelUrl: string;
  /** Spelled out rather than a slug: this string is the attribution notice. */
  license: string;
};

export type SensorModel = {
  label: string;
  /**
   * GLB under `public/models`, or null to always draw the primitive.
   *
   * Files are produced by `scripts/fetch-sensor-models.mjs`; a URL that 404s is
   * survivable -- the loader falls back to `shape` -- so this may point at a
   * file that has not been fetched yet.
   */
  url: string | null;
  /** Longest axis in world units after normalisation, before the rig scales it. */
  footprint: number;
  /** Rotation about Y, radians. Exports face wherever the artist left them. */
  yaw: number;
  /**
   * Rotation about X, radians. Board-shaped sensors are exported lying flat, and
   * normalising by the longest axis then squashes them into something thinner
   * than the deck they stand on; tilting one up turns it back into a panel.
   * Applied before the model is measured, so it still rests on the deck.
   */
  pitch: number;
  shape: SensorShape;
  /** CC-BY and friends require a visible notice; Station3D renders these. */
  credit: ModelCredit | null;
  /** Shown alongside the credit when the chosen model is not the obvious one. */
  note?: string;
};

export const SENSOR_MODELS: Record<SensorKind, SensorModel> = {
  temperature: {
    label: "Temperature",
    url: "/models/sensor-temperature.glb",
    footprint: 0.8,
    yaw: 0,
    pitch: 0,
    shape: "box",
    credit: {
      title: "DHT22 Temperature Sensor Module",
      author: "YouniqueĪdeaStudio",
      authorUrl: "https://sketchfab.com/sinnervoncrawsz",
      modelUrl:
        "https://sketchfab.com/3d-models/dht22-temperature-sensor-module-c46a658784034c679e4907bbddad0093",
      license: "CC BY 4.0",
    },
    // VIS-All-3D's "Temperature-Sensor-2-Module" is the better-looking match but
    // carries the Sketchfab Standard licence: embeddable in an iframe, not
    // downloadable, so it cannot enter a three.js scene. This DHT22 is the
    // closest CC-BY equivalent and is the part an IoT node would really carry.
    note: "Stand-in for VIS-All-3D's module, which is embed-only",
  },
  humidity: {
    label: "Humidity",
    url: "/models/sensor-humidity.glb",
    footprint: 0.78,
    yaw: 0,
    pitch: 0,
    shape: "cylinder",
    credit: {
      title: "LOYUNO-L",
      author: "LOYTEC",
      authorUrl: "https://sketchfab.com/LOYTEC",
      modelUrl: "https://sketchfab.com/3d-models/loyuno-l-8f84a03824d64782858dccc916aeff9a",
      license: "CC BY 4.0",
    },
  },
  pressure: {
    label: "Pressure",
    url: "/models/sensor-pressure.glb",
    footprint: 0.8,
    yaw: 0,
    pitch: -Math.PI / 2,
    shape: "octahedron",
    credit: {
      title: "Pressure sensor",
      author: "AndreiV",
      authorUrl: "https://sketchfab.com/vav206",
      modelUrl: "https://sketchfab.com/3d-models/pressure-sensor-0bbf931dcd084be197be9688685cb942",
      license: "CC BY 4.0",
    },
    // Same story as temperature: VIS-All-3D's "Pressure-Sensor-Module" is
    // Sketchfab Standard, so it is embed-only and cannot be loaded here.
    note: "Stand-in for VIS-All-3D's module, which is embed-only",
  },
  accelerometer: {
    label: "Accelerometer",
    url: "/models/sensor-accelerometer.glb",
    footprint: 0.85,
    yaw: 0,
    pitch: 0,
    shape: "octahedron",
    credit: {
      title: "Snowboard GPS Accelerometer logger",
      author: "bernebeer",
      authorUrl: "https://sketchfab.com/bernebeer",
      modelUrl:
        "https://sketchfab.com/3d-models/snowboard-gps-accelerometer-logger-527e510d5c1449d69f49fe2f50051c94",
      license: "CC BY 4.0",
    },
  },
  sound: {
    label: "Sound level",
    url: "/models/sensor-sound.glb",
    footprint: 0.85,
    yaw: 0,
    pitch: 0,
    shape: "cylinder",
    credit: {
      title: "Sound Sensor for Arduino",
      author: "arieffitrah",
      authorUrl: "https://sketchfab.com/arieffitrah",
      modelUrl:
        "https://sketchfab.com/3d-models/sound-sensor-for-arduino-a4d302a674f242b3b9f32be18ac463e8",
      license: "CC BY 4.0",
    },
  },
  heartbeat: {
    label: "Heartbeat",
    url: "/models/sensor-heartbeat.glb",
    footprint: 0.8,
    yaw: 0,
    pitch: 0,
    shape: "sphere",
    credit: {
      title: "Heartbeat - Version 3",
      author: "Apex Trace",
      authorUrl: "https://sketchfab.com/apextrace",
      modelUrl:
        "https://sketchfab.com/3d-models/heartbeat-version-3-97f8ba87dadd4ec28d29ff36055735c6",
      license: "CC BY-NC-SA 4.0",
    },
    // NonCommercial: fine for an internship demo, not for a product. If SkyPulse
    // is ever shipped commercially this model has to be replaced.
    note: "NonCommercial licence — replace before any commercial use",
  },
};

/**
 * The antenna predates this table and arrived without a recorded source.
 * Fill this in -- CC-BY requires the notice -- or drop the model.
 */
export const ANTENNA_CREDIT: ModelCredit | null = null;

/**
 * Sensors per role, keyed by the same `last_event_type` as NODE_ROLE and listing
 * the same instruments in the same order. An environment node really is three
 * separate parts, so its pad shows three.
 */
export const ROLE_SENSORS: Record<string, SensorKind[]> = {
  environment: ["temperature", "humidity", "pressure"],
  motion: ["accelerometer"],
  acoustic: ["sound"],
  status: ["heartbeat"],
};

/**
 * Empty for a node that has not sent a packet yet: its role is unknown, and
 * guessing at one would put an instrument on the deck that the node may not
 * carry. The scene draws a blank crate for this case instead.
 */
const UNKNOWN_SENSORS: SensorKind[] = [];

export function sensorsForNode(node: NodeStatus): SensorKind[] {
  return ROLE_SENSORS[node.last_event_type ?? ""] ?? UNKNOWN_SENSORS;
}

/** Every distinct model the current fleet puts on screen. */
export function sensorKindsInFleet(nodes: NodeStatus[]): SensorKind[] {
  const kinds = new Set<SensorKind>();
  for (const node of nodes) {
    for (const kind of sensorsForNode(node)) kinds.add(kind);
  }
  return [...kinds];
}

/** Attribution for exactly the models on screen, deduplicated by model URL. */
export function creditsInFleet(nodes: NodeStatus[]): Array<ModelCredit & { note?: string }> {
  const credits = new Map<string, ModelCredit & { note?: string }>();

  for (const kind of sensorKindsInFleet(nodes)) {
    const model = SENSOR_MODELS[kind];
    if (model.credit) credits.set(model.credit.modelUrl, { ...model.credit, note: model.note });
  }

  return [...credits.values()];
}
