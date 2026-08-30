import { useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame, type ThreeEvent } from "@react-three/fiber";
import { ContactShadows, Grid, Html, OrbitControls, useGLTF } from "@react-three/drei";
import * as THREE from "three";
import { useTheme } from "@/components/theme-provider";
import { NODE_STATE_STYLE, formatAge, nodeRole, type NodeStatus } from "@/lib/nodes";
import {
  ANTENNA_HEIGHT,
  DISH_FOCUS,
  placeNodes,
  shortestTurn,
  type NodePlacement,
} from "@/lib/station-layout";
import {
  RF_LABEL_STYLE,
  formatConfidence,
  formatEta,
  thermalUrgency,
  windIntensity,
  type RFPrediction,
} from "@/lib/rf-fault";

const MODEL_URL = "/models/antenna.glb";

/**
 * Rotation applied to the dish so that "azimuth 0" points down +X, matching the
 * pad angles in station-layout.
 *
 * The model arrives with no documented boresight -- it is an FBX export whose
 * dish was authored facing wherever the artist left it -- so this is a
 * by-eye calibration. If the dish appears to lag or lead the node it is
 * tracking by a constant amount, adjust this one value; nothing else depends
 * on the model's internal orientation.
 */
const DISH_AZIMUTH_OFFSET = 0;

/** How fast the dish slews, in radians per second. Slow enough to read as
 *  heavy machinery rather than a spinning top. */
const SLEW_SPEED = 1.1;

/** Seconds a telemetry pulse takes to travel from its pad to the dish. */
const PULSE_TRAVEL = 1.15;

// ---------------------------------------------------------------------------
// Weather, driven by the RF fault model
//
// Everything below reacts to a prediction rather than running all the time. A
// scene that always sways teaches the operator to ignore the swaying, so the
// idle station is deliberately dead still and every bit of motion here means
// the model saw something.
// ---------------------------------------------------------------------------

/** Widest the dish is pushed off its target by a full-strength gust, radians. */
const MAX_WIND_YAW = 0.3;
/** How far the mast leans downwind at full strength, radians. */
const MAX_WIND_LEAN = 0.03;
/** Buffeting on the dish itself — small, or it reads as a mechanical fault. */
const MAX_WIND_NOD = 0.022;

/** Wind streaks. Enough to read as air moving, few enough to stay cheap. */
const STREAK_COUNT = 72;
/** How far a streak travels before it wraps back to the upwind edge. */
const STREAK_SPAN = 52;

/** Where the warning lamp stands: clear of the mast, inside the pad ring. */
const LAMP_POSITION: [number, number, number] = [3.2, 0, 0];
const LAMP_HEIGHT = 1.2;

/**
 * Gusty pseudo-noise in roughly -1..1.
 *
 * Three sines at incommensurable rates rather than a real noise function: it
 * never repeats on any timescale a viewer will notice, costs three multiplies,
 * and needs no texture or table. The phase argument decorrelates the yaw, the
 * lean and the nod so they do not all peak on the same frame.
 */
function gust(t: number, phase: number): number {
  return (
    0.62 * Math.sin(t * 1.7 + phase) +
    0.28 * Math.sin(t * 3.3 + phase * 2.1) +
    0.1 * Math.sin(t * 7.1 + phase * 3.7)
  );
}

/** Wind bearing, veering slowly so the scene does not look like a fan. */
function windYaw(t: number): number {
  return 0.9 + 0.35 * Math.sin(t * 0.06);
}

/**
 * Ease a value towards a target, frame-rate independent.
 *
 * Used on every wind and lamp intensity: predictions arrive in steps, and
 * cutting straight to a new one makes the antenna jump and the lamp pop.
 */
function approach(current: number, target: number, delta: number, rate = 1.6): number {
  return current + (target - current) * Math.min(1, delta * rate);
}

useGLTF.preload(MODEL_URL);

// ---------------------------------------------------------------------------
// Antenna
// ---------------------------------------------------------------------------

