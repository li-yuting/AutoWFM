"""入口:加载配置、日志、启动调度器。运行:python -m collector.main(必须 -m,因本文件做 from collector import ...)。"""
import logging, sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from collector import scheduler
from collector._utils import load_cfg

def setup_logging(cfg):
    Path("logs").mkdir(exist_ok=True)
    handlers = [logging.StreamHandler(sys.stdout)]
    p = cfg.get("logging", {}).get("path")
    if p:
        Path(p).parent.mkdir(exist_ok=True)
        handlers.append(TimedRotatingFileHandler(p, when="midnight", backupCount=30, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S", handlers=handlers)
    # 压掉 APScheduler 的 INFO 噪声(Running job / executed successfully),
    # 保留 WARNING/ERROR(misfire、executor 异常),避免刷屏淹没业务日志。
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

def main():
    cfg = load_cfg()
    setup_logging(cfg)
    scheduler.start(cfg)

if __name__ == "__main__":
    main()
