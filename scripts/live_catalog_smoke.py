"""Publish the dedicated test database's catalog without changing its stock."""
import argparse
import getpass
from kosuzu.catalog import publish
from kosuzu.github import GitHub


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--branch", default="main")
    args = parser.parse_args()
    token = getpass.getpass("GitHub test token (not saved): ")
    gh = GitHub(token, args.repo, args.branch)
    before = gh.head()
    try:
        print(publish(gh))
    finally:
        assert gh.head() == before, "Publishing modified the inventory branch"
        print("Database branch unchanged")


if __name__ == "__main__":
    main()