function Antenna({ targetAzimuth, wind }: { targetAzimuth: number; wind: number }) {
  const { scene } = useGLTF(MODEL_URL);

  // The slew target the dish is tracking, kept apart from what it is actually
  // showing. Wind is added on top when the frame is drawn, so a gust pushes the
  // dish off aim without the tracking loop chasing its own buffeting.
  const aim = useRef(0);
  const strength = useRef(0);

  const { root, pivot } = useMemo(() => {
    // Clone so a hot reload -- or a second mount -- never re-parents the dish
    // out of the cached scene that useGLTF hands every consumer.
    const model = scene.clone(true);

    // Normalise to a known height instead of trusting the export's units. This
    // model happens to arrive in centimetres via two nested Sketchfab wrapper
    // nodes; measuring the result is cheaper than reasoning about that chain,
    // and it survives swapping the model for a different one.
    const box = new THREE.Box3().setFromObject(model);
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    const scale = ANTENNA_HEIGHT / size.y;

    model.scale.setScalar(scale);
    // Mast centred on the origin, feet on the ground plane.
    model.position.set(-center.x * scale, -box.min.y * scale, -center.z * scale);

    model.traverse((child) => {
      if ((child as THREE.Mesh).isMesh) {
        child.castShadow = true;
        child.receiveShadow = true;
      }
    });

    const root = new THREE.Group();
    root.add(model);

    // A pivot sitting on the mast axis. Rotating the dish's own node would
    // spin it about whichever permuted local axis the FBX left behind, so
    // instead the dish is re-parented under a group we control. `attach`
    // preserves its world transform, so nothing visibly moves.
    const pivot = new THREE.Group();
    root.add(pivot);

    root.updateMatrixWorld(true);

    const dish = findDish(model);
    if (dish) pivot.attach(dish);

    return { root, pivot };
  }, [scene]);

  useFrame((state, delta) => {
    const turn = shortestTurn(aim.current, targetAzimuth + DISH_AZIMUTH_OFFSET);
    // Ease toward the target but cap the step, so a re-target reads as the dish
    // slewing rather than snapping.
    aim.current += THREE.MathUtils.clamp(turn, -SLEW_SPEED * delta, SLEW_SPEED * delta);

    strength.current = approach(strength.current, wind, delta);
    const t = state.clock.elapsedTime;
    const gustiness = strength.current;

    pivot.rotation.y = aim.current + gust(t, 0) * gustiness * MAX_WIND_YAW;
    pivot.rotation.x = gust(t, 2.4) * gustiness * MAX_WIND_NOD;

    // The whole assembly leans away from the wind, about its feet: `root` sits
    // at the origin with the mast standing on it, so rotating it is the mast
    // flexing rather than the model tipping over.
    const yaw = windYaw(t);
    const lean = gustiness * MAX_WIND_LEAN * (0.6 + 0.4 * gust(t, 1.3));
    root.rotation.x = lean * Math.cos(yaw);
    root.rotation.z = -lean * Math.sin(yaw);
  });

  return <primitive object={root} />;
}

/**
 * The dish is the topmost part of the assembly.
 *
 * Picked by height rather than by mesh name: the export's names
 * (`AM227_009.002_...`) carry no meaning and would not survive re-exporting
 * the model.
 */
function findDish(model: THREE.Object3D): THREE.Object3D | null {
  let best: THREE.Object3D | null = null;
  let bestY = -Infinity;

  model.traverse((child) => {
    if (!(child as THREE.Mesh).isMesh) return;
    const centreY = new THREE.Box3().setFromObject(child).getCenter(new THREE.Vector3()).y;
    if (centreY > bestY) {
      bestY = centreY;
      best = child;
    }
  });

  // Re-parent the mesh's parent when it is a plain wrapper, so the dish keeps
  // whatever local offset the export gave it.
  const mesh = best as THREE.Object3D | null;
  if (mesh?.parent && mesh.parent.children.length === 1 && mesh.parent.type === "Object3D") {
    return mesh.parent;
  }
  return mesh;
}

// ---------------------------------------------------------------------------
// Wind
// ---------------------------------------------------------------------------

/**
 * Streaks of air blowing across the station.
 *
 * One InstancedMesh rather than 72 meshes: this redraws every frame while the
 * wind is up, and 72 draw calls a frame would cost more than everything else in
 * the scene put together.
 *
 * The streaks are the only thing in the view that shows wind *direction* — the
 * dish being pushed about tells you there is wind, not which way it is coming
 * from — so they share windYaw() with the antenna rather than drifting on their
 * own.
 */
