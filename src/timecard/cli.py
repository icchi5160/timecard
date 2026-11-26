import argparse
import os
from datetime import timedelta

from timecard.config import Config
from timecard.database.connection import Database
from timecard.domain.errors import DomainError
from timecard.domain.models import parse, utcnow
from timecard.logging_config import configure
from timecard.services.users import UserService


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(prog="timecard")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db")
    add = commands.add_parser("add-user")
    add.add_argument("user")
    add.add_argument("--admin", action="store_true")
    add.add_argument("--name")
    commands.add_parser("list-users")
    disable = commands.add_parser("disable-user")
    disable.add_argument("user")
    purge = commands.add_parser("purge-history")
    purge.add_argument("--actor", required=True, help="Registered ADMIN Slack user ID")
    purge.add_argument("--before", help="Exclusive UTC-aware ISO cutoff; default retention policy")
    commands.add_parser("run")
    args = parser.parse_args()
    try:
        config = Config.load()
        configure(config.log_level)
        if args.command == "run":
            config.validate_slack()
        database = Database(config.database_path)
        # Bot migrates only after obtaining its lifetime lock.
        if args.command == "run":
            from timecard.main import run

            run(config, database)
            return
        database.migrate()
        users = UserService(database)
        if args.command == "init-db":
            print("DB initialized.")
        elif args.command == "add-user":
            users.add(args.user, args.admin, args.name)
            print("User registered.")
        elif args.command == "list-users":
            for row in users.list():
                print(
                    row["slack_user_id"],
                    row["display_name"],
                    "active" if row["is_active"] else "disabled",
                    "ADMIN" if row["is_admin"] else "MEMBER",
                )
        elif args.command == "disable-user":
            users.disable(args.user)
            print("User disabled.")
        elif args.command == "purge-history":
            cutoff = (
                parse(args.before)
                if args.before
                else (utcnow() - timedelta(days=config.retention_days))
            )
            if cutoff > utcnow():
                raise ValueError("Cutoff must not be in the future")
            print(f"Deleted {users.purge(args.actor, cutoff)} sessions.")
    except (DomainError, ValueError) as exc:
        parser.exit(2, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
