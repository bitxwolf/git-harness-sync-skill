#!/usr/bin/env python3
"""
git-workspace-sync: Production-grade multi-repo synchronizer for AI coding harnesses.
Zero external dependencies (pure Python 3 standard library).
Compatible with Antigravity, Claude Code, Cursor, Codex, and OpenCode.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path


def run_git(cwd: Path, args: list[str], timeout: int = 20) -> tuple[int, str, str]:
    """Executes a git command in target directory with UTF-8 safety, non-blocking auth, and timeout."""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    if "GIT_SSH_COMMAND" not in env:
        env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"
    try:
        res = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", f"Git command timed out after {timeout}s: git {' '.join(args)}"
    except Exception as e:
        return 1, "", str(e)


def extract_github_username(user_or_url: str) -> str:
    """Extracts clean GitHub username or organization from a username, handle, or URL."""
    if not user_or_url or not user_or_url.strip():
        return ""
    s = user_or_url.strip().strip("/")
    s = s.split("?")[0].split("#")[0].strip("/")
    if not s or s.lower() in ("github.com", "http://github.com", "https://github.com", "www.github.com", "http://www.github.com", "https://www.github.com"):
        return ""
    if "github.com/" in s.lower() or "github.com:" in s.lower():
        delimiter = "github.com/" if "github.com/" in s.lower() else "github.com:"
        idx = s.lower().find(delimiter)
        after = s[idx + len(delimiter):].strip("/")
        parts = [p for p in after.split("/") if p]
        is_ssh_url = s.lower().startswith("ssh://") or "ssh." in s.lower()
        if is_ssh_url and delimiter == "github.com:" and parts and parts[0].isdigit():
            parts = parts[1:]
        if parts:
            if parts[0].lower() in ("orgs", "users"):
                if len(parts) > 1:
                    return parts[1].removesuffix(".git")
                return ""
            return parts[0].removesuffix(".git")
        return ""

    if s.startswith("@"):
        s = s[1:]
    clean = s.split("/")[-1].removesuffix(".git")
    if clean.lower() in ("github.com", "www.github.com"):
        return ""
    return clean


def detect_github_user() -> str | None:
    """Attempts to auto-detect GitHub username from environment, gh CLI, or git config."""
    # 1. Environment variable
    env_user = os.environ.get("GITHUB_USER")
    if env_user and env_user.strip():
        u = extract_github_username(env_user)
        if u:
            return u

    # 2. gh CLI
    try:
        res = subprocess.run(
            ["gh", "api", "user", "-q", ".login"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
            encoding="utf-8",
            errors="replace",
        )
        if res.returncode == 0 and res.stdout.strip():
            u = extract_github_username(res.stdout.strip())
            if u:
                return u
    except Exception:
        pass

    # 3. git config github.user
    try:
        res = subprocess.run(
            ["git", "config", "--global", "--get", "github.user"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3,
            encoding="utf-8",
            errors="replace",
        )
        if res.returncode == 0 and res.stdout.strip():
            u = extract_github_username(res.stdout.strip())
            if u:
                return u
    except Exception:
        pass

    return None


def fetch_github_repos(user_or_url: str) -> list[str]:
    """Fetches list of repository names from GitHub API or gh CLI with pagination and token support."""
    username = extract_github_username(user_or_url)
    if not username:
        return []

    # 1. Try gh CLI first if available
    try:
        res = subprocess.run(
            ["gh", "repo", "list", username, "--limit", "200", "--json", "name"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
        )
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout)
            return [r["name"].lower() for r in data if isinstance(r, dict) and "name" in r]
    except Exception:
        pass

    # 2. Fallback to public GitHub API with token support and pagination
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    headers = {"User-Agent": "Antigravity-Git-Sync/1.0", "Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"Bearer {token.strip()}"

    all_repos: list[str] = []
    endpoints = [
        f"https://api.github.com/users/{username}/repos?per_page=100&type=all",
        f"https://api.github.com/orgs/{username}/repos?per_page=100",
    ]

    rate_limited = False
    for base_url in endpoints:
        page = 1
        endpoint_success = False
        while page <= 3:  # Fetch up to 300 repos
            url = f"{base_url}&page={page}" if "?" in base_url else f"{base_url}?page={page}"
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=10) as response:
                    if response.status == 200:
                        data = json.loads(response.read().decode("utf-8"))
                        if not isinstance(data, list) or not data:
                            break
                        names = [r["name"].lower() for r in data if isinstance(r, dict) and "name" in r]
                        all_repos.extend(names)
                        endpoint_success = True
                        if len(data) < 100:
                            break
                        page += 1
                    else:
                        break
            except urllib.error.HTTPError as e:
                if e.code in (403, 429):
                    rate_limited = True
                break
            except Exception:
                break
        if endpoint_success or rate_limited:
            break

    if not all_repos:
        if rate_limited:
            sys.stderr.write("Notice: GitHub API rate limit reached. Falling back to local discovery. (Tip: set GITHUB_TOKEN or login with 'gh auth login')\n")
        else:
            sys.stderr.write(f"Notice: Could not fetch remote list for '{username}'. Falling back to local discovery.\n")

    return all_repos


def get_default_search_roots() -> list[Path]:
    """Gathers targeted harness directories without scanning entire drives or root home dirs."""
    roots: list[Path] = []
    home_dir = Path.home().resolve()
    cwd = Path.cwd().resolve()

    # Only include cwd if it is not the bare home directory or drive root, or if cwd itself has a .git
    is_home_or_root = (cwd == home_dir) or (cwd.parent == cwd)
    if not is_home_or_root or (cwd / ".git").exists():
        roots.append(cwd)

    # 1. Antigravity projects from projects.json
    gemini_projects = Path.home() / ".gemini" / "projects.json"
    if gemini_projects.exists():
        try:
            with open(gemini_projects, "r", encoding="utf-8") as f:
                pdata = json.load(f)
                for p in pdata.get("projects", {}).keys():
                    ppath = Path(p).resolve()
                    # Never crawl entire user root directory or drive root
                    if ppath.exists() and ppath != home_dir and ppath.parent != ppath and ppath not in roots:
                        roots.append(ppath)
        except Exception:
            pass

    # 2. Common harness directories
    candidates = [
        Path("C:/antigravity"),
        Path.home() / ".gemini" / "antigravity" / "scratch",
        Path.home() / ".gemini" / "antigravity" / "worktrees",
        Path.home() / ".claude" / "workspaces",
    ]

    for c in candidates:
        c_res = c.resolve()
        if c_res.exists() and c_res != home_dir and c_res.parent != c_res and c_res not in roots:
            roots.append(c_res)

    return roots


def normalize_name(s: str) -> str:
    """Removes non-alphanumeric characters and lowercases string for fuzzy matching."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def extract_repo_name_from_url(url: str) -> str:
    """Extracts clean repository name from git remote URL."""
    clean = url.strip().replace("\\", "/").rstrip("/").removesuffix(".git")
    if "/" in clean:
        clean = clean.split("/")[-1]
    if ":" in clean:
        clean = clean.split(":")[-1]
    return clean.lower()


