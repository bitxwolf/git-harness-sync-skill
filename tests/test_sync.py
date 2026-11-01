import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
import urllib.error
from pathlib import Path

# Self-contained and portable import
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
import sync


class TestGitWorkspaceSync(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="sync_test_")
        self.root = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _init_git_repo(self, path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
        return path

    def _commit_file(self, repo: Path, filename: str, content: str, msg: str):
        (repo / filename).write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", filename], cwd=repo, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "commit", "-m", msg], cwd=repo, check=True, stdout=subprocess.DEVNULL)

    def test_normalize_name(self):
        self.assertEqual(sync.normalize_name("world of 3js"), "worldof3js")
        self.assertEqual(sync.normalize_name("medical-for-non-medics"), "medicalfornonmedics")
        self.assertEqual(sync.normalize_name("R1(kitty)"), "r1kitty")
        self.assertEqual(sync.normalize_name("vectorbridge----pvt"), "vectorbridgepvt")

    def test_extract_github_username(self):
        self.assertEqual(sync.extract_github_username("vantr"), "vantr")
        self.assertEqual(sync.extract_github_username("@vantr"), "vantr")
        self.assertEqual(sync.extract_github_username("https://github.com/vantr"), "vantr")
        self.assertEqual(sync.extract_github_username("https://github.com/vantr/"), "vantr")
        self.assertEqual(sync.extract_github_username("https://github.com/vantr/some-repo"), "vantr")
        self.assertEqual(sync.extract_github_username("https://github.com/orgs/my-org/repositories"), "my-org")
        # /users/ prefix support
        self.assertEqual(sync.extract_github_username("https://github.com/users/vantr"), "vantr")
        self.assertEqual(sync.extract_github_username("https://github.com/users/vantr/repos"), "vantr")
        self.assertEqual(sync.extract_github_username("https://github.com/users/vantr/"), "vantr")
        self.assertEqual(sync.extract_github_username("git@github.com:users/vantr/repo.git"), "vantr")
        # Query parameters and URL fragments sanitization
        self.assertEqual(sync.extract_github_username("https://github.com/vantr?tab=repositories"), "vantr")
        self.assertEqual(sync.extract_github_username("https://github.com/users/vantr?tab=repositories"), "vantr")
        self.assertEqual(sync.extract_github_username("https://github.com/users/vantr#readme"), "vantr")
        self.assertEqual(sync.extract_github_username("https://api.github.com/users/vantr"), "vantr")
        self.assertEqual(sync.extract_github_username("https://api.github.com/users/vantr/repos"), "vantr")
        # SSH SCP syntax
        self.assertEqual(sync.extract_github_username("git@github.com:vantr/repo.git"), "vantr")
        self.assertEqual(sync.extract_github_username("git@github.com:my-org/cool-project.git"), "my-org")
        self.assertEqual(sync.extract_github_username("git@github.com:orgs/enterprise-team/repo.git"), "enterprise-team")
        self.assertEqual(sync.extract_github_username("git@github.com:octocat"), "octocat")

    def test_matches_repository(self):
        repo_path = self.root / "world of 3js"
        repo_path.mkdir()
        keywords = ["worldof3js", "bughunter"]
        norm_keywords = [sync.normalize_name(k) for k in keywords]
        
        self.assertTrue(sync.matches_repository(repo_path, keywords, norm_keywords))
        
        other_path = self.root / "unrelated_project"
        other_path.mkdir()
        self.assertFalse(sync.matches_repository(other_path, keywords, norm_keywords))

    def test_matches_repository_safeguards(self):
        keywords = ["algorithm", "pineapple", "bughunter"]
        norm_keywords = [sync.normalize_name(k) for k in keywords]

        # False-positive probes: Short folders must NOT match longer repo names
        p_go = self.root / "go"
        p_go.mkdir()
        self.assertFalse(sync.matches_repository(p_go, keywords, norm_keywords))

        p_app = self.root / "app"
        p_app.mkdir()
        self.assertFalse(sync.matches_repository(p_app, keywords, norm_keywords))

        # False-positive probes: Arbitrary substrings inside words must NOT match
        p_attestation = self.root / "attestation"
        p_attestation.mkdir()
        self.assertFalse(sync.matches_repository(p_attestation, ["test"], ["test"]))

        p_reaction = self.root / "reaction"
        p_reaction.mkdir()
        self.assertFalse(sync.matches_repository(p_reaction, ["react"], ["react"]))

        # True-positive probes: Delimited prefix, suffix, or token boundary
        p_bg = self.root / "bughunter-backend"
        p_bg.mkdir()
        self.assertTrue(sync.matches_repository(p_bg, keywords, norm_keywords))

        p_test_suite = self.root / "test-suite"
        p_test_suite.mkdir()
        self.assertTrue(sync.matches_repository(p_test_suite, ["test"], ["test"]))

        p_react_app = self.root / "react.app"
        p_react_app.mkdir()
        self.assertTrue(sync.matches_repository(p_react_app, ["react"], ["react"]))

    def test_matches_repository_remote_url(self):
        repo = self._init_git_repo(self.root / "custom_folder_name")
        self._commit_file(repo, "README.md", "init", "init")
        subprocess.run(["git", "remote", "add", "upstream", "https://github.com/company/super-engine.git"], cwd=repo, check=True)

        keywords = ["super-engine"]
        norm_keywords = [sync.normalize_name(k) for k in keywords]
        self.assertTrue(sync.matches_repository(repo, keywords, norm_keywords))

    def test_empty_repo(self):
        repo = self._init_git_repo(self.root / "empty_repo")
        res = sync.reconcile_repo(repo, action="status")
        self.assertEqual(res["status"], "EMPTY_REPO")

    def test_no_remote(self):
        repo = self._init_git_repo(self.root / "local_only")
        self._commit_file(repo, "README.md", "# Hello", "Initial commit")
        res = sync.reconcile_repo(repo, action="status")
        self.assertEqual(res["status"], "NO_REMOTE")

    def test_detached_head(self):
        repo = self._init_git_repo(self.root / "detached_repo")
        self._commit_file(repo, "f1.txt", "1", "c1")
        self._commit_file(repo, "f2.txt", "2", "c2")
        subprocess.run(["git", "checkout", "HEAD~1"], cwd=repo, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        res = sync.reconcile_repo(repo, action="status")
        self.assertEqual(res["status"], "DETACHED_HEAD")

    def test_dirty_skipped_uncommitted_file(self):
        remote = self.root / "remote.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, stdout=subprocess.DEVNULL)

        local = self.root / "local_repo"
        subprocess.run(["git", "clone", str(remote), str(local)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=local, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=local, check=True)
        self._commit_file(local, "main.py", "print('hello')", "commit 1")
        subprocess.run(["git", "push", "origin", "main"], cwd=local, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        (local / "main.py").write_text("print('dirty change')", encoding="utf-8")

        res = sync.reconcile_repo(local, action="reconcile")
        self.assertEqual(res["status"], "DIRTY_SKIPPED")
        self.assertIn("Uncommitted modifications", res["detail"])
        self.assertEqual((local / "main.py").read_text(encoding="utf-8"), "print('dirty change')")

    def test_untracked_branch(self):
        remote = self.root / "remote2.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, stdout=subprocess.DEVNULL)

        local = self.root / "local2"
        subprocess.run(["git", "clone", str(remote), str(local)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=local, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=local, check=True)
        self._commit_file(local, "init.txt", "init", "init")
        subprocess.run(["git", "push", "origin", "main"], cwd=local, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        subprocess.run(["git", "checkout", "-b", "feature/unpushed"], cwd=local, check=True, stdout=subprocess.DEVNULL)
        self._commit_file(local, "feature.txt", "feat", "feature work")

        res = sync.reconcile_repo(local, action="status")
        self.assertEqual(res["status"], "UNTRACKED_BRANCH")

    def test_commit_message_with_gone_does_not_trigger_pr_resolved(self):
        """Verifies commit message with ': gone]' on an untracked branch does NOT falsely trigger PR recovery."""
        remote = self.root / "remote_cm.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, stdout=subprocess.DEVNULL)

        local = self.root / "local_cm"
        subprocess.run(["git", "clone", str(remote), str(local)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=local, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=local, check=True)
        self._commit_file(local, "init.txt", "init", "init")
        subprocess.run(["git", "push", "origin", "main"], cwd=local, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Checkout untracked branch with commit message containing ': gone]'
        subprocess.run(["git", "checkout", "-b", "feat/my-fix"], cwd=local, check=True, stdout=subprocess.DEVNULL)
        self._commit_file(local, "fix.txt", "content", "fix: gone] handle edge case")

        res = sync.reconcile_repo(local, action="reconcile")
        self.assertEqual(res["status"], "UNTRACKED_BRANCH")
        self.assertEqual(res["branch"], "feat/my-fix")

    def test_up_to_date(self):
        remote = self.root / "remote3.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, stdout=subprocess.DEVNULL)

        local = self.root / "local3"
        subprocess.run(["git", "clone", str(remote), str(local)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=local, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=local, check=True)
        self._commit_file(local, "file.txt", "v1", "v1")
        subprocess.run(["git", "push", "origin", "main"], cwd=local, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        res = sync.reconcile_repo(local, action="status")
        self.assertEqual(res["status"], "UP_TO_DATE")

    def test_behind_and_reconcile_update(self):
        remote = self.root / "remote4.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, stdout=subprocess.DEVNULL)

        w1 = self.root / "worker1"
        subprocess.run(["git", "clone", str(remote), str(w1)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=w1, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=w1, check=True)
        self._commit_file(w1, "code.txt", "v1", "v1")
        subprocess.run(["git", "push", "origin", "main"], cwd=w1, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        w2 = self.root / "worker2"
        subprocess.run(["git", "clone", str(remote), str(w2)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=w2, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=w2, check=True)

        self._commit_file(w1, "code.txt", "v2-merged-pr", "merged pr commit")
        subprocess.run(["git", "push", "origin", "main"], cwd=w1, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        res_status = sync.reconcile_repo(w2, action="status")
        self.assertEqual(res_status["status"], "BEHIND")

        res_reconcile = sync.reconcile_repo(w2, action="reconcile")
        self.assertEqual(res_reconcile["status"], "UPDATED")
        self.assertEqual((w2 / "code.txt").read_text(encoding="utf-8"), "v2-merged-pr")

    def test_pr_merged_and_deleted_remote_branch(self):
        remote = self.root / "remote5.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, stdout=subprocess.DEVNULL)

        local = self.root / "local5"
        subprocess.run(["git", "clone", str(remote), str(local)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=local, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=local, check=True)
        self._commit_file(local, "main.txt", "main v1", "main v1")
        subprocess.run(["git", "push", "origin", "main"], cwd=local, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        subprocess.run(["git", "checkout", "-b", "feat/my-pr"], cwd=local, check=True, stdout=subprocess.DEVNULL)
        self._commit_file(local, "feat.txt", "feat code", "feat commit")
        subprocess.run(["git", "push", "-u", "origin", "feat/my-pr"], cwd=local, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        other = self.root / "other_clone"
        subprocess.run(["git", "clone", str(remote), str(other)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=other, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=other, check=True)
        subprocess.run(["git", "merge", "origin/feat/my-pr", "--ff-only"], cwd=other, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "push", "origin", "main"], cwd=other, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "push", "origin", "--delete", "feat/my-pr"], cwd=other, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        res = sync.reconcile_repo(local, action="reconcile")
        self.assertEqual(res["status"], "PR_RESOLVED")
        self.assertEqual(res["branch"], "main")
        self.assertTrue((local / "feat.txt").exists())

    def test_upstream_remote_reconcile(self):
        """Verifies full reconcile and PR recovery on repository whose remote is 'upstream' instead of 'origin'."""
        remote = self.root / "upstream_remote.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, stdout=subprocess.DEVNULL)

        local = self.root / "local_upstream"
        subprocess.run(["git", "clone", str(remote), str(local), "-o", "upstream"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=local, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=local, check=True)
        self._commit_file(local, "base.txt", "v1", "v1")
        subprocess.run(["git", "push", "upstream", "main"], cwd=local, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Create branch and push to upstream
        subprocess.run(["git", "checkout", "-b", "feat/upstream-pr"], cwd=local, check=True, stdout=subprocess.DEVNULL)
        self._commit_file(local, "feat.txt", "work", "feat work")
        subprocess.run(["git", "push", "-u", "upstream", "feat/upstream-pr"], cwd=local, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Merge on upstream and delete branch
        other = self.root / "other_upstream"
        subprocess.run(["git", "clone", str(remote), str(other), "-o", "upstream"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=other, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=other, check=True)
        subprocess.run(["git", "merge", "upstream/feat/upstream-pr", "--ff-only"], cwd=other, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "push", "upstream", "main"], cwd=other, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "push", "upstream", "--delete", "feat/upstream-pr"], cwd=other, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Reconcile on local must switch to main and fast-forward cleanly without origin errors
        res = sync.reconcile_repo(local, action="reconcile")
        self.assertEqual(res["status"], "PR_RESOLVED")
        self.assertEqual(res["branch"], "main")
        self.assertTrue((local / "feat.txt").exists())

    def test_upstream_fallback_when_u_unconfigured(self):
        """Verifies that a branch pushed to upstream without @{u} config is still detected as UP_TO_DATE."""
        remote = self.root / "upstream_fallback.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, stdout=subprocess.DEVNULL)

        local = self.root / "local_upstream_fallback"
        subprocess.run(["git", "init", "-b", "main", str(local)], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=local, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=local, check=True)
        self._commit_file(local, "main.txt", "content", "initial")
        subprocess.run(["git", "remote", "add", "upstream", str(remote)], cwd=local, check=True)
        subprocess.run(["git", "push", "upstream", "main"], cwd=local, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        res = sync.reconcile_repo(local, action="status")
        self.assertEqual(res["status"], "UP_TO_DATE")

    def test_multi_remote_offline_fallback(self):
        """Verifies that an offline secondary remote does not abort sync when tracking remote is reachable."""
        remote = self.root / "primary.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, stdout=subprocess.DEVNULL)

        local = self.root / "local_multiremote"
        subprocess.run(["git", "clone", str(remote), str(local)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=local, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=local, check=True)
        self._commit_file(local, "f.txt", "1", "1")
        subprocess.run(["git", "push", "origin", "main"], cwd=local, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Add offline/broken secondary remote
        subprocess.run(["git", "remote", "add", "broken_secondary", "https://broken.invalid.example/repo.git"], cwd=local, check=True)

        res = sync.reconcile_repo(local, action="status")
        # Should gracefully fallback to primary remote and succeed with UP_TO_DATE
        self.assertEqual(res["status"], "UP_TO_DATE")

    def test_worktree_detection(self):
        """Verifies that git worktrees (.git file) are discovered and audited properly."""
        remote = self.root / "wt_remote.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, stdout=subprocess.DEVNULL)

        main_repo = self.root / "main_repo"
        subprocess.run(["git", "clone", str(remote), str(main_repo)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=main_repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=main_repo, check=True)
        self._commit_file(main_repo, "init.txt", "init", "init")
        subprocess.run(["git", "push", "origin", "main"], cwd=main_repo, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        wt_dir = self.root / "wt_linked"
        subprocess.run(["git", "worktree", "add", "-b", "feat/wt-test", str(wt_dir), "main"], cwd=main_repo, check=True, stdout=subprocess.DEVNULL)

        discovered = sync.discover_repositories([self.root])
        self.assertIn(wt_dir.resolve(), [p.resolve() for p in discovered])

        res = sync.reconcile_repo(wt_dir, action="status")
        self.assertEqual(res["repo"], "wt_linked")
        self.assertEqual(res["branch"], "feat/wt-test")

    def test_ahead_status(self):
        remote = self.root / "remote6.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, stdout=subprocess.DEVNULL)

        local = self.root / "local6"
        subprocess.run(["git", "clone", str(remote), str(local)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=local, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=local, check=True)
        self._commit_file(local, "file.txt", "initial", "initial")
        subprocess.run(["git", "push", "origin", "main"], cwd=local, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        self._commit_file(local, "file.txt", "local-ahead", "ahead commit")
        res = sync.reconcile_repo(local, action="status")
        self.assertEqual(res["status"], "AHEAD")

    def test_diverged_status(self):
        remote = self.root / "remote7.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, stdout=subprocess.DEVNULL)

        w1 = self.root / "w1_diverge"
        subprocess.run(["git", "clone", str(remote), str(w1)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=w1, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=w1, check=True)
        self._commit_file(w1, "base.txt", "base", "base")
        subprocess.run(["git", "push", "origin", "main"], cwd=w1, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        w2 = self.root / "w2_diverge"
        subprocess.run(["git", "clone", str(remote), str(w2)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=w2, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=w2, check=True)

        self._commit_file(w1, "remote_commit.txt", "remote", "remote commit")
        subprocess.run(["git", "push", "origin", "main"], cwd=w1, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        self._commit_file(w2, "local_commit.txt", "local", "local commit")

        res = sync.reconcile_repo(w2, action="status")
        self.assertEqual(res["status"], "DIVERGED")

    def test_fetch_error(self):
        repo = self._init_git_repo(self.root / "broken_remote_repo")
        self._commit_file(repo, "README.md", "content", "msg")
        subprocess.run(["git", "remote", "add", "origin", "https://invalid.domain.example/nonexistent.git"], cwd=repo, check=True)
        res = sync.reconcile_repo(repo, action="status")
        self.assertEqual(res["status"], "FETCH_ERROR")

    def test_json_output_contract(self):
        repo = self._init_git_repo(self.root / "sample_repo")
        self._commit_file(repo, "README.md", "# Sample", "initial")
        
        out_file = self.root / "summary.json"
        cmd = [
            sys.executable,
            str(SCRIPTS_DIR / "sync.py"),
            "status",
            "--roots", str(self.root),
            "--output", str(out_file),
            "--non-interactive",
        ]
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(p.returncode, 0)
        self.assertTrue(out_file.exists())
        
        data = json.loads(out_file.read_text(encoding="utf-8"))
        self.assertIn("github_user", data)
        self.assertIn("discovered_repos", data)
        self.assertIn("total_repos", data)
        self.assertIn("results", data)
        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(data["results"][0]["repo"], "sample_repo")

    def test_run_git_gcm_env(self):
        """Verifies run_git includes GCM_INTERACTIVE=never to avoid GUI popups."""
        import unittest.mock
        with unittest.mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = unittest.mock.MagicMock(returncode=0, stdout="", stderr="")
            sync.run_git(self.root, ["status"])
            self.assertTrue(mock_run.called)
            _, kwargs = mock_run.call_args
            env = kwargs.get("env", {})
            self.assertEqual(env.get("GCM_INTERACTIVE"), "never")
            self.assertEqual(env.get("GIT_TERMINAL_PROMPT"), "0")

    def test_pr_checkout_tracking_branch_non_destructive(self):
        """Verifies that PR resolution checks out default_branch with non-destructive tracking branch."""
        remote = self.root / "remote_pr_non_destr.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, stdout=subprocess.DEVNULL)

        local = self.root / "local_pr_non_destr"
        subprocess.run(["git", "clone", str(remote), str(local)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=local, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=local, check=True)
        self._commit_file(local, "main.txt", "main v1", "main v1")
        subprocess.run(["git", "push", "origin", "main"], cwd=local, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Switch to feature branch and push
        subprocess.run(["git", "checkout", "-b", "feat/tracked-pr"], cwd=local, check=True, stdout=subprocess.DEVNULL)
        self._commit_file(local, "feat.txt", "feat content", "feat commit")
        subprocess.run(["git", "push", "-u", "origin", "feat/tracked-pr"], cwd=local, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Delete local main branch to force the fallback tracking branch checkout path
        subprocess.run(["git", "branch", "-D", "main"], cwd=local, check=True, stdout=subprocess.DEVNULL)

        # Merge and delete feature branch on remote
        other = self.root / "other_clone_pr"
        subprocess.run(["git", "clone", str(remote), str(other)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=other, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=other, check=True)
        subprocess.run(["git", "merge", "origin/feat/tracked-pr", "--ff-only"], cwd=other, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "push", "origin", "main"], cwd=other, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "push", "origin", "--delete", "feat/tracked-pr"], cwd=other, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        res = sync.reconcile_repo(local, action="reconcile")
        self.assertEqual(res["status"], "PR_RESOLVED")
        self.assertEqual(res["branch"], "main")
        self.assertTrue((local / "feat.txt").exists())

    def test_upstream_gone_with_unpushed_commits_preserved(self):
        """Verifies that if remote PR is deleted but local has unpushed commits ([ahead N, gone]), branch is preserved."""
        remote = self.root / "remote_ahead_gone.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, stdout=subprocess.DEVNULL)

        local = self.root / "local_ahead_gone"
        subprocess.run(["git", "clone", str(remote), str(local)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=local, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=local, check=True)
        self._commit_file(local, "main.txt", "main v1", "main v1")
        subprocess.run(["git", "push", "origin", "main"], cwd=local, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Create feat branch and push to origin
        subprocess.run(["git", "checkout", "-b", "feat/ahead-pr"], cwd=local, check=True, stdout=subprocess.DEVNULL)
        self._commit_file(local, "feat1.txt", "feat 1", "feat 1")
        subprocess.run(["git", "push", "-u", "origin", "feat/ahead-pr"], cwd=local, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Add local unpushed commit on feat branch
        self._commit_file(local, "feat2.txt", "feat 2 unpushed", "feat 2 unpushed")

        # Remote branch is deleted on origin
        subprocess.run(["git", "push", "origin", "--delete", "feat/ahead-pr"], cwd=local, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        real_run_git = sync.run_git
        def mock_run_git(cwd, args, timeout=20):
            if "for-each-ref" in args and "--format=%(upstream:track)" in args and "refs/heads/feat/ahead-pr" in args:
                return 0, "[ahead 1, gone]", ""
            return real_run_git(cwd, args, timeout)

        with unittest.mock.patch("sync.run_git", side_effect=mock_run_git):
            # Status mode check
            res_status = sync.reconcile_repo(local, action="status")
            self.assertEqual(res_status["status"], "AHEAD")
            self.assertEqual(res_status["branch"], "feat/ahead-pr")
            self.assertIn("unpushed commit", res_status["detail"])

            # Reconcile mode check: MUST PRESERVE the branch!
            res_reconcile = sync.reconcile_repo(local, action="reconcile")
            self.assertEqual(res_reconcile["status"], "AHEAD")
            self.assertEqual(res_reconcile["branch"], "feat/ahead-pr")
            self.assertIn("unpushed commit", res_reconcile["detail"])
            self.assertTrue((local / "feat2.txt").exists())

    def test_fast_forward_merge_ff_only(self):
        """Verifies that fast-forward pull uses local tracking ref merge --ff-only first without network call."""
        remote = self.root / "remote_ff.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, stdout=subprocess.DEVNULL)

        w1 = self.root / "w1_ff"
        subprocess.run(["git", "clone", str(remote), str(w1)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=w1, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=w1, check=True)
        self._commit_file(w1, "file.txt", "v1", "v1")
        subprocess.run(["git", "push", "origin", "main"], cwd=w1, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        w2 = self.root / "w2_ff"
        subprocess.run(["git", "clone", str(remote), str(w2)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=w2, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=w2, check=True)

        self._commit_file(w1, "file.txt", "v2-updated", "v2-updated")
        subprocess.run(["git", "push", "origin", "main"], cwd=w1, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        res = sync.reconcile_repo(w2, action="reconcile")
        self.assertEqual(res["status"], "UPDATED")
        self.assertIn("Fast-forwarded +1 commits", res["detail"])
        self.assertEqual((w2 / "file.txt").read_text(encoding="utf-8"), "v2-updated")

    def test_upstream_track_ahead_gone_explicit_format(self):
        """Verifies parsing of explicit [ahead N, gone] string from %(upstream:track)."""
        remote = self.root / "remote_mock_ahead_gone.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, stdout=subprocess.DEVNULL)

        local = self.root / "local_mock_ahead_gone"
        subprocess.run(["git", "clone", str(remote), str(local)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=local, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=local, check=True)
        self._commit_file(local, "main.txt", "main v1", "main v1")
        subprocess.run(["git", "push", "origin", "main"], cwd=local, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        subprocess.run(["git", "checkout", "-b", "feat/ahead-gone"], cwd=local, check=True, stdout=subprocess.DEVNULL)
        self._commit_file(local, "f.txt", "f", "f")

        real_run_git = sync.run_git

        def mock_run_git(cwd, args, timeout=20):
            if "for-each-ref" in args and "--format=%(upstream:track)" in args:
                return 0, "[ahead 4, gone]", ""
            return real_run_git(cwd, args, timeout)

        import unittest.mock
        with unittest.mock.patch("sync.run_git", side_effect=mock_run_git):
            res = sync.reconcile_repo(local, action="reconcile")
            self.assertEqual(res["status"], "AHEAD")
            self.assertEqual(res["branch"], "feat/ahead-gone")
            self.assertIn("4 unpushed commit(s)", res["detail"])

    def test_pr_checkout_worktree_locked(self):
        """Verifies WORKTREE_BRANCH_LOCKED is reported when default_branch is locked in a sibling worktree."""
        remote = self.root / "remote_wt_locked.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, stdout=subprocess.DEVNULL)

        local = self.root / "local_wt_main"
        subprocess.run(["git", "clone", str(remote), str(local)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=local, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=local, check=True)
        self._commit_file(local, "main.txt", "main content", "main commit")
        subprocess.run(["git", "push", "origin", "main"], cwd=local, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # local has main checked out. Create linked worktree for feat/pr
        wt_dir = self.root / "wt_locked_pr"
        subprocess.run(["git", "worktree", "add", "-b", "feat/pr", str(wt_dir), "main"], cwd=local, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "push", "-u", "origin", "feat/pr"], cwd=wt_dir, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Delete remote feat/pr so it is upstream gone
        subprocess.run(["git", "push", "origin", "--delete", "feat/pr"], cwd=wt_dir, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        res = sync.reconcile_repo(wt_dir, action="reconcile")
        self.assertEqual(res["status"], "WORKTREE_BRANCH_LOCKED")
        self.assertIn("checked out in another worktree", res["detail"])

    def test_matches_tokens_case_insensitivity(self):
        """Verifies _matches_tokens works reliably regardless of keyword casing."""
        self.assertTrue(sync._matches_tokens("my-app-v2", "My-App", "myapp"))
        self.assertTrue(sync._matches_tokens("my-app-v2", "MY-APP"))
        self.assertTrue(sync._matches_tokens("React-App", "react", "react"))
        self.assertTrue(sync._matches_tokens("cool_service_api", "Cool-Service"))
        self.assertFalse(sync._matches_tokens("reaction", "React"))

    def test_matches_repository_non_alphanumeric_keyword_safe(self):
        """Verifies matches_repository handles non-alphanumeric keywords without zip misalignment or crashes."""
        repo_path = self.root / "my-awesome-repo"
        repo_path.mkdir()
        # "---" normalizes to empty, which previously desynchronized zip(keywords, norm_keywords)
        keywords = ["---", "my-awesome-repo"]
        self.assertTrue(sync.matches_repository(repo_path, keywords, None))

    def test_extract_github_username_ssh_with_port(self):
        """Verifies extract_github_username properly skips port numbers in SSH URLs."""
        self.assertEqual(sync.extract_github_username("ssh://git@github.com:22/vantr/repo.git"), "vantr")
        self.assertEqual(sync.extract_github_username("ssh://git@ssh.github.com:443/vantr/repo.git"), "vantr")
        self.assertEqual(sync.extract_github_username("ssh://git@ssh.github.com:443/orgs/enterprise/repo.git"), "enterprise")

    def test_get_default_branch_with_slash(self):
        """Verifies get_default_branch does not truncate branch names containing slashes."""
        repo = self._init_git_repo(self.root / "slash_branch_repo")
        self._commit_file(repo, "README.md", "content", "initial")
        subprocess.run(["git", "remote", "add", "origin", "https://github.com/company/repo.git"], cwd=repo, check=True)

        real_run_git = sync.run_git

        def mock_run_git(cwd, args, timeout=20):
            if "symbolic-ref" in args:
                return 0, "origin/release/v1.0", ""
            return real_run_git(cwd, args, timeout)

        import unittest.mock
        with unittest.mock.patch("sync.run_git", side_effect=mock_run_git):
            default_b = sync.get_default_branch(repo)
            self.assertEqual(default_b, "release/v1.0")

    def test_upstream_gone_on_default_branch(self):
        """Verifies that if remote tracking branch for default branch is deleted, UPSTREAM_GONE is returned."""
        remote = self.root / "remote_default_gone.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, stdout=subprocess.DEVNULL)

        local = self.root / "local_default_gone"
        subprocess.run(["git", "clone", str(remote), str(local)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=local, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=local, check=True)
        self._commit_file(local, "init.txt", "init", "init")
        subprocess.run(["git", "push", "origin", "main"], cwd=local, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Delete main branch on remote bare repo
        subprocess.run(["git", "update-ref", "-d", "refs/heads/main"], cwd=remote, check=True)

        # Status mode: prune remote tracking branch and check status
        res_status = sync.reconcile_repo(local, action="status")
        self.assertEqual(res_status["status"], "UPSTREAM_GONE")
        self.assertIn("Remote tracking branch for default branch [main] is gone on origin", res_status["detail"])

        # Reconcile mode: should not alter branch and should return UPSTREAM_GONE
        res_reconcile = sync.reconcile_repo(local, action="reconcile")
        self.assertEqual(res_reconcile["status"], "UPSTREAM_GONE")
        self.assertEqual(res_reconcile["branch"], "main")
        self.assertIn("Remote tracking branch for default branch [main] is gone on origin", res_reconcile["detail"])

    def test_fetch_github_repos_rate_limit_tip(self):
        """Verifies that an informative rate limit tip is printed to stderr when GitHub API returns HTTP 403."""
        import io
        import unittest.mock
        import urllib.error

        mock_err = urllib.error.HTTPError(
            url="https://api.github.com/users/testuser/repos",
            code=403,
            msg="rate limit exceeded",
            hdrs={},
            fp=io.BytesIO(b'{"message": "API rate limit exceeded"}'),
        )

        with unittest.mock.patch("subprocess.run", side_effect=Exception("No gh CLI")), \
             unittest.mock.patch("urllib.request.urlopen", side_effect=mock_err), \
             unittest.mock.patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            repos = sync.fetch_github_repos("https://github.com/users/testuser")
            self.assertEqual(repos, [])
            err_output = mock_stderr.getvalue()
            self.assertIn("Notice: GitHub API rate limit reached. Falling back to local discovery.", err_output)
            self.assertIn("Tip: set GITHUB_TOKEN or login with 'gh auth login'", err_output)


    def test_upstream_gone_on_default_branch_custom_remote(self):
        """Verifies that UPSTREAM_GONE on default branch correctly reports custom remote name (e.g. upstream)."""
        remote = self.root / "remote_custom_default_gone.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, stdout=subprocess.DEVNULL)

        local = self.root / "local_custom_default_gone"
        subprocess.run(["git", "clone", str(remote), str(local), "-o", "upstream"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=local, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=local, check=True)
        self._commit_file(local, "init.txt", "init", "init")
        subprocess.run(["git", "push", "upstream", "main"], cwd=local, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Delete main branch on remote bare repo
        subprocess.run(["git", "update-ref", "-d", "refs/heads/main"], cwd=remote, check=True)

        res = sync.reconcile_repo(local, action="reconcile")
        self.assertEqual(res["status"], "UPSTREAM_GONE")
        self.assertEqual(res["branch"], "main")
        self.assertIn("Remote tracking branch for default branch [main] is gone on upstream", res["detail"])

    def test_pr_merged_remote_status_mode(self):
        """Verifies that in status mode, a merged and deleted remote PR branch returns PR_MERGED_REMOTE."""
        remote = self.root / "remote_pr_status.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, stdout=subprocess.DEVNULL)

        local = self.root / "local_pr_status"
        subprocess.run(["git", "clone", str(remote), str(local)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=local, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=local, check=True)
        self._commit_file(local, "main.txt", "init", "init")
        subprocess.run(["git", "push", "origin", "main"], cwd=local, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        subprocess.run(["git", "checkout", "-b", "feat/status-pr"], cwd=local, check=True, stdout=subprocess.DEVNULL)
        self._commit_file(local, "f.txt", "f", "f")
        subprocess.run(["git", "push", "-u", "origin", "feat/status-pr"], cwd=local, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Merge on remote and delete branch
        other = self.root / "other_clone_pr_status"
        subprocess.run(["git", "clone", str(remote), str(other)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=other, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=other, check=True)
        subprocess.run(["git", "merge", "origin/feat/status-pr", "--ff-only"], cwd=other, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "push", "origin", "main"], cwd=other, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "push", "origin", "--delete", "feat/status-pr"], cwd=other, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Status mode on local
        res = sync.reconcile_repo(local, action="status")
        self.assertEqual(res["status"], "PR_MERGED_REMOTE")
        self.assertEqual(res["branch"], "feat/status-pr")
        self.assertIn("Remote PR deleted for [feat/status-pr]. Ready to switch to [main].", res["detail"])

    def test_fetch_github_repos_rate_limit_429(self):
        """Verifies that an informative rate limit tip is printed to stderr when GitHub API returns HTTP 429."""
        import io
        import unittest.mock
        import urllib.error

        mock_err = urllib.error.HTTPError(
            url="https://api.github.com/users/testuser/repos",
            code=429,
            msg="too many requests",
            hdrs={},
            fp=io.BytesIO(b'{"message": "Too Many Requests"}'),
        )

        with unittest.mock.patch("subprocess.run", side_effect=Exception("No gh CLI")), \
             unittest.mock.patch("urllib.request.urlopen", side_effect=mock_err), \
             unittest.mock.patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            repos = sync.fetch_github_repos("https://github.com/users/testuser")
            self.assertEqual(repos, [])
            err_output = mock_stderr.getvalue()
            self.assertIn("Notice: GitHub API rate limit reached. Falling back to local discovery.", err_output)
            self.assertIn("Tip: set GITHUB_TOKEN or login with 'gh auth login'", err_output)

    def test_extract_github_username_domain_only_returns_empty(self):
        """Verifies domain-only URLs or missing username return empty string instead of 'github.com'."""
        self.assertEqual(sync.extract_github_username("https://github.com"), "")
        self.assertEqual(sync.extract_github_username("https://github.com/"), "")
        self.assertEqual(sync.extract_github_username("http://github.com/"), "")
        self.assertEqual(sync.extract_github_username("https://www.github.com"), "")
        self.assertEqual(sync.extract_github_username("www.github.com"), "")
        self.assertEqual(sync.extract_github_username("github.com"), "")
        self.assertEqual(sync.extract_github_username("github.com/"), "")
        self.assertEqual(sync.extract_github_username("git@github.com:"), "")
        self.assertEqual(sync.extract_github_username("https://github.com/users/"), "")
        self.assertEqual(sync.extract_github_username("https://github.com/orgs/"), "")
        self.assertEqual(sync.extract_github_username(""), "")
        self.assertEqual(sync.extract_github_username("   "), "")

    def test_squash_merged_pr_resolution(self):
        """Verifies that squash-merged PRs whose commits have new hashes on main are cleanly resolved via git cherry."""
        remote = self.root / "remote_squash.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, stdout=subprocess.DEVNULL)

        local = self.root / "local_squash"
        subprocess.run(["git", "clone", str(remote), str(local)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=local, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=local, check=True)
        self._commit_file(local, "main.txt", "main v1", "main v1")
        subprocess.run(["git", "push", "origin", "main"], cwd=local, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Feature branch created and pushed
        subprocess.run(["git", "checkout", "-b", "feat/squash-pr"], cwd=local, check=True, stdout=subprocess.DEVNULL)
        self._commit_file(local, "feat.txt", "squash content", "squash commit")
        subprocess.run(["git", "push", "-u", "origin", "feat/squash-pr"], cwd=local, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # On remote, squash and merge into main, then delete feature branch
        other = self.root / "other_squash"
        subprocess.run(["git", "clone", str(remote), str(other)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=other, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=other, check=True)
        subprocess.run(["git", "merge", "--squash", "origin/feat/squash-pr"], cwd=other, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "commit", "-m", "Squash PR #42 feat"], cwd=other, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "push", "origin", "main"], cwd=other, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "push", "origin", "--delete", "feat/squash-pr"], cwd=other, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Reconcile on local must detect patch equivalence and resolve PR
        res = sync.reconcile_repo(local, action="reconcile")
        self.assertEqual(res["status"], "PR_RESOLVED")
        self.assertEqual(res["branch"], "main")
        self.assertTrue((local / "feat.txt").exists())

    def test_squash_merged_pr_with_unpushed_commits_preserved(self):
        """Verifies that if a PR was squashed on GitHub but local made additional unpushed commits, branch is preserved."""
        remote = self.root / "remote_squash_unpushed.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, stdout=subprocess.DEVNULL)

        local = self.root / "local_squash_unpushed"
        subprocess.run(["git", "clone", str(remote), str(local)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=local, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=local, check=True)
        self._commit_file(local, "main.txt", "main v1", "main v1")
        subprocess.run(["git", "push", "origin", "main"], cwd=local, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Feature branch created and pushed
        subprocess.run(["git", "checkout", "-b", "feat/squash-extra"], cwd=local, check=True, stdout=subprocess.DEVNULL)
        self._commit_file(local, "feat.txt", "feat content", "feat commit")
        subprocess.run(["git", "push", "-u", "origin", "feat/squash-extra"], cwd=local, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # On remote, squash and merge into main, then delete feature branch
        other = self.root / "other_squash_extra"
        subprocess.run(["git", "clone", str(remote), str(other)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=other, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=other, check=True)
        subprocess.run(["git", "merge", "--squash", "origin/feat/squash-extra"], cwd=other, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "commit", "-m", "Squash PR #43 feat"], cwd=other, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "push", "origin", "main"], cwd=other, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "push", "origin", "--delete", "feat/squash-extra"], cwd=other, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Local adds an unpushed commit
        self._commit_file(local, "unpushed.txt", "unpushed content", "unpushed commit")

        real_run_git = sync.run_git
        def mock_run_git(cwd, args, timeout=20):
            if "for-each-ref" in args and "--format=%(upstream:track)" in args and "refs/heads/feat/squash-extra" in args:
                return 0, "[ahead 1, gone]", ""
            return real_run_git(cwd, args, timeout)

        with unittest.mock.patch("sync.run_git", side_effect=mock_run_git):
            # Reconcile on local must preserve branch
            res = sync.reconcile_repo(local, action="reconcile")
            self.assertEqual(res["status"], "AHEAD")
            self.assertEqual(res["branch"], "feat/squash-extra")
            self.assertIn("unpushed commit(s)", res["detail"])
            self.assertTrue((local / "unpushed.txt").exists())

    def test_squash_merged_pr_with_subsequent_main_commits(self):
        """Verifies multi-commit squash PR cleanly resolves even when subsequent commits land on main modifying the same file."""
        remote = self.root / "remote_squash_subsequent.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, stdout=subprocess.DEVNULL)

        local = self.root / "local_squash_subsequent"
        subprocess.run(["git", "clone", str(remote), str(local)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=local, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=local, check=True)
        self._commit_file(local, "main.txt", "line1\nline2\n", "init")
        subprocess.run(["git", "push", "origin", "main"], cwd=local, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Feature branch with multiple commits
        subprocess.run(["git", "checkout", "-b", "feat/subsequent"], cwd=local, check=True, stdout=subprocess.DEVNULL)
        self._commit_file(local, "f1.txt", "f1", "f1")
        self._commit_file(local, "main.txt", "line1\nline2-feature\n", "f2")
        subprocess.run(["git", "push", "-u", "origin", "feat/subsequent"], cwd=local, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # On remote, squash and merge into main
        other = self.root / "other_squash_subsequent"
        subprocess.run(["git", "clone", str(remote), str(other)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=other, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=other, check=True)
        subprocess.run(["git", "merge", "--squash", "origin/feat/subsequent"], cwd=other, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "commit", "-m", "Squash PR #55 feat"], cwd=other, check=True, stdout=subprocess.DEVNULL)

        # Another commit lands on main subsequently modifying the same file
        self._commit_file(other, "main.txt", "line1\nline2-feature\nline3-added-later\n", "line3")
        subprocess.run(["git", "push", "origin", "main"], cwd=other, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "push", "origin", "--delete", "feat/subsequent"], cwd=other, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Reconcile on local must cleanly resolve PR, switch to main, and fast-forward
        res = sync.reconcile_repo(local, action="reconcile")
        self.assertEqual(res["status"], "PR_RESOLVED")
        self.assertEqual(res["branch"], "main")
        self.assertTrue((local / "f1.txt").exists())
        self.assertEqual((local / "main.txt").read_text(encoding="utf-8"), "line1\nline2-feature\nline3-added-later\n")

    def test_rebase_merged_pr_resolution(self):
        """Verifies rebase-merged PR cleanly resolves to PR_RESOLVED when remote branch is deleted."""
        remote = self.root / "remote_rebase.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, stdout=subprocess.DEVNULL)

        local = self.root / "local_rebase"
        subprocess.run(["git", "clone", str(remote), str(local)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=local, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=local, check=True)
        self._commit_file(local, "main.txt", "main v1", "main v1")
        subprocess.run(["git", "push", "origin", "main"], cwd=local, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Feature branch
        subprocess.run(["git", "checkout", "-b", "feat/rebase-pr"], cwd=local, check=True, stdout=subprocess.DEVNULL)
        self._commit_file(local, "rebase_feat.txt", "content", "rebase commit")
        subprocess.run(["git", "push", "-u", "origin", "feat/rebase-pr"], cwd=local, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Rebase and merge on other clone
        other = self.root / "other_rebase"
        subprocess.run(["git", "clone", str(remote), str(other)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=other, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=other, check=True)
        subprocess.run(["git", "checkout", "-b", "feat/rebase-pr", "origin/feat/rebase-pr"], cwd=other, check=True, stdout=subprocess.DEVNULL)
        # Rebase onto origin/main and fast-forward main
        subprocess.run(["git", "rebase", "main"], cwd=other, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "checkout", "main"], cwd=other, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "merge", "--ff-only", "feat/rebase-pr"], cwd=other, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "push", "origin", "main"], cwd=other, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "push", "origin", "--delete", "feat/rebase-pr"], cwd=other, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Reconcile on local must cleanly resolve PR and switch to main
        res = sync.reconcile_repo(local, action="reconcile")
        self.assertEqual(res["status"], "PR_RESOLVED")
        self.assertEqual(res["branch"], "main")
        self.assertTrue((local / "rebase_feat.txt").exists())

    def test_matches_repository_by_username_owner(self):
        """Verifies matches_repository matches repos whose remote URL belongs to username even if repo name is not in keywords."""
        repo = self._init_git_repo(self.root / "unlisted_private_repo")
        self._commit_file(repo, "README.md", "content", "msg")
        subprocess.run(["git", "remote", "add", "origin", "https://github.com/myuser/unlisted_private_repo.git"], cwd=repo, check=True)

        # Keywords list does not have unlisted_private_repo
        keywords = ["some-other-repo", "public-tool"]
        norm_keywords = [sync.normalize_name(k) for k in keywords]

        # Without username, does not match
        self.assertFalse(sync.matches_repository(repo, keywords, norm_keywords))
        # With username matching owner in remote URL, matches!
        self.assertTrue(sync.matches_repository(repo, keywords, norm_keywords, username="myuser"))
        self.assertTrue(sync.matches_repository(repo, keywords, norm_keywords, username="@myuser"))
        self.assertTrue(sync.matches_repository(repo, keywords, norm_keywords, username="https://github.com/myuser"))



    def test_squash_merged_multi_commit_pr_resolution(self):
        """Verifies multi-commit PR squash-merged into main cleanly resolves to PR_RESOLVED (Bug 1)."""
        remote = self.root / "remote_squash_multi.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, stdout=subprocess.DEVNULL)

        local = self.root / "local_squash_multi"
        subprocess.run(["git", "clone", str(remote), str(local)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=local, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=local, check=True)
        self._commit_file(local, "main.txt", "main v1", "main v1")
        subprocess.run(["git", "push", "origin", "main"], cwd=local, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Feature branch with multiple commits
        subprocess.run(["git", "checkout", "-b", "feat/multi-pr"], cwd=local, check=True, stdout=subprocess.DEVNULL)
        self._commit_file(local, "feat1.txt", "feat 1 content", "commit 1")
        self._commit_file(local, "feat2.txt", "feat 2 content", "commit 2")
        subprocess.run(["git", "push", "-u", "origin", "feat/multi-pr"], cwd=local, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Squash merge on remote
        other = self.root / "other_squash_multi"
        subprocess.run(["git", "clone", str(remote), str(other)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=other, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=other, check=True)
        subprocess.run(["git", "merge", "--squash", "origin/feat/multi-pr"], cwd=other, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "commit", "-m", "Squash PR #99 multi commits"], cwd=other, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "push", "origin", "main"], cwd=other, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "push", "origin", "--delete", "feat/multi-pr"], cwd=other, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Reconcile on local must detect that HEAD changes are in origin/main and resolve PR cleanly
        res = sync.reconcile_repo(local, action="reconcile")
        self.assertEqual(res["status"], "PR_RESOLVED")
        self.assertEqual(res["branch"], "main")
        self.assertTrue((local / "feat1.txt").exists())
        self.assertTrue((local / "feat2.txt").exists())

    def test_roots_with_github_user_filtering_enabled(self):
        """Verifies passing --roots alongside --github-user does NOT disable keyword filtering (Bug 2)."""
        matched_repo = self._init_git_repo(self.root / "my-match-repo")
        self._commit_file(matched_repo, "README.md", "match", "init")
        subprocess.run(["git", "remote", "add", "origin", "https://github.com/myuser/my-match-repo.git"], cwd=matched_repo, check=True)

        unrelated_repo = self._init_git_repo(self.root / "unrelated-tool")
        self._commit_file(unrelated_repo, "README.md", "other", "init")
        subprocess.run(["git", "remote", "add", "origin", "https://github.com/someoneelse/unrelated-tool.git"], cwd=unrelated_repo, check=True)

        with unittest.mock.patch("sync.fetch_github_repos", return_value=["my-match-repo"]):
            with unittest.mock.patch("sys.argv", ["sync.py", "status", "--roots", str(self.root), "--github-user", "myuser"]):
                with unittest.mock.patch("sync.discover_repositories", wraps=sync.discover_repositories) as mock_discover:
                    with unittest.mock.patch("sys.stdout", new_callable=io.StringIO):
                        sync.main()
                    mock_discover.assert_called_once()
                    _, kwargs = mock_discover.call_args
                    self.assertTrue(kwargs.get("filter_by_keywords"), "filter_by_keywords should be True when --github-user is passed even with --roots")

    def test_extract_github_username_numeric(self):
        """Verifies numeric GitHub usernames in HTTPS and SSH URLs are properly preserved (Bug 3)."""
        self.assertEqual(sync.extract_github_username("https://github.com/123456"), "123456")
        self.assertEqual(sync.extract_github_username("https://github.com/123456/my-repo"), "123456")
        self.assertEqual(sync.extract_github_username("https://github.com/123456/my-repo.git"), "123456")
        self.assertEqual(sync.extract_github_username("ssh://git@github.com:22/123456/my-repo"), "123456")
        self.assertEqual(sync.extract_github_username("git@github.com:987654/repo.git"), "987654")

    def test_git_status_error_treated_as_dirty_skipped(self):
        """Verifies non-zero git status returncode fails safe to DIRTY_SKIPPED (Bug 5)."""
        repo = self._init_git_repo(self.root / "status_err_repo")
        self._commit_file(repo, "README.md", "content", "init")

        real_run_git = sync.run_git
        def mock_run_git(cwd, args, timeout=20):
            if "status" in args and "--porcelain" in args:
                return 124, "", "Git command timed out after 5s: git status --porcelain"
            return real_run_git(cwd, args, timeout)

        with unittest.mock.patch("sync.run_git", side_effect=mock_run_git):
            res = sync.reconcile_repo(repo, action="reconcile")
            self.assertEqual(res["status"], "DIRTY_SKIPPED")
            self.assertIn("Unable to verify working tree cleanliness", res["detail"])

    def test_extract_repo_name_windows_remote_path(self):
        """Verifies Windows backslash file paths in git remotes are properly parsed (Bug 6)."""
        self.assertEqual(sync.extract_repo_name_from_url(r"C:\local_remotes\my-repo.git"), "my-repo")
        self.assertEqual(sync.extract_repo_name_from_url(r"D:\projects\code\app"), "app")
        self.assertEqual(sync.extract_repo_name_from_url(r"\\server\share\repos\my-proj.git"), "my-proj")

    def test_matches_repository_no_folder_match_when_remotes_exist(self):
        """Verifies a repo with remotes does not match solely by local folder name if remotes don't match (Bug 7)."""
        repo = self._init_git_repo(self.root / "auth")
        self._commit_file(repo, "README.md", "content", "init")
        subprocess.run(["git", "remote", "add", "origin", "https://github.com/company/different-service.git"], cwd=repo, check=True)

        keywords = ["auth"]
        norm_keywords = [sync.normalize_name(k) for k in keywords]

        # Has remote pointing to 'different-service', local folder is 'auth', keywords has 'auth'.
        # Because remote exists and points to different service, must NOT match by folder name!
        self.assertFalse(sync.matches_repository(repo, keywords, norm_keywords))

        # But for a local-only repo with NO remotes configured, fallback to folder matching IS permitted:
        local_only = self._init_git_repo(self.root / "local_only_auth")
        auth_dir = self.root / "unrelated_dir" / "auth"
        auth_dir.parent.mkdir(parents=True, exist_ok=True)
        local_only.rename(auth_dir)
        self.assertTrue(sync.matches_repository(auth_dir, keywords, norm_keywords))

    def test_reconcile_repo_malformed_rev_list_output(self):
        """Verifies malformed rev-list counts string returns COMPARE_ERROR without crash (Bug 8)."""
        repo = self._init_git_repo(self.root / "malformed_revlist_repo")
        self._commit_file(repo, "README.md", "content", "init")
        subprocess.run(["git", "remote", "add", "origin", "https://github.com/user/repo.git"], cwd=repo, check=True)
        subprocess.run(["git", "update-ref", "refs/remotes/origin/main", "HEAD"], cwd=repo, check=True)
        subprocess.run(["git", "branch", "--set-upstream-to=origin/main", "main"], cwd=repo, check=True)

        real_run_git = sync.run_git
        def mock_run_git(cwd, args, timeout=20):
            if "rev-list" in args and "--left-right" in args:
                return 0, "malformed_output", ""
            if "fetch" in args:
                return 0, "", ""
            return real_run_git(cwd, args, timeout)

        with unittest.mock.patch("sync.run_git", side_effect=mock_run_git):
            res = sync.reconcile_repo(repo, action="reconcile")
            self.assertEqual(res["status"], "COMPARE_ERROR")
            self.assertIn("Unexpected rev-list output: malformed_output", res["detail"])

    def test_discover_repositories_permission_error_resilience(self):
        """Verifies discover_repositories handles PermissionError during os.walk gracefully (Bug 9)."""
        real_walk = os.walk
        def mock_walk(top, **kwargs):
            onerror = kwargs.get("onerror")
            if onerror:
                onerror(PermissionError("Permission denied on restricted directory"))
            return real_walk(top, **kwargs)

        with unittest.mock.patch("os.walk", side_effect=mock_walk):
            # Must not raise PermissionError
            repos = sync.discover_repositories([self.root])
            self.assertIsInstance(repos, list)

    def test_output_parent_directories_auto_created(self):
        """Verifies --output automatically creates missing parent directories when writing summary (Bug 10)."""
        out_file = self.root / "nested" / "output" / "dir" / "summary.json"
        self.assertFalse(out_file.parent.exists())

        with unittest.mock.patch("sys.argv", ["sync.py", "status", "--roots", str(self.root), "--output", str(out_file)]):
            with unittest.mock.patch("sys.stdout", new_callable=io.StringIO):
                sync.main()

        self.assertTrue(out_file.exists())
        data = json.loads(out_file.read_text(encoding="utf-8"))
        self.assertIn("results", data)


if __name__ == "__main__":
    unittest.main()
