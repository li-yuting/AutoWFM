# -*- coding: utf-8 -*-
"""AutoWFM FastAPI 服务:只读 data/*.db,向看板和第三方暴露 REST API。

独立进程运行(python -m api.app),绑定 0.0.0.0:8081。
认证:复用看板的 AUTOWFM_DASH_TOKEN(Bearer Token);/health 免认证。
数据:复用 dashboard.queries 的聚合逻辑(方案D 增量等),通过 Repository 只读 SQLite。
"""
import os

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from dashboard import queries

# 加载 .env(若存在),使直接 python -m api.app 也能读到 AUTOWFM_DASH_TOKEN
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = FastAPI(title="AutoWFM API", version="1.0")
DATA_DIR = os.environ.get("AUTOWFM_DATA_DIR", "data")

# Bearer Token 认证:与看板共享 AUTOWFM_DASH_TOKEN;留空则不启用(本地开发)
_DASH_TOKEN = os.environ.get("AUTOWFM_DASH_TOKEN", "").strip()
_security = HTTPBearer(auto_error=False)


async def _verify_token(cred: HTTPAuthorizationCredentials | None = Depends(_security)):
    """Bearer Token 校验;未配置 token 时放行(向后兼容本地开发)。"""
    if not _DASH_TOKEN:
        return None
    if cred is None or cred.credentials != _DASH_TOKEN:
        raise HTTPException(status_code=401, detail="未授权: 缺少或无效的 Bearer Token")
    return cred


@app.get("/health")
async def health():
    """健康检查端点:免认证,返回 {"status":"ok"}。"""
    return {"status": "ok"}


@app.get("/api/latest-date", dependencies=[Depends(_verify_token)])
async def latest_date():
    """看板默认日期:热线/在线 db 中最新日期(YYYY-MM-DD)。"""
    return {"date": queries.latest_data_date(DATA_DIR)}


@app.get("/api/day", dependencies=[Depends(_verify_token)])
async def day(date: str):
    """日视图数据:build_day 完整 JSON(card/inbound/outbound/tables/headers)。"""
    return queries.build_day(date, DATA_DIR)


@app.get("/api/month", dependencies=[Depends(_verify_token)])
async def month(date: str):
    """月视图数据:build_month 完整 JSON。"""
    return queries.build_month(date, DATA_DIR)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app, host="0.0.0.0", port=8081,
        reload=os.environ.get("AUTOWFM_DEBUG", "0") == "1",
    )
