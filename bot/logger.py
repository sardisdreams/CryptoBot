import logging
import os
import colorlog

def setup_logger(name: str = "cryptobot") -> logging.Logger:
    os.makedirs("logs", exist_ok=True)
    level = os.getenv("LOG_LEVEL", "INFO").upper()

    handler = colorlog.StreamHandler()
    handler.setFormatter(colorlog.ColoredFormatter(
        "%(log_color)s%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        log_colors={
            "DEBUG": "cyan",
            "INFO": "green",
            "WARNING": "yellow",
            "ERROR": "red",
            "CRITICAL": "bold_red",
        },
    ))

    file_handler = logging.FileHandler("logs/bot.log")
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level, logging.INFO))
    logger.addHandler(handler)
    logger.addHandler(file_handler)
    return logger
