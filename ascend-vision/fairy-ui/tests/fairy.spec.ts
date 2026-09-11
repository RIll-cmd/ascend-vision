import { test, expect } from '@playwright/test';

test('eye renders, appearance changes, and mobile layout fits', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error' && /shader|WebGL|GLSL/i.test(message.text())) errors.push(message.text()); });
  await page.goto('/');
  await expect(page.getByTestId('fairy-renderer').locator('canvas')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Here, with you.' })).toBeVisible();
  await expect(page.getByRole('complementary', { name: 'Camera preview' })).toHaveCount(0);
  await expect(page.locator('.camera-status')).toContainText('Camera active');
  await page.screenshot({ path: 'test-results/fairy-desktop.png' });
  await page.getByRole('button', { name: 'Eye appearance', exact: true }).click();
  await page.getByRole('button', { name: 'Aurora theme' }).click();
  await expect(page.getByRole('button', { name: 'Aurora theme' })).toHaveAttribute('aria-pressed', 'true');
  await page.getByLabel('Reduce eye motion').check();
  await page.keyboard.press('Escape');
  await expect(page.getByLabel('Eye appearance settings')).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: 'test-results/fairy-mobile.png' });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(errors).toEqual([]);
});

test('camera stays live while hidden; camera and microphone stop on unmount', async ({ page }) => {
  await page.addInitScript(() => {
    const streams: MediaStream[] = [];
    (window as any).testStreams = streams;
    const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = async constraints => { const stream = await original(constraints); streams.push(stream); return stream; };
  });
  await page.goto('/tests/harness.html');
  await expect.poll(() => page.evaluate(() => (window as any).testStreams.length)).toBe(1);
  await page.getByRole('button', { name: 'Show camera', exact: true }).click();
  await expect(page.locator('video')).toBeVisible();
  await expect.poll(() => page.locator('video').evaluate((video: HTMLVideoElement) => video.readyState)).toBeGreaterThan(1);
  await page.getByRole('button', { name: 'Hide camera', exact: true }).click();
  expect(await page.evaluate(() => (window as any).testStreams[0].getVideoTracks()[0].readyState)).toBe('live');
  await page.getByRole('button', { name: 'Enable microphone' }).click();
  await expect.poll(() => page.evaluate(() => (window as any).testStreams.length)).toBe(2);
  await page.getByRole('button', { name: 'Eye appearance', exact: true }).click();
  await page.getByRole('button', { name: 'Solstice theme' }).click();
  expect(await page.evaluate(() => (window as any).testStreams.length)).toBe(2);
  await page.getByRole('button', { name: 'Toggle component mount' }).click();
  await expect.poll(() => page.evaluate(() => (window as any).testStreams.every((stream: MediaStream) => stream.getTracks().every(track => track.readyState === 'ended')))).toBe(true);
});

test('runtime mode uses Python telemetry and commands without browser capture', async ({ page }) => {
  await page.addInitScript(() => { navigator.mediaDevices.getUserMedia = async () => { throw new Error('Runtime must not open browser devices'); }; });
  let level = 0;
  const commands: string[] = [];
  await page.route('**/api/fairy/state', route => route.fulfill({ json: { mode: 'focus', cameraReady: true, audioLevel: level, voiceEnabled: true, muted: false, speaking: false, elapsedSeconds: 61 } }));
  await page.route('**/api/fairy/command', route => { commands.push(route.request().postDataJSON().command); return route.fulfill({ status: 202, json: { queued: true } }); });
  await page.goto('/?runtime=1');
  await expect(page.locator('time')).toHaveText('00:01:01');
  level = .8;
  await expect(page.getByRole('heading', { name: 'I’m listening.' })).toBeVisible();
  await page.getByRole('button', { name: 'Pause focus', exact: true }).click();
  await page.getByRole('button', { name: 'Mute voice', exact: true }).click();
  expect(commands).toEqual(['toggle-focus', 'toggle-voice']);
  await expect(page.getByRole('alert')).toHaveCount(0);
});

