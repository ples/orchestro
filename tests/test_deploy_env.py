"""Tests for deploy environment resolution."""

from agent_graph.deploy_env import (
    build_env_tag_name,
    normalize_deploy_env,
    parse_deploy_env_from_text,
    resolve_deploy_env,
    resolve_deploy_env_with_source,
)


def test_normalize_deploy_env_valid():
    assert normalize_deploy_env("DEV") == "dev"
    assert normalize_deploy_env("stage") == "stage"
    assert normalize_deploy_env("prod") == "prod"


def test_normalize_deploy_env_invalid():
    assert normalize_deploy_env("") is None
    assert normalize_deploy_env("production") is None
    assert normalize_deploy_env(None) is None


def test_build_env_tag_name():
    assert build_env_tag_name("dev", "hotfix/foo-bar") == "env.dev.branch.hotfix/foo-bar"


def test_parse_deploy_env_from_prompt_patterns():
    assert parse_deploy_env_from_text("deploy-env: dev") == "dev"
    assert parse_deploy_env_from_text("deploy_env=stage") == "stage"
    assert parse_deploy_env_from_text("Please deploy to prod after review") == "prod"
    assert parse_deploy_env_from_text("release for rc2") == "rc2"
    assert parse_deploy_env_from_text("make tag-env ENV=dev1") == "dev1"
    assert parse_deploy_env_from_text("ENV=dev2\n") == "dev2"
    assert parse_deploy_env_from_text("lets deploy dev2") == "dev2"
    assert parse_deploy_env_from_text("deploy to dev2.") == "dev2"


def test_parse_deploy_env_ignores_invalid():
    assert parse_deploy_env_from_text("deploy to production") is None


def test_resolve_deploy_env_priority():
    assert (
        resolve_deploy_env(
            cli="prod",
            issue="deploy to dev",
            input_prompt="deploy to stage",
            env_var="rc2",
        )
        == "prod"
    )
    assert (
        resolve_deploy_env(
            cli=None,
            issue="",
            input_prompt="",
            env_var="rc2",
        )
        == "rc2"
    )
    assert (
        resolve_deploy_env(
            cli=None,
            issue="MINSKY-1: fix",
            input_prompt="deploy to dev1 when ready",
            env_var=None,
        )
        == "dev1"
    )


def test_resolve_deploy_env_with_source():
    env, source = resolve_deploy_env_with_source(
        cli="prod", issue="deploy to dev1", input_prompt="", env_var="dev2"
    )
    assert env == "prod"
    assert source == "cli"

    env, source = resolve_deploy_env_with_source(
        cli=None, issue="nothing", input_prompt="nothing", env_var="stage"
    )
    assert env == "stage"
    assert source == "env"

    env, source = resolve_deploy_env_with_source(
        cli=None,
        issue="MINSKY-14576 body",
        input_prompt="[User Context]: https://x.atlassian.net/browse/MINSKY-14576 ... lets deploy to dev2",
        env_var=None,
    )
    assert env == "dev2"
    assert source == "prompt/issue"
