"use client";

import { useEffect, useId, useRef, useState } from 'react';
import { Mesh, Program, Renderer, Triangle } from 'ogl';
import { cn } from '@/lib/utils';
import { useMicrophone } from '@/hooks/use-microphone';
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
  /** Hue degree rotation (-180..180) for dynamic palette shifts. */
  hue?: number;
  maxRotationSpeed?: number;
  maxHoverIntensity?: number;
}

const vertex = `attribute vec2 position; attribute vec2 uv; varying vec2 vUv;
void main(){vUv=uv;gl_Position=vec4(position,0.,1.);}`;

// Procedural geometry only: no external textures, images, or prerecorded clips.
const fragment = `precision highp float;
varying vec2 vUv;
uniform float time, level, irisScale, dilation, rotation, lid, lowerLid, lidSlant, focused, hue;
uniform vec2 gaze, resolution;
uniform vec3 baseColor, glowColor, coreColor;

float disk(float r, float radius, float feather) {
  return 1.0 - smoothstep(radius - feather, radius + feather, r);
}

float ring(float r, float radius, float width) {
  return 1.0 - smoothstep(width, width + 0.0025, abs(r - radius));
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
  float r = length(p);
  float a = atan(p.y, p.x);

  vec3 bCol = adjustHue(baseColor, hue);
  vec3 gCol = adjustHue(glowColor, hue);
  vec3 cCol = adjustHue(coreColor, hue);

  // 1. Canonical Fairy Idle Breathing (~3-5% over 3-4s cycle) & Speech Dilation Boost (15-25%)
  float idleBreath = sin(time * 1.8) * 0.042;
  float speechDilation = level * (0.16 + sin(time * (2.4 + level * 3.2)) * 0.08);
  float totalExpansion = idleBreath * (1.0 - smoothstep(0.0, 0.45, level)) + speechDilation;

  // Outer radial bloom and atmospheric rim (intensifies during speech peaks)
  vec3 col = gCol * (0.065 + level * 0.20) * exp(-r * r * (2.9 - level * 0.8));
  col += gCol * (0.20 + level * 0.48) * exp(-pow((r - 0.65) * 12.0, 2.0));

  // Translucent spherical outer lens and luminous rim
  col += bCol * 0.32 * disk(r, 0.65, 0.005);
  col += gCol * (0.18 + 0.12 * sin(a + time * 0.15) + level * 0.22) * ring(r, 0.651, 0.0015);
  col += gCol * (0.10 + level * 0.16) * ring(r, 0.625, 0.001);

  // 2. Outer Iris/Aperture Wobble with 4 subtle perimeter notches & continuous slow micro-spin
  float outerWobble = (sin(a * 4.0 + time * 1.25) + sin(a * 8.0 - time * 2.1) * 0.45) * 0.006;
  float corner = pow(abs(cos(2.0 * (a - rotation - 0.785398))), 24.0);
  float shell = 0.526 + 0.065 * corner + outerWobble;
  col = mix(col, bCol * 0.11, disk(r, shell, 0.003));
  col += bCol * 0.36 * ring(r, shell, 0.004);
  col += gCol * (0.045 + level * 0.08) * ring(r, 0.50, 0.001);

  // 3. High-Frequency Audio Jitter & Acoustic Waveform Ripple
  float voiceAcoustic = sin(a * 32.0 + time * 46.0) * 0.50
                      + sin(a * 64.0 - time * 68.0) * 0.35
                      + sin(a * 128.0 + time * 98.0) * 0.15;
  float ripple = voiceAcoustic * (level * 0.018);
  vec2 jitter = vec2(sin(time * 108.0 + a * 3.0), cos(time * 132.0 - a * 3.0)) * (level * 0.007);

  // Scaled iris coordinates (applying breathing + speech dilation)
  float scale = irisScale * (1.0 + totalExpansion) * mix(1.0, 0.92, focused);
  vec2 q = (p - gaze * 0.025) / scale;
  float angle = atan(q.y, q.x);
  float ir = length(q) + ripple;

  // 4. Concentric Rings: Bright Inner White-Pink Ring & Secondary Cyan Depth Rings
  float innerGlow = exp(-pow((ir - 0.425) * 27.0, 2.0));
  col += gCol * (0.24 + level * 0.45) * innerGlow;
  // Emissive white-pink pearl ring that flares with volume
  vec3 brightRingCol = mix(cCol, vec3(1.0, 0.92, 0.96), 0.70) * (0.96 + level * 0.40);
  col = mix(col, brightRingCol, disk(ir, 0.426, 0.0025));

  for (int i = 0; i < 7; i++) {
    float depth = float(i) / 6.0;
    vec2 center = gaze * (0.035 + depth * 0.155) + jitter * depth;
    float depthBreath = sin(time * 1.8 + depth * 0.55) * 0.014;
    float cr = length((p - center) / scale) + ripple * (1.0 - depth * 0.5) - depthBreath;
    float radius = mix(0.331, 0.148, depth) * mix(1.0, 0.80, focused) + dilation * 0.018 + level * 0.025;
    float lighting = 0.82 + 0.18 * cos(angle - rotation * 2.0 + depth * 0.8);
    vec3 cone = mix(mix(cCol, gCol, 0.32) * 0.76, bCol * 0.24, depth) * lighting;
    col = mix(col, cone, disk(cr, radius, 0.002));
    col += gCol * (0.14 + 0.24 * depth + level * 0.35) * ring(cr, radius, 0.0009);
  }

  // Deep dark pupil center
  float pupilRadius = clamp(0.14 + dilation * 0.018 + level * 0.02, 0.10, 0.22) * mix(1.0, 0.78, focused);
  float pradius = length((p - gaze * 0.19 - jitter) / scale);
  col = mix(col, bCol * 0.10, disk(pradius, pupilRadius, 0.002));
  col += gCol * (0.35 + level * 0.35) * ring(pradius, pupilRadius, 0.001);

  // 5. Orbiting Satellite / Pearl with Harmonic Idle Pendulum Sway & Speech Flare
  float satSway = sin(time * 1.5) * 0.15 + cos(time * 0.85) * 0.06;
  float satBob = cos(time * 1.7) * 0.012;
  float speechOrbit = time * (level * 2.0);
  float satAngle = -0.815 + satSway + speechOrbit;
  float satDist = (0.203 + satBob) * (1.0 + level * 0.14);
  vec2 satTarget = vec2(cos(satAngle), sin(satAngle)) * satDist;

  vec2 pearl = (p - gaze * 0.16 - jitter * 1.5) / scale - satTarget;
  float pr = length(pearl);
  float satRadius = 0.085 + level * 0.022;

  // Satellite flare & intensified bloom during vocal peaks
  col += cCol * (0.20 + level * 1.8) * exp(-pr * pr * (260.0 - level * 110.0));
  col += gCol * (0.35 + level * 1.4) * exp(-pr * pr * (95.0 - level * 35.0));
  col = mix(col, cCol * (1.0 + level * 0.4), disk(pr, satRadius, 0.0025));

  col += gCol * 0.24 * exp(-pow((ir - 0.426) * 35.0, 2.0)) * (1.0 - disk(ir, 0.423, 0.003));

  // Dual-visor mask: Upper eyelid with brow slant + Smiling lower eyelid crescent
  float upperLidCut = lid + 0.12 * p.x * p.x + abs(p.x) * lidSlant;
  float topVisor = smoothstep(upperLidCut - 0.003, upperLidCut + 0.003, p.y) * disk(r, shell, 0.003);

  // Lower smiling crescent (pushes up when smiling or happy)
  float lowerLidCut = -0.65 + lowerLid - 0.22 * p.x * p.x;
  float bottomVisor = (1.0 - smoothstep(lowerLidCut - 0.003, lowerLidCut + 0.003, p.y)) * step(0.001, lowerLid) * disk(r, shell, 0.003);

  float visor = clamp(topVisor + bottomVisor, 0.0, 1.0);
  col = mix(col, bCol * 0.095 + gCol * 0.014, visor);

  // Luminous eyelid rim outlines
  col += gCol * 0.09 * ring(p.y - 0.12 * p.x * p.x - abs(p.x) * lidSlant, lid, 0.001) * disk(r, shell, 0.003);
  col += gCol * 0.10 * ring(p.y + 0.22 * p.x * p.x, lowerLidCut, 0.0012) * step(0.001, lowerLid) * disk(r, shell, 0.003);

  // Atmospheric edge glow and film grain
  col += gCol * (0.05 + level * 0.08) * ring(r, 0.685, 0.0008);
  float grain = fract(sin(dot(gl_FragCoord.xy, vec2(12.9898, 78.233))) * 43758.5453);
  col += (grain - 0.5) * 0.012 * disk(r, 0.70, 0.05);
  col *= 1.0 - 0.045 * (0.5 + 0.5 * sin(gl_FragCoord.y * 3.14159265));

  float alpha = clamp(max(col.r, max(col.g, col.b)) * 1.5 + disk(r, 0.65, 0.006) * 0.92, 0.0, 1.0) * (1.0 - smoothstep(0.8, 0.98, r));
  gl_FragColor = vec4(max(col, vec3(0.0)), alpha);
}`;

