# Comprehensive Audit: Camera AI Features & Fairy UI Integration

**Date:** 2026-09-10  
**Project:** Ascend Vision (`ascend-vision`)  
**Scope:** Camera pipeline, AI detectors (posture, expression, finger counter, phone, drowsiness, yawn), and Fairy UI companion integration.

---

## 1. Executive Summary

When running Ascend Vision with the `--focus` flag (which activates the Fairy UI companion), **all underlying computer vision AI models and detectors remain 100% active and running on the Python backend**.

The architecture uses a **single camera owner** design:
1. **Python (`CameraCapture`)** is the sole hardware owner of the webcam device.
2. **Every frame** is processed through the complete AI detector stack (YOLO, MediaPipe Hands, MediaPipe Face Mesh, Posture Machine, Expression Tracker, and Finger Counter).
3. **Fairy UI** functions as an ambient, non-intrusive companion frontend that receives lightweight telemetry (face center target, audio RMS, session state, and raw JPEG preview on demand).

The primary difference is **visual presentation**: by default, `--focus` suppresses the desktop OpenCV diagnostic window (the window with landmark dots, bounding boxes, and text lines) to maintain a serene companion aesthetic. However, all AI logic, state transitions, speech roasts, and safety warnings continue operating as intended.

---

## 2. System Architecture & Data Flow

