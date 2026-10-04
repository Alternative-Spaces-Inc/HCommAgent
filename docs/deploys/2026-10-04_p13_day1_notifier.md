# 2026-10-04: HCA-P13 Day 1, HCA Notifier runs and posts a banner

## Result

Owner proof 2026-10-04 13:0x PT: the app's "Send test notification
(Approve / Deny)" menu item produced a macOS banner titled "HCA test:
approve this test?", the owner clicked Approve, and the menu's second line
read "Last action: approve on test-approve-...". Action taps reach the app.

## Evidence

- Branch p13/day1-notifier, commit 97bcba7, PR 42.
- Owner Mac: macOS 27.0.1 arm64, Xcode 27.0 (27A266a), Swift 6.4. Xcode
  license accepted by the owner this day (`sudo xcodebuild -license accept`).
- `cd macos && swift test`: Executed 7 tests, with 0 failures.
- `bash macos/scripts/bundle.sh`: Build complete, bundle
  `~/hca-notifier/HCA Notifier.app`, version 97bcba7, codesign
  Identifier=com.alternativespaces.hca.notifier, Signature=adhoc,
  TeamIdentifier not set. Launched, process present.

## What Day 1 contains

- `macos/Sources/HCACore/NotificationShape.swift`: control to shape.
  approve_deny gives two buttons; radio with 1 to 4 options gives one
  button per option; every other control (or more than 4 options) is
  alert-only with the hint "Answer in Chrome". Notification identifier is
  `hca-` plus request id. Pure logic, tested.
- `macos/Sources/HCANotifier/main.swift`: accessory app (no Dock icon),
  status item, permission request, categories for every shape, two test
  notifications, taps recorded in the menu. No network, no keys.
- `macos/scripts/bundle.sh`: release build, Info.plist with LSUIElement,
  ad-hoc codesign. UNUserNotificationCenter refuses a bare executable, so
  the bundle is the only way to run it.

## Lessons recorded this day

- Merging PRs 39, 40 and 41 within seconds started three Cloud Builds;
  two failed at the deploy step with Cloud Run "ABORTED: Conflict for
  resource" (version moved on) and the newest (8aecfa00, aa67dbce)
  succeeded, revision hca-api-service-00020-bkq at 100%. Live equals main,
  but the procedure is now: one merge at a time, wait for its build.
  Candidate fix for the pipeline, to ship with the P13 server change: the
  deploy step retries once on that conflict only when its commit is still
  the tip of main.
- Google OAuth verification is not needed for HCA's scopes (PR 40).
- `git fetch origin <branch>` in the deploy clone does not create
  `origin/<branch>`; check out `FETCH_HEAD`.

## Next

Day 2: Keychain P-256 device key, signing on the devicesig contract with
shared test vectors (`api/tests/vectors/devicesig_v1.json`) asserted by
pytest and XCTest, public key export for the owner's registration block.