def _matches_tokens(target: str, kw: str, norm_kw: str = "") -> bool:
    """
    Checks if target matches kw via exact match or token boundaries (-, _, ., space).
    Rejects arbitrary substring matches (e.g. 'test' in 'attestation', 'react' in 'reaction').
    """
    if not target or not kw:
        return False

    target_lower = target.lower().strip()
    kw_lower = kw.lower().strip()
    norm_kw = norm_kw or normalize_name(kw_lower)
    norm_target = normalize_name(target_lower)

    # 1. Exact match (raw or normalized)
    if kw_lower == target_lower or (norm_kw and norm_kw == norm_target):
        return True

    # 2. Tokenize by -, _, ., space
    target_tokens = [t for t in re.split(r"[-_.\s]+", target_lower) if t]
    norm_target_tokens = [normalize_name(t) for t in target_tokens if normalize_name(t)]

    kw_tokens = [t for t in re.split(r"[-_.\s]+", kw_lower) if t]
    norm_kw_tokens = [normalize_name(t) for t in kw_tokens if normalize_name(t)]

    # Single token match
    if kw_lower in target_tokens or (norm_kw and norm_kw in norm_target_tokens):
        return True

    # Multi-token keyword match (e.g. kw='my-app' in target='my-app-v2')
    if len(kw_tokens) > 1 and len(target_tokens) >= len(kw_tokens):
        for i in range(len(target_tokens) - len(kw_tokens) + 1):
            if target_tokens[i : i + len(kw_tokens)] == kw_tokens:
                return True
            if norm_kw_tokens and norm_target_tokens[i : i + len(norm_kw_tokens)] == norm_kw_tokens:
                return True

    return False


