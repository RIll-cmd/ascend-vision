"use client";

import { useEffect, useId, useRef, useState } from 'react';
import { Mesh, Program, Renderer, Triangle } from 'ogl';
import { cn } from '@/lib/utils';
import { useMicrophone } from '@/hooks/use-microphone';
import { stepEyeAnimation, type EyeAnimationState, type EyeMode } from '@/lib/eye-animation';
import { constrainGaze, EXPRESSIONS, stepSpring, type EyeExpression } from '@/lib/eye-motion';
export type { EyeExpression } from '@/lib/eye-motion';

export interface FairyEyeProps {
  className?: string;
  pupilOffset?: { x: number; y: number };
  irisScale?: number;
  dilation?: number;
  baseColor?: string;
  glowColor?: string;
  coreColor?: string;
  enableVoiceControl?: boolean;
  voiceSensitivity?: number;
  /** Supplying external RMS disables browser capture (e.g. Python's voice listener). */
  audioLevel?: number;
  onVoiceDetected?: (detected: boolean) => void;
  onAudioError?: (message: string) => void;
  reducedMotion?: boolean;
  expression?: EyeExpression;
  audioReactive?: boolean;
  /** Synthesized speaking envelope when a TTS activity flag is available without PCM. */
  speaking?: boolean;
  motionMode?: EyeMode;
  /** Hue degree rotation (-180..180) for dynamic palette shifts. */
  hue?: number;
  maxHoverIntensity?: number;
}

const vertex = `attribute vec2 position; attribute vec2 uv; varying vec2 vUv;
void main(){vUv=uv;gl_Position=vec4(position,0.,1.);}`;