function WindField({ intensity, color }: { intensity: number; color: string }) {
  const meshRef = useRef<THREE.InstancedMesh>(null);
  const materialRef = useRef<THREE.MeshBasicMaterial>(null);
  const strength = useRef(0);

  const dummy = useMemo(() => new THREE.Object3D(), []);

  // Fixed per mount: re-randomising on a re-render would make the whole field
  // twitch every time a prediction arrives.
  const streaks = useMemo(
    () =>
      Array.from({ length: STREAK_COUNT }, () => ({
        lateral: (Math.random() - 0.5) * STREAK_SPAN * 0.8,
        height: 0.35 + Math.random() * 13,
        speed: 6 + Math.random() * 11,
        offset: Math.random() * STREAK_SPAN,
        length: 0.8 + Math.random() * 2.6,
        bob: Math.random() * Math.PI * 2,
      })),
    [],
  );

  useFrame((state, delta) => {
    const mesh = meshRef.current;
    if (!mesh) return;

    strength.current = approach(strength.current, intensity, delta);
    const gustiness = strength.current;

    // Fully faded out: stop paying for the matrix rebuild entirely.
    mesh.visible = gustiness > 0.01;
    if (materialRef.current) materialRef.current.opacity = gustiness * 0.55;
    if (!mesh.visible) return;

    const t = state.clock.elapsedTime;
    const yaw = windYaw(t);
    const dirX = Math.sin(yaw);
    const dirZ = Math.cos(yaw);

    streaks.forEach((streak, index) => {
      // Wrap within the span so the field is endless without ever growing.
      const travel =
        ((streak.offset + t * streak.speed * (0.35 + 0.65 * gustiness)) % STREAK_SPAN) -
        STREAK_SPAN / 2;

      dummy.position.set(
        dirX * travel + dirZ * streak.lateral,
        streak.height + Math.sin(t * 1.4 + streak.bob) * 0.25,
        dirZ * travel - dirX * streak.lateral,
      );
      // The rod is modelled along local +Z, so yawing it by the wind bearing is
      // all the orientation it needs.
      dummy.rotation.set(0, yaw, 0);
      dummy.scale.set(1, 1, streak.length * (0.4 + gustiness));
      dummy.updateMatrix();
      mesh.setMatrixAt(index, dummy.matrix);
    });

    mesh.instanceMatrix.needsUpdate = true;
  });

  return (
    <instancedMesh ref={meshRef} args={[undefined, undefined, STREAK_COUNT]} frustumCulled={false}>
      <boxGeometry args={[0.035, 0.035, 1]} />
      <meshBasicMaterial
        ref={materialRef}
        color={color}
        transparent
        opacity={0}
        // Streaks are atmosphere, not geometry: they must not occlude the
        // station or each other.
        depthWrite={false}
        toneMapped={false}
      />
    </instancedMesh>
  );
}

/**
 * The dish's own read-out while wind is loading it.
 *
 * Anchored to the antenna rather than to the corner of the canvas, because the
 * thing it is explaining is the antenna moving.
 */
function WindBadge({ prediction }: { prediction: RFPrediction }) {
  const style = RF_LABEL_STYLE.wind;

  return (
    <Html
      position={[0, ANTENNA_HEIGHT * 1.06, 0]}
      center
      distanceFactor={20}
      style={{ pointerEvents: "none" }}
    >
      <div
        className="whitespace-nowrap rounded-lg border px-2.5 py-1.5 text-center backdrop-blur-sm"
        style={{
          borderColor: style.color,
          background: "rgba(10,12,16,.82)",
          color: "#f8fafc",
          boxShadow: `0 0 18px ${style.color}55`,
        }}
      >
        <div
          className="text-[13px] font-semibold uppercase leading-none tracking-wide"
          style={{ color: style.color }}
        >
          Wind on the antenna
        </div>
        <div className="mt-1.5 text-[11px] leading-none opacity-85">
          {formatConfidence(prediction.confidence)} confidence ·{" "}
          {prediction.reading.wind_speed_mps.toFixed(1)} m/s
        </div>
        <div className="mt-1 text-[11px] leading-none opacity-70">
          Off boresight {prediction.reading.antenna_pointing_error_deg.toFixed(1)}° ·{" "}
          {formatEta(prediction.eta_seconds)}
        </div>
      </div>
    </Html>
  );
}

// ---------------------------------------------------------------------------
// Overheat warning lamp
// ---------------------------------------------------------------------------

