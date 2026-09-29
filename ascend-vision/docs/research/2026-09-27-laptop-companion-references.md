# Laptop and phone companion references

Research date: 2026-09-27. Scope: Ascend Vision laptop webcam, microphone, screen/context awareness, and Android/iOS companion. Research only; no implementation or device testing. Recommendations below are architectural inferences, not claims about Luna's implementation.

## Creator reference: evidence boundary

The user supplied [@subject_shinji on TikTok](https://www.tiktok.com/@subject_shinji) as the Luna inspiration. The profile could not be retrieved by the research browser (fetch error). Exact-handle searches and searches restricted to YouTube/Instagram did not produce an attributable creator page or accessible source video. No video was watched, no transcript verified, and no creator-specific stack, latency, memory design, always-on behavior, or reliability claim is established here. This access failure does not establish that the content or accounts do not exist. Exa fallback was unavailable because its connector lacked an API key.

Treat Luna as the user's experience reference pending accessible creator material. Continuous presence, conversation, memory, and proactive assistance are product hypotheses for Ascend, not reverse-engineered facts about Luna. A later evidence pass should record exact video URLs, timestamps, demonstrated behavior, creator statements, and unresolved implementation assumptions separately.

## Windows: strongest baseline for passive context

| Capability | Verified platform fact | Practical implication (inference) |
| --- | --- | --- |
| Foreground application | `GetForegroundWindow` returns the foreground window handle, and can return NULL during activation transitions. [Microsoft](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getforegroundwindow) | Use a native per-user adapter for foreground-app context; handle unknown/transitional states. A foreground app is not evidence of productive attention. |
| Input inactivity | `GetLastInputInfo` supports idle detection only for the calling session; its tick count is not guaranteed to increase monotonically. [Microsoft](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getlastinputinfo) | Keep input-idle distinct from absence, reading, thinking, or watching media. Handle timing discontinuities and session changes. |
| Lock/unlock | Register a window using `WTSRegisterSessionNotification`; `WM_WTSSESSION_CHANGE` reports session lock/unlock and connection changes. [Registration](https://learn.microsoft.com/en-us/windows/win32/api/wtsapi32/nf-wtsapi32-wtsregistersessionnotification), [events](https://learn.microsoft.com/en-us/windows/win32/termserv/wm-wtssession-change) | Pause observation according to the user's lock policy and reconcile state after unlock, reconnect, or restart. |
| Screen capture | Windows Graphics Capture supports a system picker for the user to select a display or application window. [Microsoft](https://learn.microsoft.com/en-us/windows/apps/develop/media-authoring-processing/screen-capture) | Offer explicit selected-window capture; start with app metadata before adding pixels/OCR. A picker workflow is a supported option, not a claim that every native capture API requires that picker. |
| Camera/microphone | Windows camera privacy settings can deny access; camera APIs can return access denied. Edge also depends on Windows desktop camera/microphone access settings. [Camera API guidance](https://learn.microsoft.com/en-us/windows/apps/develop/camera/camera-privacy-setting), [Edge camera/microphone guidance](https://learn.microsoft.com/en-us/troubleshoot/microsoft-edge/development/edge-camera-microphone-not-working) | Show permission/device availability, provide independent mic/camera switches, and continue without unavailable sensors. Validate the actual packaging and capture stack on target hardware. |

Recommended baseline: a native desktop observer feeding timestamped, minimal events to the companion UI. Webcam presence, screen interpretation, speech recognition, and wake-word detection need separate accuracy, latency, resource, and privacy validation. None of the APIs above measures motivation, emotion, or intent.

## Android: useful usage context, constrained background sensing

`UsageStatsManager` provides time-range usage queries and events. Access to other apps generally requires `PACKAGE_USAGE_STATS` plus the user's grant in Settings; declaring the permission alone is insufficient. Events are retained for only a few days. Some queries return null when the user storage is not unlocked (`UserManager.isUserUnlocked`); do not confuse that state with every ordinary lock-screen appearance. [Android API reference](https://developer.android.com/reference/android/app/usage/UsageStatsManager)

Android 12+ generally restricts starting foreground services from the background, with documented exceptions. Camera/microphone services face additional while-in-use permission restrictions; Android 14+ can reject creation immediately when the permission is unavailable in that state. Required service types and permissions must be declared. These rules do not mean all user-started audio sessions must stop the instant the app loses focus. [Background start restrictions](https://developer.android.com/develop/background-work/services/fgs/restrictions-bg-start), [service types](https://developer.android.com/develop/background-work/services/fgs/service-types)

Recommendation: begin with notifications, session controls, explicit voice/check-ins, and optional usage-history synchronization. Add a native Android module only when usage access is needed. Treat usage as delayed evidence with permission/freshness state, not an exact live feed. Validate continuing voice sessions as a separate, visible foreground-service feature; do not promise silently starting camera/mic from arbitrary background states.

## iOS: distinguish ordinary Screen Time from regional data access

Individual users can authorize Family Controls themselves; this is not restricted to child accounts. The framework's picker ordinarily preserves privacy through opaque selections. Distribution requires Apple approval for the Family Controls entitlement, including relevant Screen Time extensions. [Family Controls](https://developer.apple.com/documentation/familycontrols?changes=latest_major), [individual authorization introduction](https://developer.apple.com/videos/play/wwdc2022/110336/), [distribution entitlement](https://developer.apple.com/documentation/familycontrols/requesting-the-family-controls-entitlement)

Ordinary `DeviceActivityReport` extensions render activity in a sandbox that blocks network requests and moving sensitive content outside the extension. Do not build the general iPhone companion around extracting that report into a laptop/cloud timeline. [DeviceActivityReport](https://developer.apple.com/documentation/deviceactivity/deviceactivityreport?language=objc)

Current docs also describe a distinct export path: `DeviceActivityData.activityData(filteredBy:using:)` can export activity for another app/platform. It requires the additional Family Controls App and Website Usage entitlement and `approvedWithDataAccess`. Customer installations require both an EU-located device and an EU Apple Account region; development testing can work elsewhere. Only one app per device can hold this authorization, and granting it to another app revokes that status from the first. This is a conditional regional branch, not universal iOS support. [Export API](https://developer.apple.com/documentation/deviceactivity/deviceactivitydata/activitydata%28filteredby%3Ausing%3A%29), [authorization status](https://developer.apple.com/documentation/FamilyControls/AuthorizationStatus/approvedWithDataAccess?changes=__3), [additional entitlement](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.developer.family-controls.app-and-website-usage?changes=_7)

iOS normally suspends background apps; runtime and background pushes are discretionary. Apple's newer continued-processing support serves explicit user-started work, not a general permanent observer. [Apple WWDC25 transcript](https://developer.apple.com/videos/play/wwdc2025/227/)

Recommendation: ship check-ins, reminders, voice sessions, and desktop handoff first. Gate native Screen Time features on entitlement, OS support, authorization, region, and physical-device verification. Keep the regional export branch separate from ordinary on-device reports/threshold features. Do not infer continuous background camera/mic availability from generic background-task APIs.

## PWA: companion interface and notifications

iOS/iPadOS support Web Push for Home Screen web apps from 16.4; notification permission is requested in response to user interaction. [WebKit](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/)

Service workers wake for events; they are not permanent processes. Periodic Background Sync has limited browser support and provides content-refresh opportunities rather than a universal precise scheduler. [W3C service-worker model](https://www.w3.org/TR/service-workers/), [Google implementation guide and compatibility](https://web.dev/articles/web-apps/periodic-background-sync)

Browser screen capture through `getDisplayMedia` requires transient user activation and does not persist granted permission across capture requests. Camera/microphone web capture is defined separately by Media Capture and Streams. [W3C Screen Capture](https://www.w3.org/TR/screen-capture/), [W3C Media Capture and Streams](https://www.w3.org/TR/mediacapture-streams/)

Recommendation: use the PWA for pairing, session state, manual reports, push, and explicit foreground media. Use platform adapters for OS usage information. Persist commands/events for eventual reconciliation; avoid background polling or screen capture promises that depend on an always-running browser context.

## Roadmap implications and confidence

1. Establish consent, independent sensor switches, data retention, stale/unknown states, device pairing, and event provenance before proactive behavior.
2. Prove the Windows app/idle/lock loop and a manual phone handoff; a break or an idle timer must not automatically become a judgment about the person.
3. Add user-started laptop voice, then optional webcam presence and selected-screen interpretation with separately measured error rates.
4. Add proactive suggestions only after confidence, cooldowns, quiet hours, dismissal, and correction are tested.
5. Add Android usage access and iOS Screen Time as separate capability branches. Cross-device continuity must work when either branch is unavailable.

Confidence is high for the documented API contracts above, moderate for architectural implications until device tests, and unverified for creator-specific behavior. Search-indexed Apple documentation supplied substantive text where the directly opened page required JavaScript. No physical devices, entitlement approvals, notification delivery timing, OEM battery behavior, video demonstrations, or end-to-end capture performance were tested. Recheck platform documentation and distribution requirements when each phase begins.