test('media permission failures remain visible and do not break the eye', async ({ page }) => {
  await page.addInitScript(() => { navigator.mediaDevices.getUserMedia = async () => { throw new DOMException('Permission denied', 'NotAllowedError'); }; });
  await page.goto('/');
  await expect(page.getByRole('alert')).toContainText('Permission denied');
  await page.getByRole('button', { name: 'Enable microphone' }).click();
  await expect(page.getByRole('alert')).toContainText('Permission denied');
  await expect(page.getByTestId('fairy-renderer').locator('canvas')).toBeVisible();
});

test('camera permission granted after unmount immediately releases the stream', async ({ page }) => {
  await page.addInitScript(() => {
    const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = constraints => new Promise(resolve => {
      (window as any).grantCamera = async () => {
        const stream = await original(constraints);
        (window as any).lateStream = stream;
        resolve(stream);
      };
    });
  });
  await page.goto('/tests/harness.html');
  await expect.poll(() => page.evaluate(() => typeof (window as any).grantCamera)).toBe('function');
  await page.getByRole('button', { name: 'Toggle component mount' }).click();
  await page.evaluate(() => (window as any).grantCamera());
  await expect.poll(() => page.evaluate(() => (window as any).lateStream.getTracks().every((track: MediaStreamTrack) => track.readyState === 'ended'))).toBe(true);
});

test('SVG fallback is visible when WebGL is unavailable', async ({ page }) => {
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (type: string, ...args: any[]) {
      if (type.includes('webgl')) return null;
      return (original as any).call(this, type, ...args);
    } as any;
  });
  await page.goto('/');
  await expect(page.locator('.eye-fallback')).toBeVisible();
  await page.getByRole('button', { name: 'Show camera', exact: true }).click();
  await expect(page.locator('video')).toBeVisible();
});

test('all expressions render distinctly and audio toggle does not stop capture', async ({ page }) => {
  await page.goto('/');
  const eye = page.getByRole('img', { name: 'Fairy eye, an audio-reactive celestial iris' });
  const pictures: Buffer[] = [];
  for (const expression of ['open', 'half', 'squint', 'focus']) {
    await page.getByRole('button', { name: `${expression} expression`, exact: true }).click();
    await expect(eye).toHaveAttribute('data-expression', expression);
    await page.waitForTimeout(500); // Allow the intentional interpolated shutter to settle.
    pictures.push(await eye.screenshot({ path: `test-results/eye-${expression}.png` }));
  }
  expect(new Set(pictures.map(image => image.toString('base64'))).size).toBe(4);
  await page.getByRole('button', { name: 'Audio reactivity', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Audio reactivity' })).toHaveAttribute('aria-pressed', 'false');
  await expect(page.locator('.camera-status')).toContainText('Camera active');
});

test('face detection runs with hidden preview and falls back after face loss', async ({ page }) => {
  await page.addInitScript(() => {
    (window as any).hasTestFace = true;
    (window as any).faceCalls = 0;
    (window as any).FaceDetector = class {
      async detect() {
        (window as any).faceCalls++;
        return (window as any).hasTestFace ? [{ boundingBox: { x: 0, y: 40, width: 100, height: 100 } }] : [];
      }
    };
  });
  await page.goto('/');
  await expect(page.getByTestId('tracking-source')).toHaveText('FACE TRACKING');
  await expect(page.getByRole('complementary', { name: 'Camera preview' })).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => (window as any).faceCalls)).toBeGreaterThan(2);
  await page.evaluate(() => { (window as any).hasTestFace = false; });
  await expect(page.getByTestId('tracking-source')).toHaveText('POINTER TRACKING');
  const eye = page.locator('.eye-stage');
  const box = (await eye.boundingBox())!;
  await page.mouse.move(box.x + box.width * .98, box.y + box.height / 2);
  await expect.poll(() => page.locator('.eye-visual').evaluate(element => new DOMMatrix(getComputedStyle(element).transform).m41)).toBeGreaterThan(5);
  await page.mouse.move(0, 0);
  await expect.poll(() => page.locator('.eye-visual').evaluate(element => Math.abs(new DOMMatrix(getComputedStyle(element).transform).m41))).toBeLessThan(1);
});