def matches_repository(repo_path: Path, keywords: list[str], norm_keywords: list[str] = None, username: str = "") -> bool:
    """
    Checks if a repository matches GitHub account repo keywords, username, or remote URL.
    Safeguards against false positives (e.g. short folder 'go' matching 'algorithm',
    or substring 'test' matching 'attestation', 'react' matching 'reaction').
    """
    if not keywords and not norm_keywords and not username:
        return True

    # Build reliable 1:1 keyword pairs (raw_lower, normalized) to prevent zip misalignment
    kw_pairs: list[tuple[str, str]] = []
    for k in (keywords or []):
        k_clean = k.lower().strip()
        if k_clean:
            kw_pairs.append((k_clean, normalize_name(k_clean)))

    clean_user = extract_github_username(username).lower() if username else ""

    # 1. Remote URL match (Authoritative)
    code, remotes_out, _ = run_git(repo_path, ["config", "--get-regexp", r"^remote\..*\.url"], timeout=3)
    remote_urls = []
    if code == 0 and remotes_out:
        for line in remotes_out.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2:
                remote_urls.append(parts[1])
    else:
        c, u, _ = run_git(repo_path, ["config", "--get", "remote.origin.url"], timeout=2)
        if c == 0 and u:
            remote_urls.append(u)

    for u in remote_urls:
        if clean_user:
            url_owner = extract_github_username(u).lower()
            if url_owner and url_owner == clean_user:
                return True
        r_name = extract_repo_name_from_url(u)
        norm_r_name = normalize_name(r_name)
        for kw, nkw in kw_pairs:
            if r_name == kw or (nkw and norm_r_name == nkw) or _matches_tokens(r_name, kw, nkw):
                return True

    # 2. Folder name match (Only fall back to folder matching if remote_urls is empty)
    if not remote_urls:
        folder_name = repo_path.name.lower()
        norm_folder = normalize_name(folder_name)

        # Exact or normalized match or token boundary match
        for kw, nkw in kw_pairs:
            if folder_name == kw or (nkw and norm_folder == nkw) or _matches_tokens(folder_name, kw, nkw):
                return True

    return False


