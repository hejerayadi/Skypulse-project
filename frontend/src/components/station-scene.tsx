import { useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame, type ThreeEvent } from "@react-three/fiber";
import { ContactShadows, Grid, Html, OrbitControls, useGLTF } from "@react-three/drei";
import * as THREE from "three";
import { useTheme } from "@/components/theme-provider";
import { SensorModel } from "@/components/sensor-model";
import { NODE_STATE_STYLE, formatAge, nodeRole, type NodeStatus } from "@/lib/nodes";
import {
  SENSOR_MODELS,
  sensorKindsInFleet,
  sensorsForNode,
  type SensorKind,
} from "@/lib/sensor-models";
import {
  ANTENNA_HEIGHT,
  DISH_FOCUS,
  placeNodes,
  shortestTurn,
  type NodePlacement,
} from "@/lib/station-layout";

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

useGLTF.preload(MODEL_URL);

// ---------------------------------------------------------------------------
// Antenna
// ---------------------------------------------------------------------------

function Antenna({ targetAzimuth }: { targetAzimuth: number }) {
  const { scene } = useGLTF(MODEL_URL);

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

  useFrame((_, delta) => {
    const turn = shortestTurn(pivot.rotation.y, targetAzimuth + DISH_AZIMUTH_OFFSET);
    // Ease toward the target but cap the step, so a re-target reads as the dish
    // slewing rather than snapping.
    pivot.rotation.y += THREE.MathUtils.clamp(turn, -SLEW_SPEED * delta, SLEW_SPEED * delta);
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
// Sensor nodes
// ---------------------------------------------------------------------------

/** Top of the mast, where the instrument deck sits. */
const DECK_HEIGHT = 0.95;
/** Status beacon, kept clear of the tallest sensor on the deck. */
const BEACON_HEIGHT = DECK_HEIGHT + 1.0;

/**
 * The instruments a node carries, mounted on its deck.
 *
 * An environment node reports temperature, humidity *and* pressure, so it gets
 * three models arranged around the mast head; a single-instrument node gets one
 * in the centre, larger. The models keep their own materials -- tinting them by
 * status would throw away the reason for having them, and would force a
 * material clone per node. Status stays where it already was: the pad, the
 * beacon and the selection ring.
 */
function SensorRig({ kinds, color }: { kinds: SensorKind[]; color: string }) {
  const solo = kinds.length <= 1;
  const deckRadius = solo ? 0.5 : 0.8;
  const ringRadius = solo ? 0 : 0.4;
  // Several instruments share the deck, so each is drawn smaller.
  const scale = solo ? 1 : 0.6;

  return (
    <group position={[0, DECK_HEIGHT, 0]}>
      <mesh castShadow receiveShadow>
        <cylinderGeometry args={[deckRadius, deckRadius * 0.92, 0.08, 24]} />
        <meshStandardMaterial color="#4b5563" roughness={0.7} metalness={0.3} />
      </mesh>

      {/* A node that has not reported yet gets an anonymous crate: which
          instruments it carries is exactly what is not known. */}
      {kinds.length === 0 && (
        <mesh castShadow position={[0, 0.34, 0]}>
          <boxGeometry args={[0.6, 0.6, 0.6]} />
          <meshStandardMaterial color={color} roughness={0.5} metalness={0.15} />
        </mesh>
      )}

      {kinds.map((kind, index) => {
        const angle = (index / kinds.length) * Math.PI * 2;
        return (
          <group
            key={kind}
            position={[Math.cos(angle) * ringRadius, 0.04, Math.sin(angle) * ringRadius]}
            scale={scale}
            // Face each instrument outward, so three on one deck do not all
            // present the same side to the camera.
            rotation={[0, -angle, 0]}
          >
            <SensorModel kind={kind} color={color} />
          </group>
        );
      })}
    </group>
  );
}

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
      <mesh castShadow position={[0, DECK_HEIGHT / 2, 0]}>
        <cylinderGeometry args={[0.07, 0.09, DECK_HEIGHT, 12]} />
        <meshStandardMaterial color="#6b7280" roughness={0.6} metalness={0.4} />
      </mesh>

      <SensorRig kinds={sensorsForNode(node)} color={style.color} />

      {/* Beacon */}
      <mesh position={[0, BEACON_HEIGHT, 0]}>
        <sphereGeometry args={[0.16, 20, 16]} />
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
}: {
  nodes: NodeStatus[];
  selectedNodeId: string | null;
  onSelect: (nodeId: string) => void;
}) {
  const placements = useMemo(() => placeNodes(nodes), [nodes]);
  const [pulses, setPulses] = useState<Pulse[]>([]);

  // Fetch the models this fleet needs and no others -- preloading the whole
  // table would spend bandwidth on roles that are not deployed. Keyed on which
  // roles are present rather than on `nodes`, which is a fresh array every poll
  // and would otherwise re-run this twelve times a minute for an unchanged fleet.
  const sensorKey = useMemo(() => sensorKindsInFleet(nodes).sort().join(","), [nodes]);

  useEffect(() => {
    if (!sensorKey) return;
    for (const kind of sensorKey.split(",") as SensorKind[]) {
      const url = SENSOR_MODELS[kind].url;
      if (url) useGLTF.preload(url);
    }
  }, [sensorKey]);

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

      <Antenna targetAzimuth={targetAzimuth} />

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
}: {
  nodes: NodeStatus[];
  selectedNodeId: string | null;
  onSelect: (nodeId: string) => void;
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
      <Scene nodes={nodes} selectedNodeId={selectedNodeId} onSelect={onSelect} />
    </Canvas>
  );
}
