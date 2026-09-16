import { Canvas } from '@react-three/fiber';
import { useLayoutEffect, useMemo, useRef } from 'react';
import {
  BufferGeometry,
  Color,
  DoubleSide,
  Float32BufferAttribute,
  type InstancedMesh,
  Object3D,
} from 'three';
import type {
  SatisfactoryObject,
  SatisfactoryScene,
  SatisfactoryViewOptions,
} from '../api/satisfactory';
import { type SatisfactoryEntry, satisfactoryRibbons } from './satisfactoryGeometry';
import { CameraRig } from './CameraRig';
import { SatisfactoryLabels } from './SatisfactoryLabels';

type Position = [number, number, number];
type Rotation = [number, number, number, number];
interface Instance {
  index: number;
  position: Position;
  size: Position;
  quaternion: Rotation;
  color: number;
}

const SELECTED = 0xffe071;
const UNKNOWN = 0xff38d1;
const IDENTITY: Rotation = [0, 0, 0, 1];

function isMachine(object: SatisfactoryObject): boolean {
  return object.kind === 'machine' || object.kind === 'storage';
}

function visible(
  object: SatisfactoryObject,
  view: Omit<SatisfactoryViewOptions, 'labels'>,
): boolean {
  switch (object.kind) {
    case 'machine':
    case 'storage':
      return view.machines !== 'hidden';
    case 'foundation':
      return view.foundations;
    case 'belt':
    case 'splitter':
    case 'merger':
    case 'pipe':
      return view.belts;
    case 'lift':
      return view.lifts;
    case 'power':
      return view.power;
    default:
      return true;
  }
}

function objectColor(object: SatisfactoryObject): number {
  if (object.kind === 'splitter') return 0xf0a545;
  if (object.kind === 'merger') return 0x63c7d2;
  if (object.kind === 'other') return UNKNOWN;
  return object.color;
}

function Instances({
  instances,
  selectedIndex,
  onSelect,
  ghosted = false,
  shape = 'box',
}: {
  instances: Instance[];
  selectedIndex: number | null;
  onSelect: (index: number | null) => void;
  ghosted?: boolean;
  shape?: 'box' | 'port' | 'unknown';
}) {
  const meshRef = useRef<InstancedMesh>(null);
  useLayoutEffect(() => {
    const mesh = meshRef.current;
    if (!mesh) return;
    const dummy = new Object3D();
    for (const [i, instance] of instances.entries()) {
      dummy.position.fromArray(instance.position);
      dummy.quaternion.fromArray(instance.quaternion);
      dummy.scale.fromArray(instance.size);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
    }
    mesh.instanceMatrix.needsUpdate = true;
    mesh.computeBoundingSphere();
  }, [instances]);
  useLayoutEffect(() => {
    const mesh = meshRef.current;
    if (!mesh) return;
    const color = new Color();
    instances.forEach((instance, i) =>
      mesh.setColorAt(
        i,
        color.setHex(instance.index === selectedIndex ? SELECTED : instance.color),
      ),
    );
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  }, [instances, selectedIndex]);
  if (instances.length === 0) return null;
  return (
    // oxlint-disable-next-line jsx-a11y/no-static-element-interactions -- r3f mesh, not a DOM element
    <instancedMesh
      key={instances.length}
      ref={meshRef}
      args={[undefined, undefined, instances.length]}
      onClick={(event) => {
        event.stopPropagation();
        onSelect(
          event.instanceId === undefined ? null : (instances[event.instanceId]?.index ?? null),
        );
      }}
    >
      {shape === 'box' ? (
        <boxGeometry args={[1, 1, 1]} />
      ) : shape === 'port' ? (
        <sphereGeometry args={[0.5, 10, 6]} />
      ) : (
        <octahedronGeometry args={[0.75]} />
      )}
      <meshStandardMaterial
        roughness={0.55}
        metalness={0.1}
        transparent={ghosted}
        opacity={ghosted ? 0.16 : 1}
        depthWrite={!ghosted}
        emissive={shape === 'unknown' ? UNKNOWN : 0}
        emissiveIntensity={0.3}
      />
    </instancedMesh>
  );
}

