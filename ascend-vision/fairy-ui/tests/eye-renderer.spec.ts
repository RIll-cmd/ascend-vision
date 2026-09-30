import { expect, test } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';

test.use({ video: 'on' });
const captureDir = resolve(process.cwd(), 'artifacts/fairy-eye-review');

test('SVG eye exposes clipped sclera, iris, pupil, catchlight and both lids', async ({ page }) => {
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (type: string, ...args: any[]) {
      if (type.includes('webgl')) return null;
      return (original as any).call(this, type, ...args);
    } as any;
  });
  await page.goto('/');
  const eye = page.locator('.eye-fallback');
  await expect(eye.locator('.eye-sclera')).toBeVisible();
  await expect(eye.locator('.eye-iris')).toBeVisible();
  await expect(eye.locator('.eye-pupil')).toBeVisible();
  await expect(eye.locator('.eye-catchlight')).toBeVisible();
  await expect(eye.locator('.eye-upper-lid')).toBeVisible();
  await expect(eye.locator('.eye-lower-lid')).toBeVisible();
  await expect(eye.locator('.eye-iris-group')).toHaveAttribute('clip-path', /^url\(#.+\)$/);
  await page.waitForTimeout(350);
  await mkdir(captureDir, { recursive: true });
  await page.screenshot({ path: resolve(captureDir, 'task3-fallback-desktop.png') });
  const scleraFill = await eye.locator('.eye-sclera').getAttribute('fill');
  await page.getByRole('button', { name: 'Eye appearance', exact: true }).click();
  await page.getByRole('button', { name: 'Aurora theme' }).click();
  expect(await eye.locator('.eye-sclera').getAttribute('fill')).toBe(scleraFill);
});

for (const renderer of ['webgl', 'fallback'] as const) {
  test(`${renderer} anatomy captures neutral, listening, speaking and sleepy at desktop and 390px`, async ({ page }) => {
    if (renderer === 'fallback') {
      await page.addInitScript(() => {
        const original = HTMLCanvasElement.prototype.getContext;
        HTMLCanvasElement.prototype.getContext = function (type: string, ...args: any[]) {
          if (type.includes('webgl')) return null;
          return (original as any).call(this, type, ...args);
        } as any;
      });
    }
    let speaking = false;
    let audioLevel = 0;
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/fairy/state', route => route.fulfill({ json: {
      mode: 'focus', cameraReady: true, audioLevel,
      voiceEnabled: true, muted: false, speaking, elapsedSeconds: 3,
      coreConnection: { configured: true, state: 'connected', lastCheckedAt: '2026-09-30T00:00:00Z' },
    } }));
    await page.route('**/api/fairy/chat/events**', route => route.fulfill({ json: { events: [], cursor: 0, sessionId: 'anatomy-capture' } }));
    await page.goto('/?runtime=1');
    await page.getByRole('button', { name: 'Close chat panel' }).click();
    await expect(page.locator(renderer === 'webgl' ? '.eye-visual canvas' : '.eye-fallback')).toBeVisible();
    await mkdir(captureDir, { recursive: true });
    const capture = async (state: string, size: string) => {
      await page.waitForTimeout(350);
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: resolve(captureDir, `task3-${renderer}-${state}-${size}.png`), fullPage: true });
    };
    for (const [size, width, height] of [['desktop', 1440, 1000], ['390', 390, 844]] as const) {
      await page.setViewportSize({ width, height });
      speaking = false;
      audioLevel = 0;
      await page.getByRole('button', { name: 'open expression', exact: true }).click();
      await expect(page.getByRole('heading', { name: 'Here, with you.' })).toBeVisible();
      await capture('neutral', size);

      audioLevel = 0.8;
      await page.locator('.eye-stage').hover({ position: { x: 100, y: 100 } });
      await expect(page.getByRole('heading', { name: 'I’m listening.' })).toBeVisible();
      await capture('listening', size);

      speaking = true;
      await expect(page.getByRole('heading', { name: 'Fairy is speaking.' })).toBeVisible();
      await capture('speaking', size);

      speaking = false;
      audioLevel = 0;
      await page.getByRole('button', { name: 'sleepy expression', exact: true }).click();
      await expect(page.getByRole('img', { name: 'Fairy eye, an audio-reactive celestial iris' })).toHaveAttribute('data-expression', 'sleepy');
      await capture('sleepy', size);
      if (width === 390) expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    }
    expect(errors).toEqual([]);
  });
}

test('WebGL context loss releases the canvas and resize observer while SVG continues', async ({ page }) => {
  await page.addInitScript(() => {
    const OriginalObserver = window.ResizeObserver;
    (window as any).eyeResizeDisconnected = false;
    window.ResizeObserver = class extends OriginalObserver {
      private eyeRenderer = false;
      observe(target: Element, options?: ResizeObserverOptions) {
        if (target.matches('[data-testid="fairy-renderer"]')) this.eyeRenderer = true;
        return super.observe(target, options);
      }
      disconnect() {
        if (this.eyeRenderer) (window as any).eyeResizeDisconnected = true;
        return super.disconnect();
      }
    };
  });
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  const canvas = page.getByTestId('fairy-renderer').locator('canvas');
  await expect(canvas).toBeVisible();
  await canvas.dispatchEvent('webglcontextlost', { bubbles: true, cancelable: true });
  await expect(page.locator('.eye-fallback')).toBeVisible();
  await expect(canvas).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).eyeResizeDisconnected)).toBe(true);
  await expect.poll(() => page.locator('.eye-iris-group').getAttribute('transform')).not.toBeNull();
  expect(errors).toEqual([]);
});

