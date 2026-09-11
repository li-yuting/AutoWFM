# 设计：CRM 登录凭据迁移到 `.env`

## 背景与目标

当前 CRM 账号密码存放在根目录 `login.json`，token 刷新脚本在该文件缺失或密码过期时无法自动续期。目标是将 CRM 登录凭据统一放入项目已有的 `.env` 本地配置，删除 `login.json`，并保留现有采集失败后的 token 自动刷新流程。

## 配置与接口

- 新增环境变量：
  - `AUTOWFM_CRM_USERNAME`
  - `AUTOWFM_CRM_PASSWORD`
- `.env.example` 只保留空占位与使用说明，不提交真实凭据。
- `抓取Token.py` 运行时通过 `python-dotenv` 加载 `.env`，从环境变量读取账号密码。
- 缺少任一变量时立即退出，并提示用户参照 `.env.example` 配置。
- 不再读取 `login.json`，迁移完成后删除当前项目根目录的 `login.json`。

## 数据流

1. 采集器启动时沿用 `collector/_utils.load_cfg()` 加载 `.env`。
2. 明细接口返回登录态异常时，`collector/detail.py` 调用 `token_store.refresh_token()`。
3. `refresh_token()` 启动 `抓取Token.py --headless` 子进程，并继承当前环境变量。
4. `抓取Token.py` 使用环境变量中的账号密码完成 SSO 登录，从网络请求中提取 token。
5. 新 token 写入 `token.json` 并回填 `.env` 的 `AUTOWFM_TOKEN`，随后明细请求重试一次。
6. 手动执行 `抓取Token.py` 时也使用相同的 `.env` 凭据来源。

## 错误处理与安全

- 账号或密码缺失时不启动浏览器，直接给出明确的配置错误。
- 登录失败或未捕获 token 时脚本以非零状态退出；现有刷新逻辑返回失败，不覆盖有效 token。
- 全程只记录脱敏 token，不打印账号、密码或完整 token。
- `.env` 继续由 `.gitignore` 排除；`login.json` 的忽略规则保留，防止历史文件被误提交。

## 测试与验收

- 新增纯环境变量测试：凭据完整时返回账号密码，缺失时明确失败。
- 保留并运行现有 `tests/test_token_store.py`。
- 将参考目录中的最新凭据迁移到本地 `.env` 后，运行 `抓取Token.py --headless`，确认获得新 token。
- 运行一次“会话记录”和“工单明细”下载，确认 token 失效时能够刷新并重试成功。

## 范围外

- 不增加 `login.json` 兼容回退。
- 不引入系统密钥环、云密钥服务或额外依赖。
- 不改变 token 保存格式和现有采集调度频率。
