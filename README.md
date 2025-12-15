# Reddit Custom Feed Fetcher

使用 Reddit 官方 OAuth API 抓取指定 custom feed（即 multireddit / multi）内容的工具。

## 功能特性

- 使用 OAuth refresh_token 认证
- 增量抓取新帖子（基于 seen_fullnames 策略）
- 批量获取帖子详情
- 可选抓取评论树
- 自动限速控制（QPM 限制 + X-Ratelimit 响应头）
- 自动重试（超时、5xx、429 错误）
- JSONL 格式存储
- 合规清理（purge 已删除内容）

## 安装

```bash
# 克隆项目
cd reddit_custom_feed_fetcher

# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 或安装为可执行命令
pip install -e .
```

## 配置

1. 复制配置模板：
```bash
cp .env.example .env
```

2. 在 `.env` 中填入 Reddit API 凭证：
```
REDDIT_CLIENT_ID=your_client_id
REDDIT_CLIENT_SECRET=your_client_secret
REDDIT_REFRESH_TOKEN=your_refresh_token
```

3. 编辑 `config.yml`，修改以下内容：
   - `reddit.user_agent`: 设置唯一的 User-Agent
   - `custom_feed.url`: 你的 custom feed URL
   - `custom_feed.multipath`: 对应的 multipath
   - `custom_feed.owner`: custom feed 所有者
   - `custom_feed.name`: custom feed 名称

## 获取 Reddit API 凭证

1. 访问 https://www.reddit.com/prefs/apps
2. 点击 "create another app..."
3. 选择 "script" 类型
4. 填写名称和 redirect URI（如 `http://localhost:8080/reddit_callback`）
5. 获取 `client_id`（app 下面的字符串）和 `client_secret`

### 获取 refresh_token

需要完成 OAuth 授权流程获取 refresh_token：

```python
import requests
import base64

client_id = "YOUR_CLIENT_ID"
client_secret = "YOUR_CLIENT_SECRET"
redirect_uri = "http://localhost:8080/reddit_callback"

# 1. 生成授权 URL
auth_url = (
    f"https://www.reddit.com/api/v1/authorize"
    f"?client_id={client_id}"
    f"&response_type=code"
    f"&state=random_string"
    f"&redirect_uri={redirect_uri}"
    f"&duration=permanent"
    f"&scope=read identity"
)
print(f"请访问: {auth_url}")

# 2. 用户授权后，从回调 URL 获取 code
code = input("输入回调 URL 中的 code: ")

# 3. 用 code 换取 tokens
response = requests.post(
    "https://www.reddit.com/api/v1/access_token",
    auth=(client_id, client_secret),
    data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    },
    headers={"User-Agent": "YourApp/1.0"},
)

tokens = response.json()
print(f"refresh_token: {tokens['refresh_token']}")
```

## 使用方法

### 验证配置

```bash
# 使用 Python 模块方式
python -m src.cli validate --config config.yml

# 或安装后使用命令
redditfeed validate --config config.yml
```

### 单次抓取

```bash
python -m src.cli once --config config.yml
```

### 持续轮询

```bash
python -m src.cli run --config config.yml
# 按 Ctrl+C 优雅退出
```

## 输出格式

抓取的帖子保存在 `data/posts/` 目录下，每天一个 JSONL 文件：

```
data/posts/posts_2024-01-15.jsonl
```

每行一个 JSON 对象，包含以下字段：

```json
{
  "id": "abc123",
  "fullname": "t3_abc123",
  "created_utc": 1705312000,
  "subreddit": "programming",
  "author": "username",
  "title": "Post title",
  "selftext": "Post content",
  "url": "https://...",
  "permalink": "/r/programming/comments/abc123/...",
  "is_self": true,
  "over_18": false,
  "score": 100,
  "num_comments": 25,
  "raw_listing_item": {...},
  "detail": {...},
  "comments": [...],
  "fetched_at_utc": 1705312100,
  "is_deleted_or_removed": false,
  "removed_hint": null
}
```

## 状态文件

程序维护 `data/state.json` 记录已处理的帖子：

```json
{
  "seen_fullnames": ["t3_abc123", "t3_def456"],
  "last_run_utc": 1705312100
}
```

## 项目结构

```
.
├── config.yml          # 配置文件
├── requirements.txt    # Python 依赖
├── setup.py           # 安装脚本
├── pyproject.toml     # 项目配置
├── src/
│   ├── __init__.py
│   ├── cli.py          # 命令行接口
│   ├── config.py       # 配置加载与校验
│   ├── reddit_auth.py  # OAuth 认证
│   ├── reddit_client.py # HTTP 请求封装
│   ├── multi_validator.py # Multi 校验
│   ├── fetcher.py      # 抓取逻辑
│   └── storage.py      # 存储逻辑
├── data/               # 数据目录
│   ├── posts/          # 帖子 JSONL 文件
│   └── state.json      # 状态文件
└── logs/               # 日志目录
    └── app.log
```

## License

MIT
