import { afterEach, expect, test } from '@rstest/core';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import type { SatisfactoryScene } from '../../src/api/satisfactory';
import { parseBlueprint } from '../../src/format';
import {
  BlueprintProvider,
  useBlueprint,
  type BlueprintState,
} from '../../src/state/BlueprintProvider';
import { InputPanel } from '../../src/ui/InputPanel';
import { BuildPanel } from '../../src/ui/BuildPanel';
import { Toolbar } from '../../src/ui/Toolbar';
import { A_BLUEPRINT, restoreFetch } from '../support/build';
import { realCatalog } from '../support/catalog';

const scene: SatisfactoryScene = {
  game: 'sfy',
  title: 'Satisfactory import',
  saveVersion: 60,
  bounds: { min: [0, 0, 0], max: [1, 1, 1] },
  center: [0.5, 0.5, 0.5],
  radius: 1,
  objects: [],
  materials: [],
  warnings: [],
};
afterEach(restoreFetch);

let state: BlueprintState;
function Probe() {
  state = useBlueprint();
  return null;
}

test('a delayed binary import cannot replace a newer DSP document or its selection', async () => {
  const response = Promise.withResolvers<Response>();
  globalThis.fetch = Object.assign(() => response.promise, {
    preconnect: globalThis.fetch.preconnect,
  });
  render(
    <BlueprintProvider catalog={realCatalog}>
      <InputPanel />
      <Toolbar />
      <Probe />
    </BlueprintProvider>,
  );
  fireEvent.change(screen.getByLabelText('Open blueprint file (.sbp or DSP .txt)'), {
    target: { files: [new File(['binary'], 'factory.sbp')] },
  });
  const importer = within(screen.getByTestId('dropzone'));
  fireEvent.change(importer.getByLabelText('Blueprint string'), { target: { value: A_BLUEPRINT } });
  fireEvent.click(importer.getByRole('button', { name: 'Load' }));
  act(() => state.select(2));
  await act(async () => {
    response.resolve(new Response(JSON.stringify(scene)));
  });
  expect(screen.getByText(parseBlueprint(A_BLUEPRINT).header.shortDesc)).toBeVisible();
  expect(screen.queryByText('Satisfactory import')).toBeNull();
  expect(state.selectedIndex).toBe(2);
});

test('a Satisfactory document replaces DSP geometry and rejects an earlier build completion', () => {
  render(
    <BlueprintProvider catalog={realCatalog}>
      <Toolbar />
      <Probe />
    </BlueprintProvider>,
  );
  let earlierBuild = 0;
  act(() => {
    state.publishArtifact(A_BLUEPRINT, state.beginPublication());
    state.select(2);
    earlierBuild = state.beginPublication();
    state.publishSatisfactory(scene, state.beginPublication());
    state.publishArtifact(A_BLUEPRINT, earlierBuild, { kind: 'build', jobId: 'old-build' });
  });
  expect(screen.getByText('Satisfactory import')).toBeVisible();
  expect(state.blueprint).toBeNull();
  expect(state.sceneModel).toBeNull();
  expect(state.selectedIndex).toBeNull();
});

test('game controls follow explicit URLs and imports without needing an initial blueprint', () => {
  render(
    <BlueprintProvider catalog={realCatalog}>
      <BuildPanel />
      <InputPanel />
      <Toolbar />
      <Probe />
    </BlueprintProvider>,
  );
  const url = screen.getByLabelText('FactorioLab URL');
  expect(screen.getByLabelText('Blueprint Designer')).toBeVisible();
  expect(screen.getByLabelText('Satisfactory machine display')).toBeVisible();
  expect(screen.queryByLabelText('Latitude band')).toBeNull();

  fireEvent.change(url, {
    target: { value: 'https://factoriolab.github.io/dsp/flow?o=graphene*60&v=11' },
  });
  expect(screen.getByLabelText('Latitude band')).toBeVisible();
  expect(screen.queryByLabelText('Satisfactory machine display')).toBeNull();
  expect(screen.getByLabelText('Strategy')).toHaveValue('best');

  fireEvent.change(url, {
    target: { value: 'https://factoriolab.github.io/sfy/flow?o=copper-ingot*480&v=11' },
  });
  expect(screen.getByLabelText('Blueprint Designer')).toBeVisible();
  expect(screen.getByLabelText('Satisfactory machine display')).toBeVisible();
  expect(screen.getByLabelText('Strategy')).toHaveValue('sections');
  expect(screen.getByRole('button', { name: 'Build' })).toBeEnabled();

  fireEvent.change(url, { target: { value: 'https://factoriolab.github.io/factorio/flow' } });
  expect(screen.getByRole('button', { name: 'Build' })).toBeDisabled();
  expect(screen.queryByLabelText('Latitude band')).toBeNull();

  fireEvent.change(url, { target: { value: '' } });
  const importer = within(screen.getByTestId('dropzone'));
  fireEvent.change(importer.getByLabelText('Blueprint string'), { target: { value: A_BLUEPRINT } });
  fireEvent.click(importer.getByRole('button', { name: 'Load' }));
  expect(screen.getByLabelText('Latitude band')).toBeVisible();
  expect(screen.getByLabelText('sorter ties')).toBeVisible();
  act(() => state.publishSatisfactory(scene, state.beginPublication()));
  expect(screen.getByLabelText('Blueprint Designer')).toBeVisible();
  expect(screen.getByLabelText('Satisfactory machine display')).toBeVisible();
  expect(screen.queryByLabelText('sorter ties')).toBeNull();
});