/**
 * A flashing beacon on a post beside the mast.
 *
 * It is a real light, not just an emissive ball: the point of a warning lamp is
 * that it throws colour onto the hardware around it, so the station itself
 * turns red and the alert is visible from any camera angle rather than only
 * when the lamp is in frame.
 */
function WarningLamp({
  active,
  urgency,
  color,
  prediction,
}: {
  active: boolean;
  urgency: number;
  color: string;
  prediction: RFPrediction | null;
}) {
  const lampRef = useRef<THREE.MeshStandardMaterial>(null);
  const haloRef = useRef<THREE.MeshBasicMaterial>(null);
  const ringRef = useRef<THREE.MeshBasicMaterial>(null);
  const lightRef = useRef<THREE.PointLight>(null);

  const level = useRef(0);

  useFrame((state, delta) => {
    // Faded rather than switched, so an alert clearing does not look like the
    // lamp burning out.
    level.current = approach(level.current, active ? 1 : 0, delta, 2.4);
    const lit = level.current;

    // 0.8 Hz when there is time to act, up to 3 Hz when it is imminent. Cubed
    // so the lamp reads as flashing — a plain sine is a lamp fading, which is
    // a different signal.
    const rate = 0.8 + urgency * 2.2;
    const pulse = Math.pow(0.5 + 0.5 * Math.sin(state.clock.elapsedTime * rate * Math.PI * 2), 3);

    if (lampRef.current) {
      lampRef.current.emissiveIntensity = 0.08 + lit * pulse * (2.4 + urgency * 3.4);
    }
    if (haloRef.current) haloRef.current.opacity = lit * pulse * 0.32;
    if (ringRef.current) ringRef.current.opacity = lit * (0.1 + pulse * 0.35);
    if (lightRef.current) lightRef.current.intensity = lit * pulse * (14 + urgency * 26);
  });

  return (
    <group position={LAMP_POSITION}>
      {/* Base */}
      <mesh receiveShadow position={[0, 0.05, 0]}>
        <cylinderGeometry args={[0.34, 0.4, 0.1, 20]} />
        <meshStandardMaterial color="#4b5563" roughness={0.85} />
      </mesh>

      {/* Post */}
      <mesh castShadow position={[0, LAMP_HEIGHT / 2, 0]}>
        <cylinderGeometry args={[0.045, 0.055, LAMP_HEIGHT, 10]} />
        <meshStandardMaterial color="#6b7280" roughness={0.6} metalness={0.4} />
      </mesh>

      {/* Lamp */}
      <mesh position={[0, LAMP_HEIGHT, 0]}>
        <sphereGeometry args={[0.19, 20, 16]} />
        <meshStandardMaterial
          ref={lampRef}
          color={color}
          emissive={color}
          emissiveIntensity={0.08}
          roughness={0.35}
          toneMapped={false}
        />
      </mesh>

      {/* Halo — the glow around the bulb, which a bare emissive material does
          not produce without a bloom pass. */}
      <mesh position={[0, LAMP_HEIGHT, 0]}>
        <sphereGeometry args={[0.44, 16, 12]} />
        <meshBasicMaterial
          ref={haloRef}
          color={color}
          transparent
          opacity={0}
          depthWrite={false}
          toneMapped={false}
        />
      </mesh>

      {/* Light cast onto the station */}
      <pointLight
        ref={lightRef}
        position={[0, LAMP_HEIGHT, 0]}
        color={color}
        intensity={0}
        distance={16}
        decay={2}
      />

      {/* Pool of light on the ground */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.02, 0]}>
        <circleGeometry args={[1.9, 40]} />
        <meshBasicMaterial
          ref={ringRef}
          color={color}
          transparent
          opacity={0}
          depthWrite={false}
          toneMapped={false}
        />
      </mesh>

      {active && prediction && <LampBadge prediction={prediction} color={color} />}
    </group>
  );
}

function LampBadge({ prediction, color }: { prediction: RFPrediction; color: string }) {
  const style = RF_LABEL_STYLE[prediction.label];

  return (
    <Html
      position={[0, LAMP_HEIGHT + 0.85, 0]}
      center
      distanceFactor={14}
      style={{ pointerEvents: "none" }}
    >
      <div
        className="whitespace-nowrap rounded-lg border px-2.5 py-1.5 text-center backdrop-blur-sm"
        style={{
          borderColor: color,
          background: "rgba(10,12,16,.85)",
          color: "#f8fafc",
          boxShadow: `0 0 20px ${color}66`,
        }}
      >
        <div
          className="text-[13px] font-semibold uppercase leading-none tracking-wide"
          style={{ color }}
        >
          {style.short}
        </div>
        <div className="mt-1.5 text-[11px] leading-none opacity-85">
          {formatConfidence(prediction.confidence)} confidence ·{" "}
          {prediction.reading.sdr_temperature_c.toFixed(1)} °C
        </div>
        <div className="mt-1 text-[11px] font-medium leading-none" style={{ color }}>
          {formatEta(prediction.eta_seconds)}
        </div>
      </div>
    </Html>
  );
}

