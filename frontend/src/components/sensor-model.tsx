import { Component, Suspense, useMemo, type ReactNode } from "react";
import { Clone, useGLTF } from "@react-three/drei";
import * as THREE from "three";
import { SENSOR_MODELS, type SensorKind, type SensorShape } from "@/lib/sensor-models";

/**
 * Loading one sensor model.
 *
 * Three things make this cheap enough to put on every pad:
 *
 * 1. Each GLB is fetched once. `useGLTF` is backed by a global suspense cache,
 *    so eight nodes sharing a model share one network request and one parse.
 * 2. Each GLB is *normalised* once, into a template cached per URL. Measuring a
 *    bounding box walks the whole mesh, which is far too expensive to redo per
 *    node, per mount.
 * 3. Each node then gets a `<Clone>` of that template. Cloning copies the node
 *    graph but keeps geometries and materials by reference, so N nodes cost N
 *    draw calls over one set of GPU buffers rather than N uploads.
 */

/** Normalised, ready-to-clone model per URL. Module scope so it outlives the
 *  scene being unmounted and remounted -- e.g. when the route is revisited. */
const templates = new Map<string, THREE.Object3D>();

function templateFor(
  url: string,
  scene: THREE.Object3D,
  { footprint, yaw, pitch }: { footprint: number; yaw: number; pitch: number },
): THREE.Object3D {
  const cached = templates.get(url);
  if (cached) return cached;

  // Clone before touching anything: `scene` is the cache's copy, handed to every
  // other consumer of this URL.
  const model = scene.clone(true);

  // Orient first, measure second. Tilting a model after it has been grounded
  // would swing half of it below the deck; measuring the oriented model is what
  // keeps "resting on the deck" true whatever `pitch` is set to.
  model.rotation.set(pitch, yaw, 0);
  model.updateMatrixWorld(true);

  // Scale by measurement rather than by trusting the export's units -- these six
  // arrive at scales from 800 to 4,000,000 units across. Uniform scale commutes
  // with rotation, so the box measured here stays valid once it is applied.
  const box = new THREE.Box3().setFromObject(model);
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const scale = footprint / Math.max(size.x, size.y, size.z);

  model.scale.setScalar(scale);
  // Centred on the mast, resting on the deck rather than sunk into it.
  model.position.set(-center.x * scale, -box.min.y * scale, -center.z * scale);

  model.traverse((child) => {
    if ((child as THREE.Mesh).isMesh) {
      child.castShadow = true;
      child.receiveShadow = true;
    }
  });

  const template = new THREE.Group();
  template.add(model);
  templates.set(url, template);
  return template;
}

/**
 * The old role primitives, kept as the fallback.
 *
 * A pad is never empty: this is what shows while the GLB is in flight, and what
 * stays if the file is missing entirely.
 */
function PrimitiveSensor({
  shape,
  color,
  size,
}: {
  shape: SensorShape;
  color: string;
  size: number;
}) {
  const material = <meshStandardMaterial color={color} roughness={0.45} metalness={0.15} />;
  const half = size / 2;

  switch (shape) {
    case "octahedron":
      return (
        <mesh castShadow position={[0, half, 0]}>
          <octahedronGeometry args={[half]} />
          {material}
        </mesh>
      );
    case "cylinder":
      return (
        <mesh castShadow position={[0, half, 0]}>
          <cylinderGeometry args={[half * 0.72, half * 0.72, size, 20]} />
          {material}
        </mesh>
      );
    case "sphere":
      return (
        <mesh castShadow position={[0, half, 0]}>
          <sphereGeometry args={[half, 24, 20]} />
          {material}
        </mesh>
      );
    default:
      return (
        <mesh castShadow position={[0, half, 0]}>
          <boxGeometry args={[size * 0.8, size * 0.8, size * 0.8]} />
          {material}
        </mesh>
      );
  }
}

/**
 * Suspense covers a model that is *slow*; this covers one that never arrives.
 *
 * Two of the six models are licensed embed-only and a third may be swapped out,
 * so a 404 under `public/models` is an expected state, not a broken build. The
 * boundary keeps that failure local to one sensor: the rest of the station, and
 * the WebGL context, carry on.
 */
class ModelBoundary extends Component<
  { fallback: ReactNode; children: ReactNode; url: string },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch() {
    console.warn(`[station] ${this.props.url} did not load — drawing the placeholder instead.`);
  }

  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

function LoadedSensor({ url, kind }: { url: string; kind: SensorKind }) {
  const { scene } = useGLTF(url);
  const { footprint, yaw, pitch } = SENSOR_MODELS[kind];
  const template = useMemo(
    () => templateFor(url, scene, { footprint, yaw, pitch }),
    [url, scene, footprint, yaw, pitch],
  );

  return <Clone object={template} />;
}

export function SensorModel({ kind, color }: { kind: SensorKind; color: string }) {
  const model = SENSOR_MODELS[kind];
  const fallback = <PrimitiveSensor shape={model.shape} color={color} size={model.footprint} />;

  if (!model.url) return fallback;

  return (
    <ModelBoundary url={model.url} fallback={fallback}>
      <Suspense fallback={fallback}>
        <LoadedSensor url={model.url} kind={kind} />
      </Suspense>
    </ModelBoundary>
  );
}
