"""Tests for deploy env git tagging in a local repo."""

import subprocess

from agent_graph.deploy_env import tag_deploy_env


def _init_bare_remote(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "--bare"],
        cwd=path,
        check=True,
        capture_output=True,
    )


def _init_repo(path, remote_path=None):
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    (path / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "branch", "-M", "main"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "-b", "feature/test-branch"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    if remote_path is not None:
        subprocess.run(
            ["git", "remote", "add", "origin", str(remote_path)],
            cwd=path,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "push", "-u", "origin", "main"],
            cwd=path,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "push", "-u", "origin", "feature/test-branch"],
            cwd=path,
            check=True,
            capture_output=True,
        )


def test_tag_deploy_env_creates_and_pushes_tag(tmp_path, monkeypatch):
    bare = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_bare_remote(bare)
    _init_repo(repo, bare)

    monkeypatch.setattr("agent_graph.deploy_env.time.sleep", lambda _: None)

    tag_name, err = tag_deploy_env(str(repo), "feature/test-branch", "dev")
    assert err == ""
    assert tag_name == "env.dev.branch.feature/test-branch"

    list_tags = subprocess.run(
        ["git", "-C", str(repo), "tag", "-l", "env.dev.branch.*"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert tag_name in list_tags.stdout.splitlines()


def test_tag_deploy_env_replaces_old_tag(tmp_path, monkeypatch):
    bare = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_bare_remote(bare)
    _init_repo(repo, bare)

    subprocess.run(
        ["git", "tag", "-a", "env.dev.branch.old-branch", "-m", "old"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    monkeypatch.setattr("agent_graph.deploy_env.time.sleep", lambda _: None)

    tag_name, err = tag_deploy_env(str(repo), "feature/test-branch", "dev")
    assert err == ""
    assert tag_name == "env.dev.branch.feature/test-branch"

    list_tags = subprocess.run(
        ["git", "-C", str(repo), "tag", "-l"],
        capture_output=True,
        text=True,
        check=True,
    )
    tags = [t for t in list_tags.stdout.splitlines() if t]
    assert "env.dev.branch.old-branch" not in tags
    assert tag_name in tags

    remote_tags = subprocess.run(
        ["git", "-C", str(bare), "tag", "-l"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert tag_name in remote_tags.stdout.splitlines()


def test_tag_deploy_env_push_fails_without_remote(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    monkeypatch.setattr("agent_graph.deploy_env.time.sleep", lambda _: None)

    tag_name, err = tag_deploy_env(str(repo), "feature/test-branch", "dev")
    assert tag_name == ""
    assert "push" in err.lower() or "tag" in err.lower()