def discover_repositories(roots: list[Path], repo_keywords: list[str] = None, filter_by_keywords: bool = False, username: str = "") -> list[Path]:
    """
    Discovers git repositories strictly within targeted roots.
    If filter_by_keywords is True, strictly restricts matches to repo_keywords or username.
    Otherwise, discovers all valid repositories in the targeted roots.
    """
    discovered: list[Path] = []
    seen: set[str] = set()

    keywords = [k.lower() for k in (repo_keywords or []) if k]
    norm_keywords = [normalize_name(k) for k in keywords if normalize_name(k)]

    skip_dirs = {
        "node_modules", ".venv", "venv", "env", ".git", "__pycache__", "dist", "build", ".next", ".nuget",
        "appdata", ".gemini", "downloads", "pictures", "videos", "music", ".cache", ".cargo", ".rustup",
        ".gradle", ".m2", "saved games", "searches", "contacts", "links", "favorites"
    }

    for root in roots:
        root = root.resolve()
        if not root.exists():
            continue

        # If root itself is a git repo
        if (root / ".git").exists():
            root_key = str(root).lower()
            if root_key not in seen:
                if not filter_by_keywords or matches_repository(root, keywords, norm_keywords, username=username):
                    discovered.append(root)
                    seen.add(root_key)
            continue

        for dirpath, dirnames, _ in os.walk(root, onerror=lambda err: None):
            curr_path = Path(dirpath).resolve()

            # Bound scan depth (max 3 levels relative to root)
            try:
                rel = os.path.relpath(dirpath, root)
                depth = 0 if rel == "." else len(Path(rel).parts)
            except Exception:
                depth = 0

            if depth > 3:
                dirnames.clear()
                continue

            # In-place slice pruning to avoid traversing giant/system trees
            dirnames[:] = [d for d in dirnames if d.lower() not in skip_dirs]

            if (curr_path / ".git").exists():
                curr_key = str(curr_path).lower()
                if curr_key not in seen:
                    if not filter_by_keywords or matches_repository(curr_path, keywords, norm_keywords, username=username):
                        discovered.append(curr_path)
                        seen.add(curr_key)
                dirnames.clear()  # Do not recurse into nested directories within git repo

    return sorted(discovered, key=lambda x: x.name.lower())


def get_configured_remotes(repo: Path) -> list[str]:
    """Returns list of configured remote names for a repository."""
    code, out, _ = run_git(repo, ["remote"], timeout=3)
    if code != 0 or not out.strip():
        return []
    return [r.strip() for r in out.splitlines() if r.strip()]


def get_default_branch(repo: Path, remotes: list[str] = None) -> str:
    """Detects default branch (symbolic-ref on remote, standard branch names, or remote inspection)."""
    if remotes is None:
        remotes = get_configured_remotes(repo)

    remote_candidates = (["origin"] if "origin" in remotes else []) + [r for r in remotes if r != "origin"]

    # 1. Check symbolic-ref on remotes
    for r in remote_candidates:
        code, out, _ = run_git(repo, ["symbolic-ref", f"refs/remotes/{r}/HEAD", "--short"], timeout=3)
        if code == 0 and out:
            prefix = f"{r}/"
            return out[len(prefix):] if out.startswith(prefix) else out.split("/")[-1]

    # 2. Check standard branch names on remotes in order of priority
    for b in ["main", "master", "trunk", "develop"]:
        for r in remote_candidates:
            code, _, _ = run_git(repo, ["rev-parse", "--verify", f"{r}/{b}"], timeout=2)
            if code == 0:
                return b

    # 3. Check all remote branches to find symbolic target
    code, rbranches, _ = run_git(repo, ["branch", "-r"], timeout=3)
    if code == 0 and rbranches:
        for line in rbranches.splitlines():
            clean_line = line.strip()
            if "->" in clean_line:
                target = clean_line.split("->")[-1].strip()
                if "/" in target:
                    return target.partition("/")[2]
                return target

    # 4. Check local branch names in order of priority
    for b in ["main", "master", "trunk", "develop"]:
        code, _, _ = run_git(repo, ["rev-parse", "--verify", b], timeout=2)
        if code == 0:
            return b

    return "main"


