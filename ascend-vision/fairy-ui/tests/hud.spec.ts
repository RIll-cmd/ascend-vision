import { test, expect } from '@playwright/test';

test('AI HUD overlays targeting reticle and responds to posture & phone telemetry', async ({ page }) => {
  const posture = 'good';
  const phoneBox = {
    x1: 0.2,
    y1: 0.2,
    x2: 0.5,
    y2: 0.7,
    confidence: 0.95,
    posture: 'VERTICAL',
  };

  await page.route('**/api/fairy/state', route => {
    route.fulfill({
      json: {
        mode: 'focus',
        cameraReady: true,
        audioLevel: 0.25,
        voiceEnabled: true,
        muted: false,
        speaking: false,
        elapsedSeconds: 42,
        posture,
        phoneBox,
        gesture: '5 fingers',
        lastHeard: 'Focus on coding',
        hands: [{ points: [[0.5, 0.5], [0.55, 0.55]] }],
        eyePoints: [[0.4, 0.4], [0.6, 0.4]],
        lipPoints: [[0.5, 0.6]],
      },
    });
  });

  // Mock camera frame so preview shows ready
  await page.route('**/api/fairy/frame.jpg', route => {
    // Return 1x1 black jpeg
    const dummyJpeg = Buffer.from('/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA=', 'base64');
    route.fulfill({
      status: 200,
      contentType: 'image/jpeg',
      body: dummyJpeg,
    });
  });

  await page.goto('/?runtime=1');

  // Verify topbar gesture status badge
  await expect(page.locator('.topbar')).toContainText('5 FINGERS');

  // Verify Dynamic Island subtitle toast
  await expect(page.getByTestId('dynamic-island')).toBeVisible();
  await expect(page.getByText('Focus on coding')).toBeVisible();

  // Open camera preview
  await page.getByRole('button', { name: 'Show camera', exact: true }).click();
  const preview = page.getByRole('complementary', { name: 'Camera preview' });
  await expect(preview).toBeVisible();

  // Verify Clean / AI HUD toggle is present
  const cleanTab = preview.getByRole('tab', { name: 'Clean' });
  const hudTab = preview.getByRole('tab', { name: 'AI HUD' });
  await expect(cleanTab).toBeVisible();
  await expect(hudTab).toBeVisible();

  // Wait for targeting reticle to appear in HUD mode
  await expect(preview.getByTestId('targeting-reticle')).toBeVisible();

  // Switch to Clean mode
  await cleanTab.click();
  await expect(cleanTab).toHaveAttribute('aria-selected', 'true');
  await expect(preview.getByTestId('targeting-reticle')).toHaveCount(0);

  // Switch back to AI HUD mode
  await hudTab.click();
  await expect(hudTab).toHaveAttribute('aria-selected', 'true');
  await expect(preview.getByTestId('targeting-reticle')).toBeVisible();
});

test('all 10 expanded expressions are selectable and render on the eye without errors', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'error' && /shader|WebGL|GLSL/i.test(message.text())) {
      errors.push(message.text());
    }
  });

  await page.goto('/');
  const eye = page.getByRole('img', { name: 'Fairy eye, an audio-reactive celestial iris' });

  const expressions = ['open', 'happy', 'sleepy', 'sad', 'focus', 'stern', 'curious', 'surprised', 'half', 'squint'];
  for (const expr of expressions) {
    const btn = page.getByRole('button', { name: `${expr} expression`, exact: true });
    await expect(btn).toBeVisible();
    await btn.click();
    await expect(eye).toHaveAttribute('data-expression', expr);
  }

  expect(errors).toEqual([]);
});

test('mood colors dynamically update according to mood specifications', async ({ page }) => {
  await page.goto('/');
  const eye = page.getByRole('img', { name: 'Fairy eye, an audio-reactive celestial iris' });

  // 1. angry = red
  const angryBtn = page.getByRole('button', { name: 'angry expression', exact: true });
  await expect(angryBtn).toBeVisible();
  await angryBtn.click();
  await expect(eye).toHaveAttribute('data-expression', 'angry');
  await expect(page.locator('main.fairy-app')).toHaveCSS('--fairy-glow', '#ff2e3b');

  // 2. happy = yellow
  const happyBtn = page.getByRole('button', { name: 'happy expression', exact: true });
  await happyBtn.click();
  await expect(eye).toHaveAttribute('data-expression', 'happy');
  await expect(page.locator('main.fairy-app')).toHaveCSS('--fairy-glow', '#ffc814');

  // 3. sad = other shade of blue
  const sadBtn = page.getByRole('button', { name: 'sad expression', exact: true });
  await sadBtn.click();
  await expect(eye).toHaveAttribute('data-expression', 'sad');
  await expect(page.locator('main.fairy-app')).toHaveCSS('--fairy-glow', '#3867d6');

  // 4. surprised = light blue
  const surprisedBtn = page.getByRole('button', { name: 'surprised expression', exact: true });
  await surprisedBtn.click();
  await expect(eye).toHaveAttribute('data-expression', 'surprised');
  await expect(page.locator('main.fairy-app')).toHaveCSS('--fairy-glow', '#5ce1e6');

  // 5. disappointed = grey
  const disappointedBtn = page.getByRole('button', { name: 'disappointed expression', exact: true });
  await expect(disappointedBtn).toBeVisible();
  await disappointedBtn.click();
  await expect(eye).toHaveAttribute('data-expression', 'disappointed');
  await expect(page.locator('main.fairy-app')).toHaveCSS('--fairy-glow', '#8f9baa');

  // 6. normal = original color (celestial theme glow #58bcff)
  const normalBtn = page.getByRole('button', { name: 'open expression', exact: true });
  await normalBtn.click();
  await expect(eye).toHaveAttribute('data-expression', 'open');
  await expect(page.locator('main.fairy-app')).toHaveCSS('--fairy-glow', '#58bcff');
});

