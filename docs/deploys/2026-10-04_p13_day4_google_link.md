# P13 Day 4: Google link for the Mac device and the first macOS banner answer

Date: 2026-10-04 (PT). Branch p13/day4-link-fix on top of main 50925e7 (PR 45).

## What was proven

1. The Mac device (label "Al MacBook Notifier", surface macos_notification,
   device id prefix dev_41d7938b) linked its Google account through the
   Desktop OAuth client: PKCE, loopback listener, code exchange, then
   POST /v1/devices/self/google-link. Server record: google_linked_at
   2026-10-05T00:21:57Z. The gate opened at once: GET /v1/inbox from the
   device was 403 not-linked before and 200 after.
2. A live ask posted by an agent key (caller agent-p13-proof, prefix
   ec44e882ff79, revoked 00:48:50Z) was answered from the macOS banner.
   Record 668b7b4a: state answered, value a, answered_by "Al MacBook
   Notifier", surface macos_notification, device dev_41d7938b, answered_at
   2026-10-05T00:47:37Z, path_taken [macos_notification, banner, option_0].

## Defects found by the proof and fixed on this branch

- Google refuses the code exchange for Desktop (installed app) clients
  without the client secret, even with PKCE ("client_secret is missing").
  The app now sends the secret when configured (defaults key
  googleClientSecret, stored on the Mac only, never in the repo). The
  Console no longer shows or downloads an existing secret; a new secret
  must be created with "+ Add secret" and its JSON downloaded at that
  moment. Commit 95906ef.
- SIGUSR1 (the sign-in trigger) is ignored until the app is ready, so a
  signal during start-up cannot start a half-initialised flow. Commit
  473092b.
- Ask banners showed without Approve and Deny: the per-ask notification
  category was registered and the notification added in the same
  instant, before usernoted had applied the new category set. The app now
  reads the category set back (getNotificationCategories) and adds the
  notification behind that read, with a fall-back to the base category if
  the id is still missing. Commit c827a74. Verified: the re-posted banners
  carried buttons and the tap recorded.

## Proof takes, for the record

- Take 1 (a4ef6759) and take 2 (71d8a1fe) were first answered or shown on
  the Chrome extension, because every owner device receives every owner
  ask. For take 3 the owner paused the extension (chrome://extensions
  toggle) so the only banner was the Mac one. Take 2 was also answered
  from the Mac banner at 00:47:41Z after the button fix.
- Lesson for the ladder: two surfaces on one machine both ring; the
  dedupe via stream events removes the second banner only after an answer,
  not before. Surface preference per device is a plan item, not a defect.

## Secrets and keys

- Desktop OAuth client secret: in the Mac app's preferences only. The
  downloaded JSON files were deleted with rm -P after reading.
- Proof keys cb45825d8ab6, e4749541f8c1, ec44e882ff79 revoked.
- Still live from earlier phases: agent-p6-proof, agent-p7-proof and two
  smoke keys. Candidate for a clean-up PR.

## Next

Day 5 of the P13 plan: radio asks with up to 4 options on the banner,
red-gate time-sensitive level, and the dedupe between surfaces.