def reconcile_repo(repo: Path, action: str = "reconcile") -> dict:
    """
    Audits or reconciles a single git repository.
    Enforces strict non-destructive safety: never touches dirty working trees.
    """
    repo_name = repo.name
    res = {
        "repo": repo_name,
        "path": str(repo.resolve()),
        "branch": "unknown",
        "status": "UNKNOWN",
        "detail": "",
    }

    # 1. Empty repository check
    code, head_commit, _ = run_git(repo, ["rev-parse", "--verify", "HEAD"], timeout=3)
    if code != 0:
        res["status"] = "EMPTY_REPO"
        res["detail"] = "Repository has no commits yet (empty repository)."
        return res

    # 2. Identify current branch
    _, branch, _ = run_git(repo, ["rev-parse", "--abbrev-ref", "HEAD"], timeout=3)
    res["branch"] = branch or "detached"

    # 3. Detached HEAD check
    if res["branch"] == "HEAD":
        res["status"] = "DETACHED_HEAD"
        res["detail"] = "Repository is in detached HEAD state. Safe skipping automatic branch pull."
        return res

    # 4. Invariant: Never touch a dirty working tree
    code_st, status_out, err_st = run_git(repo, ["status", "--porcelain"], timeout=5)
    if code_st != 0:
        res["status"] = "DIRTY_SKIPPED"
        res["detail"] = f"Unable to verify working tree cleanliness ({err_st}). Safe skipped."
        return res
    if bool(status_out):
        res["status"] = "DIRTY_SKIPPED"
        res["detail"] = f"Uncommitted modifications on [{res['branch']}]. Safe skipped."
        return res

    # 5. Check if any remotes exist
    remotes = get_configured_remotes(repo)
    if not remotes:
        res["status"] = "NO_REMOTE"
        res["detail"] = "No git remotes configured for this repository."
        return res

    # Determine tracking remote and primary remote
    code_up, tracking_upstream, _ = run_git(repo, ["for-each-ref", "--format=%(upstream:short)", f"refs/heads/{branch}"], timeout=3)
    tracked_remote = tracking_upstream.split("/")[0] if (code_up == 0 and tracking_upstream and "/" in tracking_upstream) else None
    primary_remote = tracked_remote if (tracked_remote and tracked_remote in remotes) else ("origin" if "origin" in remotes else remotes[0])

    # 6. Fetch updates & prune deleted remote tracking branches
    code_fetch, _, err_fetch = run_git(repo, ["fetch", "--all", "--prune", "--quiet"], timeout=15)
    if code_fetch != 0:
        # Fallback to fetching primary remote specifically if secondary remote failed
        code_pri, _, err_pri = run_git(repo, ["fetch", primary_remote, "--prune", "--quiet"], timeout=15)
        if code_pri != 0:
            res["status"] = "FETCH_ERROR"
            res["detail"] = f"Remote fetch failed ({primary_remote}): {err_pri or err_fetch}"
            return res

    default_branch = get_default_branch(repo, remotes)

    # 7. PR Resolution Gate: Use official Git plumbing %(upstream:track)
    code_track, upstream_track, _ = run_git(repo, ["for-each-ref", "--format=%(upstream:track)", f"refs/heads/{branch}"], timeout=3)
    upstream_gone = (code_track == 0 and bool(re.search(r"\bgone\b", upstream_track)))

    if upstream_gone:
        if branch == default_branch:
            res["status"] = "UPSTREAM_GONE"
            res["detail"] = f"Remote tracking branch for default branch [{branch}] is gone on {primary_remote}."
            return res

        # If the feature branch has local unpushed commits, preserve branch and report unpushed commits
        ahead_count = 0
        if "ahead" in upstream_track:
            m_ahead = re.search(r"ahead (\d+)", upstream_track)
            ahead_count = int(m_ahead.group(1)) if m_ahead else 1

        if ahead_count > 0:
            res["status"] = "AHEAD"
            res["detail"] = f"Remote branch is gone, but local [{branch}] has {ahead_count} unpushed commit(s). Preserved branch."
            return res

        if branch != default_branch:
            target_remote = primary_remote
            if action == "reconcile":
                # Check if local default_branch already exists
                c_has_branch, _, _ = run_git(repo, ["rev-parse", "--verify", f"refs/heads/{default_branch}"], timeout=2)
                if c_has_branch == 0:
                    c_co, _, err_co = run_git(repo, ["checkout", default_branch], timeout=10)
                else:
                    c_co, _, err_co = run_git(repo, ["checkout", "-b", default_branch, "--track", f"{target_remote}/{default_branch}"], timeout=10)

                if c_co == 0:
                    # Prefer fast-forwarding from local tracking ref first (zero network roundtrip)
                    target_tracking = f"{target_remote}/{default_branch}"
                    c_pull, _, err_pull = run_git(repo, ["merge", "--ff-only", target_tracking], timeout=10)
                    if c_pull != 0:
                        c_pull, _, err_pull = run_git(repo, ["pull", "--ff-only", target_remote, default_branch], timeout=15)
                        if c_pull != 0:
                            c_pull, _, err_pull = run_git(repo, ["pull", "--ff-only"], timeout=15)

                    res["branch"] = default_branch
                    if c_pull == 0:
                        res["status"] = "PR_RESOLVED"
                        res["detail"] = f"Remote PR merged. Switched to [{default_branch}] and fast-forwarded from {target_remote}."
                    else:
                        res["status"] = "CONFLICT"
                        res["detail"] = f"Remote PR merged. Switched to [{default_branch}], but fast-forward pull failed: {err_pull}"
                    return res
                else:
                    if "already used by worktree" in err_co.lower() or "already checked out at" in err_co.lower():
                        res["status"] = "WORKTREE_BRANCH_LOCKED"
                        res["detail"] = f"Remote PR merged for [{branch}], but [{default_branch}] is checked out in another worktree: {err_co}"
                    else:
                        res["status"] = "CHECKOUT_FAILED"
                        res["detail"] = f"Remote PR gone, but checkout of [{default_branch}] failed: {err_co}"
                    return res
            else:
                res["status"] = "PR_MERGED_REMOTE"
                res["detail"] = f"Remote PR deleted for [{branch}]. Ready to switch to [{default_branch}]."
                return res
        else:
            res["status"] = "UPSTREAM_GONE"
            res["detail"] = f"Remote tracking branch for default branch [{branch}] is gone on {primary_remote}."
            return res

    # 8. Check upstream tracking branch
    tracking_ref = None
    if code_up == 0 and tracking_upstream:
        c_v, _, _ = run_git(repo, ["rev-parse", "--verify", tracking_upstream], timeout=2)
        if c_v == 0:
            tracking_ref = tracking_upstream

    if not tracking_ref:
        c_u, up_ref, _ = run_git(repo, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], timeout=2)
        if c_u == 0 and up_ref and up_ref != "@{u}":
            c_v, _, _ = run_git(repo, ["rev-parse", "--verify", up_ref], timeout=2)
            if c_v == 0:
                tracking_ref = up_ref

    if not tracking_ref:
        for r in remotes:
            c_v, _, _ = run_git(repo, ["rev-parse", "--verify", f"{r}/{branch}"], timeout=2)
            if c_v == 0:
                tracking_ref = f"{r}/{branch}"
                break

    if not tracking_ref:
        res["status"] = "UNTRACKED_BRANCH"
        res["detail"] = f"Local branch [{branch}] has no remote tracking branch on {primary_remote}."
        return res

    # 9. Ahead / Behind check against tracking reference
    code, counts, err_rev = run_git(repo, ["rev-list", "--left-right", "--count", f"HEAD...{tracking_ref}"], timeout=5)
    if code != 0:
        res["status"] = "COMPARE_ERROR"
        res["detail"] = f"Could not compare HEAD with {tracking_ref}: {err_rev}"
        return res

    ahead, behind = 0, 0
    if counts:
        parts = counts.split()
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            ahead, behind = int(parts[0]), int(parts[1])
        else:
            res["status"] = "COMPARE_ERROR"
            res["detail"] = f"Unexpected rev-list output: {counts}"
            return res

    if behind == 0 and ahead == 0:
        res["status"] = "UP_TO_DATE"
        res["detail"] = f"Matches {tracking_ref}"
    elif behind > 0 and ahead == 0:
        if action == "reconcile":
            # Prefer fast-forwarding from local tracking ref first (zero network roundtrip)
            c, _, err_p = run_git(repo, ["merge", "--ff-only", tracking_ref], timeout=10)
            if c != 0:
                c, _, err_p = run_git(repo, ["pull", "--ff-only"], timeout=15)
                if c != 0 and "/" in tracking_ref:
                    remote_name, _, remote_branch = tracking_ref.partition("/")
                    c, _, err_p = run_git(repo, ["pull", "--ff-only", remote_name, remote_branch], timeout=15)

            if c == 0:
                res["status"] = "UPDATED"
                res["detail"] = f"Fast-forwarded +{behind} commits from {tracking_ref}"
            else:
                res["status"] = "CONFLICT"
                res["detail"] = f"Failed fast-forward pull: {err_p}"
        else:
            res["status"] = "BEHIND"
            res["detail"] = f"Behind {tracking_ref} by {behind} commits"
    elif behind > 0 and ahead > 0:
        res["status"] = "DIVERGED"
        res["detail"] = f"+{ahead}/-{behind} commits diverged from {tracking_ref}"
    elif ahead > 0 and behind == 0:
        res["status"] = "AHEAD"
        res["detail"] = f"Local has {ahead} unpushed commits"

    return res


