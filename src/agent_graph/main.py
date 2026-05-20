"""AI Engineering Agent CLI entry point."""

import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv

from agent_graph.agents.bitbucket_fetcher import BitbucketFetcher
from agent_graph.agents.executor import ExecutorAgent
from agent_graph.agents.github_fetcher import GitHubFetcher
from agent_graph.agents.jira_fetcher import JiraFetcher
from agent_graph.agents.planner import PlannerAgent
from agent_graph.agents.pr_creator import PrCreatorAgent
from agent_graph.agents.verifier import VerifierAgent
from agent_graph.cli_output import print_final_result
from agent_graph.graph_builder import build_graph
from agent_graph.state import TaskState

load_dotenv()


def cli():
    parser = argparse.ArgumentParser(
        description="AI Engineering Agent - Automated code workflow"
    )
    parser.add_argument(
        "--issue",
        type=str,
        default="Add JWT authentication to FastAPI backend",
        help="The issue or task to work on",
    )
    parser.add_argument(
        "--repo",
        type=str,
        default=None,
        help="Target repository path or URL to modify",
    )
    parser.add_argument(
        "--github",
        action="store_true",
        help="Fetch issues from GitHub and pick one to work on",
    )
    parser.add_argument(
        "--github-repo",
        type=str,
        default=None,
        help="GitHub repository in owner/repo format (required with --github)",
    )
    parser.add_argument(
        "--jira",
        action="store_true",
        help="Fetch issues from Jira",
    )
    parser.add_argument(
        "--jira-project",
        type=str,
        default=None,
        help="Jira project key (e.g. PROJ) for --jira",
    )
    parser.add_argument(
        "--jql",
        type=str,
        default=None,
        help="Custom JQL query for --jira",
    )
    parser.add_argument(
        "--bitbucket",
        action="store_true",
        help="Fetch issues from Bitbucket",
    )
    parser.add_argument(
        "--bitbucket-repo",
        type=str,
        default=None,
        help="Bitbucket repository in owner/repo format (required with --bitbucket)",
    )
    args = parser.parse_args()

    issue_text = args.issue
    github_url = ""
    bitbucket_url = ""
    jira_url = ""
    target_repo = args.repo or os.getenv("TARGET_REPO_PATH", "")
    source_platform = "github"

    if args.jira:
        source_platform = "jira"
        jira_project = args.jira_project or os.getenv("JIRA_PROJECT", "")
        jql_query = args.jql

        if not jql_query and not jira_project:
            print("Error: --jira-project or --jql is required when using --jira")
            parser.print_help()
            return

        token = os.getenv("JIRA_API_TOKEN", "")
        jira_fetcher = JiraFetcher(token=token)

        if jira_project and not jql_query:
            jql_query = jira_fetcher.suggest_jql(jira_project)
            print(f"\nUsing JQL: {jql_query}")

        print(f"\nFetching Jira issues...")
        try:
            issues = jira_fetcher.fetch_issues(jql_query)
        except RuntimeError as e:
            print(f"Error fetching issues: {e}")
            return

        print(f"Found {len(issues)} issues.\n")

        if issue_number is not None:
            selected = None
            for issue in issues:
                if issue.key == issue_number:
                    selected = issue
                    break
            if not selected:
                print(f"Error: Issue {issue_number} not found or does not match.")
                return
            issue_text = f"{selected.key}: {selected.title}\n\n{selected.body}" if selected.body else f"{selected.key}: {selected.title}"
            jira_url = selected.url
        else:
            try:
                selected = jira_fetcher.pick_issue(issues)
            except KeyboardInterrupt:
                return
            issue_text = f"{selected.key}: {selected.title}\n\n{selected.body}" if selected.body else f"{selected.key}: {selected.title}"
            jira_url = selected.url

    elif args.bitbucket:
        source_platform = "bitbucket"
        bitbucket_repo = args.bitbucket_repo or os.getenv("BITBUCKET_REPO", "")
        if not bitbucket_repo:
            print("Error: --bitbucket-repo is required when using --bitbucket")
            parser.print_help()
            return

        fetcher = BitbucketFetcher()

        if bitbucket_repo.startswith("http"):
            try:
                owner, repo = BitbucketFetcher.parse_issue_url(bitbucket_repo)
            except ValueError as e:
                print(f"Error: {e}")
                return
        else:
            parts = bitbucket_repo.split("/")
            if len(parts) != 2:
                print("Error: --bitbucket-repo must be in owner/repo format")
                return
            owner, repo = parts

        if not target_repo:
            target_repo = f"https://bitbucket.org/{owner}/{repo}.git"

        print(f"\nFetching open issues from {owner}/{repo}...")
        try:
            issues = fetcher.fetch_issues(owner, repo)
        except RuntimeError as e:
            print(f"Error fetching issues: {e}")
            return

        print(f"Found {len(issues)} issues.\n")

        if issue_number is not None:
            selected = None
            for issue in issues:
                if issue.id == issue_number:
                    selected = issue
                    break
            if not selected:
                print(f"Error: Issue #{issue_number} not found or not open.")
                return
            issue_text = f"#{issue_number}: {selected.title}\n\n{selected.body}" if selected.body else f"#{issue_number}: {selected.title}"
            bitbucket_url = selected.url
        else:
            try:
                selected = fetcher.pick_issue(issues)
            except KeyboardInterrupt:
                return
            issue_text = f"#{selected.id}: {selected.title}\n\n{selected.body}" if selected.body else f"#{selected.id}: {selected.title}"
            bitbucket_url = selected.url

    elif args.github:
        github_repo = args.github_repo or os.getenv("GITHUB_REPO", "")
        if not github_repo:
            print("Error: --github-repo is required when using --github")
            parser.print_help()
            return

        fetcher = GitHubFetcher()
        owner: str
        repo: str
        issue_number: int | None

        if github_repo.startswith("http"):
            try:
                owner, repo, issue_number = fetcher.parse_github_url(github_repo)
            except ValueError as e:
                print(f"Error: {e}")
                return
        else:
            parts = github_repo.split("/")
            if len(parts) != 2:
                print("Error: --github-repo must be in owner/repo format")
                return
            owner, repo = parts
            issue_number = None

        if not target_repo:
            target_repo = f"https://github.com/{owner}/{repo}.git"

        print(f"\nFetching open issues from {owner}/{repo}...")
        try:
            issues = fetcher.fetch_issues(owner, repo)
        except RuntimeError as e:
            print(f"Error fetching issues: {e}")
            return

        print(f"Found {len(issues)} issues.\n")

        if issue_number is not None:
            selected = None
            for issue in issues:
                if issue.number == issue_number:
                    selected = issue
                    break
            if not selected:
                print(f"Error: Issue #{issue_number} not found or not open.")
                return
            issue_text = f"{selected.title}\n\n{selected.body}" if selected.body else selected.title
            github_url = selected.url
        else:
            try:
                selected = fetcher.pick_issue(issues)
            except KeyboardInterrupt:
                return
            issue_text = f"{selected.title}\n\n{selected.body}" if selected.body else selected.title
            github_url = selected.url

    planner = PlannerAgent()
    executor = ExecutorAgent()
    verifier = VerifierAgent()
    pr_creator = PrCreatorAgent()

    app = build_graph(
        planner_fn=planner.run,
        executor_fn=executor.run,
        verifier_fn=verifier.run,
        pr_creator_fn=pr_creator.run,
    )

    initial_state: TaskState = {
        "issue": issue_text,
        "plan": "",
        "implementation_result": "",
        "verification_result": "",
        "target_repo_path": target_repo,
        "work_repo_path": "",
        "repo_baseline_sha": "",
        "diff_patch": "",
        "change_stat": "",
        "github_issue_url": github_url,
        "jira_issue_url": jira_url,
        "bitbucket_issue_url": bitbucket_url,
        "source_platform": source_platform,
        "pr_url": "",
        "pr_error": "",
        "pr_skip_reason": "",
    }

    result = app.invoke(initial_state)
    print_final_result(result)


def telegram_cli():
    """CLI entry point for the Telegram bot mode."""
    from telegram_bot.bot import TelegramBot

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        print("Error: TELEGRAM_BOT_TOKEN environment variable is required.")
        print("Get a token from @BotFather on Telegram.")
        sys.exit(1)

    bot = TelegramBot(bot_token=bot_token)

    # Build graph builder
    def _graph_builder():
        planner = PlannerAgent()
        executor = ExecutorAgent()
        verifier = VerifierAgent()

        pr_creator = PrCreatorAgent()

        return build_graph(
            planner_fn=planner.run,
            executor_fn=executor.run,
            verifier_fn=verifier.run,
            pr_creator_fn=pr_creator.run,
        )

    bot.register_graph_builder(_graph_builder)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(bot.start_polling())
    except KeyboardInterrupt:
        print("Stopping bot...")
    finally:
        loop.run_until_complete(bot.cleanup())
        loop.close()

if __name__ == "__main__":
    cli()
