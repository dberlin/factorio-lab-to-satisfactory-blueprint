import { useLayoutEffect, useMemo } from 'react';
import { Box3, CanvasTexture, Matrix4, Quaternion, SRGBColorSpace, Vector3 } from 'three';
import type { SatisfactoryObject } from '../api/satisfactory';
import type { SatisfactoryEntry } from './satisfactoryGeometry';

type Position = [number, number, number];

function labelPosition(object: SatisfactoryObject): Position {
  const bounds = new Box3();
  const matrix = new Matrix4();
  for (const box of object.boxes) {
    matrix.compose(
      new Vector3(...box.center),
      new Quaternion(...box.quaternion),
      new Vector3(...box.size),
    );
    bounds.union(
      new Box3(new Vector3(-0.5, -0.5, -0.5), new Vector3(0.5, 0.5, 0.5)).applyMatrix4(matrix),
    );
  }
  for (const point of object.points) bounds.expandByPoint(new Vector3(...point));
  if (bounds.isEmpty()) return [object.position[0], object.position[1] + 1, object.position[2]];
  const center = bounds.getCenter(new Vector3());
  return [center.x, bounds.max.y + 0.75, center.z];
}

function labelTexture(lines: readonly string[], selected: boolean) {
  const canvas = document.createElement('canvas');
  const context = canvas.getContext('2d');
  if (!context) return null;
  const font = '500 26px system-ui, sans-serif';
  context.font = font;
  const width = Math.min(
    1536,
    Math.max(100, ...lines.map((line) => context.measureText(line).width + 28)),
  );
  canvas.width = Math.ceil(width);
  canvas.height = lines.length * 34 + 16;
  context.font = font;
  context.fillStyle = selected ? 'rgba(61, 48, 9, 0.94)' : 'rgba(12, 19, 28, 0.88)';
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.strokeStyle = selected ? '#ffe071' : '#6b8199';
  context.lineWidth = 2;
  context.strokeRect(1, 1, canvas.width - 2, canvas.height - 2);
  context.fillStyle = '#f2f6ff';
  context.textBaseline = 'middle';
  lines.forEach((line, i) => context.fillText(line, 14, 25 + i * 34, canvas.width - 28));
  const texture = new CanvasTexture(canvas);
  texture.colorSpace = SRGBColorSpace;
  return { texture, aspect: canvas.width / canvas.height, rows: lines.length };
}

function Label({
  text,
  position,
  selected,
  onSelect,
}: {
  text: string;
  position: Position;
  selected: boolean;
  onSelect: () => void;
}) {
  const image = useMemo(() => labelTexture(text.split('\n'), selected), [text, selected]);
  useLayoutEffect(() => () => image?.texture.dispose(), [image]);
  if (!image) return null;
  const height = 0.62 * image.rows;
  return (
    // oxlint-disable-next-line jsx-a11y/no-static-element-interactions -- r3f sprite, not a DOM element
    <sprite
      position={position}
      scale={[height * image.aspect, height, 1]}
      onClick={(event) => {
        event.stopPropagation();
        onSelect();
      }}
    >
      <spriteMaterial map={image.texture} transparent depthWrite={false} toneMapped={false} />
    </sprite>
  );
}

export function SatisfactoryLabels({
  entries,
  selectedIndex,
  onSelect,
  ports,
}: {
  entries: SatisfactoryEntry[];
  selectedIndex: number | null;
  onSelect: (index: number | null) => void;
  ports: boolean;
}) {
  const labels = useMemo(
    () =>
      entries.flatMap(({ object, index }) => {
        const transport =
          object.kind === 'belt' ||
          object.kind === 'lift' ||
          object.kind === 'pipe' ||
          object.kind === 'splitter' ||
          object.kind === 'merger';
        const lines = [`#${index + 1} ${object.name}`];
        if (object.recipe) lines.push(object.recipe);
        if (object.item)
          lines.push(
            `${object.item}${object.ratePerMinute === null ? '' : ` · ${object.ratePerMinute.toLocaleString()} /min`}`,
          );
        if (object.clockPercent !== null)
          lines.push(`${object.clockPercent.toLocaleString()}% clock`);
        const text = lines.join('\n');
        const result = [
          {
            key: object.id,
            index,
            text: transport ? `#${index + 1}` : text,
            selectedText: text,
            selectedOnly: object.kind === 'foundation' || object.kind === 'power',
            position: labelPosition(object),
          },
        ];
        if (ports)
          object.ports.forEach((port, portIndex) => {
            const text = `${port.direction === 'input' ? 'IN' : port.direction === 'output' ? 'OUT' : 'PORT'} ${port.name}\n${port.connectedTo ? 'Connected' : 'Open'}`;
            result.push({
              key: `${object.id}:port:${portIndex}`,
              index,
              text,
              selectedText: text,
              selectedOnly: object.kind === 'foundation' || object.kind === 'power',
              position: [port.position[0], port.position[1] + 0.4, port.position[2]],
            });
          });
        return result;
      }),
    [entries, ports],
  );
  return labels.map((label) => {
    const selected = label.index === selectedIndex;
    if (label.selectedOnly && !selected) return null;
    return (
      <Label
        key={label.key}
        text={selected ? label.selectedText : label.text}
        position={label.position}
        selected={selected}
        onSelect={() => onSelect(label.index)}
      />
    );
  });
}