// ---------------------------------------------------------------------------
// Sensor nodes
//
// A node is a pad, a mast and a beacon -- no instrument models. What a node
// carries is already written on its label, and drawing it as well cost a GLB
// per role and put five detailed objects around a station whose subject is the
// antenna. The state is what has to read at a glance, and state was never in
// the instruments: it is the pad, the beacon and the selection ring.
// ---------------------------------------------------------------------------

/** Height of the mast the beacon sits on. */
const MAST_HEIGHT = 1.6;
/** Beacon, perched on the mast head. */
const BEACON_HEIGHT = MAST_HEIGHT + 0.16;
const BEACON_RADIUS = 0.2;

function SensorNode({
  placement,
  selected,
  onSelect,
}: {
  placement: NodePlacement;
  selected: boolean;
  onSelect: (nodeId: string) => void;
}) {
  const { node, position } = placement;
  const style = NODE_STATE_STYLE[node.state];
  const beaconRef = useRef<THREE.MeshStandardMaterial>(null);
  const [hovered, setHovered] = useState(false);

  useFrame((state) => {
    if (!beaconRef.current) return;
    // A healthy node breathes, a degraded one blinks harder and faster, an
    // offline one is simply dark -- readable without reading the label.
    const t = state.clock.elapsedTime;
    if (node.state === "offline") {
      beaconRef.current.emissiveIntensity = 0.05;
    } else if (node.state === "degraded") {
      beaconRef.current.emissiveIntensity = 1.1 + Math.sin(t * 6) * 0.9;
    } else {
      beaconRef.current.emissiveIntensity = 1.0 + Math.sin(t * 1.8) * 0.35;
    }
  });

  const handleOver = (event: ThreeEvent<PointerEvent>) => {
    event.stopPropagation();
    setHovered(true);
    document.body.style.cursor = "pointer";
  };

  const handleOut = () => {
    setHovered(false);
    document.body.style.cursor = "auto";
  };

  return (
    <group
      position={position}
      onClick={(event) => {
        event.stopPropagation();
        onSelect(node.node_id);
      }}
      onPointerOver={handleOver}
      onPointerOut={handleOut}
    >
      {/* Pad */}
      <mesh receiveShadow position={[0, 0.04, 0]}>
        <cylinderGeometry args={[0.95, 1.05, 0.08, 32]} />
        <meshStandardMaterial
          color={selected || hovered ? style.color : "#4b5563"}
          roughness={0.8}
        />
      </mesh>

      {/* Mast */}
      <mesh castShadow position={[0, MAST_HEIGHT / 2, 0]}>
        <cylinderGeometry args={[0.07, 0.09, MAST_HEIGHT, 12]} />
        <meshStandardMaterial color="#6b7280" roughness={0.6} metalness={0.4} />
      </mesh>

      {/* Beacon */}
      <mesh position={[0, BEACON_HEIGHT, 0]}>
        <sphereGeometry args={[BEACON_RADIUS, 20, 16]} />
        <meshStandardMaterial
          ref={beaconRef}
          color={style.color}
          emissive={style.color}
          emissiveIntensity={1}
          toneMapped={false}
        />
      </mesh>

      {/* Selection ring */}
      {selected && (
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.1, 0]}>
          <ringGeometry args={[1.25, 1.45, 48]} />
          <meshBasicMaterial
            color={style.color}
            transparent
            opacity={0.85}
            side={THREE.DoubleSide}
          />
        </mesh>
      )}

      <Html
        position={[0, BEACON_HEIGHT + 0.75, 0]}
        center
        distanceFactor={16}
        style={{ pointerEvents: "none" }}
      >
        <div
          className="whitespace-nowrap rounded-lg border px-2 py-1 text-center backdrop-blur-sm"
          style={{
            borderColor: selected ? style.color : "rgba(120,120,120,.4)",
            background: "rgba(10,12,16,.78)",
            color: "#f8fafc",
          }}
        >
          <div className="flex items-center gap-1.5 text-[13px] font-medium leading-none">
            <span
              className="inline-block h-[7px] w-[7px] rounded-full"
              style={{ background: style.color }}
            />
            {node.node_id}
          </div>
          <div className="mt-1 text-[10px] leading-none opacity-70">{nodeRole(node)}</div>
          <div className="mt-1 text-[10px] leading-none" style={{ color: style.color }}>
            {style.label} · {formatAge(node.seconds_since_seen)}
          </div>
        </div>
      </Html>
    </group>
  );
}