// Procedural geometry only: no external textures, images, or prerecorded clips.
const fragment = `precision highp float;
varying vec2 vUv;
uniform float level, irisScale, dilation, blink, lid, lowerLid, lidSlant, focused, hue;
uniform vec2 gaze, resolution;
uniform vec3 baseColor, glowColor, coreColor;

float disk(float r, float radius, float feather) {
  return 1.0 - smoothstep(radius - feather, radius + feather, r);
}

float eyeHeight(float x) {
  float xx = abs(x) / 0.75;
  return 0.43 * pow(max(0.0, 1.0 - xx * xx), 0.82);
}

vec3 rgb2yiq(vec3 c) {
  float y = dot(c, vec3(0.299, 0.587, 0.114));
  float i = dot(c, vec3(0.596, -0.274, -0.322));
  float q = dot(c, vec3(0.211, -0.523, 0.312));
  return vec3(y, i, q);
}

vec3 yiq2rgb(vec3 c) {
  float r = c.x + 0.956 * c.y + 0.621 * c.z;
  float g = c.x - 0.272 * c.y - 0.647 * c.z;
  float b = c.x - 1.106 * c.y + 1.703 * c.z;
  return vec3(r, g, b);
}

vec3 adjustHue(vec3 color, float hueDeg) {
  if (abs(hueDeg) < 0.001) return color;
  float hueRad = hueDeg * 3.14159265 / 180.0;
  vec3 yiq = rgb2yiq(color);
  float cosA = cos(hueRad);
  float sinA = sin(hueRad);
  float i = yiq.y * cosA - yiq.z * sinA;
  float q = yiq.y * sinA + yiq.z * cosA;
  yiq.y = i;
  yiq.z = q;
  return yiq2rgb(yiq);
}

void main() {
  vec2 p = (vUv - 0.5) * 2.0;
  p.x *= resolution.x / resolution.y;
  vec3 bCol = adjustHue(baseColor, hue);
  vec3 gCol = adjustHue(glowColor, hue);
  vec3 cCol = adjustHue(coreColor, hue);
  float height = eyeHeight(p.x);
  float edgeDistance = max(abs(p.x) - 0.75, abs(p.y) - height);
  float scleraMask = 1.0 - smoothstep(-0.006, 0.006, edgeDistance);
  float rim = exp(-pow(edgeDistance * 125.0, 2.0));
  vec3 sclera = mix(vec3(0.94, 0.97, 0.98), vec3(0.61, 0.73, 0.79),
    0.48 * smoothstep(0.08, 0.43, abs(p.y)));
  vec3 col = sclera;

  // The iris is half the visible eye width and clipped by the sclera mask.
  vec2 irisCenter = gaze * vec2(0.15, 0.11);
  vec2 irisPoint = (p - irisCenter) / irisScale;
  float irisRadius = length(irisPoint);
  float irisMask = disk(irisRadius, 0.37, 0.006);
  float radial = 1.0 - smoothstep(0.06, 0.37, irisRadius);
  float texture = sin(atan(irisPoint.y, irisPoint.x) * 22.0 + irisRadius * 25.0) * 0.035;
  vec3 iris = mix(bCol * 0.62, gCol * (0.70 + level * 0.16), clamp(radial * 0.76 + texture, 0.0, 1.0));
  col = mix(col, iris, irisMask);
  col += gCol * 0.13 * exp(-pow((irisRadius - 0.36) * 95.0, 2.0)) * irisMask;

  float pupilRadius = clamp(0.16 + dilation * 0.018 - focused * 0.016, 0.11, 0.20);
  col = mix(col, vec3(0.016, 0.036, 0.055), disk(irisRadius, pupilRadius, 0.006));
  vec2 reflection = p - vec2(-0.095, 0.115);
  float catchlight = disk(length(reflection), 0.036, 0.008);
  col = mix(col, mix(vec3(1.0), cCol, 0.12), catchlight * irisMask);

  float closure = clamp((0.65 - lid) * 0.8 + blink * 0.9, 0.0, 1.0);
  float upperEdge = height - closure * 0.55 + p.x * lidSlant * 0.12;
  float lowerEdge = -height + max(lowerLid, 0.0) * 0.32 + blink * 0.42;
  float upperCover = smoothstep(upperEdge - 0.006, upperEdge + 0.006, p.y);
  float lowerCover = 1.0 - smoothstep(lowerEdge - 0.006, lowerEdge + 0.006, p.y);
  col = mix(col, vec3(0.025, 0.055, 0.078), clamp(upperCover + lowerCover, 0.0, 1.0));
  col += gCol * 0.24 * exp(-pow((p.y - upperEdge) * 105.0, 2.0)) * scleraMask;
  col += gCol * 0.13 * exp(-pow((p.y - lowerEdge) * 105.0, 2.0)) * scleraMask;
  col += gCol * (0.22 + level * 0.13) * rim;
  gl_FragColor = vec4(max(col, vec3(0.0)), max(scleraMask, rim * 0.68));
}`;

function rgb(hex: string) {
  const valid = /^#[0-9a-f]{6}$/i.test(hex) ? hex : '#3987c6';
  return [1, 3, 5].map(index => parseInt(valid.slice(index, index + 2), 16) / 255);
}
const clamp = (value: number, min: number, max: number) => Number.isFinite(value) ? Math.min(max, Math.max(min, value)) : min;
const eyeOutline = 'M50 200 C107 84 293 84 350 200 C293 316 107 316 50 200 Z';