function Ribbons({
  entries,
  selectedIndex,
  onSelect,
}: {
  entries: SatisfactoryEntry[];
  selectedIndex: number | null;
  onSelect: (index: number | null) => void;
}) {
  const batch = useMemo(() => satisfactoryRibbons(entries), [entries]);
  useLayoutEffect(() => () => batch.geometry.dispose(), [batch]);
  useLayoutEffect(() => {
    const colors = batch.geometry.getAttribute('color');
    const color = new Color();
    for (const range of batch.ranges) {
      color.setHex(range.highlight && range.index === selectedIndex ? SELECTED : range.color);
      for (let i = range.start; i < range.start + range.count; i++)
        colors.setXYZ(i, color.r, color.g, color.b);
    }
    colors.needsUpdate = true;
  }, [batch, selectedIndex]);
  return (
    // oxlint-disable-next-line jsx-a11y/no-static-element-interactions -- r3f mesh, not a DOM element
    <mesh
      geometry={batch.geometry}
      onClick={(event) => {
        event.stopPropagation();
        onSelect(
          event.faceIndex === undefined || event.faceIndex === null
            ? null
            : (batch.faces[event.faceIndex] ?? null),
        );
      }}
    >
      <meshStandardMaterial vertexColors side={DoubleSide} roughness={0.7} />
    </mesh>
  );
}

/** Actual wire/pipe polylines, batched with exact segment-to-actor picking. */
function Paths({
  entries,
  selectedIndex,
  onSelect,
}: {
  entries: SatisfactoryEntry[];
  selectedIndex: number | null;
  onSelect: (index: number | null) => void;
}) {
  const batch = useMemo(() => {
    const positions: number[] = [];
    const owners: { index: number; color: number }[] = [];
    for (const { object, index } of entries) {
      for (const [i, b] of object.points.entries()) {
        const a = object.points[i - 1];
        if (!a) continue;
        positions.push(...a, ...b);
        owners.push({ index, color: objectColor(object) });
      }
    }
    const geometry = new BufferGeometry();
    geometry.setAttribute('position', new Float32BufferAttribute(positions, 3));
    geometry.setAttribute(
      'color',
      new Float32BufferAttribute(new Float32Array(positions.length), 3),
    );
    geometry.computeBoundingSphere();
    return { geometry, owners };
  }, [entries]);
  useLayoutEffect(() => () => batch.geometry.dispose(), [batch]);
  useLayoutEffect(() => {
    const colors = batch.geometry.getAttribute('color');
    const color = new Color();
    batch.owners.forEach((owner, segment) => {
      color.setHex(owner.index === selectedIndex ? SELECTED : owner.color);
      colors.setXYZ(segment * 2, color.r, color.g, color.b);
      colors.setXYZ(segment * 2 + 1, color.r, color.g, color.b);
    });
    colors.needsUpdate = true;
  }, [batch, selectedIndex]);
  return (
    // oxlint-disable-next-line jsx-a11y/no-static-element-interactions -- r3f line, not a DOM element
    <lineSegments
      geometry={batch.geometry}
      onClick={(event) => {
        event.stopPropagation();
        onSelect(
          event.index === undefined
            ? null
            : (batch.owners[Math.floor(event.index / 2)]?.index ?? null),
        );
      }}
    >
      <lineBasicMaterial vertexColors toneMapped={false} />
    </lineSegments>
  );
}

