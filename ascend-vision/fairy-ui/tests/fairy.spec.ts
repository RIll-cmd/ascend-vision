import { test, expect } from '@playwright/test';
import { mkdir } from 'node:fs/promises';

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
  await page.route('**/api/fairy/state', route => route.fulfill({ json: { mode: 'focus', cameraReady: true, audioLevel: level, voiceEnabled: true, muted: false, speaking: false, elapsedSeconds: 61, coreConnection: { configured: true, state: 'connected', lastCheckedAt: '2026-09-30T00:00:00Z' } } }));
  await page.route('**/api/fairy/chat/events**', route => route.fulfill({ json: { events: [], cursor: 0, sessionId: 'test-session' } }));
  await page.route('**/api/fairy/command', route => { commands.push(route.request().postDataJSON().command); return route.fulfill({ status: 202, json: { queued: true } }); });
  await page.goto('/?runtime=1');
  await expect(page.locator('time')).toHaveText('00:01:01');
  level = .8;
  await expect(page.getByRole('heading', { name: 'I’m listening.' })).toBeVisible();
  await page.getByRole('button', { name: 'Pause focus', exact: true }).click();
  await page.getByRole('button', { name: 'Pause microphone', exact: true }).click();
  expect(commands).toEqual(['toggle-focus', 'toggle-voice']);
  // Everything working stays quiet: no status pills in the top bar.
  await expect(page.locator('.top-status .notice')).toHaveCount(0);
  await expect(page.locator('.fairy-chat-core-time').last()).toContainText('Core connected');
  await expect(page.getByRole('alert')).toHaveCount(0);
});

test('Fairy Eye typed chat works while microphone input is unavailable', async ({ page }) => {
  const events: any[] = [];
  let cursor = 0;
  await page.route('**/api/fairy/state', route => route.fulfill({ json: {
    mode: 'focus', cameraReady: true, audioLevel: 0, voiceEnabled: false,
    muted: false, speaking: false, elapsedSeconds: 3,
    coreConnection: { configured: true, state: 'offline', lastCheckedAt: '2026-09-30T00:00:00Z' },
  } }));
  await page.route('**/api/fairy/chat/events**', route => route.fulfill({ json: {
    events: events.filter(event => event.cursor > Number(new URL(route.request().url()).searchParams.get('after') || 0)),
    cursor, sessionId: 'typed-chat-session',
  } }));
  await page.route('**/api/fairy/chat', async route => {
    const text = route.request().postDataJSON().text;
    events.push(
      { cursor: ++cursor, turn_id: 't1', source: 'fairy', kind: 'user', text, status: 'received' },
      { cursor: ++cursor, turn_id: 't1', source: 'fairy', kind: 'status', text: '', status: 'thinking' },
      { cursor: ++cursor, turn_id: 't1', source: 'fairy', kind: 'assistant', text: 'I received your typed message.', status: 'reply' },
    );
    await route.fulfill({ status: 202, json: { messageId: 't1' } });
  });
  await page.goto('/?runtime=1');
  await expect(page.getByText('Core offline', { exact: true })).toBeVisible();
  const composer = page.getByRole('textbox', { name: 'Message Vision' });
  await expect(composer).toBeEnabled();
  await composer.fill('I cannot use my microphone right now.');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.getByText('I received your typed message.')).toBeVisible();
  await expect(page.getByText('YOU', { exact: true })).toBeVisible();
});

test('AI unavailable is shown separately from connected Core and offline replies are labeled', async ({ page }) => {
  await page.route('**/api/fairy/state', route => route.fulfill({ json: {
    mode: 'focus', cameraReady: true, audioLevel: 0, voiceEnabled: true,
    muted: false, speaking: false, elapsedSeconds: 1,
    coreConnection: { configured: true, state: 'connected', lastCheckedAt: '2026-09-30T00:00:00Z' },
    aiStatus: { configured: false, state: 'not-configured', lastFailure: 'not_configured' },
  } }));
  await page.route('**/api/fairy/chat/events**', route => route.fulfill({ json: {
    events: [{ cursor: 1, turn_id: 'turn-one', source: 'fairy', kind: 'assistant',
      text: "I'm Ascend Vision, and my AI connection is unavailable.", status: 'reply',
      reply_source: 'offline', failure_reason: 'not_configured' }],
    cursor: 1, sessionId: 'offline-chat',
  } }));
  await page.goto('/?runtime=1');
  await expect(page.locator('.top-status')).not.toContainText('Core connected');
  await expect(page.locator('.top-status').getByText('AI not configured', { exact: true })).toBeVisible();
  await expect(page.getByText('VISION · OFFLINE RESPONSE')).toBeVisible();
  await expect(page.getByText("I'm Ascend Vision, and my AI connection is unavailable.")).toBeVisible();
  await expect(page.getByText('Voice link active')).toBeVisible();
});