test('thirty simulated seconds of speaking keep gaze and orientation fixed', async ({ page }) => {
  await page.addInitScript(() => {
    const callbacks = new Map<number, FrameRequestCallback>();
    let frameId = 0;
    let now = performance.now();
    window.requestAnimationFrame = callback => {
      const id = ++frameId;
      callbacks.set(id, callback);
      return id;
    };
    window.cancelAnimationFrame = id => { callbacks.delete(id); };
    (window as any).advanceEyeFrames = (count: number) => {
      const observations: { pupil: string | null; assembly: string }[] = [];
      for (let frame = 0; frame < count; frame += 1) {
        now += 1000 / 60;
        const ready = [...callbacks.values()];
        callbacks.clear();
        ready.forEach(callback => callback(now));
        const pupil = document.querySelector('.eye-iris-group')?.getAttribute('transform') ?? null;
        const assembly = (document.querySelector('.eye-visual') as HTMLElement | null)?.style.transform ?? '';
        observations.push({ pupil, assembly });
      }
      return observations;
    };
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (type: string, ...args: any[]) {
      if (type.includes('webgl')) return null;
      return (original as any).call(this, type, ...args);
    } as any;
  });
  let speaking = false;
  await page.route('**/api/fairy/state', route => route.fulfill({ json: {
    mode: 'focus', cameraReady: true, audioLevel: speaking ? 0.95 : 0,
    voiceEnabled: true, muted: false, speaking, elapsedSeconds: 3,
    coreConnection: { configured: true, state: 'connected', lastCheckedAt: '2026-09-30T00:00:00Z' },
  } }));
  await page.route('**/api/fairy/chat/events**', route => route.fulfill({ json: { events: [], cursor: 0, sessionId: 'motion-gate' } }));
  await page.goto('/?runtime=1');
  await expect(page.locator('.eye-fallback')).toBeVisible();
  await page.locator('.eye-stage').hover({ position: { x: 100, y: 200 } });
  await page.evaluate(() => (window as any).advanceEyeFrames(120));
  speaking = true;
  await expect(page.getByRole('heading', { name: 'Fairy is speaking.' })).toBeVisible();
  const observations = await page.evaluate(() => (window as any).advanceEyeFrames(30 * 60)) as {
    pupil: string | null; assembly: string;
  }[];
  const values = observations.map(({ pupil }) => {
    const match = pupil?.match(/^translate\(([-\d.e]+) ([-\d.e]+)\) translate\(200 200\) scale\(([-\d.e]+)\)/);
    return match?.slice(1).map(Number);
  });
  expect(observations).toHaveLength(1800);
  expect(values.every(value => value?.length === 3 && value.every(Number.isFinite))).toBe(true);
  expect(Math.abs(values[0]![0])).toBeGreaterThan(1);
  expect(values.every(value => Math.abs(value![0] - values[0]![0]) < 0.001 && Math.abs(value![1] - values[0]![1]) < 0.001)).toBe(true);
  expect(values.every(value => value![2] >= 0.94 && value![2] <= 1.06)).toBe(true);
  const peakFrames: number[] = [];
  for (let frame = 2; frame < values.length; frame += 1) {
    if (values[frame - 1]![2] > values[frame - 2]![2] && values[frame - 1]![2] >= values[frame]![2]) {
      peakFrames.push(frame - 1);
    }
  }
  expect(peakFrames.length).toBeGreaterThanOrEqual(20);
  expect(peakFrames.length).toBeLessThanOrEqual(66);
  expect(peakFrames.every((peak, index) => index === 0 || peak - peakFrames[index - 1] >= 60 / 2.2)).toBe(true);
  expect(observations.every(({ assembly }) => assembly === observations[0].assembly)).toBe(true);
});

test('SVG fallback settles to a still eye when motion is reduced', async ({ page }) => {
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (type: string, ...args: any[]) {
      if (type.includes('webgl')) return null;
      return (original as any).call(this, type, ...args);
    } as any;
  });
  await page.goto('/');
  await expect(page.locator('.eye-fallback')).toBeVisible();
  await page.locator('.eye-stage').hover({ position: { x: 30, y: 30 } });
  await page.getByRole('button', { name: 'Eye appearance', exact: true }).click();
  await page.getByLabel('Reduce eye motion').check();
  await expect.poll(() => page.locator('.eye-iris-group').getAttribute('transform'))
    .toMatch(/^translate\(0 0\) translate\(200 200\) scale\(1\)/);
});

test('idle and speaking states render without WebGL errors', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'error' && /shader|WebGL|GLSL/i.test(message.text())) errors.push(message.text());
  });
  let speaking = false;
  await page.route('**/api/fairy/state', route => route.fulfill({ json: {
    mode: 'focus', cameraReady: true, audioLevel: speaking ? 0.9 : 0,
    voiceEnabled: true, muted: false, speaking, elapsedSeconds: 3,
    coreConnection: { configured: true, state: 'connected', lastCheckedAt: '2026-09-30T00:00:00Z' },
  } }));
  await page.route('**/api/fairy/chat/events**', route => route.fulfill({ json: { events: [], cursor: 0, sessionId: 'capture' } }));
  await page.goto('/?runtime=1');
  await expect(page.getByTestId('fairy-renderer').locator('canvas')).toBeVisible();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: 'test-results/task2-idle.png' });
  speaking = true;
  await expect(page.getByRole('heading', { name: 'Fairy is speaking.' })).toBeVisible();
  await page.waitForTimeout(2000);
  await page.screenshot({ path: 'test-results/task2-speaking.png' });
  expect(errors).toEqual([]);
});
