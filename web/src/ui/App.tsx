import { useEffect, useState } from 'react';
import type { Catalog } from '../model/catalog';
import { BlueprintCanvas } from '../scene/BlueprintCanvas';
import { isAbortError, loadCatalog } from '../state/assets';
import { BlueprintProvider, useBlueprint } from '../state/BlueprintProvider';
import { BomPanel } from './BomPanel';
import { BuildPanel } from './BuildPanel';
import { InfoPanel } from './InfoPanel';
import { InputPanel } from './InputPanel';
import { Toolbar } from './Toolbar';
import './app.css';

function DspAssets({ onCatalog }: { onCatalog(catalog: Catalog): void }) {
  const { game, catalog } = useBlueprint();
  return game === 'dsp' && !catalog ? <DspCatalogLoader onCatalog={onCatalog} /> : null;
}

function DspCatalogLoader({ onCatalog }: { onCatalog(catalog: Catalog): void }) {
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    loadCatalog(controller.signal).then(
      (loaded) => {
        if (!cancelled) onCatalog(loaded);
      },
      (cause: unknown) => {
        if (cancelled || isAbortError(cause)) return;
        setError(cause instanceof Error ? cause.message : String(cause));
      },
    );
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [onCatalog]);
  if (!error) return null;
  return (
    <p role="alert" className="note warn">
      DSP viewer assets unavailable: {error}. Satisfactory builds and visualization remain
      available.
    </p>
  );
}

export function App() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  return (
    <BlueprintProvider catalog={catalog}>
      <div className="layout">
        <Toolbar />
        {/* Building and loading are the same act from two directions -- solve a
            FactorioLab URL into a blueprint, or bring a blueprint you already
            have -- so they share a scrolling column beside the canvas rather
            than stacking on top of it and squeezing the 3D view. */}
        <div className="sidebar">
          <DspAssets onCatalog={setCatalog} />
          <BuildPanel />
          <InputPanel />
        </div>
        <BlueprintCanvas />
        <InfoPanel />
        <BomPanel />
      </div>
    </BlueprintProvider>
  );
}
