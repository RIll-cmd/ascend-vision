import { expect, test } from '@playwright/test';

test.use({ video: 'on' });

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
  await expect.poll(() => page.locator('.eye-fallback > g').getAttribute('transform'))
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