/** Canonical Fairy Eye with a shared bounded motion state for WebGL and SVG. */
export function FairyEye({
  className, pupilOffset = { x: 0, y: 0 }, irisScale = 1, dilation = 0,
  baseColor = '#175286', glowColor = '#58bcff', coreColor = '#e8f5ff',
  enableVoiceControl = false, voiceSensitivity = 1.5, audioLevel,
  onVoiceDetected, onAudioError, reducedMotion = false,
  expression = 'open', audioReactive = true, speaking = false, motionMode,
  hue = 0, maxHoverIntensity = 0.8,
}: FairyEyeProps) {
  const host = useRef<HTMLDivElement>(null);
  const assembly = useRef<HTMLDivElement>(null);
  const fallbackPupil = useRef<SVGGElement>(null);
  const fallbackUpperLid = useRef<SVGPathElement>(null);
  const fallbackLowerLid = useRef<SVGPathElement>(null);
  const clipId = useId().replace(/:/g, '');
  const scleraGradientId = `${clipId}-sclera`;
  const irisGradientId = `${clipId}-iris`;
  const [webgl, setWebgl] = useState(false);
  const mic = useMicrophone(enableVoiceControl && audioLevel === undefined, voiceSensitivity);
  const latest = useRef({ pupilOffset, irisScale, dilation, baseColor, glowColor, coreColor, audioLevel, onVoiceDetected, reducedMotion, expression, audioReactive, speaking, motionMode, hue, maxHoverIntensity });
  latest.current = { pupilOffset, irisScale, dilation, baseColor, glowColor, coreColor, audioLevel, onVoiceDetected, reducedMotion, expression, audioReactive, speaking, motionMode, hue, maxHoverIntensity };

  useEffect(() => { if (mic.error) onAudioError?.(mic.error); }, [mic.error, onAudioError]);

  useEffect(() => {
    const container = host.current;
    if (!container) return;
    let renderer: Renderer | undefined;
    let geometry: Triangle | undefined;
    let program: Program | undefined;
    let mesh: Mesh | undefined;
    let observer: ResizeObserver | undefined;
    let frame = 0;
    const motionQuery = matchMedia('(prefers-reduced-motion: reduce)');
    const lost = (event: Event) => {
      event.preventDefault();
      releaseGL(true);
      setWebgl(false);
    };
    const releaseGL = (contextLost: boolean) => {
      const gl = renderer?.gl;
      observer?.disconnect();
      observer = undefined;
      gl?.canvas.removeEventListener('webglcontextlost', lost);
      if (gl && !contextLost && !gl.isContextLost()) {
        geometry?.remove();
        program?.remove();
        gl.getExtension('WEBGL_lose_context')?.loseContext();
      }
      gl?.canvas.remove();
      mesh = undefined;
      geometry = undefined;
      program = undefined;
      renderer = undefined;
    };

    try {
      renderer = new Renderer({ alpha: true, antialias: true, dpr: Math.min(devicePixelRatio || 1, 2) });
      const gl = renderer.gl;
      gl.clearColor(0, 0, 0, 0);
      container.appendChild(gl.canvas);
      geometry = new Triangle(gl);
      program = new Program(gl, { vertex, fragment, transparent: true, uniforms: {
        level: { value: 0.4 }, blink: { value: 0 },
        gaze: { value: [0, 0] }, resolution: { value: [1, 1] },
        irisScale: { value: 1 }, dilation: { value: 0 },
        lid: { value: 0.65 }, lowerLid: { value: 0 }, lidSlant: { value: 0 },
        focused: { value: 0 }, hue: { value: 0 },
        baseColor: { value: rgb(baseColor) }, glowColor: { value: rgb(glowColor) }, coreColor: { value: rgb(coreColor) },
      } });
      mesh = new Mesh(gl, { geometry, program });
      const resize = () => {
        const { width, height } = container.getBoundingClientRect();
        renderer!.setSize(Math.max(1, width), Math.max(1, height));
        program!.uniforms.resolution.value = [Math.max(1, width), Math.max(1, height)];
      };
      observer = new ResizeObserver(resize); observer.observe(container); resize();
      gl.canvas.addEventListener('webglcontextlost', lost);
      setWebgl(true);
    } catch { releaseGL(false); setWebgl(false); }

    let previous = performance.now(), detected = false;
    let eyeMotion: EyeAnimationState | null = null;
    const initialMood = EXPRESSIONS[latest.current.expression] || EXPRESSIONS.open;
    let lid = initialMood.lid, focused = initialMood.focus;
    let lowerLid = initialMood.lowerLid || 0, lidSlant = initialMood.lidSlant || 0;
    let dilationMood = initialMood.dilation || 0;
    const initPal = initialMood.palette;
    const curBase = initPal ? rgb(initPal.base) : rgb(latest.current.baseColor);
    const curGlow = initPal ? rgb(initPal.glow) : rgb(latest.current.glowColor);
    const curCore = initPal ? rgb(initPal.core) : rgb(latest.current.coreColor);
    const drag = { x: 0, y: 0 }, velocity = { x: 0, y: 0 };

    const render = (now: number) => {
      const dtMs = Math.max(0, Math.min(now - previous, 50)); previous = now;
      const dt = dtMs / 1000;
      const props = latest.current;
      const rms = clamp(props.audioLevel ?? mic.level.current, 0, 1);

      if ((rms > 0.08) !== detected) {
        detected = rms > 0.08;
        props.onVoiceDetected?.(detected);
      }

      const reduced = props.reducedMotion || motionQuery.matches;
      const stillMode = reduced || props.motionMode === 'paused' || props.motionMode === 'offline';
      const mood = EXPRESSIONS[props.expression] || EXPRESSIONS.open;
      const rate = stillMode ? 1 : 1 - Math.exp(-dt * 9);
      lid += (mood.lid - lid) * rate;
      lowerLid += ((mood.lowerLid || 0) - lowerLid) * rate;
      lidSlant += ((mood.lidSlant || 0) - lidSlant) * rate;
      focused += (mood.focus - focused) * rate;
      dilationMood += ((mood.dilation || 0) - dilationMood) * rate;

      // Organic color transition towards mood palette (or original color if normal/open)
      const targetBase = mood.palette ? rgb(mood.palette.base) : rgb(props.baseColor);
      const targetGlow = mood.palette ? rgb(mood.palette.glow) : rgb(props.glowColor);
      const targetCore = mood.palette ? rgb(mood.palette.core) : rgb(props.coreColor);
      const colorRate = stillMode ? 1 : 1 - Math.exp(-dt * 6.5);
      curBase[0] += (targetBase[0] - curBase[0]) * colorRate;
      curBase[1] += (targetBase[1] - curBase[1]) * colorRate;
      curBase[2] += (targetBase[2] - curBase[2]) * colorRate;

      curGlow[0] += (targetGlow[0] - curGlow[0]) * colorRate;
      curGlow[1] += (targetGlow[1] - curGlow[1]) * colorRate;
      curGlow[2] += (targetGlow[2] - curGlow[2]) * colorRate;

      curCore[0] += (targetCore[0] - curCore[0]) * colorRate;
      curCore[1] += (targetCore[1] - curCore[1]) * colorRate;
      curCore[2] += (targetCore[2] - curCore[2]) * colorRate;

      const constrained = constrainGaze(props.pupilOffset);
      const mode = props.motionMode ?? (props.speaking ? 'speaking'
        : props.audioReactive && (rms > 0.08 || Math.hypot(constrained.pupil.x, constrained.pupil.y) > 0.01)
          ? 'listening' : 'idle');
      eyeMotion = stepEyeAnimation(eyeMotion, {
        mode, audioLevel: props.audioReactive ? rms : 0, gaze: constrained.pupil, reducedMotion: reduced,
      }, dtMs);
      const still = reduced || mode === 'paused' || mode === 'offline';
      if (still) {
        drag.x = drag.y = velocity.x = velocity.y = 0;
      } else if (mode !== 'speaking') {
        stepSpring(drag, velocity, constrained.drag, dt);
      }

      if (assembly.current) {
        assembly.current.style.transform = `perspective(800px) translate3d(${drag.x}px, ${drag.y}px, 0) rotateX(${-drag.y * 0.35}deg) rotateY(${drag.x * 0.35}deg)`;
      }

      // The fallback uses the same bounded iris scale, gaze and lid motion.
      if (fallbackPupil.current) {
        const totalScale = clamp(props.irisScale, 0.65, 1.2) * (1 - focused * 0.12) * eyeMotion.irisScale;
        fallbackPupil.current.setAttribute('transform', `translate(${eyeMotion.gaze.x * 30} ${eyeMotion.gaze.y * 22}) translate(200 200) scale(${totalScale}) translate(-200 -200)`);
      }
      if (fallbackUpperLid.current && fallbackLowerLid.current) {
        const closure = clamp((0.65 - lid) * 0.8 + eyeMotion.blink * 0.9, 0, 1);
        const upperY = 113 + closure * 110;
        const lowerY = 287 - Math.max(lowerLid, 0) * 64 - eyeMotion.blink * 84;
        const upperControl = (4 * upperY - 200) / 3;
        const lowerControl = (4 * lowerY - 200) / 3;
        fallbackUpperLid.current.setAttribute('d', `M0 0H400V200C300 ${upperControl + lidSlant * 18} 100 ${upperControl - lidSlant * 18} 0 200Z`);
        fallbackLowerLid.current.setAttribute('d', `M0 400H400V200C300 ${lowerControl} 100 ${lowerControl} 0 200Z`);
      }

      if (mesh && program && renderer) {
        const u = program.uniforms;
        u.level.value = eyeMotion.glow;
        u.blink.value = eyeMotion.blink;
        u.gaze.value = [eyeMotion.gaze.x, -eyeMotion.gaze.y];
        u.lid.value = lid;
        u.lowerLid.value = lowerLid;
        u.lidSlant.value = lidSlant;
        u.focused.value = focused;
        u.hue.value = props.hue ?? 0;
        u.irisScale.value = clamp(props.irisScale, 0.65, 1.2) * eyeMotion.irisScale;
        u.dilation.value = clamp(props.dilation + dilationMood, -1, 1);
        u.baseColor.value = curBase;
        u.glowColor.value = curGlow;
        u.coreColor.value = curCore;
        renderer.render({ scene: mesh });
      }

      frame = requestAnimationFrame(render);
    };

    frame = requestAnimationFrame(render);
    return () => { cancelAnimationFrame(frame); releaseGL(false); latest.current.onVoiceDetected?.(false); };
  }, [mic.level]);

  const activePal = (expression ? EXPRESSIONS[expression]?.palette : undefined);
  const activeBase = activePal?.base ?? baseColor;
  const activeGlow = activePal?.glow ?? glowColor;
  const activeCore = activePal?.core ?? coreColor;

  return <div ref={assembly} className={cn('relative aspect-square', className)} role="img" aria-label="Fairy eye, an audio-reactive celestial iris" data-expression={expression}>
    {!webgl && <svg className="eye-fallback" viewBox="0 0 400 400" aria-hidden="true">
      <defs>
        <clipPath id={clipId}><path d={eyeOutline}/></clipPath>
        <radialGradient id={scleraGradientId}>
          <stop offset="0%" stopColor="#f3f8fa"/>
          <stop offset="72%" stopColor="#e6f0f4"/>
          <stop offset="100%" stopColor="#9fb4c0"/>
        </radialGradient>
        <radialGradient id={irisGradientId}>
          <stop offset="0%" stopColor={activeCore}/>
          <stop offset="48%" stopColor={activeGlow}/>
          <stop offset="100%" stopColor={activeBase}/>
        </radialGradient>
      </defs>
      <path className="eye-sclera" d={eyeOutline} fill={`url(#${scleraGradientId})`}/>
      <g ref={fallbackPupil} className="eye-iris-group" clipPath={`url(#${clipId})`}>
        <circle className="eye-iris" cx="200" cy="200" r="73" fill={`url(#${irisGradientId})`} stroke={activeBase} strokeWidth="2"/>
        <circle className="eye-pupil" cx="200" cy="200" r={32 + dilation * 4} fill="#07131f"/>
      </g>
      <ellipse className="eye-catchlight" cx="181" cy="177" rx="7" ry="9" fill="#f8fdff" clipPath={`url(#${clipId})`}/>
      <g clipPath={`url(#${clipId})`}>
        <path ref={fallbackUpperLid} className="eye-upper-lid" fill="#07131f" stroke={activeGlow} strokeWidth="1"/>
        <path ref={fallbackLowerLid} className="eye-lower-lid" fill="#07131f" stroke={activeGlow} strokeWidth="1"/>
      </g>
      <path d={eyeOutline} fill="none" stroke={activeGlow} strokeOpacity=".55" strokeWidth="2"/>
    </svg>}
    <div ref={host} className="absolute inset-0" data-testid="fairy-renderer" />
  </div>;
}

/** Alias VoicePoweredOrb to FairyEye for 21st.dev component compatibility */
export const VoicePoweredOrb = FairyEye;
