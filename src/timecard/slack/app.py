from slack_bolt import App

from timecard.slack.handlers import register


def create_app(config, database, attendance, sync):
    app = App(token=config.bot_token)
    register(app, config, database, attendance, sync)
    return app