function rgb(hex: string) {
  const valid = /^#[0-9a-f]{6}$/i.test(hex) ? hex : '#3987c6';
  return [1, 3, 5].map(index => parseInt(valid.slice(index, index + 2), 16) / 255);
}
const clamp = (value: number, min: number, max: number) => Number.isFinite(value) ? Math.min(max, Math.max(min, value)) : min;

/** Canonical Fairy Eye with procedural WebGL OGL shader, harmonic idle loop, and voice amplification. */
export function FairyEye({
  className, pupilOffset = { x: 0, y: 0 }, irisScale = 1, dilation = 0,
  baseColor = '#175286', glowColor = '#58bcff', coreColor = '#e8f5ff',
  enableVoiceControl = false, voiceSensitivity = 1.5, audioLevel,
  onVoiceDetected, onAudioError, reducedMotion = false,
  expression = 'open', audioReactive = true, speaking = false,
  hue = 0, maxRotationSpeed = 1.2, maxHoverIntensity = 0.8,
}: FairyEyeProps) {
  const host = useRef<HTMLDivElement>(null);
  const assembly = useRef<HTMLDivElement>(null);
  const fallbackPupil = useRef<SVGGElement>(null);
  const fallbackVisor = useRef<SVGPathElement>(null);
  const clipId = useId().replace(/:/g, '');
  const [webgl, setWebgl] = useState(false);
  const mic = useMicrophone(enableVoiceControl && audioLevel === undefined, voiceSensitivity);
  const latest = useRef({ pupilOffset, irisScale, dilation, baseColor, glowColor, coreColor, audioLevel, onVoiceDetected, reducedMotion, expression, audioReactive, speaking, hue, maxRotationSpeed, maxHoverIntensity });
  latest.current = { pupilOffset, irisScale, dilation, baseColor, glowColor, coreColor, audioLevel, onVoiceDetected, reducedMotion, expression, audioReactive, speaking, hue, maxRotationSpeed, maxHoverIntensity };

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
    const lost = (event: Event) => { event.preventDefault(); mesh = undefined; setWebgl(false); };
    const release = () => {
      cancelAnimationFrame(frame); observer?.disconnect();
      renderer?.gl.canvas.removeEventListener('webglcontextlost', lost);
      geometry?.remove(); program?.remove();
      renderer?.gl.getExtension('WEBGL_lose_context')?.loseContext();
      renderer?.gl.canvas.remove();
    };

    try {
      renderer = new Renderer({ alpha: true, antialias: true, dpr: Math.min(devicePixelRatio || 1, 2) });
      const gl = renderer.gl;
      gl.clearColor(0, 0, 0, 0);
      container.appendChild(gl.canvas);
      geometry = new Triangle(gl);
      program = new Program(gl, { vertex, fragment, transparent: true, uniforms: {
        time: { value: 0 }, level: { value: 0 }, rotation: { value: 0 },
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
    } catch { release(); mesh = undefined; setWebgl(false); }

    let previous = performance.now(), smooth = 0, rotation = 0, elapsed = 0, detected = false;
    const initialMood = EXPRESSIONS[latest.current.expression] || EXPRESSIONS.open;
    let lid = initialMood.lid, focused = initialMood.focus;
    let lowerLid = initialMood.lowerLid || 0, lidSlant = initialMood.lidSlant || 0;
    let tempo = initialMood.tempo || 1.0;
    let dilationMood = initialMood.dilation || 0;
    const initPal = initialMood.palette;
    const curBase = initPal ? rgb(initPal.base) : rgb(latest.current.baseColor);
    const curGlow = initPal ? rgb(initPal.glow) : rgb(latest.current.glowColor);
    const curCore = initPal ? rgb(initPal.core) : rgb(latest.current.coreColor);
    const gaze = { x: 0, y: 0 }, drag = { x: 0, y: 0 }, velocity = { x: 0, y: 0 };

    const render = (now: number) => {
      const dt = Math.min((now - previous) / 1000, 0.05); previous = now;
      const props = latest.current;
      const speechEnvelope = props.speaking ? 0.22 + 0.18 * Math.pow(Math.sin(elapsed * 13), 2) : 0;
      const rms = clamp(props.audioLevel ?? mic.level.current, 0, 1);

      // Smooth transition & blending: asymmetric attack (fast ~35ms) / release (smooth decay ~220ms)
      const targetEnergy = props.audioReactive ? Math.max(rms, speechEnvelope) : 0;
      const attackRate = 1 - Math.exp(-dt * 26);
      const decayRate = 1 - Math.exp(-dt * 6.5);
      smooth += (targetEnergy - smooth) * (targetEnergy > smooth ? attackRate : decayRate);

      if ((rms > 0.08) !== detected) {
        detected = rms > 0.08;
        props.onVoiceDetected?.(detected);
      }

      const mood = EXPRESSIONS[props.expression] || EXPRESSIONS.open;
      const rate = 1 - Math.exp(-dt * 9);
      lid += (mood.lid - lid) * rate;
      lowerLid += ((mood.lowerLid || 0) - lowerLid) * rate;
      lidSlant += ((mood.lidSlant || 0) - lidSlant) * rate;
      focused += (mood.focus - focused) * rate;
      tempo += ((mood.tempo || 1.0) - tempo) * rate;
      dilationMood += ((mood.dilation || 0) - dilationMood) * rate;

      // Organic color transition towards mood palette (or original color if normal/open)
      const targetBase = mood.palette ? rgb(mood.palette.base) : rgb(props.baseColor);
      const targetGlow = mood.palette ? rgb(mood.palette.glow) : rgb(props.glowColor);
      const targetCore = mood.palette ? rgb(mood.palette.core) : rgb(props.coreColor);
      const colorRate = 1 - Math.exp(-dt * 6.5);
      curBase[0] += (targetBase[0] - curBase[0]) * colorRate;
      curBase[1] += (targetBase[1] - curBase[1]) * colorRate;
      curBase[2] += (targetBase[2] - curBase[2]) * colorRate;

      curGlow[0] += (targetGlow[0] - curGlow[0]) * colorRate;
      curGlow[1] += (targetGlow[1] - curGlow[1]) * colorRate;
      curGlow[2] += (targetGlow[2] - curGlow[2]) * colorRate;

      curCore[0] += (targetCore[0] - curCore[0]) * colorRate;
      curCore[1] += (targetCore[1] - curCore[1]) * colorRate;
      curCore[2] += (targetCore[2] - curCore[2]) * colorRate;

      const reduced = props.reducedMotion || motionQuery.matches;
      if (!reduced) {
        elapsed += dt * tempo;
        // Outer iris continuous micro-spin: slow base rotation, accelerates with speech
        const voiceSpin = 0.024 + smooth * (props.maxRotationSpeed * 0.35);
        rotation += dt * voiceSpin * tempo;
      }

      // Face tracking with boundary recoil physics
      const constrained = constrainGaze(props.pupilOffset);
      const lerp = 1 - Math.exp(-dt * 10);
      gaze.x += (constrained.pupil.x - gaze.x) * lerp;
      gaze.y += (constrained.pupil.y - gaze.y) * lerp;

      // Harmonic idle sway layered on top of gaze:
      // When gaze is stationary, adds soft harmonic orbital floating so the eye feels alive
      const idleGazeSwayX = reduced ? 0 : Math.sin(elapsed * 1.3) * 0.016 + Math.sin(elapsed * 0.65) * 0.007;
      const idleGazeSwayY = reduced ? 0 : Math.cos(elapsed * 1.5) * 0.014 + Math.cos(elapsed * 0.8) * 0.006;
      const effectiveGaze = {
        x: gaze.x + idleGazeSwayX * (1 - smooth),
        y: gaze.y + idleGazeSwayY * (1 - smooth),
      };

      stepSpring(drag, velocity, reduced ? { x: 0, y: 0 } : constrained.drag, dt);

      if (assembly.current) {
        assembly.current.style.transform = `perspective(800px) translate3d(${drag.x}px, ${drag.y}px, 0) rotateX(${-drag.y * 0.35}deg) rotateY(${drag.x * 0.35}deg)`;
      }

      // SVG Fallback update with idle breathing & voice dilation
      if (fallbackPupil.current) {
        const idleBreath = Math.sin(elapsed * 1.8) * 0.04;
        const totalScale = clamp(props.irisScale, 0.65, 1.2) * (1 - focused * 0.12) * (1 + (reduced ? 0 : idleBreath * (1 - smooth) + smooth * 0.20));
        fallbackPupil.current.setAttribute('transform', `translate(${effectiveGaze.x * 32} ${effectiveGaze.y * 32}) translate(200 200) scale(${totalScale}) translate(-200 -200)`);
      }
      if (fallbackVisor.current) {
        const edge = 200 - lid * 200;
        if (lowerLid > 0.04) {
          const bEdge = 200 + 130 - lowerLid * 160;
          fallbackVisor.current.setAttribute('d', `M60 40H340V${edge - 24}Q200 ${edge + 24} 60 ${edge - 24}ZM60 360H340V${bEdge + 24}Q200 ${bEdge - 24} 60 ${bEdge + 24}Z`);
        } else {
          fallbackVisor.current.setAttribute('d', `M60 40H340V${edge - 24}Q200 ${edge + 24} 60 ${edge - 24}Z`);
        }
      }

      if (mesh && program && renderer) {
        const u = program.uniforms;
        u.time.value = elapsed;
        u.level.value = reduced ? 0 : smooth;
        u.rotation.value = rotation;
        u.gaze.value = [effectiveGaze.x, -effectiveGaze.y];
        u.lid.value = lid;
        u.lowerLid.value = lowerLid;
        u.lidSlant.value = lidSlant;
        u.focused.value = focused;
        u.hue.value = props.hue ?? 0;
        u.irisScale.value = clamp(props.irisScale, 0.65, 1.2);
        u.dilation.value = clamp(props.dilation + dilationMood, -1, 1);
        u.baseColor.value = curBase;
        u.glowColor.value = curGlow;
        u.coreColor.value = curCore;
        renderer.render({ scene: mesh });
      }

      frame = requestAnimationFrame(render);
    };

    frame = requestAnimationFrame(render);
    return () => { release(); latest.current.onVoiceDetected?.(false); };
  }, [mic.level]);

  const activePal = (expression ? EXPRESSIONS[expression]?.palette : undefined);
  const activeBase = activePal?.base ?? baseColor;
  const activeGlow = activePal?.glow ?? glowColor;
  const activeCore = activePal?.core ?? coreColor;

  return <div ref={assembly} className={cn('relative aspect-square', className)} role="img" aria-label="Fairy eye, an audio-reactive celestial iris" data-expression={expression}>
    {!webgl && <svg className="eye-fallback" viewBox="0 0 400 400" aria-hidden="true">
      <circle cx="200" cy="200" r="133" fill={activeBase} stroke={activeGlow} />
      <path d="M 117 86 Q200 116 283 86 L314 117 Q284 200 314 283 L283 314 Q200 284 117 314 L86 283 Q116 200 86 117 Z" fill="#04152b"/>
      <g ref={fallbackPupil}>
        <circle cx="200" cy="200" r="86" fill={activeCore}/><circle cx="200" cy="200" r="64" fill={activeGlow}/>
        <circle cx="200" cy="200" r="58" fill={activeBase} stroke={activeGlow}/>
        <circle cx="200" cy="200" r="51" fill={activeBase} stroke={activeGlow} strokeOpacity=".6"/>
        <circle cx="200" cy="200" r={43 + dilation * 7} fill={activeBase}/><circle cx="229" cy="231" r="17" fill={activeCore}/>
      </g>
      <defs><clipPath id={clipId}><circle cx="200" cy="200" r="110"/></clipPath></defs>
      <path ref={fallbackVisor} clipPath={`url(#${clipId})`} fill="#04152b"/>
    </svg>}
    <div ref={host} className="absolute inset-0" data-testid="fairy-renderer" />
  </div>;
}

/** Alias VoicePoweredOrb to FairyEye for 21st.dev component compatibility */
export const VoicePoweredOrb = FairyEye;
