# Results Go To Repo First

Every research or drafting worker writes its deliverable to the repository before the sandbox:
1. Clone the target repository with the token in env var GITHUB_CLASSIC_TOKEN (never printed).
2. Create the branch named in the brief.
3. Write the file under docs/ or appropriate repository path.
4. Commit, push, and open the PR through the GitHub REST API.
5. Only then copy the file into the conversation workspace.

The return leads with the pushed commit sha and the PR number. A result that exists only in chat or only in the sandbox is not done.
