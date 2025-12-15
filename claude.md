# Reddit Custom Feed（Multi/Multireddit）抓取器 - claude.md（需求 + 配置）

> 目标：使用 Reddit 官方 OAuth API 抓取指定 custom feed（即 multireddit / multi）内容，按“新帖子”增量处理，并对每个新帖子逐个拉取详情（可选评论树），落盘保存。  
> 说明：Reddit 官方 multi API 中 `multipath` 的定义是 **multireddit url path**（形如 `/user/{username}/m/{multi}`），需要做严格校验。 

---

## 1. 配置（config.yml 内容直接内嵌在此文档中）

实现必须支持读取同目录下的 `config.yml`。下面为配置模板（字段和默认值建议）：

```yaml
app:
  name: reddit-custom-feed-fetcher
  version: "0.1.0"

reddit:
  # Reddit 官方建议使用唯一且描述性的 User-Agent；默认 UA 可能会被严格限制。 
  user_agent: "linux:com.ones.redditfeedfetcher:v0.1.0 (by /u/YOUR_REDDIT_USERNAME)"

  endpoints:
    www_base: "https://www.reddit.com"
    oauth_base: "https://oauth.reddit.com"

  auth:
    # 推荐使用 refresh_token 流程：运行时用 refresh_token 换 access_token（避免每次人工授权）
    grant_type: "refresh_token"

    client_id: "REDDIT_CLIENT_ID"
    client_secret: "REDDIT_CLIENT_SECRET"
    refresh_token: "REDDIT_REFRESH_TOKEN"

    # 仅首次授权拿 refresh_token 时需要（运行抓取时不一定要用）
    redirect_uri: "http://localhost:8080/reddit_callback"

    # 最小权限：read（读取内容）；若需要读取用户 multi 列表等，可加 identity
    scopes:
      - "read"
      - "identity"

custom_feed:
  # ✅ 本项目把 “custom feed” 按 multi（multireddit）处理
  type: "multi"

  # 你提供的 custom feed URL（用于解析&校验）
  url: "https://www.reddit.com/user/bushacker/m/myreddit/"

  # ✅ multipath：官方定义为 multireddit url path（末尾不带 /） 
  multipath: "/user/bushacker/m/myreddit"

  owner: "bushacker"
  name: "myreddit"

fetch:
  listing:
    sort: "new"          # new/hot/top/rising
    limit: 50            # <=100（listing limit 最大 100） 
    poll_interval_sec: 60

    incremental:
      strategy: "seen_fullnames"
      max_seen_keep: 2000

  per_post:
    # 对每个新帖子逐个获取“内容”
    # - detail: 帖子详情（建议补拉一次以稳妥）
    # - comments: 评论树（可选）
    fetch_post_detail: true
    fetch_comments: true

    comments:
      # /comments/{article} 支持 depth/limit/sort/truncate 等参数 
      limit: 50
      depth: 5
      sort: "top"
      truncate: 50

rate_limit:
  # Reddit Data API 免费访问建议每分钟 100 QPM，并监控 X-Ratelimit-* 响应头。 
  max_qpm: 100
  respect_response_headers: true
  safety_min_interval_ms: 700

network:
  timeout_sec: 30
  retries: 3
  backoff_sec: 1.0
  proxy: ""   # e.g. "http://127.0.0.1:7890"

storage:
  data_dir: "./data"
  state_file: "./data/state.json"

  output:
    format: "jsonl"          # jsonl/markdown
    posts_dir: "./data/posts"

  # 合规：建议支持删除已在 Reddit 删除的用户内容的清理任务（按开关执行）。 
  compliance:
    purge_deleted_content: true
    purge_interval_hours: 24

logging:
  level: "INFO"
  file: "./logs/app.log"


⸻

2. 名词与定义（必须实现校验）

2.1 custom feed / multi
	•	将 custom feed 视为 Reddit multireddit（multi）。
	•	multi 的关键标识是 multipath，其定义为 multireddit url path，例如：/user/bushacker/m/myreddit。

2.2 校验规则（validate 命令必须实现）

对 config.yml 做以下校验：
	1.	custom_feed.type 必须为 multi
	2.	custom_feed.multipath 必须匹配正则：^/user/[^/]+/m/[^/]+$
	3.	custom_feed.url（若提供）必须可解析出 owner 和 name，并与 custom_feed.owner/name 一致
	4.	custom_feed.multipath 必须与 owner/name 组装一致：/user/{owner}/m/{name}
	5.	reddit.user_agent 必填且不可为默认库 UA（如 python-requests/x.y、Java/1.x 等）
	6.	client_id/client_secret/refresh_token 必填；日志不得打印 secret/token 原文

⸻

3. 目标行为与流程

3.1 总流程
	1.	读取并校验 config.yml
	2.	OAuth：使用 refresh_token 换取 access_token（缓存直到过期）
	3.	校验 multi 定义（需要实际请求 API）：
	•	GET https://oauth.reddit.com/api/multi{multipath}
	•	目的：确认 multi 存在且可访问，并记录其包含的 subreddits（用于日志）
	4.	拉取 custom feed 的最新帖子 listing：
	•	GET https://oauth.reddit.com{multipath}/{sort}?limit={limit}
	•	sort 默认 new
	5.	增量识别“新帖子”：
	•	使用 data.children[].data.name（fullname，如 t3_xxx）作为去重键
	6.	对每条新帖子逐个抓取内容：
	•	帖子详情：优先批量补拉（减少请求数）：
	•	GET /api/info?id=t3_xxx,t3_yyy,...（或 /by_id/{names}）
	•	评论树（可选）：GET /comments/{article}，其中 article=帖子 id36（不含 t3_）
	7.	落盘：JSONL 逐行追加；同时维护 state.json
	8.	轮询模式：按 poll_interval_sec 重复执行 4-7

⸻

4. API 调用要求（必须实现）

4.1 Multi 定义校验接口（必须调用）
	•	GET https://oauth.reddit.com/api/multi{multipath}
	•	multipath 为 multireddit url path（如 /user/bushacker/m/myreddit）。
	•	若返回 404/403 等，validate 必须失败并给出明确报错

4.2 Listing（custom feed 内容流）
	•	GET https://oauth.reddit.com{multipath}/{sort}
	•	参数：
	•	limit（<=100）
	•	可选 after/before/count（如后续需要翻页扩展）

4.3 单帖详情（必须实现）
	•	推荐批量：
	•	GET https://oauth.reddit.com/api/info?id=t3_xxx,t3_yyy,...
	•	目的：对 listing 中的新帖补齐字段（例如媒体字段、crosspost 信息等）

4.4 评论树（可选，但本需求默认为开启）
	•	GET https://oauth.reddit.com/comments/{article}
	•	参数支持 depth/limit/sort/truncate
	•	返回结构：数组 [link listing, comment listing]

⸻

5. 增量与状态（必须实现）

5.1 seen_fullnames 策略（必须实现）
	•	每轮只拉第一页 listing（limit=N）
	•	遍历 children：
	•	fullname = data.name（例如 t3_abc123）
	•	若不在 state.seen_fullnames，则为“新帖子”
	•	新帖子处理完成后将 fullname 写入 state.seen_fullnames
	•	state 需要裁剪：只保留最近 max_seen_keep 条（FIFO 或 LRU 均可）

5.2 state.json 建议结构

{
  "seen_fullnames": ["t3_abc123", "t3_def456"],
  "last_run_utc": 0
}


⸻

6. 速率限制与可靠性（必须实现）

6.1 速率限制
	•	控制在 rate_limit.max_qpm（默认 100 QPM）以内
	•	若 respect_response_headers=true：
	•	读取并尊重 X-Ratelimit-* 响应头（接近耗尽时 sleep）
	•	同时加安全间隔：safety_min_interval_ms（默认 700ms）
	•	任何 429/限流情况要自动退避并重试（受 network.retries/backoff_sec 控制）

Reddit 对 Data API 免费访问的速率限制建议和 X-Ratelimit-* 头说明见官方说明。

6.2 网络重试
	•	仅对可重试错误重试（超时、5xx、429）
	•	对 401/403（鉴权或权限问题）：
	•	尝试刷新 token（仅一次）
	•	仍失败则退出并明确报错

⸻

7. 输出与数据模型（必须实现）

7.1 输出目录
	•	storage.output.posts_dir 存放抓取结果
	•	storage.output.format=jsonl：
	•	每个新帖子追加一行 JSON（建议一个文件或按日期分文件均可，但需文档化）

7.2 每条帖子最少字段（必须包含）
	•	id（id36）
	•	fullname（data.name，例如 t3_xxx）
	•	created_utc
	•	subreddit
	•	author
	•	title
	•	selftext
	•	url
	•	permalink
	•	is_self
	•	over_18
	•	score
	•	num_comments
	•	raw_listing_item（可选：保存原始 listing 的 data）
	•	detail（可选：保存 api/info 返回的补充 data）
	•	comments（若开启：保存 /comments 返回的原始结构或解析后的结构）
	•	fetched_at_utc

7.3 deleted/removed 标记（必须实现）

如果检测到 deleted/removed 迹象（示例：作者为 [deleted]、正文为 [removed] 等）：
	•	在记录中增加字段：
	•	is_deleted_or_removed: true
	•	removed_hint: "author_deleted"|"text_removed"|...

⸻

8. 合规清理（必须实现可选任务）

当 storage.compliance.purge_deleted_content=true：
	•	定期（purge_interval_hours）扫描落盘数据
	•	对已标记 is_deleted_or_removed=true 的记录执行删除/清理（具体策略：删除文件或写 tombstone 由实现决定，但必须可配置且有日志）

合规要求与建议参考 Reddit 官方 Data API 使用规范。

⸻

9. CLI 设计（必须实现）

提供可执行命令（名称可为 redditfeed）：
	1.	redditfeed validate --config config.yml

	•	仅做配置解析 + multipath/url 一致性校验
	•	必须发起一次 GET /api/multi{multipath} 验证可访问性
	•	失败返回非 0，输出明确错误

	2.	redditfeed once --config config.yml

	•	跑一轮：multi 校验 -> 拉 listing -> 处理新帖 -> 落盘 -> 更新 state
	•	返回 0 表示成功

	3.	redditfeed run --config config.yml

	•	持续轮询：每 poll_interval_sec 执行一次 once 流程（不中断为佳）
	•	可用 Ctrl+C 正常退出并保存 state

⸻

10. 代码结构建议（强烈建议）

语言建议 Python 3.11（也可 Node，但默认按 Python 设计）：
	•	config.py：加载与校验 config.yml（含 url/multipath 解析与一致性检查）
	•	reddit_auth.py：用 refresh_token 换 access_token（缓存到期时间）
	•	reddit_client.py：统一请求封装（headers、UA、超时、重试、限速、X-Ratelimit 处理）
	•	multi_validator.py：GET /api/multi{multipath} 校验与解析 subreddits
	•	fetcher.py：
	•	fetch_listing()
	•	filter_new_posts(seen_fullnames)
	•	fetch_details_batch(fullnames)
	•	fetch_comments(post_id36)
	•	storage.py：state.json 读写；jsonl 写入；purge 任务
	•	cli.py：validate/once/run

⸻

11. 验收标准（必须满足）
	1.	用 config 中的 URL https://www.reddit.com/user/bushacker/m/myreddit/ 能解析并校验出：
	•	owner=bushacker, name=myreddit, multipath=/user/bushacker/m/myreddit
	2.	validate 能实际请求 GET /api/multi{multipath} 并通过（或失败给出清晰错误）
	3.	once 能拉到 listing 并只处理“新帖子”，逐帖抓取内容（detail + comments），写入 jsonl
	4.	重复执行 once 不会重复写入已处理帖子（依赖 seen_fullnames）
	5.	QPM 控制在配置上限内；日志可见限速与重试行为
	6.	secret/token 不会出现在日志或异常打印中

⸻


如果你还想把 `config.yml` 也“生成到仓库里”（即让 Claude 直接创建文件），你只需要告诉我项目名/语言（Python or Node），我可以把 claude.md 里再加上明确的目录结构与需要创建的文件清单。
