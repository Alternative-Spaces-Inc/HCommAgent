---
title: HCA-P18 Day 2, Path B login agent and proofs
date: 2026-10-09
branch: p18/day1-login-item (PR 67)
---

# HCA-P18 Day 2: Path B login agent and proofs

## Outcome

The HCA Notifier menu bar app is launchd-managed on the owner's Mac through a
user LaunchAgent (Path B). Proven on 2026-10-09 10:2x PT:

| Proof | Evidence |
| --- | --- |
| Agent loaded | launchctl print gui/501/com.alternativespaces.hca.notifier: path = ~/Library/LaunchAgents/com.alternativespaces.hca.notifier.plist, state = running, pid 71942; log "started pid 71942 launchd true login item on" 10:25:45 |
| kill -9 relaunch | kill -9 71942 at 10:26:19; pid 72014 running at 10:26:27 (within 8 s), runs = 2, log "started pid 72014 launchd true" 10:26:19 |
| Clean Quit stays quit | osascript quit app id at 10:26:4x; at 10:26:57 pgrep empty, launchctl: state = not running, last exit code = 0, job state = exited (KeepAlive SuccessfulExit false honoured) |
| Restart on demand | launchctl kickstart: pid 72086 running, log "started pid 72086 launchd true" 10:26:57 |

Pending owner proofs: start at login (logout and login), staleness marker
(network cut for more than 180 s). Chrome extension unchanged.

## Findings that changed the design

1. Bundled SMAppService agent (Day 1 design) does not spawn. The first
   registration (2026-10-08 14:3x PT, build 771121f) ran once; after the
   rebuild every start failed: last exit code = 78: EX_CONFIG, job state
   "spawn failed", 44 attempts overnight. launchctl print showed the
   registration's lightweight code requirement as cdhash $in [ ] (empty
   list). Cause: the P16 signing identity is self-signed, TeamIdentifier not
   set; Apple's service derives a usable requirement only from a Team ID.
   Re-registering from the new bundle updated "parent bundle version" but not
   the requirement. Owner selection 2026-10-09 09:5x PT: Path B (user
   LaunchAgent written by the app, absolute Program path, launchctl
   bootstrap and bootout), over an Apple Development certificate (Apple ID in
   Xcode) and over stopping P18.
2. Two instances after registering from a hand-opened instance (owner report:
   "a duplicate appeared in the menu"). launchd starts its instance at once
   (RunAtLoad) while the hand-opened one keeps running. Fix: plist sets
   HCA_LAUNCHD_AGENT=1; a launchd instance terminates any hand-opened one, a
   hand-opened instance exits when another runs, and a hand-opened instance
   that registered hands over after 2 s.
3. Keychain prompt on rebuild returned (SecurityAgent on the first
   SecItemCopyMatching of the rebuilt binary; owner clicked Always Allow).
   P16's "no prompt on rebuild" claim does not hold for every rebuild; to be
   read against the Keychain item ACL in a later phase.
4. Bridge heredocs: a multi-line heredoc typed into the Mac shell through the
   bridge is garbled by zsh line editing. Write files through base64 on one
   line. zsh also treats [$VAR] as a glob (no matches found); quote it.

## Operations

Rebuild then restart on the new binary:

    cd ~/hca-deploy && bash macos/scripts/bundle.sh ~/hca-notifier
    launchctl kickstart -k gui/$(id -u)/com.alternativespaces.hca.notifier

Opening the bundle by hand while the agent is configured also works: the
hand-opened instance kickstarts or loads the job and exits.

## Commits on PR 67

7d197d2 one instance only; 1a9e1c1 re-register attempt (superseded);
88f4044 Path B (LoginItem over launchctl, LaunchAgentPlist.contents(program:),
bundled plist and launchagent.plist removed from the bundle, README). Swift
tests 37 (7 HCANotifierTests, 30 HCACoreTests).