// ---------------------------------------------------------------------------
// Telemetry pulses
// ---------------------------------------------------------------------------

type Pulse = { id: number; from: THREE.Vector3; color: string; born: number };

function Pulses({ pulses, now }: { pulses: Pulse[]; now: () => number }) {
  const focus = useMemo(() => new THREE.Vector3(...DISH_FOCUS), []);
  const groupRef = useRef<THREE.Group>(null);

  useFrame(() => {
    const group = groupRef.current;
    if (!group) return;

    const t = now();
    group.children.forEach((child, index) => {
      const pulse = pulses[index];
      if (!pulse) return;

      const progress = Math.min((t - pulse.born) / PULSE_TRAVEL, 1);
      // Quadratic bezier with a lifted control point, so the packet arcs up to
      // the dish instead of clipping through the mast.
      const control = pulse.from
        .clone()
        .lerp(focus, 0.5)
        .setY(Math.max(pulse.from.y, focus.y) + 2.4);

      const inv = 1 - progress;
      child.position
        .copy(pulse.from)
        .multiplyScalar(inv * inv)
        .addScaledVector(control, 2 * inv * progress)
        .addScaledVector(focus, progress * progress);

      const scale = Math.sin(progress * Math.PI) * 0.9 + 0.15;
      child.scale.setScalar(scale);
    });
  });

  return (
    <group ref={groupRef}>
      {pulses.map((pulse) => (
        <mesh key={pulse.id}>
          <sphereGeometry args={[0.17, 16, 12]} />
          <meshBasicMaterial color={pulse.color} toneMapped={false} transparent opacity={0.95} />
        </mesh>
      ))}
    </group>
  );
}

// ---------------------------------------------------------------------------
// Scene
// ---------------------------------------------------------------------------

