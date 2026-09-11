# Fairy Eye

A procedural OGL/WebGL eye with an SVG fallback, React 19, TypeScript, Tailwind 4,
and shadcn-style `src/components/ui` and `src/lib/utils` aliases.

The eye includes a seven-layer conical aperture, four perimeter notches, subtle
CRT scanlines, speech ripple, and a vibrating/flaring satellite. Its four
expressions (`open`, `half`, `squint`, `focus`) smoothly interpolate the curved
visor and inner-ring contraction. The expression strip selects them; the waveform
button toggles visual audio reactivity independently of microphone capture/mute.

## Run with Vision

```powershell
cd D:\ascend-vision\ascend-vision\fairy-ui
npm install
npm run build
cd ..
.venv\Scripts\python.exe main.py --focus
```

`--focus` opens Fairy in your browser on a loopback-only, dynamically assigned port.
Python continues to own the camera and voice listener. The UI receives live RMS and
session state and face position; Show camera displays a small, mirrored JPEG preview from that same
capture. Hiding the card stops preview requests without stopping detection.
Pause switches to the existing background mode. Mute voice uses the existing
feedback mute, which also suppresses voice command handling. It does not disable
microphone hardware capture; RMS monitoring continues in the Python listener.
If voice commands are disabled in config, the voice button is disabled.

Close the browser tab to dismiss the interface; Vision keeps monitoring. Stop
Vision with Ctrl+C or existing tray controls to release Python resources and the
HTTP server. `--no-fairy-ui` restores the previous OpenCV interface;
`--focus --preview` opens the diagnostic camera window as well as Fairy.
Use `--fairy-ui` to open Fairy when starting in background mode.

## Standalone component development

```powershell
npm run dev
```

Open the printed localhost URL. Standalone mode acquires a webcam on mount and
keeps it live while the preview is hidden. Enable microphone grants audio access
for RMS animation only; this standalone preview does not run Whisper, automation,
or the Python detection pipeline. Browser media require localhost/HTTPS and user
permission. Both streams, audio nodes, animation loops, and WebGL resources are
released on unmount, including late permission responses. Closing/reloading the
page also releases browser-owned streams. Images and audio are not uploaded by
these components.

Face tracking stays active with the camera card hidden. Integrated mode reuses
Python's face landmarks and transfers only a mirrored, normalized face-center
target to the UI. Standalone mode uses native `FaceDetector` when available,
otherwise the local MediaPipe BlazeFace short-range detector, throttled to 10 Hz.
No camera, no detected face, or detector failure selects pointer tracking.
This tracks face position, not ocular gaze or identity. Follow targets are smoothed,
the pupil is bounded to a circular socket, and excess target displacement pulls
the whole eye 18px with a bounded spring and perspective tilt. Reduced motion
disables recoil, spin, jitter, and automatic pulsation.

The first `npm run dev` or `npm run build` downloads the versioned face model and
stages the installed MediaPipe WebAssembly files into `public/vision`; subsequent
runs reuse the model. These generated files are copied into the production build,
so detection makes no external network requests at runtime. Source:
[Google's MediaPipe vision tasks and BlazeFace model](https://github.com/google-ai-edge/mediapipe/blob/master/mediapipe/tasks/web/vision/README.md).

```tsx
import { FairyEye } from '@/components/ui/fairy-eye';

<FairyEye
  pupilOffset={{ x: 0.3, y: -0.2 }} // normalized -1..1
  expression="half"               // open | half | squint | focus
  audioReactive={true}             // visual response, independent of mic ownership
  irisScale={1}                    // clamped .65..1.2
  dilation={0.2}                   // -1..1
  baseColor="#175286"             // six-digit hex
  glowColor="#58bcff"
  coreColor="#e8f5ff"
  enableVoiceControl
  voiceSensitivity={1.5}
  onVoiceDetected={detected => console.log(detected)}
/>
```

Pass `audioLevel` (0..1 normalized RMS) to feed an existing audio pipeline; this
disables browser microphone acquisition. Props are applied to uniforms without
recreating the renderer. OS reduced-motion preferences and `reducedMotion`
disable automatic rotation, pulse, and audio dilation. The SVG fallback remains
usable when WebGL is unavailable. The appearance menu provides three themes,
audio sensitivity, and reduced motion.

`speaking` optionally drives an illustrative syllabic envelope for the existing
Python TTS activity flag when PCM audio is unavailable; microphone animation uses
measured RMS. `src/demo.tsx` wraps the full companion with all controls. Applications
embedding only `FairyEye` pass `pupilOffset` from their own tracker; stream ownership
and face detection live in the companion/hooks, not inside the shader component.

The supplied 21st.dev orb is the reference for OGL setup and analyser-driven
reactivity. Its renderer has been adapted to a procedural iris shader, and the
duplicate microphone initialization paths have been replaced by one lifecycle
hook. The supplied Fairy images are visual references, not runtime textures.

## Verify

```powershell
npm run build
npm test
cd ..
.venv\Scripts\python.exe -m pytest -q tests/test_focus_ui.py tests/test_main.py tests/test_voice_commands.py
```

Browser tests use synthetic camera/microphone input, verify media cleanup,
appearance controls, mobile layout, and Python telemetry routing. If Chromium is
not installed for Playwright, run `npx playwright install chromium` first.
