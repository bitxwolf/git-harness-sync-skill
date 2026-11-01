---
name: git-workspace-sync
description: >-
  Discovers, audits, and safely synchronizes local Git repositories with remote
  GitHub branches across multi-repo workspaces. Detects merged PRs, fast-forwards
  clean branches, prunes stale tracking branches, and reports dirty worktrees without
  clobbering active edits. Prompts for GitHub account link before scanning targeted harness
  roots. Use when asked to sync repos, update local code from git, check multi-repo status,
  or pull merged PRs.
---

# Git Workspace Synchronizer

Safely synchronizes local Git repositories in AI coding harnesses (Antigravity, Claude Code, Cursor, Codex) with GitHub.

## Overview

In multi-repo agentic workflows, PRs are constantly merged on GitHub while local workspaces fall behind. This skill provides a targeted, safe reconciliation protocol that:
- Queries your GitHub account for repository names (or auto-detects via `gh` CLI).
- Scans only targeted harness directories (no full-disk scanning).
- Preserves active uncommitted edits (`DIRTY_SKIPPED`).
- Fast-forwards clean default branches (`UPDATED`).
- Automatically transitions merged PR branches whose remote tracking is gone back to the default branch (`PR_RESOLVED`).
- Reports untracked, diverged, and unpushed branches without guessing.

## Dependencies
Pure Python 3 standard library. No pip packages required. Uses local `git` and optional `gh` CLI.

## Quick Start

### 1. Reconcile with GitHub Account (Safe Fast-Forward)
```bash
python scripts/sync.py reconcile --github-user <USERNAME_OR_URL> --output summary.json
```

### 2. Read-Only Status Audit
```bash
python scripts/sync.py status --github-user <USERNAME_OR_URL>
```

### 3. Target Specific Workspace Roots
```bash
python scripts/sync.py reconcile --roots "C:/antigravity" "C:/Users/vantr/.gemini/antigravity/scratch" --github-user <USERNAME>
```

## Status Reference

| Status | Meaning | Action Taken |
|---|---|---|
| `UP_TO_DATE` | Local matches remote tracking branch | None needed |
| `UPDATED` | Behind origin on clean branch | `git pull --ff-only` executed |
| `PR_RESOLVED` | Feature branch PR merged & deleted on origin | Switched to default branch & fast-forwarded |
| `PR_MERGED_REMOTE` | Remote PR deleted on origin (reported in `status` mode) | Inspection only; ready to switch to default branch |
| `DIRTY_SKIPPED` | Working tree has uncommitted edits | Safe skip; preserves user/agent work |
| `BEHIND` | Behind origin (reported in `status` mode) | Inspection only |
| `AHEAD` | Local has commits not yet pushed | Inspection only |
| `DIVERGED` | Both local and origin have diverged commits | Inspection only |
| `UNTRACKED_BRANCH` | Local branch has no upstream on origin | Inspection only |
| `UPSTREAM_GONE` | Remote tracking branch for default branch is deleted on origin | Inspection only |
| `WORKTREE_BRANCH_LOCKED` | Remote PR merged, but default branch is checked out in another worktree | Safe skip & reported |
| `CHECKOUT_FAILED` | Remote PR deleted, but checkout of default branch failed | Safe skip & reported |
| `COMPARE_ERROR` | Could not compare HEAD with remote tracking branch | Safe skip & reported |
| `CONFLICT` | Fast-forward pull failed due to divergence or merge conflict | Safe skip & reported |
| `NO_REMOTE` | Repository has no remotes configured | Safe skip |
| `DETACHED_HEAD` | Repository is in detached HEAD state | Safe skip |
| `EMPTY_REPO` | Repository has no commits yet | Safe skip |
| `FETCH_ERROR` | Network or authentication failure on fetch | Safe skip & reported |

## Safety Invariants
1. **Zero Overwrite Guarantee:** Any repository with unstaged or staged modifications is marked `DIRTY_SKIPPED`.
2. **Fast-Forward Only:** Uses `git pull --ff-only` to guarantee no unwanted merge commits or conflict markers.
