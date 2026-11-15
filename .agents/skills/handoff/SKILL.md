---
name: handoff
description: "Generate a copy-ready handoff prompt for the current task or workstream, tailored to a fresh ChatGPT.com chat (chat), VS Code Codex session (codex), or VS Code GitHub Copilot session (copilot). Use when asked to hand off, transfer, continue in another session, or prepare a new agent prompt; do not perform the handoff task itself."
argument-hint: "chat | codex | copilot"
---

# Handoff — transfer the current task to a fresh session

Produce **one standalone, copy-ready prompt** for the requested destination. This skill only prepares the prompt; it does not launch a session, run the next task, or change repository files.

## Invocation

- `handoff chat` — a fresh conversation on ChatGPT.com.
- `handoff codex` — a fresh Codex session inside VS Code.
- `handoff copilot` — a fresh GitHub Copilot Chat **Agent** session inside VS Code.

Accept the destination from the user's wording or the skill argument. If omitted and the destination is not otherwise clear, ask only which of these three they want. Never silently choose a destination. If the user explicitly wants multiple destinations, produce one separately labeled prompt per destination.

## Gather the current state (read-only)

1. **Scope:** Identify the specific current task, its intended outcome and the most immediate unfinished step from the conversation. Consult maintained plans (for this repository, `docs/TODO.md`) when needed. Do **not** substitute an unrelated backlog item if the user is mid-task, or claim that the task is unfinished after it was completed.
2. **Workspace:** If tools are available, inspect the authorized repository's *current* branch, full HEAD SHA, latest commit and working-tree status. Note meaningful modified, staged, untracked and ignored work when material. Do not assume this information from an earlier message if it can be checked now.
3. **Progress:** Collect decisions and constraints, files changed, implemented behavior, unresolved problems, concrete next steps, and acceptance criteria. Include the minimum useful source paths and commands. For larger work, distinguish what was finished from what is merely planned.
4. **Validation:** Record which checks actually ran and their results, with exact useful counts/commands when known. Mark unrun, stale, blocked or environment-specific checks clearly. Do not represent an agent's report as independent verification unless it was verified.
5. **Boundaries:** Read the relevant `AGENTS.md` and project development instructions if accessible. Preserve existing work, source identities and any user decisions about commits, pushes, deployment, network activity and adjacent repositories. Mention material restrictions, but avoid generic policy dumps.
6. **Missing access:** If this session cannot inspect the files or Git state, use only grounded conversation facts and label them **last reported / unverified**, instructing the next agent to inspect the live checkout. Never invent a branch, path, SHA, tool, test result, or external access.

This is a **read-only handoff**: do not stage, commit, push, sync, reset, run long tests, modify task documents or generate a new artifact unless the user separately requests that work. Avoid needless exhaustive audits just to write a prompt.

## Choose the destination

### `chat` — fresh ChatGPT.com conversation

Make the prompt self-contained: the next chat may have **none of this conversation's context**. Include repository identity, scope, project background necessary for the task, exact last-observed branch/HEAD, uncommitted changes, recent decisions, work done, validation, pitfalls, next steps and an explicit request to proceed.

Tell the next chat to inspect the **connected repository/workspace tools if available** before editing. Do not assume the user has connected the same MCP tools in the fresh chat. If repository access is unavailable, the next chat should say so and ask for connection or the relevant files instead of pretending to have edited them. Refer to project-relative paths wherever possible.

### `codex` — fresh Codex session in VS Code

Assume the user will open the intended repository/worktree in VS Code. Write an actionable, implementation-oriented prompt that tells Codex to read `AGENTS.md` (and applicable local instructions), inspect `git status` and `git rev-parse HEAD` first, and **preserve all existing unstaged/staged/untracked changes**. Prefer project-relative file paths, concrete implementation starting points, and focused validation commands over repeated background narrative.

Clearly distinguish facts established by the preceding session from things Codex should re-check in its workspace. Do not assume the workspace matches an old SHA or that commits were pushed. Do not grant implicit permission to commit, push, reset, install packages, or run live crawls.

### `copilot` — fresh GitHub Copilot session in VS Code

Assume GitHub Copilot Chat **Agent mode** in the repository workspace. Instruct Copilot to read `.github/copilot-instructions.md`, `AGENTS.md` and applicable development guidance, inspect the actual Git working tree before editing, and work directly with workspace files.

Use concrete file anchors, discrete next actions and intended observable results, with focused tests and the required stop condition. Explicitly preserve the current uncommitted work and do not assume Copilot has access to tools or other sessions' chat history. Do not grant implicit commit, push, deployment, network or sibling-repository permissions.

## Output contract

Return the prompt **directly in the response**, in a single fenced Markdown block so the user can copy and paste it. Do not create or write a handoff file by default. Keep commentary outside the block minimal.

Each prompt should contain, adapted to its destination:

1. **Actionable heading:** `Continue <specific task>` (not a vague generic handoff).
2. **Repository/workspace:** Project name, branch and exact HEAD *if verified*, plus current working-tree status, especially uncommitted work.
3. **Current objective and decisions:** What the user wants, relevant constraints, and why the current approach was chosen.
4. **Completed work and evidence:** Actual implementation, important paths and observed checks.
5. **Next steps / acceptance criteria:** The smallest coherent next batch and how to validate success.
6. **Operational boundaries:** What not to overwrite, restart, publish, push, or silently assume.

Write it as instructions to **the next agent**, not as a report addressed to the current user. Use the user's requested language, otherwise the project's working language. Make the handoff detailed enough to continue immediately but omit unnecessary chronology, large logs, credentials, personal/absolute host paths, private dataset contents, and speculative claims. Never claim the receiving agent will remember this conversation.

If the current task is finished, state that accurately and identify the next user-approved task or planned follow-up as **next**, not as unfinished work on the completed task. If there is no clear next action, ask the new agent to assess the actual state rather than invent a task.

For this producer repository, maintain the separation between crawler/source work, consumer InfinityDB changes, live acquisition and snapshot publication. Point to `docs/TODO.md` as the maintained roadmap; no cross-repository edits or releases are implied.
