"""入口:加载配置、日志、启动调度器。运行:python -m collector.main(必须 -m,因本文件做 from collector import ...)。"""
import json
import logging, sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from collector import scheduler
from collector._utils import load_cfg


class JsonFormatter(logging.Formatter):
    """每行输出一个 JSON 对象: {ts, level, logger, message, (可选)exc_info}。"""

    def format(self, record):
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(cfg):
    Path("logs").mkdir(exist_ok=True)
    fmt = JsonFormatter()
    handlers = [logging.StreamHandler(sys.stdout)]
    for h in handlers:
        h.setFormatter(fmt)
    p = cfg.get("logging", {}).get("path")
    if p:
        Path(p).parent.mkdir(exist_ok=True)
        fh = TimedRotatingFileHandler(p, when="midnight", backupCount=30, encoding="utf-8")
        fh.setFormatter(fmt)
        handlers.append(fh)
    logging.basicConfig(level=logging.INFO, handlers=handlers)
    # 压掉 APScheduler 的 INFO 噪声(Running job / executed successfully),
    # 保留 WARNING/ERROR(misfire、executor 异常),避免刷屏淹没业务日志。
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

def main():
    cfg = load_cfg()
    setup_logging(cfg)
    # 启动时建索引(幂等),加速看板按日/月前缀查询
    from collector import storage
    for src in storage.SCHEMAS:
        try:
            storage.ensure_index(src, cfg["storage"]["dir"])
        except Exception:
            logging.getLogger("autowfm").exception(f"[storage] {src} 建索引失败")
    scheduler.start(cfg)

if __name__ == "__main__":
    main()