export function SatisfactoryCanvas({
  scene,
  selectedIndex,
  onSelect,
  view,
}: {
  scene: SatisfactoryScene;
  selectedIndex: number | null;
  onSelect: (index: number | null) => void;
  view: SatisfactoryViewOptions;
}) {
  const {
    machines: machineLook,
    belts: showBelts,
    lifts: showLifts,
    foundations: showFoundations,
    power: showPower,
    ports: showPorts,
  } = view;
  const layers = useMemo(() => {
    const visibility = {
      machines: machineLook,
      belts: showBelts,
      lifts: showLifts,
      foundations: showFoundations,
      power: showPower,
      ports: showPorts,
    };
    const entries: SatisfactoryEntry[] = [];
    const belts: SatisfactoryEntry[] = [];
    const paths: SatisfactoryEntry[] = [];
    const machines: Instance[] = [];
    const solid: Instance[] = [];
    const ports: Instance[] = [];
    const unknown: Instance[] = [];
    scene.objects.forEach((object, index) => {
      const shown = visible(object, visibility);
      if (shown) {
        const entry = { object, index };
        entries.push(entry);
        const firstPoint = object.points[0];
        const usablePath =
          firstPoint !== undefined &&
          object.points.some(
            (point) =>
              point[0] !== firstPoint[0] ||
              point[1] !== firstPoint[1] ||
              point[2] !== firstPoint[2],
          );
        const ribbon =
          object.kind === 'belt' &&
          object.pathWidth !== null &&
          object.pathHeight !== null &&
          usablePath;
        if (ribbon) belts.push(entry);
        if (
          object.kind === 'power' ||
          object.kind === 'pipe' ||
          object.kind === 'lift' ||
          (object.kind === 'belt' && !ribbon)
        )
          paths.push(entry);
        const target = isMachine(object) ? machines : solid;
        // Belt envelopes are clearance, not a substitute for the sampled route.
        if (!ribbon)
          object.boxes.forEach((box) =>
            target.push({
              index,
              position: box.center,
              size: box.size,
              quaternion: box.quaternion,
              color: objectColor(object),
            }),
          );
        const hasPath =
          usablePath &&
          (object.kind === 'power' || object.kind === 'pipe' || object.kind === 'lift');
        if (
          object.kind === 'other' ||
          (object.kind === 'belt' && !ribbon) ||
          (object.boxes.length === 0 && !ribbon && !hasPath)
        ) {
          // Diagnostic glyph, deliberately unlike a game mesh; never pretend an unknown actor is absent.
          unknown.push({
            index,
            position: object.position,
            size: [1, 1, 1],
            quaternion: IDENTITY,
            color: UNKNOWN,
          });
        }
      }
      // Machine ghost/hidden modes do not remove their transport connection markers.
      if ((showPorts && (shown || isMachine(object))) || (shown && object.kind === 'lift'))
        object.ports.forEach((port) =>
          ports.push({
            index,
            position: port.position,
            size: [0.35, 0.35, 0.35],
            quaternion: IDENTITY,
            color:
              port.direction === 'input'
                ? 0x59bdff
                : port.direction === 'output'
                  ? 0x85e89d
                  : port.direction === 'any'
                    ? 0xffdd77
                    : UNKNOWN,
          }),
        );
    });
    return { entries, belts, paths, machines, solid, ports, unknown };
  }, [scene, machineLook, showBelts, showLifts, showFoundations, showPower, showPorts]);
  const gridSize = Math.max(16, Math.ceil((scene.radius * 2) / 8) * 8);
  return (
    <Canvas
      orthographic
      dpr={[1, 2]}
      className="canvas"
      resize={{ debounce: 0, scroll: false }}
      onCreated={({ raycaster }) => {
        raycaster.params.Line.threshold = 0.25;
      }}
      onPointerMissed={() => onSelect(null)}
    >
      <color attach="background" args={['#10141a']} />
      <hemisphereLight args={['#cfe3ff', '#2a2f38', 1.1]} />
      <directionalLight position={[40, 80, 30]} intensity={1.4} />
      <CameraRig model={scene} />
      <Instances
        instances={layers.machines}
        selectedIndex={selectedIndex}
        onSelect={onSelect}
        ghosted={view.machines === 'ghosted'}
      />
      <Instances instances={layers.solid} selectedIndex={selectedIndex} onSelect={onSelect} />
      <Ribbons entries={layers.belts} selectedIndex={selectedIndex} onSelect={onSelect} />
      <Paths entries={layers.paths} selectedIndex={selectedIndex} onSelect={onSelect} />
      <Instances
        instances={layers.ports}
        selectedIndex={selectedIndex}
        onSelect={onSelect}
        shape="port"
      />
      <Instances
        instances={layers.unknown}
        selectedIndex={selectedIndex}
        onSelect={onSelect}
        shape="unknown"
      />
      {view.labels && (
        <SatisfactoryLabels
          entries={layers.entries}
          selectedIndex={selectedIndex}
          onSelect={onSelect}
          ports={view.ports}
        />
      )}
      <gridHelper
        position={[scene.center[0], scene.bounds.min[1] - 0.03, scene.center[2]]}
        args={[gridSize, Math.min(400, Math.ceil(gridSize)), '#29384b', '#1a2533']}
        raycast={() => null}
      />
    </Canvas>
  );
}
