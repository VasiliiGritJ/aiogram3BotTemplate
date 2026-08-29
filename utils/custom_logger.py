import logging

logging.basicConfig(level=logging.INFO, format="SCRIPT | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def configure_logging(*, debug: bool) -> None:
    logging.getLogger().setLevel(logging.DEBUG if debug else logging.INFO)

def log(department, level, msg, user_id="SYSTEM"):
    levels = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
        "critical": logging.CRITICAL
    }
    logger.log(levels[level], f"{department} | {user_id} | {msg}")