def main():
    parser = argparse.ArgumentParser(description="Multi-repo Git Synchronizer for AI Coding Harnesses")
    parser.add_argument("command", choices=["status", "reconcile"], default="reconcile", nargs="?",
                        help="Action: 'status' (read-only audit) or 'reconcile' (safe fast-forward pull)")
    parser.add_argument("--github-user", help="GitHub username, handle (@user), or profile URL")
    parser.add_argument("--roots", nargs="*", help="Specific workspace directories to scan")
    parser.add_argument("--output", help="Path to write JSON results file")
    parser.add_argument("--non-interactive", action="store_true", help="Do not prompt interactively for input")
    args = parser.parse_args()

    explicit_gh_user = bool(args.github_user)
    gh_user = args.github_user or detect_github_user()
    if not gh_user:
        if not args.non-interactive and sys.stdin.isatty():
            try:
                entered = input("GitHub user not detected. Enter GitHub username or profile link (or leave blank to skip): ").strip()
                if entered:
                    gh_user = entered
                    explicit_gh_user = True
            except (EOFError, KeyboardInterrupt):
                pass

    repo_keywords: list[str] = []
    clean_user = extract_github_username(gh_user) if gh_user else ""
    if gh_user and clean_user:
        print(f"Querying GitHub account: {clean_user}...", flush=True)
        repo_keywords = fetch_github_repos(gh_user)
        print(f"Found {len(repo_keywords)} remote repositories on account.", flush=True)
    else:
        print("Note: No GitHub user provided or detected. Proceeding with targeted workspace repository discovery...", flush=True)

    # Determine search roots
    search_roots = [Path(r).resolve() for r in args.roots] if args.roots else get_default_search_roots()

    # Filter strictly by keywords if user explicitly passed --github-user and keywords were found
    filter_by_keywords = explicit_gh_user and bool(repo_keywords)
    repos = discover_repositories(search_roots, repo_keywords, filter_by_keywords=filter_by_keywords, username=clean_user)

    if not repos:
        msg = "No matching Git repositories found in targeted harness roots."
        if args.output:
            payload = {
                "github_user": gh_user or "",
                "discovered_repos": 0,
                "total_repos": 0,
                "results": [],
                "message": msg
            }
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(msg, flush=True)
        return

    print(f"\nDiscovered {len(repos)} repositories in targeted harness roots. Running '{args.command}'...\n", flush=True)
    print(f"{'Repository':<28} | {'Branch':<15} | {'Status':<18} | {'Details'}", flush=True)
    print("-" * 95, flush=True)

    results = []
    for r in repos:
        res = reconcile_repo(r, action=args.command)
        results.append(res)
        print(f"{res['repo']:<28} | {res['branch']:<15} | {res['status']:<18} | {res['detail']}", flush=True)

    if args.output:
        payload = {
            "github_user": gh_user or "",
            "discovered_repos": len(repos),
            "total_repos": len(repos),
            "results": results
        }
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nMachine-readable summary written to: {Path(args.output).resolve()}", flush=True)


if __name__ == "__main__":
    main()
