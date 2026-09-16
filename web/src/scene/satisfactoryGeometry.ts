import { BufferGeometry, Color, Float32BufferAttribute, Vector3 } from 'three';
import type { SatisfactoryObject } from '../api/satisfactory';

export interface SatisfactoryEntry {
  object: SatisfactoryObject;
  index: number;
}

export interface RibbonRange {
  index: number;
  start: number;
  count: number;
  color: number;
  highlight: boolean;
}

/** Sweep the supplied samples without inventing corners or replacing curves by chords. */
export function satisfactoryRibbons(entries: readonly SatisfactoryEntry[]) {
  const positions: number[] = [];
  const ranges: RibbonRange[] = [];
  const faces: number[] = [];
  const colorValues: number[] = [];
  const tint = new Color();
  const triangle = (a: Vector3, b: Vector3, c: Vector3, index: number) => {
    positions.push(a.x, a.y, a.z, b.x, b.y, b.z, c.x, c.y, c.z);
    faces.push(index);
  };
  const quad = (a: Vector3, b: Vector3, c: Vector3, d: Vector3, index: number) => {
    triangle(a, b, c, index);
    triangle(a, c, d, index);
  };
  const finishRange = (
    entry: SatisfactoryEntry,
    start: number,
    color: number,
    highlight = true,
  ) => {
    const count = positions.length / 3 - start;
    if (count === 0) return;
    ranges.push({ index: entry.index, start, count, color, highlight });
    tint.setHex(color);
    for (let i = 0; i < count; i++) colorValues.push(tint.r, tint.g, tint.b);
  };

  for (const entry of entries) {
    const { pathWidth, pathHeight } = entry.object;
    if (pathWidth === null || pathHeight === null) continue;
    const halfWidth = pathWidth / 2;
    const halfDepth = pathHeight / 2;
    const samples: Vector3[] = [];
    for (const point of entry.object.points) {
      const value = new Vector3(...point);
      const previous = samples.at(-1);
      if (!previous || value.distanceToSquared(previous) > 1e-12) {
        samples.push(value);
      }
    }
    if (samples.length < 2) continue;
    const frames: {
      point: Vector3;
      side: Vector3;
      up: Vector3;
      tangent: Vector3;
      corners: [Vector3, Vector3, Vector3, Vector3];
    }[] = [];
    const start = positions.length / 3;
    for (const [i, p] of samples.entries()) {
      const before = samples[i - 1] ?? p;
      const after = samples[i + 1] ?? p;
      const previous = frames.at(-1);
      const tangent = after.clone().sub(before);
      if (tangent.lengthSq() < 1e-12) tangent.copy(after).sub(p);
      if (tangent.lengthSq() < 1e-12) tangent.copy(p).sub(before);
      tangent.normalize();
      const side = new Vector3(tangent.z, 0, -tangent.x);
      if (side.lengthSq() < 1e-12) side.copy(previous?.side ?? new Vector3(1, 0, 0));
      else side.normalize();
      if (previous && side.dot(previous.side) < 0) side.negate();
      const up = new Vector3().crossVectors(tangent, side).normalize();
      const corners: [Vector3, Vector3, Vector3, Vector3] = [
        p.clone().addScaledVector(side, -halfWidth).addScaledVector(up, halfDepth),
        p.clone().addScaledVector(side, halfWidth).addScaledVector(up, halfDepth),
        p.clone().addScaledVector(side, halfWidth).addScaledVector(up, -halfDepth),
        p.clone().addScaledVector(side, -halfWidth).addScaledVector(up, -halfDepth),
      ];
      frames.push({ point: p, side, up, tangent, corners });
      if (!previous) continue;
      const a = previous.corners;
      quad(a[0], corners[0], corners[1], a[1], entry.index);
      quad(a[1], corners[1], corners[2], a[2], entry.index);
      quad(a[2], corners[2], corners[3], a[3], entry.index);
      quad(a[3], corners[3], corners[0], a[0], entry.index);
    }
    const first = frames[0]?.corners;
    const last = frames.at(-1)?.corners;
    if (!first || !last) continue;
    quad(first[3], first[2], first[1], first[0], entry.index);
    quad(last[0], last[1], last[2], last[3], entry.index);
    finishRange(entry, start, entry.object.color);
    if (entry.object.flowDirection === 'unknown') continue;

    const arrowStart = positions.length / 3;
    let distance = 0;
    for (const [i, sample] of samples.entries()) {
      const previous = samples[i - 1];
      if (previous) distance += sample.distanceTo(previous);
    }
    const spacing = 3;
    let nextArrow = Math.min(distance / 2, spacing / 2);
    let traversed = 0;
    for (const [i, frame] of frames.entries()) {
      const previous = frames[i - 1];
      if (!previous) continue;
      const a = previous.point;
      const b = frame.point;
      const length = a.distanceTo(b);
      while (nextArrow < traversed + length && nextArrow < distance) {
        const t = (nextArrow - traversed) / length;
        const center = a
          .clone()
          .lerp(b, t)
          .addScaledVector(frame.up, halfDepth + 0.015);
        const tangent = frame.tangent
          .clone()
          .multiplyScalar(entry.object.flowDirection === 'reverse' ? -1 : 1);
        const side = frame.side;
        const arrowLength = Math.min(halfWidth * 0.9, distance / 4);
        triangle(
          center.clone().addScaledVector(tangent, arrowLength),
          center
            .clone()
            .addScaledVector(tangent, -arrowLength)
            .addScaledVector(side, halfWidth * 0.6),
          center
            .clone()
            .addScaledVector(tangent, -arrowLength)
            .addScaledVector(side, -halfWidth * 0.6),
          entry.index,
        );
        nextArrow += spacing;
      }
      traversed += length;
    }
    finishRange(entry, arrowStart, 0x14202c, false);
  }
  const geometry = new BufferGeometry();
  geometry.setAttribute('position', new Float32BufferAttribute(positions, 3));
  geometry.setAttribute('color', new Float32BufferAttribute(colorValues, 3));
  geometry.computeVertexNormals();
  geometry.computeBoundingSphere();
  return { geometry, ranges, faces };
}