test('AI success, provider failure, local-tool provenance, and runtime disconnect stay distinct', async ({ page }) => {
  let aiState = 'available';
  let disconnected = false;
  await page.route('**/api/fairy/state', route => disconnected
    ? route.fulfill({ status: 503, json: { error: 'runtime unavailable' } })
    : route.fulfill({ json: {
      mode: 'focus', cameraReady: false, audioLevel: 0, voiceEnabled: false,
      muted: false, speaking: false, elapsedSeconds: 0,
      coreConnection: { configured: false, state: 'unconfigured' },
      aiStatus: { configured: true, state: aiState, provider: 'gemini', model: 'gemini-test',
        lastSuccessAt: '2026-09-30T00:00:00Z', lastFailure: aiState === 'request-failed' ? 'timeout' : null },
    } }));
  await page.route('**/api/fairy/chat/events**', route => route.fulfill({ json: {
    events: [
      { cursor: 1, turn_id: 'turn-one', source: 'fairy', kind: 'assistant', text: 'Provider answer', status: 'reply', reply_source: 'model', provider: 'gemini' },
      { cursor: 2, turn_id: 'turn-two', source: 'fairy', kind: 'assistant', text: 'Core read-only answer', status: 'reply', reply_source: 'tool' },
    ], cursor: 2, sessionId: 'provenance-chat',
  } }));
  await page.goto('/?runtime=1');
  await expect(page.locator('.top-status')).not.toContainText('AI responding');
  await expect(page.locator('.fairy-chat-core-time').first()).toContainText('AI responding · gemini');
  await expect(page.getByText('VISION · AI / gemini')).toBeVisible();
  await expect(page.getByText('VISION · LOCAL TOOL')).toBeVisible();
  aiState = 'request-failed';
  await expect(page.locator('.top-status').getByText('AI request failed · timeout', { exact: true })).toBeVisible();
  disconnected = true;
  await expect(page.locator('.top-status').getByText('AI status unknown · runtime disconnected', { exact: true })).toBeVisible();
});

test('Core badge follows offline, stale, and recovered connection states', async ({ page }) => {
  let coreState = 'offline';
  await page.route('**/api/fairy/state', route => route.fulfill({ json: {
    mode: 'focus', cameraReady: false, audioLevel: 0, voiceEnabled: false,
    muted: false, speaking: false, elapsedSeconds: 0,
    coreConnection: { configured: true, state: coreState, lastCheckedAt: '2026-09-30T00:00:00Z' },
  } }));
  await page.goto('/?runtime=1');
  const coreBadge = page.locator('.top-status').getByText('Core offline', { exact: true });
  await expect(coreBadge).toBeVisible();
  coreState = 'stale';
  await expect(page.locator('.top-status').getByText('Core status stale', { exact: true })).toBeVisible();
  coreState = 'connected';
  await expect(page.locator('.top-status .notice')).toHaveCount(0);
});

test('repeating a gesture reopens its panel after the user closes it', async ({ page }) => {
  let panelSequence = 1;
  await page.route('**/api/fairy/state', route => route.fulfill({ json: {
    mode: 'focus', cameraReady: false, audioLevel: 0, voiceEnabled: false,
    muted: false, speaking: false, elapsedSeconds: 0,
    activePanel: 'chat', activePanelSequence: panelSequence,
  } }));
  await page.goto('/?runtime=1');
  await expect(page.getByRole('complementary', { name: 'Talk to Vision' })).toBeVisible();
  await page.getByRole('button', { name: 'Close chat panel' }).click();
  await expect(page.getByRole('complementary', { name: 'Talk to Vision' })).toHaveCount(0);
  panelSequence += 1;
  await expect(page.getByRole('complementary', { name: 'Talk to Vision' })).toBeVisible();
});

