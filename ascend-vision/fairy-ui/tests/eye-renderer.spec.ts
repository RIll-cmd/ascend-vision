import { expect, test } from '@playwright/test';

test.use({ video: 'on' });

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
  await expect.poll(() => page.locator('.eye-fallback > g').nth(1).getAttribute('transform')).not.toBeNull();
  expect(errors).toEqual([]);
});

test('thirty simulated seconds of speaking keep the original orb visually still', async ({ page }) => {
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
      const observations: { pupil: string | null; spikes: string | null; assembly: string }[] = [];
      for (let frame = 0; frame < count; frame += 1) {
        now += 1000 / 60;
        const ready = [...callbacks.values()];
        callbacks.clear();
        ready.forEach(callback => callback(now));
        const rings = document.querySelectorAll('.eye-fallback > g');
        const spikes = rings[0]?.getAttribute('transform') ?? null;
        const pupil = rings[1]?.getAttribute('transform') ?? null;
        const assembly = (document.querySelector('.eye-visual') as HTMLElement | null)?.style.transform ?? '';
        observations.push({ pupil, spikes, assembly });
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
  let emotion = 'neutral';
  await page.route('**/api/fairy/state', route => route.fulfill({ json: {
    mode: 'focus', cameraReady: true, audioLevel: speaking ? 0.95 : 0.8,
    voiceEnabled: true, muted: false, speaking, emotion, elapsedSeconds: 3,
    coreConnection: { configured: true, state: 'connected', lastCheckedAt: '2026-09-30T00:00:00Z' },
  } }));
  await page.route('**/api/fairy/chat/events**', route => route.fulfill({ json: { events: [], cursor: 0, sessionId: 'motion-gate' } }));
  await page.goto('/?runtime=1');
  await expect(page.locator('.eye-fallback')).toBeVisible();
  await expect(page.locator('.eye-stage')).toHaveAttribute('data-eye-mode', 'listening');
  await page.locator('.eye-stage').hover({ position: { x: 100, y: 200 } });
  await page.evaluate(() => (window as any).advanceEyeFrames(120));
  speaking = true;
  await expect(page.getByRole('heading', { name: 'Fairy is speaking.' })).toBeVisible();
  emotion = 'fatigue';
  await expect(page.locator('.eye-visual')).toHaveAttribute('data-expression', 'open');
  const observations = await page.evaluate(() => (window as any).advanceEyeFrames(30 * 60)) as {
    pupil: string | null; spikes: string | null; assembly: string;
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
  expect(values.every(value => Math.abs(value![2] - values[0]![2]) < 0.001)).toBe(true);
  expect(observations.every(value => value.spikes === observations[0].spikes)).toBe(true);
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
  await expect.poll(() => page.locator('.eye-fallback > g').nth(1).getAttribute('transform'))
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
  await page.waitForTimeout(500);
  const speakingFrame = await page.getByTestId('fairy-renderer').locator('canvas').screenshot();
  await page.waitForTimeout(2000);
  const laterSpeakingFrame = await page.getByTestId('fairy-renderer').locator('canvas').screenshot();
  expect(laterSpeakingFrame.equals(speakingFrame)).toBe(true);
  await page.screenshot({ path: 'test-results/task2-speaking.png' });
  expect(errors).toEqual([]);
});