```
                     ┌──────────────────────────────┐
                     │   Physical Webcam (640x480)  │
                     └──────────────┬───────────────┘
                                    │ cv2.VideoCapture (dedicated thread)
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      Python Vision Runtime (main.py)                        │
│                                                                             │
│  ┌───────────────────────┐  ┌───────────────────────┐  ┌─────────────────┐  │
│  │ YOLO26n Phone Detector│  │ MediaPipe Hand Tracker│  │ Face Mesh +     │  │
│  │ (cell phone bbox)     │  │ (21 3D landmarks)     │  │ Blendshapes     │  │
│  └───────────┬───────────┘  └───────────┬───────────┘  └────────┬────────┘  │
│              │                          │                       │           │
│              ▼                          ▼                       ▼           │
│   ┌─────────────────────┐    ┌─────────────────────┐ ┌────────────────────┐ │
│   │ Hold Machine        │    │ Finger Counter &    │ │ Posture Machine    │ │
│   │ (proximity geometry)│    │ Gesture Recognizer  │ │ (slouch / pitch)   │ │
│   └──────────┬──────────┘    └──────────┬──────────┘ └────────┬───────────┘ │
│              │                          │                     │             │
│              ▼                          ▼                     ▼             │
│   ┌─────────────────────┐    ┌─────────────────────┐ ┌────────────────────┐ │
│   │ Drowsiness & Yawn   │    │ Voice Listener &    │ │ Expression Tracker │ │
│   │ (EAR & MAR)         │    │ TTS Feedback Engine │ │ (smile/furrow/etc.)│ │
│   └──────────┬──────────┘    └──────────┬──────────┘ └────────┬───────────┘ │
│              │                          │                     │             │
│              └──────────────────────────┴─────────────────────┘             │
│                                         │                                   │
│                           Telemetry & Frame Publishing                      │
│                                         ▼                                   │
│                        ┌─────────────────────────────────┐                  │
│                        │ FocusUI Bridge (focus_ui.py)    │                  │
│                        │ - Normalized Face Center Target │                  │
│                        │ - Audio RMS Level               │                  │
│                        │ - Bounded JPEG Preview Mailbox  │                  │
│                        │ - Loopback Flask HTTP (port 0)  │                  │
│                        └────────────────┬────────────────┘                  │
└─────────────────────────────────────────┼───────────────────────────────────┘
                                          │ HTTP Polling (150ms) / JPEG stream
                                          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                     Fairy UI (React 19 + WebGL / OGL)                       │
│                                                                             │
│  - 3D Conical Iris & Pupil: physically tracks face position                 │
│  - Audio Reactivity: pulses iris size with live microphone RMS              │
│  - On-Demand Camera Card: fetches /api/fairy/frame.jpg (clean raw stream)   │
│  - Session Timer & Mode Controls: sends toggle-focus / toggle-voice         │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Detailed Audit of AI Detectors

### 3.1. Posture Checker
* **Source:** [`posture_detector.py`](file:///d:/ascend-vision/ascend-vision/posture_detector.py) (`PostureMachine`) & [`posture.py`](file:///d:/ascend-vision/ascend-vision/posture.py)
* **Underlying Logic:**
  * Uses 3D Face Mesh landmarks (`FOREHEAD_IDX=10`, `NOSE_TIP_IDX=1`, `CHIN_IDX=152`).
  * Calculates face pitch (neck flexion) via `atan2(dz, dy)` and normalized vertical head drop relative to face height.
  * Auto-calibrates over the first 30 frames to learn the user's natural upright baseline.
  * Debounces slouch events over 30 consecutive frames with a 35-second cooldown.
* **Current State with Fairy UI:**
  * **Active:** Runs every frame in `main.py` (lines 539, 575–583).
  * **Actions Fired:** Calls `warn_and_automate('posture_observed', 'Poor posture detected...')`, saves events to SQLite, sends telemetry to Ascend Core, and triggers Kokoro TTS voice roasts.
  * **Fairy UI Display:** Does not display a visual slouch badge on the web eye canvas; reactions are auditory and database-logged.

---

### 3.2. Expression Detector
* **Source:** [`expression_tracker.py`](file:///d:/ascend-vision/ascend-vision/expression_tracker.py) (`ExpressionTracker`)
* **Underlying Logic:**
  * Ingests 52 MediaPipe face blendshapes (`mouthSmileLeft/Right`, `browDownLeft/Right`, `browInnerUp`, `jawOpen`).
  * Tracks sustained states: `smiling`, `stressed`, `fatigue`, and phone interaction expressions (`PHONE_CALL`, `PHONE_SCROLLING`, `PHONE_GAMING`, `PHONE_USE`).
  * Applies independent cooldowns and duration thresholds per expression.
* **Current State with Fairy UI:**
  * **Active:** Evaluated every frame in `main.py` (lines 518–530).
  * **Actions Fired:** Logs `VISION_TRIGGER` events and submits emotional context to `FeedbackService.submit_expression()`.
  * **Fairy UI Display:** The web UI currently allows manual selection of 4 eye visor expressions (`open`, `half`, `squint`, `focus`). The detected blendshape emotion is not yet forwarded to the browser state endpoint to automatically morph the Fairy eye.

---

### 3.3. Finger Counter & Gesture Controls
* **Source:** [`gesture_controls.py`](file:///d:/ascend-vision/ascend-vision/gesture_controls.py) (`count_fingers`, `GestureRecognizer`, `GestureController`)
* **Underlying Logic:**
  * Ingests 21 MediaPipe hand keypoints per detected hand.
  * Compares fingertip y-coordinates to PIP joint y-coordinates for index, middle, ring, and pinky, with x-axis extension checking for the thumb based on handedness.
  * Counts 0 to 5 extended fingers.
  * Debounces gestures over 0.9 seconds with a 1.5-second cooldown.
  * Gesture Mapping:
    * `0 fingers` (fist): Stop / Cancel active speech or proposal.
    * `1 finger`: Chat mode.
    * `2 fingers`: Automation mode.
    * `3 fingers`: Missions mode.
    * `4 fingers`: Habits mode.
    * `5 fingers` (open palm): Unmute / Mute toggle.
* **Current State with Fairy UI:**
  * **Active:** Runs on hand landmarks every frame in `main.py` (lines 473–504).
  * **Actions Fired:** Successfully triggers gesture actions (e.g. holding up 5 fingers calls `feedback.unmute()` and speaks `"Voice active"`).
  * **Fairy UI Display:** When unmuted or muted via hand gesture, Fairy UI updates its voice indicator (`VOICE LINK ACTIVE` / `QUIET PRESENCE`). However, the real-time finger count number (0–5) is not rendered visually in the web UI.

---

### 3.4. Phone Hold Detector & Safety Fusion
* **Source:** [`detector.py`](file:///d:/ascend-vision/ascend-vision/detector.py), [`state_machine.py`](file:///d:/ascend-vision/ascend-vision/state_machine.py), [`safety_fusion.py`](file:///d:/ascend-vision/ascend-vision/safety_fusion.py)
* **Underlying Logic:**
  * YOLO26n detects `cell phone` bounding boxes (confidence ≥ 0.5).
  * Measures euclidean distance between hand landmarks and the phone center.
  * Debounces sustained hold over 12 frames (~0.4s) with a 45s cooldown.
  * Fuses hold, drowsiness (EAR eye aspect ratio), yawn (MAR mouth aspect ratio), and posture events.
* **Current State with Fairy UI:**
  * **Active:** Completely functional in `main.py` (lines 531–606).
  * **Actions Fired:** Saves events to SQLite database, logs pickup streaks, triggers Gemini LLM sarcastic roast feedback via Kokoro TTS during focus sessions.

---

### 3.5. Fairy UI Camera Feed & Face Tracking Bridge
* **Source:** [`focus_ui.py`](file:///d:/ascend-vision/ascend-vision/focus_ui.py) (`FocusUI`) & [`fairy-ui/src/`](file:///d:/ascend-vision/ascend-vision/fairy-ui/src/)
* **Underlying Logic:**
  * `focus_ui.publish_face()` receives Face Mesh landmark coordinates from Python, computes the bounding box center, normalizes it to `[-1.8, 1.8]`, and mirrors it so the 3D pupil tracks the user's face naturally.
  * `focus_ui.publish()` buffers the latest frame only when preview requests are active (`_preview_until`), avoiding unnecessary JPEG encoding overhead when the camera card is collapsed.
  * Serves clean JPEG frames via `/api/fairy/frame.jpg` without burning OpenCV debug drawings into the stream.

---

## 4. Feature Matrix: Backend vs. UI Presentation

| Feature | Detector Source | Detection in `--focus` | Audio / TTS Alerts | OpenCV Window (`--preview`) | Fairy UI (`fairy-companion.tsx`) |
| :--- | :--- | :---: | :---: | :---: | :---: |
| **Phone Pickup & Hold** | YOLO26n + `HoldMachine` | **Active** | Spoken Roast via LLM | Green bounding box & hold stats | Collapsible clean JPEG stream |
| **Posture Checker** | `PostureMachine` | **Active** | Spoken Slouch Warning | Red warning banner & pitch degrees | Auditory alert only |
| **Drowsiness (Microsleep)** | `DrowsinessMachine` (EAR) | **Active** | Spoken Fatigue Warning | Cyan eye landmark dots | Auditory alert only |
| **Yawn Detector** | `YawnMachine` (MAR) | **Active** | Spoken Yawn Warning | Magenta mouth landmark dots | Auditory alert only |
| **Finger Counter** | `count_fingers()` | **Active** | Spoken Confirmation | Gesture HUD text line | Syncs Mute/Unmute state badge |
| **Expression Tracker** | `ExpressionTracker` | **Active** | Emotion-conditioned roast | Logged to console | Manual 4-state buttons |
| **Face Tracking (Gaze)** | MediaPipe Face Mesh | **Active** | N/A | Yellow face points | **Drives 3D WebGL pupil motion** |
| **Microphone RMS** | `VoiceCommandListener` | **Active** | Voice recognition active | Terminal volume meter | **Drives iris audio reactivity** |

---

## 5. Why You Don't See Bounding Boxes in Fairy UI

In the original desktop interface, OpenCV drew shapes directly onto the pixel buffer (`cv2.rectangle`, `cv2.circle`, `cv2.putText`).

In `--focus` mode:
1. `config.runtime.preview` is set to `False` by default so the intrusive OpenCV desktop window does not pop up over your work.
2. `focus_ui.publish(packet.image)` sends the **clean, unannotated camera frame** to the browser so the video preview card looks clean and modern.
3. The AI reasoning happens silently in the background, intervening via voice and logging to the database.

### How to Enable Both Overlays and Fairy UI:
If you want to view the Fairy companion in your browser **and** simultaneously see the OpenCV diagnostic window with green bounding boxes, facial landmark dots, posture metrics, and finger count overlays:

```powershell
cd d:\ascend-vision\ascend-vision
.\.venv\Scripts\python.exe main.py --focus --preview
```

---

## 6. Recommendations & Next Steps

To make Fairy UI visually reflect all of your AI detectors directly on the web interface:

1. **Auto-Morph Eye Expressions Based on Detected Emotion:**
   - Expose `expression_tracker._current_emotion` in `focus_ui.py` (`state['detectedEmotion'] = emotion`).
   - Map `smiling` → `'open'`, `fatigue` → `'half'`, `stressed` → `'squint'`, and `phone_detected` → `'focus'` inside `fairy-companion.tsx`.
2. **Visual Posture Alert Indicator:**
   - Pass `state['postureAlert'] = True` to `focus_ui.py` when slouching is detected.
   - Pulse the Fairy ambient glow red or orange in the web UI when posture needs correction.
3. **Finger Count / Gesture Indicator Badge:**
   - Expose `state['activeGesture']` and `state['fingerCount']` in `focus_ui.py`.
   - Render a small HUD pill in `fairy-companion.tsx` showing the currently recognized hand gesture (e.g. `🖐️ 5 Fingers · Unmuted`).
