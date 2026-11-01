# ⚡ git-harness-sync-skill

[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero%20(stdlib%20only)-brightgreen.svg)](#dependencies)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](#prerequisites)
[![Test Coverage](https://img.shields.io/badge/tests-37%2F37%20passing-success.svg)](#testing)
[![License: MIT](https://img.shields.io/badge/license-MIT-purple.svg)](LICENSE)
[![Harness Agnostic](https://img.shields.io/badge/harness-Antigravity%20|%20Claude%20Code%20|%20Cursor%20|%20OpenHands%20|%20Aider-orange.svg)](#universal-agent-setup)

> **Autonomous multi-repo synchronization and pull request reconciliation engine built for AI coding agents and development harnesses.**

---

## 🧭 Interactive Quick Index

<details open>
<summary><b>Table of Contents (Click to Expand / Collapse)</b></summary>

- [Why This Skill Exists](#why-this-skill-exists)
- [How It Works (Architecture)](#how-it-works-architecture)
- [🤖 Universal Instructions for AI Agents Across Any Harness](#-universal-instructions-for-ai-agents-across-any-harness)
  - [Antigravity (Global or Workspace)](#1-antigravity)
  - [Claude Code](#2-claude-code)
  - [Cursor & Windsurf](#3-cursor--windsurf)
  - [OpenHands & Headless Agent Runners](#4-openhands--headless-containers)
  - [Aider & Codex CLI](#5-aider--codex-cli)
- [Agent System Prompt Injection Block](#agent-system-prompt-injection-block)
- [Interactive CLI Reference](#interactive-cli-reference)
- [Status Code Decision Matrix](#status-code-decision-matrix)
- [Safety Guarantees & Invariants](#safety-guarantees--invariants)
- [Running Automated Verification](#running-automated-verification)
- [License](#license)

</details>

---

## Why This Skill Exists

In multi-repo agentic coding workflows, agents work across dozens of branched repositories simultaneously. Meanwhile:
1. **PRs are merged on GitHub**: Local working branches become orphaned (`[ahead N, gone]`).
2. **Default branches fall behind**: Upstream repositories evolve, causing agents to build patches against outdated code.
3. **Dirty worktrees get clobbered**: Blind `git pull` or `git checkout` commands in naive scripts wipe out in-progress agent or user modifications.
4. **Modal dialog hangs**: Background subprocesses freeze indefinitely waiting for interactive Windows Git Credential Manager prompts.

`git-harness-sync-skill` provides a **zero-dependency, purely deterministic, non-destructive reconciliation protocol** that solves all four problems automatically.

---

## How It Works (Architecture)

```
                    ┌─────────────────────────────────┐
                    │      GitHub API / gh CLI        │
                    │  (Query Account Repositories)   │
                    └────────────────┬────────────────┘
                                     │ User/Org Repo List
                                     ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   Targeted Harness Root Discovery                      │
│      (C:/antigravity, ~/.claude, workspace roots — depth capped)       │
└────────────────────────────────────┬───────────────────────────────────┘
                                     │ Discovered Local Repos
                                     ▼
                    ┌─────────────────────────────────┐
                    │     Safety Check (git status)   │
                    └───────┬─────────────────┬───────┘
           Dirty Worktree   │                 │ Clean Worktree
                            ▼                 ▼
             ┌─────────────────────┐   ┌───────────────────────────────┐
             │    DIRTY_SKIPPED    │   │      Fetch Remote Tracking    │
             │ (Work Preserved 0%) │   │   (GCM_INTERACTIVE="never")   │
             └─────────────────────┘   └──────────────┬────────────────┘
                                                      │
                       ┌──────────────────────────────┴──────────────────────────────┐
                       │                                                             │
                       ▼                                                             ▼
         [Remote PR Deleted / Gone]                                    [Behind Remote Default]
         Switches to default branch                                    Fast-forwards branch
         & fast-forwards cleanly:                                      `git pull --ff-only`:
             `PR_RESOLVED`                                                  `UPDATED`
```

---

## 🤖 Universal Instructions for AI Agents Across Any Harness

If you are an autonomous coding agent (Antigravity, Claude Code, Cursor, OpenHands, Devin, Codex, Aider), follow the instructions corresponding to your environment:

### 1. Antigravity
Clone directly into your global skills configuration:
```bash
git clone https://github.com/bitxwolf/git-harness-sync-skill.git ~/.gemini/config/skills/git-workspace-sync
```
*Or into a specific workspace:*
```bash
git clone https://github.com/bitxwolf/git-harness-sync-skill.git .agent/skills/git-workspace-sync
```

### 2. Claude Code
Install to your user config skills directory:
```bash
git clone https://github.com/bitxwolf/git-harness-sync-skill.git ~/.claude/skills/git-harness-sync
```
Invoke from Claude Code chat:
```text
Run python ~/.claude/skills/git-harness-sync/scripts/sync.py reconcile --github-user <username>
```

### 3. Cursor & Windsurf
Add to project `.cursor/rules` or clone into project tools:
```bash
git clone https://github.com/bitxwolf/git-harness-sync-skill.git .tools/git-harness-sync
```
Add to your agent prompt:
```text
Whenever asked to synchronize workspaces, run:
python .tools/git-harness-sync/scripts/sync.py reconcile --roots . --github-user <username>
```

### 4. OpenHands & Headless Containers
In your container initialization or bootstrap script:
```bash
git clone --depth 1 https://github.com/bitxwolf/git-harness-sync-skill.git /opt/skills/git-harness-sync
python3 /opt/skills/git-harness-sync/scripts/sync.py reconcile --roots /workspace --github-user bitxwolf --output /tmp/sync-summary.json
```

### 5. Aider & Codex CLI
Run one-shot reconciliation:
```bash
git clone https://github.com/bitxwolf/git-harness-sync-skill.git /tmp/git-harness-sync
python3 /tmp/git-harness-sync/scripts/sync.py reconcile --roots . --github-user bitxwolf
```

---

## Agent System Prompt Injection Block

Copy and paste this snippet directly into any agent configuration or system prompt:

```markdown
### Multi-Repo Git Synchronization Protocol
When the user asks to "sync repositories", "update repos from git", "pull merged PRs", or "check git status":
1. Ensure the git-harness-sync-skill is downloaded.
2. Determine the GitHub username (from git config, gh auth status, or user prompt).
3. Run safe non-destructive audit:
   python scripts/sync.py status --github-user <USERNAME>
4. To apply fast-forward updates and resolve merged PR branches:
   python scripts/sync.py reconcile --github-user <USERNAME> --output sync_summary.json
5. Inspect the generated summary JSON and report status using the standard status codes.
```

---

## Interactive CLI Reference

<details open>
<summary><b>1. Safe Reconcile Mode (Fast-Forward + PR Cleanup)</b></summary>

```bash
python scripts/sync.py reconcile --github-user <USERNAME_OR_PROFILE_URL> [--roots <DIR1> <DIR2>] [--output summary.json]
```
- Fetches upstream references safely (`git fetch --prune`).
- Fast-forwards clean tracking branches (`git merge --ff-only`).
- Safely transitions local feature branches whose PRs were merged and deleted on GitHub back to default (`PR_RESOLVED`).
- Writes structured machine-readable JSON if `--output` is specified.

</details>

<details>
<summary><b>2. Read-Only Status Audit Mode</b></summary>

```bash
python scripts/sync.py status --github-user <USERNAME_OR_PROFILE_URL> [--roots <DIR1> <DIR2>]
```
- Scans all local repositories under targeted roots.
- Audits commit drift against remote tracking branches without touching disk state or switching branches.
- Outputs clean console status report.

</details>

<details>
<summary><b>3. Custom Search Roots & Token Support</b></summary>

```bash
# Specify explicit roots
python scripts/sync.py reconcile --roots "C:/projects" "D:/repos" --github-user bitxwolf

# Pass token explicitly via environment variable (or let gh CLI handle auth)
export GITHUB_TOKEN="ghp_..."
python scripts/sync.py reconcile --github-user bitxwolf
```

</details>

---

## Status Code Decision Matrix

The synchronizer returns explicit, deterministic status codes for every scanned repository:

| Status Code | Severity | Description | Action Performed |
|---|---|---|---|
| `UP_TO_DATE` | 🟢 Success | Local branch exactly matches origin | None required |
| `UPDATED` | 🟢 Success | Local was behind clean remote default | `git pull --ff-only` executed |
| `PR_RESOLVED` | 🟢 Success | Remote feature branch was merged & deleted | Switched to default branch & fast-forwarded |
| `PR_MERGED_REMOTE` | ℹ️ Notice | Remote PR branch deleted on origin (Audit mode) | Ready for reconciliation |
| `DIRTY_SKIPPED` | 🟡 Preserved | Working directory has modified / staged files | Skipped to prevent data clobbering |
| `BEHIND` | ℹ️ Notice | Local is behind remote tracking (Audit mode) | Ready for fast-forward pull |
| `AHEAD` | ℹ️ Notice | Local has unpushed commits | Preserved; no destructive overwrite |
| `DIVERGED` | ⚠️ Diverged | Local and remote tracking branches have diverged | Skipped; manual resolution needed |
| `UNTRACKED_BRANCH` | ℹ️ Notice | Local branch has no upstream on origin | Skipped safely |
| `UPSTREAM_GONE` | ⚠️ Stale | Remote tracking branch was removed upstream | Skipped safely |
| `WORKTREE_BRANCH_LOCKED` | ⚠️ Locked | Default branch is locked in another git worktree | Skipped safely |
| `CHECKOUT_FAILED` | ❌ Error | Checkout of target branch failed | Reported safely |
| `COMPARE_ERROR` | ❌ Error | Unable to compute commit delta with remote | Reported safely |
| `CONFLICT` | ❌ Error | Fast-forward merge aborted due to conflicts | Skipped safely |
| `NO_REMOTE` | ⚪ Neutral | Repository has no remote origin configured | Skipped safely |
| `DETACHED_HEAD` | ⚪ Neutral | Repository is in detached HEAD state | Skipped safely |
| `EMPTY_REPO` | ⚪ Neutral | Repository has no commits | Skipped safely |
| `FETCH_ERROR` | ❌ Network | Network or authentication failure during fetch | Reported safely |

---

## Safety Guarantees & Invariants

1. 🛡️ **Zero Overwrite Guarantee:** Any repository with uncommitted or staged changes is immediately flagged `DIRTY_SKIPPED`. No `reset --hard`, `stash`, or force operations are ever run.
2. 🚀 **Fast-Forward Only:** Synchronization strictly uses `git pull --ff-only` or `git merge --ff-only`. It will never inject unexpected merge commits or conflict markers into working trees.
3. 🔒 **No Windows Modal Freezes:** All child git processes run with `GCM_INTERACTIVE="never"`, guaranteeing automated daemon pipelines never stall on interactive credential prompts.
4. 📦 **Zero Third-Party Dependencies:** Implemented exclusively with Python 3's standard library (`subprocess`, `urllib`, `pathlib`, `json`, `argparse`). Works out of the box on minimal Alpine/Debian containers, macOS, and Windows.

---

## Running Automated Verification

Run the bundled regression test suite covering all 18 status codes, simulated git conflicts, network retries, and worktree locks:

```bash
python -m unittest discover -s tests -v
```
*Or using pytest:*
```bash
pytest tests/ -v
```

All 37 unit and integration tests execute in < 60 seconds.

---

## License

This project is licensed under the [MIT License](LICENSE).
*(See LICENSE for our special clause on actual engineering vs. markdown prompt repos).*
