#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Scoreboard 生成器
统计用户个人 + 指定组织的贡献与 star 总数，渲染成 github-readme-stats 同风格 SVG。

设计用于 GitHub Actions（也可本地运行），零第三方依赖（仅 Python 标准库）。

环境变量:
  GITHUB_TOKEN  必填。classic PAT，建议 scope: repo + read:user
  GITHUB_USER   用户名，默认 caomengxuan666
  GITHUB_ORGS   逗号分隔的组织名，默认 unixwin
  OUTPUT_PATH   输出文件名，默认 scoreboard.svg
"""

import json
import os
import time
import urllib.request

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_USER = os.environ.get("GITHUB_USER", "caomengxuan666")
GITHUB_ORGS = [o.strip() for o in os.environ.get("GITHUB_ORGS", "unixwin").split(",") if o.strip()]
OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "scoreboard.svg")

GRAPHQL_API = "https://api.github.com/graphql"
REST_API = "https://api.github.com"


# ---------------------------------------------------------------- HTTP helpers

def http_json(url, token, payload=None, headers=None):
    """GET/POST 一个 JSON，带自动重试（GitHub API 偶发 502）。"""
    data = json.dumps(payload).encode() if payload is not None else None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
            req.add_header("Authorization", f"bearer {token}")
            req.add_header("User-Agent", "scoreboard-action")
            req.add_header("Accept", "application/vnd.github+json")
            for k, v in (headers or {}).items():
                req.add_header(k, v)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def graphql(token, query):
    return http_json(GRAPHQL_API, token, payload={"query": query})


def rest_search(token, query_string):
    """GitHub 搜索接口，返回 total_count。带 token 时可覆盖私有仓库。"""
    url = f"{REST_API}/search/{query_string}&per_page=1"
    result = http_json(url, token, headers={"Accept": "application/vnd.github+json"})
    return result.get("total_count", 0)


# ---------------------------------------------------------------- 数据抓取

def fetch_data(token, user, orgs):
    # 1. 个人信息 + 个人仓库 star（排除 fork），外加每个组织的仓库 star，一次 GraphQL 拿完
    org_repo_parts = ""
    for org in orgs:
        org_repo_parts += f'''
        org_{org}: organization(login: "{org}") {{
          login
          repositories(first: 100, isFork: false) {{
            totalCount
            nodes {{ stargazerCount nameWithOwner }}
          }}
        }}'''

    query = f'''{{
      viewer {{ login }}
      user(login: "{user}") {{
        followers {{ totalCount }}
        contributionsCollection {{
          totalPullRequestReviewContributions
        }}
        repositories(ownerAffiliations: OWNER, isFork: false, first: 100,
                      orderBy: {{field: STARGAZERS, direction: DESC}}) {{
          totalCount
          nodes {{ stargazerCount }}
        }}
      }}{org_repo_parts}
    }}'''

    data = graphql(token, query)
    if "errors" in data:
        raise RuntimeError(f"GraphQL 查询失败: {json.dumps(data['errors'], ensure_ascii=False)}")
    d = data["data"]

    own_stars = sum(n["stargazerCount"] for n in d["user"]["repositories"]["nodes"])
    own_repos = d["user"]["repositories"]["totalCount"]

    org_stars = 0
    org_repos = 0
    org_details = []
    for org in orgs:
        node = d.get(f"org_{org}")
        if node is None:
            continue  # 组织不存在或 token 无权限，跳过
        s = sum(n["stargazerCount"] for n in node["repositories"]["nodes"])
        c = node["repositories"]["totalCount"]
        org_stars += s
        org_repos += c
        org_details.append((node["login"], c, s))

    followers = d["user"]["followers"]["totalCount"]
    reviews = d["user"]["contributionsCollection"]["totalPullRequestReviewContributions"]

    # 2. 全历史 commits / PRs / issues（REST 搜索，token 覆盖私有内容）
    total_commits = rest_search(token, f"commits?q=author:{user}")
    total_prs = rest_search(token, f"issues?q=author:{user}+type:pr")
    total_issues = rest_search(token, f"issues?q=author:{user}+type:issue")

    return {
        "user": user,
        "orgs": org_details,
        "stars": {"own": own_stars, "org": org_stars, "total": own_stars + org_stars},
        "repos": {"own": own_repos, "org": org_repos},
        "commits": total_commits,
        "prs": total_prs,
        "issues": total_issues,
        "reviews": reviews,
        "followers": followers,
    }


# ---------------------------------------------------------------- 等级评定
# 算法照抄 anuraghazra/github-readme-stats src/calculateRank.js (MIT License)
# 指数 CDF(commits/PRs/issues/reviews) + 对数正态近似(stars/followers)，
# 各指标加权平均后 rank = 1 - 平均值，即"未超越全站用户的百分比"。
# 等级刻度与原版一致，九档: S/A+/A/A-/B+/B/B-/C+/C。
# 与原版仅有的口径差异: stars 采用 个人+组织 的全家桶口径，
# commits 为全历史口径(对应原版 all_commits=True 的中位数 1000)。

def exponential_cdf(x):
    return 1 - 2 ** -x


def log_normal_cdf(x):
    # 原版同款近似
    return x / (1 + x)


def calculate_rank(stats):
    """返回 (等级, top 百分位)。权重、中位数、阈值与原版完全一致。"""
    COMMITS_MEDIAN = 1000  # 原版 all_commits=True 时的中位数
    COMMITS_WEIGHT = 2
    PRS_MEDIAN, PRS_WEIGHT = 50, 3
    ISSUES_MEDIAN, ISSUES_WEIGHT = 25, 1
    REVIEWS_MEDIAN, REVIEWS_WEIGHT = 2, 1
    STARS_MEDIAN, STARS_WEIGHT = 50, 4
    FOLLOWERS_MEDIAN, FOLLOWERS_WEIGHT = 10, 1

    total_weight = (
        COMMITS_WEIGHT + PRS_WEIGHT + ISSUES_WEIGHT
        + REVIEWS_WEIGHT + STARS_WEIGHT + FOLLOWERS_WEIGHT
    )

    rank = 1 - (
        COMMITS_WEIGHT * exponential_cdf(stats["commits"] / COMMITS_MEDIAN)
        + PRS_WEIGHT * exponential_cdf(stats["prs"] / PRS_MEDIAN)
        + ISSUES_WEIGHT * exponential_cdf(stats["issues"] / ISSUES_MEDIAN)
        + REVIEWS_WEIGHT * exponential_cdf(stats["reviews"] / REVIEWS_MEDIAN)
        + STARS_WEIGHT * log_normal_cdf(stats["stars"]["total"] / STARS_MEDIAN)
        + FOLLOWERS_WEIGHT * log_normal_cdf(stats["followers"] / FOLLOWERS_MEDIAN)
    ) / total_weight

    percent = rank * 100

    # 原版九档阈值
    THRESHOLDS = [1, 12.5, 25, 37.5, 50, 62.5, 75, 87.5, 100]
    LEVELS = ["S", "A+", "A", "A-", "B+", "B", "B-", "C+", "C"]
    level = LEVELS[next(i for i, t in enumerate(THRESHOLDS) if percent <= t)]
    return level, percent


GRADE_COLOR = {
    "S": "#fe428e", "A+": "#ff7b72", "A": "#f8d847", "A-": "#7ee787",
    "B+": "#7ee787", "B": "#79c0ff", "B-": "#79c0ff", "C+": "#8b949e", "C": "#8b949e",
}


# ---------------------------------------------------------------- SVG 渲染
# 风格对齐 github-readme-stats 的 radical 主题: 深紫底 / 玫红标题 / 青色文字 / 黄色数值

BG = "#141321"
BORDER = "#fe428e"
TITLE = "#fe428e"
TEXT = "#a9fef7"
VALUE = "#f8d847"
SUBTLE = "#8b949e"


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fmt(n):
    return f"{n:,}"


def icon(kind, x, y, color=BORDER):
    """14px 小图标，位于 (x, y) 为左上角。"""
    c = color
    if kind == "star":
        return f'<path transform="translate({x},{y})" d="M7 0l2.1 4.4 4.9.6-3.6 3.3.9 4.7L7 10.7 2.7 13l.9-4.7L0 5l4.9-.6z" fill="{c}"/>'
    if kind == "commit":
        return (f'<g transform="translate({x},{y})" fill="none" stroke="{c}" stroke-width="1.6">'
                f'<circle cx="7" cy="7" r="3.2" fill="{BG}" stroke="{c}"/>'
                f'<path d="M7 0v3.4M7 10.6V14"/></g>')
    if kind == "pr":
        return (f'<g transform="translate({x},{y})" fill="none" stroke="{c}" stroke-width="1.6">'
                f'<circle cx="3" cy="3" r="2.2"/><path d="M3 5.5V13"/><circle cx="11" cy="11" r="2.2"/>'
                f'<path d="M11 8.5V6a2.5 2.5 0 0 0-2.5-2.5H6"/><path d="M7.8 1.6L6 3.5l1.8 1.9" fill="none"/></g>')
    if kind == "issue":
        return (f'<g transform="translate({x},{y})" fill="none" stroke="{c}" stroke-width="1.6">'
                f'<circle cx="7" cy="7" r="5.6"/><circle cx="7" cy="7" r="2" fill="{c}" stroke="none"/></g>')
    if kind == "follower":
        return (f'<g transform="translate({x},{y})" fill="{c}">'
                f'<circle cx="7" cy="4" r="3.2"/><path d="M1 14c0-3.3 2.7-5.5 6-5.5s6 2.2 6 5.5z"/></g>')
    if kind == "org":
        return (f'<g transform="translate({x},{y})" fill="none" stroke="{c}" stroke-width="1.6">'
                f'<rect x="1" y="2.5" width="5" height="5" rx="1"/><rect x="8" y="2.5" width="5" height="5" rx="1"/>'
                f'<rect x="1" y="9" width="5" height="5" rx="1"/><rect x="8" y="9" width="5" height="5" rx="1"/></g>')
    return ""


def render_svg(stats, grade, percentile):
    org_line = " + ".join(o for o, _, _ in stats["orgs"]) if stats["orgs"] else "—"
    rows = [
        ("star", "Total Stars", f'{fmt(stats["stars"]["total"])}', f'own {fmt(stats["stars"]["own"])} + org {fmt(stats["stars"]["org"])}'),
        ("commit", "Total Commits", fmt(stats["commits"]), "all-time, private included"),
        ("pr", "Total PRs", fmt(stats["prs"]), "all-time, private included"),
        ("issue", "Total Issues", fmt(stats["issues"]), "all-time, private included"),
        ("follower", "Followers", fmt(stats["followers"]), ""),
        ("org", "Org Repos", fmt(stats["repos"]["org"]), esc(org_line)),
    ]

    row_h = 25
    pad_top = 44
    height = pad_top + len(rows) * row_h + 14
    width = 500

    row_svg = []
    for i, (ic, label, value, extra) in enumerate(rows):
        y = pad_top + i * row_h
        text_y = y + 13
        parts = [icon(ic, 26, y)]
        parts.append(f'<text x="50" y="{text_y}" fill="{TEXT}" font-size="14">{label}</text>')
        if extra:
            parts.append(f'<text x="118" y="{text_y}" fill="{SUBTLE}" font-size="11">{extra}</text>')
        parts.append(f'<text x="{width - 130}" y="{text_y}" fill="{VALUE}" font-size="14" font-weight="600" text-anchor="end">{value}</text>')
        row_svg.append("".join(parts))

    ring_r = 46
    cx = width - 68
    cy = pad_top + 62
    gcolor = GRADE_COLOR[grade]

    svg = f'''<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" fill="none" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="{esc(stats['user'])} scoreboard">
  <style>
    text {{ font-family: 'Segoe UI', Ubuntu, Sans-Serif; }}
    .title {{ font-weight: 600; }}
  </style>
  <rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="4.5" fill="{BG}" stroke="#{BORDER[1:]}" stroke-opacity="0.6"/>
  <text x="26" y="28" fill="{TITLE}" font-size="17" class="title">{esc(stats['user'])} · GitHub Scoreboard</text>
  {"".join(row_svg)}
  <circle cx="{cx}" cy="{cy}" r="{ring_r}" fill="none" stroke="{gcolor}" stroke-width="2.5" stroke-dasharray="4 3"/>
  <circle cx="{cx}" cy="{cy}" r="{ring_r - 5}" fill="{gcolor}" fill-opacity="0.08"/>
  <text x="{cx}" y="{cy - 2}" fill="{gcolor}" font-size="30" font-weight="700" text-anchor="middle">{grade}</text>
  <text x="{cx}" y="{cy + 16}" fill="{SUBTLE}" font-size="10" text-anchor="middle">top {percentile:.1f}%</text>
</svg>'''
    return svg


# ---------------------------------------------------------------- main

def main():
    if not GITHUB_TOKEN:
        raise SystemExit("缺少环境变量 GITHUB_TOKEN")

    stats = fetch_data(GITHUB_TOKEN, GITHUB_USER, GITHUB_ORGS)
    grade, percentile = calculate_rank(stats)

    svg = render_svg(stats, grade, percentile)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(svg)

    print(f"[scoreboard] {OUTPUT_PATH} 生成完毕")
    print(json.dumps(
        {"grade": grade, "top_percent": round(percentile, 2), **stats},
        ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
