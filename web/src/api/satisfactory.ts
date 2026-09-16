import { z } from 'zod';

const Vector = z.tuple([z.number().finite(), z.number().finite(), z.number().finite()]);
const Quaternion = z.tuple([
  z.number().finite(),
  z.number().finite(),
  z.number().finite(),
  z.number().finite(),
]);

/** All geometry is world-space, Y-up metres: (UE.x, UE.z, -UE.y) / 100. */
export const SatisfactoryScene = z.object({
  game: z.literal('sfy'),
  title: z.string(),
  saveVersion: z.number().int(),
  bounds: z.object({ min: Vector, max: Vector }),
  center: Vector,
  radius: z.number().positive(),
  objects: z.array(
    z.object({
      id: z.string(),
      className: z.string(),
      name: z.string(),
      kind: z.enum([
        'machine',
        'belt',
        'lift',
        'splitter',
        'merger',
        'foundation',
        'power',
        'storage',
        'pipe',
        'other',
      ]),
      position: Vector,
      boxes: z.array(z.object({ center: Vector, size: Vector, quaternion: Quaternion })),
      points: z.array(Vector),
      pathWidth: z.number().positive().nullable(),
      pathHeight: z.number().positive().nullable(),
      flowDirection: z.enum(['forward', 'reverse', 'unknown']),
      color: z.number().int(),
      ports: z.array(
        z.object({
          name: z.string(),
          position: Vector,
          direction: z.enum(['input', 'output', 'any', 'unknown']),
          connectedTo: z.string().nullable(),
        }),
      ),
      details: z.array(z.object({ label: z.string(), value: z.string() })),
      recipe: z.string().nullable(),
      clockPercent: z.number().nullable(),
      item: z.string().nullable(),
      ratePerMinute: z.number().nullable(),
    }),
  ),
  materials: z.array(z.object({ name: z.string(), count: z.number().nonnegative() })),
  warnings: z.array(z.string()),
});
export type SatisfactoryScene = z.infer<typeof SatisfactoryScene>;
export type SatisfactoryObject = SatisfactoryScene['objects'][number];

export interface SatisfactoryViewOptions {
  machines: 'solid' | 'ghosted' | 'hidden';
  belts: boolean;
  lifts: boolean;
  foundations: boolean;
  power: boolean;
  ports: boolean;
  labels: boolean;
}
async function decodeResponse(response: Response): Promise<SatisfactoryScene> {
  if (!response.ok) {
    const reason = (await response.text()).trim();
    throw new Error(reason || `HTTP ${response.status}`);
  }
  return SatisfactoryScene.parse(await response.json());
}

export async function importSatisfactory(file: File): Promise<SatisfactoryScene> {
  return decodeResponse(
    await fetch(`/api/sfy/scene?name=${encodeURIComponent(file.name)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream' },
      body: file,
    }),
  );
}

export async function fetchSatisfactoryScene(
  jobId: string,
  signal?: AbortSignal,
): Promise<SatisfactoryScene> {
  return decodeResponse(await fetch(`/api/build/${encodeURIComponent(jobId)}/scene`, { signal }));
}