test('media permission failures remain visible and do not break the eye', async ({ page }) => {
  await page.addInitScript(() => { navigator.mediaDevices.getUserMedia = async () => { throw new DOMException('Permission denied', 'NotAllowedError'); }; });
  await page.goto('/');
  await expect(page.getByRole('alert')).toContainText('Permission denied');
  await page.getByRole('button', { name: 'Enable microphone' }).click();
  await expect(page.getByRole('alert')).toContainText('Permission denied');
  await expect(page.getByTestId('fairy-renderer').locator('canvas')).toBeVisible();
});

test('Fairy leaves the screen when Vision sleeps or disconnects, and comes back', async ({ page }) => {
  let awayState = 'active';
  let disconnected = false;
  await page.route('**/api/fairy/state', route => disconnected
    ? route.fulfill({ status: 503, json: { error: 'runtime unavailable' } })
    : route.fulfill({ json: { mode: awayState === 'sleeping' ? 'background' : 'focus', cameraReady: true, audioLevel: 0,
      voiceEnabled: true, muted: false, speaking: false, elapsedSeconds: 5, awayState } }));
  await page.goto('/?runtime=1');
  const signal = page.locator('.crt-signal');
  await expect(signal).toHaveCount(0);
  awayState = 'sleeping';
  await expect(signal).toHaveAttribute('data-phase', 'leaving');
  await expect(signal).toHaveAttribute('data-phase', 'away');
  await expect(signal).toContainText('Fairy stepped away');
  await expect(signal).toContainText('wake up');
  awayState = 'active';
  await expect(signal).toHaveAttribute('data-phase', 'returning');
  await expect(signal).toHaveCount(0);
  disconnected = true;
  await expect(signal).toContainText('No signal');
  await expect(page.locator('.top-status')).toContainText('runtime disconnected');
});

test('reduced motion keeps the away static still', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.route('**/api/fairy/state', route => route.fulfill({ status: 503, json: { error: 'offline' } }));
  await page.goto('/?runtime=1');
  const pixels = () => page.locator('.crt-static').evaluate((canvas: HTMLCanvasElement) =>
    Array.from(canvas.getContext('2d')!.getImageData(0, 0, 8, 8).data).join(','));
  await expect(page.locator('.crt-signal')).toHaveAttribute('data-phase', 'away');
  const first = await pixels();
  await page.waitForTimeout(300);
  expect(await pixels()).toBe(first);
});

