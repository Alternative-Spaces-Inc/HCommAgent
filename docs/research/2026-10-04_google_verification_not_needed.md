# 2026-10-04: Google OAuth verification, does HCA need it?

## Question

The consent screen for the HCA project is External, publishing status
Testing (P7 record, 2026-10-02). The owner picked "Google verification" as
the next phase on 2026-10-04 on the stated benefit of ending the 7-day
expiry of test-user authorizations. This note checks that benefit against
Google's own documentation before any work is planned.

## Finding

Verification buys HCA nothing functional today. Google's Manage App
Audience page (support.google.com, Cloud Console help, article 15549945,
read 2026-10-04) states the Testing-status limits (100 test users, warning
screen, authorizations expiring after seven days) and then the exception:

> The only exception to this behavior is if your app requests a subset of
> the following: name, email address, and user profile (through the
> userinfo.email, userinfo.profile, openid scopes or their OpenID Connect
> equivalents). For such requests, your users do not need to be in the
> trusted user list, they will not see a warning message, and their
> authorizations will not expire after 7 days.

The HCA extension requests exactly `openid` and `email`
(`extension/manifest.json`, `oauth2.scopes`). So, in Testing status:

- the 7-day expiry does not apply to HCA's authorizations;
- the 100 test-user cap does not apply, and owners need not be listed as
  test users (the owner list that gates linking is HCA's own
  `HCA_OWNER_EMAILS`, server side);
- no "unverified app" warning is shown.

Independently, the P7 record already noted that the link flow only needs a
short-lived access token at link time, so even where the 7-day limit
applies it would not affect HCA.

What verification would change: brand verification lets the app name and
logo appear on the consent screen instead of the bare domain (Manage OAuth
App Branding, article 10311615). It requires a homepage and a privacy
policy on a verified authorized domain, with the policy linked from both.
HCA has neither page today (`homepage_url` absent from the manifest). That
is a presentation improvement, not a security or availability one.

## Recommendation

Do not spend a phase on Google verification now. Revisit only when HCA has
a public homepage and privacy policy for another reason (for example a
Chrome Web Store listing, which needs a privacy policy of its own), at
which point brand verification is a few-minute automated check from the
Branding page.

## Correction

The 2026-10-04 phase selection described verification as ending the
7-day token expiry. That description was wrong for HCA's scopes; this note
supersedes it.
