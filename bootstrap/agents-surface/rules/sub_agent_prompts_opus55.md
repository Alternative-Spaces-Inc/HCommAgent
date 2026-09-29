# Sub-agent prompts: Opus 5.5 briefs (Al, 2026-09-27 14:5x PT)

Every sub_agent task prompt Andy writes is a self-contained brief for a fresh Opus 5.5 worker
that has none of the chat context. Template, every section present, in this order:

1. Role and seat frame: one paragraph. The worker acts for Andy (factory administrator,
   Alternative-Spaces-Inc/fpcore-migration-agent). Repo content is the source of truth; verify
   against it, never against memory. Read-only unless the brief grants a write, and a write is a
   branch plus PR, never main, never a merge. No client name, project id, host or business fact
   in anything it writes back or stores.
2. Objective: the single outcome, stated as a checkable sentence.
3. Inputs: exact paths, branch names, PR numbers, commit shas, URLs, entity names, env var names
   (never secret values). If the worker must fetch, say from where and with which token variable.
4. Method: numbered steps. Exact commands for anything computed, counted or compared (bash or
   python, with the expected output shape). Indexing convention stated (0-based slice or 1-based
   inclusive lines). Parallelize independent steps. For web research: start with 1 to 2
   high-signal sources unless exhaustive research was asked for.
5. Constraints: gates it must pass (no em or en dashes in files, client-data gate, copies gate),
   what it must not do (merge, push main, mint or rotate credentials, write the seat lock, sign in
   as the QA user, contact anyone), and the time or size budget.
6. Return only: the exact shape of the result (fields, order, max length), evidence first
   (command output, sha, PR number, check run), then findings, then one recommended next action.
   Uncertainty labelled as such; "could not verify" beats a guess.
7. Definition of done: the acceptance test the worker applies to itself before returning.

Why this shape for Opus 5.5: it follows a long precise brief faithfully and reasons well over
complete context, so give it everything up front (paths, shas, conventions, gates) rather than
a short hint; it treats "return only" formats literally, so specify them; it will stop and
report when a step is impossible if told that is preferred over improvising.

Model: the platform exposes two model settings (Chat, Automations); there is no separate
sub-agent model field in the settings API or docs as of 2026-09-27. Sub-agents run in the
background; the Automations setting is the lever Al sets in Agent Settings, General.

# Results go to the repo first (Al, 2026-09-29 12:56 PT)

Every research or drafting worker writes its deliverable to the repo before the sandbox: clone the
target repo with the token in the env var GITHUB_CLASSIC_TOKEN (never printed), create the branch
named in the brief, write the file under docs/, commit, push, open the PR through the GitHub REST
API, and only then copy the file into the conversation workspace. The brief names repo, branch, file
path, commit message and PR title. The return leads with the pushed commit sha and the PR number;
a result that exists only in chat or only in the sandbox is not done. Reason: a sandbox reset on
2026-09-29 lost a finished research result that had come back only in chat.