test('live caption types what Fairy says and shows what she heard', async ({ page }) => {
  const state: any = { mode: 'focus', cameraReady: true, audioLevel: 0, voiceEnabled: true, muted: false, speaking: false, elapsedSeconds: 5 };
  const events: any[] = [];
  await page.route('**/api/fairy/state', route => route.fulfill({ json: state }));
  await page.route('**/api/fairy/chat/events**', route => route.fulfill({ json: {
    events: events.filter(event => event.cursor > Number(new URL(route.request().url()).searchParams.get('after') || 0)),
    cursor: events.length, sessionId: 'caption-test',
  } }));
  await page.goto('/?runtime=1');
  const caption = page.locator('.crt-caption');
  await expect(caption.locator('.crt-caption-line')).toHaveCount(0);
  await page.waitForTimeout(1600);
  state.lastHeard = 'how long have I been working';
  await expect(caption).toContainText('You');
  await expect(caption).toContainText('how long have I been working');
  events.push({ cursor: 1, turn_id: 't1', source: 'voice', kind: 'assistant', text: 'One hour and fifteen minutes. Keep going, you are close to your best today.', status: 'reply' });
  state.speaking = true;
  await expect(caption).toContainText('Fairy');
  await expect(caption.locator('.crt-cursor')).toHaveCount(1);
  const early = (await caption.textContent())!.length;
  await page.waitForTimeout(800);
  expect((await caption.textContent())!.length).toBeGreaterThan(early);
  state.speaking = false;
  await expect(caption).toContainText('close to your best today.');
  await expect(caption.locator('.crt-cursor')).toHaveCount(0);
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

test('audio toggle does not stop capture and expressions stay automatic', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Eye appearance', exact: true }).click();
  await expect(page.getByRole('button', { name: /expression$/ })).toHaveCount(0);
  await page.getByRole('button', { name: 'Audio reactivity', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Audio reactivity' })).toHaveAttribute('aria-pressed', 'false');
  await expect(page.locator('.camera-status')).toContainText('Camera active');
});

test('stats and memory live in the same window and errors never look like zero', async ({ page }) => {
  await page.route('**/api/fairy/state', route => route.fulfill({ json: {
    mode: 'focus', cameraReady: false, audioLevel: 0, voiceEnabled: false,
    muted: false, speaking: false, elapsedSeconds: 0,
  } }));
  await page.route('**/api/fairy/stats**', route => route.fulfill({ json: {
    database_exists: true, summary: { pickups: 4, phone_seconds: 125, focus_seconds: 3600, longest_streak_seconds: 900 },
    days: [{ date: '2026-10-11', pickups: 1, focus: 1, background: 0 }, { date: '2026-10-12', pickups: 3, focus: 3, background: 0 }],
    distractions: [{ site: 'YouTube', seconds: 720 }, { site: 'Reddit', seconds: 95 }],
  } }));
  let pending = [{ id: 7, text: 'I like green tea' }];
  await page.route('**/api/fairy/memory', route => route.fulfill({ json: { enabled: true, pending, active: [] } }));
  await page.route('**/api/fairy/memory/proposals/7/reject', route => { pending = []; return route.fulfill({ json: { rejected: true } }); });
  await page.goto('/?runtime=1');
  await page.getByRole('button', { name: 'Open chat with Vision' }).click();
  await page.getByRole('button', { name: /stats/ }).click();
  await expect(page.getByLabel('Pickup numbers')).toContainText('Phone pickups');
  await expect(page.getByLabel('Pickup numbers')).toContainText('1h 0m');
  await expect(page.getByLabel('Time on distracting sites')).toContainText('YouTube');
  await expect(page.getByLabel('Time on distracting sites')).toContainText('12m');
  await page.getByRole('button', { name: /memory/ }).click();
  await expect(page.getByLabel('Remembered facts')).toContainText('I like green tea');
  await page.getByRole('button', { name: 'Skip' }).click();
  await expect(page.getByLabel('Remembered facts')).not.toContainText('I like green tea');
  await page.route('**/api/fairy/stats**', route => route.fulfill({ status: 503, json: { error: 'Statistics are unavailable.' } }));
  await page.getByRole('button', { name: /stats/ }).click();
  await expect(page.getByRole('alert')).toContainText('Statistics are unavailable.');
  await expect(page.getByLabel('Pickup numbers')).not.toContainText('Phone pickups');
});

test('web tab hides when off; when on it runs a task, asks before acting, and shows sources', async ({ page }) => {
  let webUse = { enabled: false, writes: false };
  await page.route('**/api/fairy/state', route => route.fulfill({ json: {
    mode: 'focus', cameraReady: false, audioLevel: 0, voiceEnabled: false,
    muted: false, speaking: false, elapsedSeconds: 0, webUse,
  } }));
  await page.goto('/?runtime=1');
  await page.getByRole('button', { name: 'Open chat with Vision' }).click();
  await expect(page.getByRole('button', { name: /^\d\s*browser$/ })).toHaveCount(0);

  webUse = { enabled: true, writes: true };
  await expect(page.getByRole('button', { name: /^\d\s*browser$/ })).toBeVisible();
  let polls = 0;
  let decided: any = null;
  await page.route('**/api/browser/tasks', route => route.fulfill({ status: 202, json: { taskId: 'task1', state: 'queued', eventCursor: 0 } }));
  await page.route('**/api/browser/tasks/task1?**', route => {
    polls += 1;
    if (decided) return route.fulfill({ json: { taskId: 'task1', state: 'completed', events: [{ sequence: 3, summary: 'Finished.' }], nextCursor: 3, resetRequired: false,
      result: { findings: [{ text: 'It is version 1.2.', sources: ['https://example.com/docs', 'javascript:alert(1)'] }] } } });
    if (polls < 2) return route.fulfill({ json: { taskId: 'task1', state: 'running', events: [{ sequence: 1, summary: 'Opened example.com' }], nextCursor: 1, resetRequired: false } });
    return route.fulfill({ json: { taskId: 'task1', state: 'waiting_for_user', events: [{ sequence: 2, summary: 'Needs your OK', proposal: {
      action_id: 'a1', digest: 'a'.repeat(64), action: 'click', origin: 'https://example.com', target_label: 'Next', expected_effect: 'Goes to the next page.' } }], nextCursor: 2, resetRequired: false } });
  });
  await page.route('**/api/browser/tasks/task1/decision', route => { decided = route.request().postDataJSON(); return route.fulfill({ json: { taskId: 'task1', state: 'running', eventCursor: 3 } }); });
  await page.getByRole('button', { name: /^\d\s*browser$/ }).click();
  await page.getByLabel('Web task').fill('find the version');
  await page.getByRole('button', { name: 'Look it up' }).click();
  await expect(page.getByText('Opened example.com')).toBeVisible();
  await expect(page.getByRole('group', { name: 'Approve this action' })).toContainText('click at https://example.com');
  await page.getByRole('button', { name: 'Approve', exact: true }).click();
  await expect(page.getByLabel('Result')).toContainText('It is version 1.2.');
  expect(decided).toEqual({ actionId: 'a1', proposalDigest: 'a'.repeat(64), approved: true });
  await expect(page.getByRole('link', { name: 'https://example.com/docs' })).toBeVisible();
  await expect(page.locator('a[href^="javascript"]')).toHaveCount(0);
});

test('key badge warns when keys run low or out, and stays hidden otherwise', async ({ page }) => {
  let aiKeys: any = { gemini: { total: 2, limited: 0, freeAt: null }, groq: { total: 5, limited: 0, freeAt: null } };
  await page.route('**/api/fairy/state', route => route.fulfill({ json: {
    mode: 'focus', cameraReady: false, audioLevel: 0, voiceEnabled: false, muted: false, speaking: false, elapsedSeconds: 0, aiKeys,
  } }));
  await page.goto('/?runtime=1');
  await expect(page.locator('.top-status')).not.toContainText('keys');
  aiKeys = { gemini: { total: 2, limited: 1, freeAt: '2026-10-13T01:40:00Z' }, groq: { total: 5, limited: 0, freeAt: null } };
  await expect(page.locator('.top-status')).toContainText('Gemini keys low · 1 of 2 left');
  aiKeys = { gemini: { total: 2, limited: 2, freeAt: '2026-10-13T01:40:00Z' }, groq: { total: 5, limited: 0, freeAt: null } };
  await expect(page.locator('.top-status')).toContainText('Gemini keys used up until');
});

test('top label shows when Vision is asking or asleep', async ({ page }) => {
  let awayState = 'active';
  let mode = 'focus';
  await page.route('**/api/fairy/state', route => route.fulfill({ json: {
    mode, cameraReady: false, audioLevel: 0, voiceEnabled: false, muted: false, speaking: false, elapsedSeconds: 0, awayState,
  } }));
  await page.goto('/?runtime=1');
  await expect(page.locator('.top-status')).toContainText('Focus mode');
  awayState = 'asking';
  await expect(page.locator('.top-status')).toContainText('Are you still there?');
  awayState = 'sleeping'; mode = 'background';
  await expect(page.locator('.top-status')).toContainText('Sleeping');
});

test('food tab shows the plan, fixes a number and adds water', async ({ page }) => {
  await page.route('**/api/fairy/state', route => route.fulfill({ json: {
    mode: 'focus', cameraReady: false, audioLevel: 0, voiceEnabled: false, muted: false, speaking: false, elapsedSeconds: 0,
  } }));
  let food: any = { ready: true, eaten: 550, target: 1770, glassMl: 250, waterMl: 750, waterTargetMl: 3000,
    meals: [{ name: 'breakfast', at: '08:00' }, { name: 'lunch', at: '14:00' }],
    entries: [{ id: 4, at: '2026-10-13T09:00:00', description: 'chicken adobo, rice', kcal: 550, source: 'ai' }] };
  const calls: any[] = [];
  await page.route('**/api/fairy/food', route => route.fulfill({ json: food }));
  await page.route('**/api/fairy/food/4', route => { calls.push([route.request().method(), route.request().postDataJSON()]); food = { ...food, eaten: 500, entries: [{ ...food.entries[0], kcal: 500, source: 'owner' }] }; return route.fulfill({ json: { updated: true } }); });
  await page.route('**/api/fairy/water', route => { calls.push(['WATER', route.request().postDataJSON()]); food = { ...food, waterMl: 1000 }; return route.fulfill({ json: { added: 250 } }); });
  await page.goto('/?runtime=1');
  await page.getByRole('button', { name: 'Open chat with Vision' }).click();
  await page.getByRole('button', { name: /^\d\s*food$/ }).click();
  const panel = page.getByLabel('Food and water', { exact: true }).last();
  await expect(panel).toContainText('550 kcal');
  await expect(panel).toContainText('1,220 kcal');
  await expect(panel).toContainText('3 / 12 glasses');
  await expect(panel).toContainText('breakfast 8:00 AM · lunch 2:00 PM');
  await expect(panel).toContainText('(estimate)');
  await page.getByRole('button', { name: 'Fix calories for chicken adobo, rice' }).click();
  await page.getByLabel('Calories for chicken adobo, rice').fill('500');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(panel).toContainText('1,270 kcal');
  await page.getByRole('button', { name: '+1 glass of water' }).click();
  await expect(panel).toContainText('4 / 12 glasses');
  expect(calls).toEqual([['PATCH', { kcal: 500 }], ['WATER', { ml: 250 }]]);
});

test('food tab explains setup when there is no plan', async ({ page }) => {
  await page.route('**/api/fairy/state', route => route.fulfill({ json: {
    mode: 'focus', cameraReady: false, audioLevel: 0, voiceEnabled: false, muted: false, speaking: false, elapsedSeconds: 0,
  } }));
  await page.route('**/api/fairy/food', route => route.fulfill({ json: { ready: false, eaten: 0, target: null, entries: [],
    waterMl: 0, waterTargetMl: null, glassMl: 250, meals: [] } }));
  await page.goto('/?runtime=1');
  await page.getByRole('button', { name: 'Open chat with Vision' }).click();
  await page.getByRole('button', { name: /^\d\s*food$/ }).click();
  await expect(page.getByText('set up my food plan')).toBeVisible();
});

test('face detection runs with hidden preview and attention moves inside the stationary orb', async ({ page }) => {
  await page.addInitScript(() => {
    (window as any).hasTestFace = true;
    (window as any).faceCalls = 0;
    (window as any).FaceDetector = class {
      async detect() {
        (window as any).faceCalls++;
        return (window as any).hasTestFace ? [{ boundingBox: { x: 0, y: 40, width: 100, height: 100 } }] : [];
      }
    };
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (type: string, ...args: any[]) {
      if (type.includes('webgl')) return null;
      return (original as any).call(this, type, ...args);
    } as any;
  });
  await page.goto('/');
  await expect(page.getByTestId('tracking-source')).toHaveText('Face tracking');
  await expect(page.getByRole('complementary', { name: 'Camera preview' })).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => (window as any).faceCalls)).toBeGreaterThan(2);
  const facePupil = page.locator('.eye-fallback > g').nth(1);
  await expect.poll(async () => {
    const transform = await facePupil.getAttribute('transform');
    const match = transform?.match(/^translate\(([-\d.e]+) ([-\d.e]+)\)/);
    return match ? Math.hypot(Number(match[1]), Number(match[2])) : 0;
  }).toBeGreaterThan(3);
  await expect.poll(async () => {
    const transform = await page.locator('.eye-fallback > g').first().getAttribute('transform');
    const degrees = Number(transform?.match(/^rotate\(([-\d.e]+)/)?.[1] ?? 0);
    return degrees;
  }).toBeGreaterThan(0.5);
  await page.evaluate(() => { (window as any).hasTestFace = false; });
  await expect(page.getByTestId('tracking-source')).toHaveText('Pointer tracking');
  const eye = page.locator('.eye-stage');
  const box = (await eye.boundingBox())!;
  await page.mouse.move(box.x + box.width * .98, box.y + box.height / 2);
  await expect.poll(() => page.locator('.eye-fallback > g').nth(1).getAttribute('transform')).not.toContain('translate(0 0)');
  await expect(page.locator('.eye-visual')).toHaveCSS('transform', 'none');
  await page.mouse.move(0, 0);
  await expect.poll(async () => {
    const transform = await page.locator('.eye-fallback > g').nth(1).getAttribute('transform');
    const match = transform?.match(/^translate\(([-\d.e]+) ([-\d.e]+)\)/);
    return match ? Math.hypot(Number(match[1]), Number(match[2])) : Number.POSITIVE_INFINITY;
  }).toBeLessThan(8);
});

test('eye and chat keep separate readable regions at laptop, mobile and 200% equivalent zoom', async ({ page }) => {
  const events = Array.from({ length: 35 }, (_, index) => ({
    cursor: index + 1, turn_id: `turn-${index}`, source: 'fairy', kind: index % 2 ? 'assistant' : 'user',
    text: `Transcript item ${index + 1}: ${'A long conversation stays in its own scrolling region. '.repeat(3)}`,
    status: 'reply',
  }));
  await page.route('**/api/fairy/state', route => route.fulfill({ json: {
    mode: 'focus', cameraReady: true, audioLevel: 0, voiceEnabled: true,
    muted: false, speaking: false, elapsedSeconds: 62,
    coreConnection: { configured: true, state: 'offline' },
  } }));
  await page.route('**/api/fairy/chat/events**', route => route.fulfill({ json: {
    events: events.filter(event => event.cursor > Number(new URL(route.request().url()).searchParams.get('after') || 0)),
    cursor: events.length, sessionId: 'layout-test',
  } }));
  await page.route('**/api/fairy/chat', route => route.fulfill({ status: 202, json: { messageId: 'layout-message' } }));
  await page.goto('/?runtime=1');
  await expect(page.getByText('Transcript item 35:', { exact: false })).toBeVisible();
  const eye = page.locator('.eye-stage');
  const chat = page.getByRole('complementary', { name: 'Talk to Vision' });
  const composer = page.getByRole('textbox', { name: 'Message Vision' });
  await mkdir('artifacts/fairy-eye-review', { recursive: true });
  for (const { name, width, height } of [
    { name: 'laptop', width: 1280, height: 800 },
    { name: 'compact-laptop', width: 1024, height: 768 },
    { name: 'mobile', width: 390, height: 844 },
    { name: 'zoom-200', width: 720, height: 450 },
  ]) {
    await page.setViewportSize({ width, height });
    const eyeBox = await eye.boundingBox();
    const chatBox = await chat.boundingBox();
    expect(eyeBox && chatBox).toBeTruthy();
    if (width >= 960) expect(eyeBox!.x + eyeBox!.width).toBeLessThanOrEqual(chatBox!.x);
    else expect(eyeBox!.y + eyeBox!.height).toBeLessThanOrEqual(chatBox!.y);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await expect(composer).toBeVisible();
    for (const label of ['Pause microphone', 'Mute speech output', 'Stop speech or current browser tasks and pending actions']) {
      const box = await page.getByRole('button', { name: label }).boundingBox();
      expect(box?.height).toBeGreaterThanOrEqual(44);
      expect(box?.width).toBeGreaterThanOrEqual(44);
    }
    const sendBox = await page.getByRole('button', { name: 'Send', exact: true }).boundingBox();
    expect(sendBox?.height).toBeGreaterThanOrEqual(44);
    await page.screenshot({ path: `artifacts/fairy-eye-review/task5-${name}.png`, fullPage: true });
  }
  for (const { name, width, height } of [
    { name: 'laptop-camera', width: 1280, height: 800 },
    { name: 'mobile-camera', width: 390, height: 844 },
  ]) {
    await page.setViewportSize({ width, height });
    await page.getByRole('button', { name: 'Show camera', exact: true }).click();
    const cameraBox = await page.getByRole('complementary', { name: 'Camera preview' }).boundingBox();
    const chatBox = await chat.boundingBox();
    expect(cameraBox && chatBox).toBeTruthy();
    if (width >= 960) expect(cameraBox!.x + cameraBox!.width).toBeLessThanOrEqual(chatBox!.x);
    else expect(cameraBox!.y).toBeGreaterThanOrEqual(chatBox!.y + chatBox!.height);
    await page.screenshot({ path: `artifacts/fairy-eye-review/task5-${name}.png`, fullPage: true });
    await page.getByRole('button', { name: 'Hide camera', exact: true }).click();
  }
  await page.setViewportSize({ width: 390, height: 844 });
  const eyeWidth = (await eye.boundingBox())!.width;
  await composer.fill('A message from the phone layout');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(composer).toHaveValue('');
  expect((await eye.boundingBox())!.width).toBe(eyeWidth);
  await composer.focus();
  expect(await composer.evaluate(element => {
    const style = getComputedStyle(element);
    return style.outlineStyle !== 'none' || style.boxShadow !== 'none';
  })).toBe(true);
});