function Scene({
  nodes,
  selectedNodeId,
  onSelect,
  prediction,
}: {
  nodes: NodeStatus[];
  selectedNodeId: string | null;
  onSelect: (nodeId: string) => void;
  prediction: RFPrediction | null;
}) {
  const placements = useMemo(() => placeNodes(nodes), [nodes]);
  const [pulses, setPulses] = useState<Pulse[]>([]);

  const clockRef = useRef(0);
  const seenRef = useRef(new Map<string, string>());
  const pulseId = useRef(0);

  useFrame((state) => {
    const t = state.clock.elapsedTime;
    clockRef.current = t;

    // Pulses retire here rather than on a setTimeout. A timer owned by the poll
    // effect below gets cleared the next time the effect re-runs, so any pulse
    // still in flight at that moment would never be removed -- a leak that only
    // shows up once telemetry arrives faster than a pulse takes to fly.
    // Returning `current` unchanged is a no-op for React, so this is free on
    // the frames where nothing has expired.
    setPulses((current) =>
      current.some((pulse) => t - pulse.born > PULSE_TRAVEL)
        ? current.filter((pulse) => t - pulse.born <= PULSE_TRAVEL)
        : current,
    );
  });

  // A packet arriving is the only real "event" the API exposes: `last_seen`
  // changing between two polls means the node reported in. Comparing the
  // timestamp rather than `seconds_since_seen` avoids firing on the clock
  // simply advancing.
  useEffect(() => {
    const seen = seenRef.current;
    const fresh: Pulse[] = [];

    for (const placement of placements) {
      const { node } = placement;
      const previous = seen.get(node.node_id);
      seen.set(node.node_id, node.last_seen);

      // Skip the first sighting: on mount every node would fire at once.
      if (previous === undefined || previous === node.last_seen) continue;
      if (node.state === "offline") continue;

      fresh.push({
        id: pulseId.current++,
        from: new THREE.Vector3(placement.position[0], BEACON_HEIGHT, placement.position[2]),
        color: NODE_STATE_STYLE[node.state].color,
        born: clockRef.current,
      });
    }

    if (fresh.length === 0) return;
    setPulses((current) => [...current, ...fresh]);
  }, [placements]);

  // The dish points at whatever last spoke -- the selected node when the user
  // has picked one, otherwise the freshest reporter.
  const targetAzimuth = useMemo(() => {
    if (placements.length === 0) return 0;

    const selected = placements.find((p) => p.node.node_id === selectedNodeId);
    if (selected) return -selected.azimuth;

    const freshest = placements
      .filter((p) => p.node.state !== "offline")
      .sort((a, b) => a.node.seconds_since_seen - b.node.seconds_since_seen)[0];

    return -(freshest ?? placements[0]).azimuth;
  }, [placements, selectedNodeId]);

  // The model's verdict, turned into the three things the scene can express:
  // how hard the wind blows, whether the lamp is lit, and how fast it flashes.
  const wind = windIntensity(prediction);
  const urgency = thermalUrgency(prediction);
  const lampLabel = prediction?.label === "degraded" ? "degraded" : "overheating";
  const lampLit =
    prediction !== null &&
    prediction.severity !== "nominal" &&
    (prediction.label === "overheating" || prediction.label === "degraded");

  return (
    <>
      <ambientLight intensity={0.55} />
      <hemisphereLight args={["#cbd5f5", "#1f2937", 0.7]} />
      <directionalLight
        position={[12, 18, 8]}
        intensity={1.9}
        castShadow
        shadow-mapSize={[1024, 1024]}
        shadow-camera-left={-18}
        shadow-camera-right={18}
        shadow-camera-top={18}
        shadow-camera-bottom={-18}
      />
      <directionalLight position={[-10, 6, -12]} intensity={0.35} />

      <Antenna targetAzimuth={targetAzimuth} wind={wind} />

      <WindField intensity={wind} color={RF_LABEL_STYLE.wind.color} />
      {wind > 0 && prediction && <WindBadge prediction={prediction} />}

      <WarningLamp
        active={lampLit}
        urgency={urgency}
        color={RF_LABEL_STYLE[lampLabel].color}
        prediction={lampLit ? prediction : null}
      />

      {placements.map((placement) => (
        <SensorNode
          key={placement.node.node_id}
          placement={placement}
          selected={placement.node.node_id === selectedNodeId}
          onSelect={onSelect}
        />
      ))}

      <Pulses pulses={pulses} now={() => clockRef.current} />

      <ContactShadows position={[0, 0.01, 0]} opacity={0.45} scale={34} blur={2.2} far={14} />
      <Grid
        args={[60, 60]}
        cellSize={1}
        cellThickness={0.5}
        cellColor="#64748b"
        sectionSize={5}
        sectionThickness={1}
        sectionColor="#0ea5e9"
        fadeDistance={46}
        fadeStrength={1.4}
        infiniteGrid
        followCamera={false}
      />

      <OrbitControls
        makeDefault
        enablePan={false}
        minDistance={9}
        maxDistance={44}
        // Stop the camera dropping under the ground plane, where the scene has
        // no floor and the station reads as floating.
        maxPolarAngle={Math.PI / 2.12}
        target={[0, ANTENNA_HEIGHT * 0.35, 0]}
        autoRotate={false}
      />
    </>
  );
}

// ---------------------------------------------------------------------------

export default function StationScene({
  nodes,
  selectedNodeId,
  onSelect,
  prediction,
}: {
  nodes: NodeStatus[];
  selectedNodeId: string | null;
  onSelect: (nodeId: string) => void;
  prediction: RFPrediction | null;
}) {
  const { theme } = useTheme();
  const background = theme === "dark" ? "#0b0f16" : "#e7edf5";

  return (
    <Canvas
      shadows
      dpr={[1, 2]}
      camera={{ position: [16, 11, 16], fov: 42 }}
      // Capped so a high-DPI laptop does not render four times the pixels for a
      // panel this size.
      gl={{ antialias: true }}
      style={{ background, borderRadius: "0.75rem" }}
    >
      <fog attach="fog" args={[background, 38, 78]} />
      <Scene
        nodes={nodes}
        selectedNodeId={selectedNodeId}
        onSelect={onSelect}
        prediction={prediction}
      />
    </Canvas>
  );
}
