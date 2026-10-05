# P13 Day 5: cross-surface proofs, partial

Date: 2026-10-04 18:1x to 2026-10-05 07:5x PT. Server main 4eb5d83b, Mac app
bundle c827a74, Chrome extension on (owner's choice for most takes). Proof key
caller agent-p13-day5, prefix 7939ee4bd901.

## Scope

Day 4 already met the plan's Day 5 row (action tap answers, record read,
record written). Day 5 therefore tried to prove the three Day 3 behaviours
that had not been exercised: dedupe across surfaces, radio banners with four
options, and the red-gate interruption level.

## Results

| Proof | Request | Outcome |
| --- | --- | --- |
| A, dedupe | ff32836b | Proven with a caveat. Answered in the Chrome popup (value a, Al MacBook Chrome, browser_overlay, 01:14:40Z). The Mac notification store held no banner for it afterwards. Caveat: the answer came 34 s after posting, so the store cannot show whether the Mac banner was removed by the request.answered event or had not yet been presented. |
| B, radio 4 | 215764dc, 5627ae57 | Not proven on the Mac. Both takes were answered from the Chrome device (value north, browser_overlay). The Mac banner was delivered each time with its per-ask category (store shows category hca.ask.<id>), but no answer call from the Mac app reached the server. |
| C, red gate | 0c45589a | Not proven on the Mac. Answered from the Chrome device (value a, browser_overlay, 13:21:10Z). |

## Findings

1. Two surfaces on one machine are not distinguishable by eye. The Chrome
   extension's desktop notification and the HCA Notifier banner land in the
   same corner with the same title; only the small app name differs. Across
   four takes the owner's tap went to the Chrome copy every time, including
   takes where the owner believed he had used the HCA Notifier item. The
   ladder needs a per-device surface preference (owner picks one primary
   surface per machine; the others stay silent until the primary has not
   answered within a window), not a visual tweak. This is the main Day 5
   result and a plan item for the next phase.
2. Focus suppresses the Mac banners. On 2026-10-05 06:1x PT two Focus
   modes were active (Sleep and a custom one) and the delivered red-gate
   banner was not shown; the owner had to turn Focus off. The app sets
   interruptionLevel timeSensitive for red gates, but on macOS that level
   needs the entitlement com.apple.developer.usernotifications.time-sensitive,
   which an ad-hoc signed app cannot carry. Red gates therefore do not break
   through Focus in P13. Fix path: Developer ID signing with the entitlement
   (listed under Not in P13 in the plan), or a macOS alert style notification
   plus the Chrome surface as the fallback.
3. A pending ask whose banner was cleared is not re-posted until the app
   restarts (shownAt is in memory). Relaunching the app re-posted proof C
   at once. Worth a periodic re-post for still-pending asks.
4. gcloud on the owner's Mac asked for reauthentication overnight. The
   proof reads were done through the app's stored API base URL instead of
   the service describe call; the admin token read (key revoke) needs the
   sign-in.

## Keys

agent-p13-day5 (7939ee4bd901) is still live: revoke needs the admin token,
which needs gcloud sign-in on the Mac. Revoke at the next sign-in together
with agent-p6-proof and agent-p7-proof.

## Next

Plan item for the next phase: per-device surface preference with a fallback
window, then redo proofs B and C with only the Mac surface active.
