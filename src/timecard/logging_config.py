import logging


def configure(level):
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # SDK debug output may contain tokens or personal message payloads.
    for name in ("slack_bolt", "slack_sdk", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)
