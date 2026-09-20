import { Fragment } from 'react';
import type { SatisfactoryScene, SatisfactoryViewOptions } from '../api/satisfactory';
import { useBlueprint } from '../state/BlueprintProvider';

const LAYERS: { key: Exclude<keyof SatisfactoryViewOptions, 'machines'>; label: string }[] = [
  { key: 'belts', label: 'belts' },
  { key: 'lifts', label: 'lifts' },
  { key: 'foundations', label: 'foundations' },
  { key: 'power', label: 'power' },
  { key: 'ports', label: 'ports' },
  { key: 'labels', label: 'labels' },
];
const round = (value: number) => Math.round(value * 100) / 100;

export function SatisfactoryToolbar({ scene }: { scene: SatisfactoryScene | null }) {
  const {
    stale,
    satisfactoryView: view,
    setSatisfactoryView: setView,
    selectedIndex,
    select,
  } = useBlueprint();
  return (
    <header className="toolbar sfy-toolbar">
      <strong>{scene?.title || 'Satisfactory blueprint'}</strong>
      <span>
        {scene ? `${scene.objects.length} objects · Satisfactory` : 'No blueprint loaded'}
      </span>
      {stale && (
        <span className="warn" data-testid="stale-blueprint">
          previous build — the last one produced no blueprint
        </span>
      )}
      <label className="toggle">
        machines
        <select
          aria-label="Satisfactory machine display"
          value={view.machines}
          onChange={(event) => {
            const machines = event.target.value;
            if (machines === 'solid' || machines === 'ghosted' || machines === 'hidden')
              setView({ ...view, machines });
          }}
        >
          <option value="ghosted">ghosted</option>
          <option value="solid">solid</option>
          <option value="hidden">hidden</option>
        </select>
      </label>
      {LAYERS.map(({ key, label }) => (
        <label className="toggle" key={key}>
          <input
            type="checkbox"
            checked={view[key]}
            onChange={(event) => setView({ ...view, [key]: event.target.checked })}
          />
          {label}
        </label>
      ))}
      <label className="toggle">
        Inspect object
        <select
          value={selectedIndex ?? ''}
          onChange={(event) =>
            select(event.target.value === '' ? null : Number(event.target.value))
          }
        >
          <option value="">None</option>
          {scene?.objects.map((object, index) => (
            <option key={object.id} value={index}>
              {index + 1}: {object.name}
            </option>
          ))}
        </select>
      </label>
      <span className="hint">Q/E rotate · O top-down · drag orbit · scroll zoom</span>
    </header>
  );
}

export function SatisfactoryInfo({ scene }: { scene: SatisfactoryScene }) {
  const { selectedIndex, select } = useBlueprint();
  const object = selectedIndex === null ? undefined : scene.objects[selectedIndex];
  if (!object) return null;
  return (
    <aside className="info sfy-info" data-testid="info">
      <button
        type="button"
        className="info-close"
        aria-label="Close object details"
        onClick={() => select(null)}
      >
        Close
      </button>
      <h2>{object.name}</h2>
      <dl>
        <dt>Object</dt>
        <dd>
          {object.kind} #{(selectedIndex ?? 0) + 1}
        </dd>
        <dt>Class</dt>
        <dd>{object.className}</dd>
        <dt>Position (m)</dt>
        <dd>{object.position.map(round).join(', ')} (Y up)</dd>
        {object.recipe && (
          <>
            <dt>Recipe</dt>
            <dd>{object.recipe}</dd>
          </>
        )}
        {object.clockPercent !== null && (
          <>
            <dt>Clock</dt>
            <dd>{round(object.clockPercent)}%</dd>
          </>
        )}
        {object.item && (
          <>
            <dt>Item</dt>
            <dd>{object.item}</dd>
          </>
        )}
        {object.ratePerMinute !== null && (
          <>
            <dt>Rate / min</dt>
            <dd>{round(object.ratePerMinute)}</dd>
          </>
        )}
        {object.details.map((detail) => (
          <Fragment key={`${detail.label}:${detail.value}`}>
            <dt>{detail.label}</dt>
            <dd>{detail.value}</dd>
          </Fragment>
        ))}
      </dl>
      {object.ports.length > 0 && (
        <>
          <h3>Connections</h3>
          <ul>
            {object.ports.map((port) => {
              const target = port.connectedTo;
              const targetIndex =
                target === null
                  ? -1
                  : scene.objects.findIndex(
                      (candidate) =>
                        target === candidate.id || target.startsWith(`${candidate.id}.`),
                    );
              return (
                <li key={port.name}>
                  <strong>{port.name}</strong> ({port.direction}){' '}
                  {targetIndex >= 0 ? (
                    <button type="button" onClick={() => select(targetIndex)}>
                      {scene.objects[targetIndex]?.name}
                    </button>
                  ) : (
                    (target ?? 'open')
                  )}
                </li>
              );
            })}
          </ul>
        </>
      )}
      <details>
        <summary>Object path</summary>
        <code>{object.id}</code>
      </details>
    </aside>
  );
}

export function SatisfactoryBom({ scene }: { scene: SatisfactoryScene }) {
  const counts = new Map<string, number>();
  for (const object of scene.objects) counts.set(object.name, (counts.get(object.name) ?? 0) + 1);
  const buildings = [...counts].sort(([left], [right]) => left.localeCompare(right));
  return (
    <aside className="bom" data-testid="bom">
      <details>
        <summary>Buildings and materials ({scene.objects.length} objects)</summary>
        <h2>Buildings</h2>
        <table>
          <tbody>
            {buildings.map(([name, count]) => (
              <tr key={name}>
                <td>{count}</td>
                <td>{name}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {scene.materials.length > 0 && (
          <>
            <h2>Blueprint construction cost</h2>
            <table>
              <tbody>
                {scene.materials.map((material) => (
                  <tr key={material.name}>
                    <td>{material.count}</td>
                    <td>{material.name}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </details>
      {scene.warnings.length > 0 && (
        <details className="warn">
          <summary>{scene.warnings.length} geometry notice(s)</summary>
          <ul>
            {scene.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </details>
      )}
    </aside>
  );
}
