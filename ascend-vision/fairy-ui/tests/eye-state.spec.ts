import { test, expect } from '@playwright/test';
import { automaticEyeExpression, resolveEyeMode } from '../src/lib/eye-state';

test('eye mode has one explicit runtime precedence, independent of Core', () => {
  const active = { runtime: true, connected: true, paused: false, speaking: false, thinking: false, listening: false };
  expect(resolveEyeMode({ ...active, connected: false, speaking: true, thinking: true, listening: true })).toBe('offline');
  expect(resolveEyeMode({ ...active, paused: true, speaking: true, thinking: true, listening: true })).toBe('paused');
  expect(resolveEyeMode({ ...active, speaking: true, thinking: true, listening: true })).toBe('speaking');
  expect(resolveEyeMode({ ...active, thinking: true, listening: true })).toBe('thinking');
  expect(resolveEyeMode({ ...active, listening: true })).toBe('listening');
  expect(resolveEyeMode(active)).toBe('idle');
  expect(resolveEyeMode({ ...active, runtime: false, connected: false, listening: true })).toBe('listening');
});

test('automatic expression ignores phone/posture and returns to neutral', () => {
  expect(automaticEyeExpression({ phoneBox: {}, posture: 'slouched' })).toBe('open');
  expect(automaticEyeExpression({ emotion: 'smiling' })).toBe('happy');
  expect(automaticEyeExpression({ emotion: 'fatigue' })).toBe('sleepy');
  expect(automaticEyeExpression({ emotion: 'stressed' })).toBe('sad');
  expect(automaticEyeExpression({ emotion: 'neutral' })).toBe('open');
  expect(automaticEyeExpression(undefined)).toBe('open');
});

test('runtime mode, transcript, mic, speech and Core status stay independent', async ({ page }) => {
  const state = {
    mode: 'focus', cameraReady: true, audioLevel: 0, voiceEnabled: true,
    muted: false, speaking: false, elapsedSeconds: 0, lastHeard: 'User said hello',
    phoneBox: { x1: 0.1 }, posture: 'slouched', emotion: 'neutral',
    coreConnection: { configured: true, state: 'offline' },
  };
  let connected = true;
  let thinking = false;
  await page.route('**/api/fairy/state', route => connected
    ? route.fulfill({ json: state }) : route.fulfill({ status: 503, json: { error: 'offline' } }));
  await page.route('**/api/fairy/chat/events**', route => route.fulfill({ json: {
    events: thinking && Number(new URL(route.request().url()).searchParams.get('after') || 0) < 1
      ? [{ cursor: 1, turn_id: 't1', source: 'fairy', kind: 'status', text: '', status: 'thinking' }] : [],
    cursor: thinking ? 1 : 0, sessionId: 'test-session',
  } }));
  await page.goto('/?runtime=1');
  const stage = page.locator('.eye-stage');
  const eye = page.getByRole('img', { name: 'Fairy eye, an audio-reactive celestial iris' });
  await expect(stage).toHaveAttribute('data-eye-mode', 'idle');
  await expect(page.getByRole('heading', { name: 'Here, with you.' })).toBeVisible();
  await expect(eye).toHaveAttribute('data-expression', 'open');
  await expect(page.getByText('User said hello')).toBeVisible();
  await expect(page.getByText('Core offline', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Pause microphone' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('button', { name: 'Mute speech output' })).toHaveAttribute('aria-pressed', 'true');

  state.audioLevel = 0.8;
  await expect(stage).toHaveAttribute('data-eye-mode', 'listening');
  await expect(page.getByRole('heading', { name: 'I’m listening.' })).toBeVisible();
  state.speaking = true;
  await expect(stage).toHaveAttribute('data-eye-mode', 'speaking');
  await expect(page.getByRole('heading', { name: 'Fairy is speaking.' })).toBeVisible();
  state.speaking = false;
  state.audioLevel = 0;
  thinking = true;
  await expect(stage).toHaveAttribute('data-eye-mode', 'thinking');
  await expect(page.getByRole('heading', { name: 'Vision is thinking.' })).toBeVisible();
  state.mode = 'background';
  await expect(stage).toHaveAttribute('data-eye-mode', 'paused');
  await expect(page.getByRole('heading', { name: 'Take a moment.' })).toBeVisible();
  connected = false;
  await expect(stage).toHaveAttribute('data-eye-mode', 'offline');
  await expect(page.getByRole('heading', { name: 'Waiting for Vision.' })).toBeVisible();
  await expect(page.getByText('Core offline', { exact: true })).toBeVisible();
});

test('automatic expression clears, manual expression persists until Automatic is chosen', async ({ page }) => {
  const state = { mode: 'focus', cameraReady: false, audioLevel: 0, voiceEnabled: false,
    muted: false, speaking: false, elapsedSeconds: 0, emotion: 'smiling', posture: 'slouched', phoneBox: { x1: .1 } };
  await page.route('**/api/fairy/state', route => route.fulfill({ json: state }));
  await page.goto('/?runtime=1');
  const eye = page.getByRole('img', { name: 'Fairy eye, an audio-reactive celestial iris' });
  await expect(eye).toHaveAttribute('data-expression', 'happy');
  state.emotion = 'neutral';
  await expect(eye).toHaveAttribute('data-expression', 'open');
  await page.getByRole('button', { name: 'Eye appearance', exact: true }).click();
  await page.getByRole('button', { name: 'angry expression' }).click();
  await expect(eye).toHaveAttribute('data-expression', 'angry');
  await expect(page.getByText('Manual · Angry')).toBeVisible();
  state.emotion = 'smiling';
  await expect(eye).toHaveAttribute('data-expression', 'angry');
  await page.getByRole('button', { name: 'Automatic expression' }).click();
  await expect(eye).toHaveAttribute('data-expression', 'happy');
  await expect(page.getByText('Automatic · Happy')).toBeVisible();
});
