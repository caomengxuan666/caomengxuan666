#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape


USER = "caomengxuan666"
ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"

LANG_COLORS = {
    "C++": "#f34b7d",
    "C": "#555555",
    "Rust": "#dea584",
    "Python": "#3572A5",
    "CMake": "#DA3434",
    "TypeScript": "#3178c6",
    "JavaScript": "#f1e05a",
    "Shell": "#89e051",
    "PowerShell": "#012456",
    "Lua": "#000080",
    "HTML": "#e34c26",
    "CSS": "#663399",
}

TRACKS = [
    ("MCP / AI Infra", ["cxxmcp", "cxxmcp-gateway", "cxxmcp-examples", "cmx-blog-mcp"]),
    ("LLM Tooling", ["ferryllm", "longctx"]),
    ("Windows/Linux DevEx", ["WinuxCmd", "winuxsh", "oh-my-winuxsh"]),
    ("Storage / Redis", ["AstraDB", "AstraKV", "resp-cli", "Astra"]),
    ("Distributed Systems", ["libgossip", "BTreeX"]),
    ("Build / Code Quality", ["CMakeHub", "clang-tidy-visualizer"]),
]


def token():
    value = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if value:
        return value
    try:
        return subprocess.check_output(["gh", "auth", "token"], text=True).strip()
    except Exception:
        return ""


TOKEN = token()


def request_json(url):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{USER}-profile-assets",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in {403, 429, 500, 502, 503, 504} and attempt < 2:
                time.sleep(2 + attempt * 3)
                continue
            raise


def github_user():
    return request_json(f"https://api.github.com/users/{USER}")


def repositories():
    repos = []
    page = 1
    while True:
        batch = request_json(
            f"https://api.github.com/users/{USER}/repos?per_page=100&page={page}&type=owner&sort=updated"
        )
        if not batch:
            break
        repos.extend(batch)
        page += 1
    return [repo for repo in repos if not repo.get("fork") and not repo.get("archived")]


def languages_for(repo_name):
    return request_json(f"https://api.github.com/repos/{USER}/{repo_name}/languages")


def collect_data():
    user = github_user()
    repos = repositories()

    languages = {}
    for repo in repos:
        for name, size in languages_for(repo["name"]).items():
            languages[name] = languages.get(name, 0) + int(size)

    stars = sum(int(repo.get("stargazers_count", 0)) for repo in repos)
    top_repos = sorted(repos, key=lambda r: int(r.get("stargazers_count", 0)), reverse=True)[:6]

    return {
        "public_repos": int(user.get("public_repos", 0)),
        "followers": int(user.get("followers", 0)),
        "stars": stars,
        "languages": languages,
        "top_repos": top_repos,
        "repo_names": {repo["name"] for repo in repos},
    }


def svg_frame(width, height, body):
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">
  <defs>
    <linearGradient id="header" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#0ea5e9"/>
      <stop offset="55%" stop-color="#22c55e"/>
      <stop offset="100%" stop-color="#f59e0b"/>
    </linearGradient>
    <filter id="shadow" x="-10%" y="-10%" width="120%" height="120%">
      <feDropShadow dx="0" dy="8" stdDeviation="10" flood-color="#0f172a" flood-opacity="0.10"/>
    </filter>
  </defs>
  <rect width="{width}" height="{height}" rx="18" fill="#ffffff"/>
  <rect x="1" y="1" width="{width - 2}" height="{height - 2}" rx="17" fill="#ffffff" stroke="#e2e8f0"/>
{body}
</svg>
"""


def language_svg(data):
    top = sorted(data["languages"].items(), key=lambda item: item[1], reverse=True)[:10]
    total = sum(size for _, size in top) or 1

    width, height = 760, 360
    rows = []
    y = 92
    for name, size in top:
        pct = size / total
        bar_width = int(500 * pct)
        color = LANG_COLORS.get(name, "#64748b")
        label = escape(name)
        rows.append(f'  <text x="46" y="{y + 14}" fill="#0f172a" font-family="Segoe UI, Arial, sans-serif" font-size="15" font-weight="650">{label}</text>')
        rows.append(f'  <rect x="170" y="{y}" width="500" height="18" rx="9" fill="#f1f5f9"/>')
        rows.append(f'  <rect x="170" y="{y}" width="{bar_width}" height="18" rx="9" fill="{color}"/>')
        rows.append(f'  <text x="690" y="{y + 14}" text-anchor="end" fill="#475569" font-family="Segoe UI, Arial, sans-serif" font-size="13">{pct * 100:.1f}%</text>')
        y += 25

    body = f"""
  <rect x="22" y="22" width="716" height="58" rx="16" fill="url(#header)" filter="url(#shadow)"/>
  <text x="46" y="58" fill="#ffffff" font-family="Segoe UI, Arial, sans-serif" font-size="23" font-weight="750">Language Footprint</text>
  <text x="714" y="58" text-anchor="end" fill="#ecfeff" font-family="Segoe UI, Arial, sans-serif" font-size="13">public original repos</text>
{chr(10).join(rows)}
  <text x="46" y="330" fill="#64748b" font-family="Segoe UI, Arial, sans-serif" font-size="13">Generated from GitHub API. Forks and archived repositories are excluded.</text>"""
    return svg_frame(width, height, body)


def snapshot_svg(data):
    width, height = 760, 360
    track_rows = []
    y = 176
    for title, projects in TRACKS:
        present = [project for project in projects if project in data["repo_names"]]
        names = ", ".join(present[:4])
        track_rows.append(f'  <text x="46" y="{y}" fill="#0f172a" font-family="Segoe UI, Arial, sans-serif" font-size="15" font-weight="700">{escape(title)}</text>')
        track_rows.append(f'  <text x="244" y="{y}" fill="#475569" font-family="Segoe UI, Arial, sans-serif" font-size="13">{escape(names)}</text>')
        y += 25

    stats = [
        ("Public Repos", f'{data["public_repos"]}+'),
        ("Original Stars", str(data["stars"])),
        ("Main Stack", "C++ / Rust"),
        ("Primary Theme", "Systems + AI Infra"),
    ]
    cards = []
    x = 46
    for label, value in stats:
        cards.append(f'  <rect x="{x}" y="92" width="158" height="58" rx="14" fill="#f8fafc" stroke="#e2e8f0"/>')
        cards.append(f'  <text x="{x + 16}" y="118" fill="#0f172a" font-family="Segoe UI, Arial, sans-serif" font-size="18" font-weight="750">{escape(value)}</text>')
        cards.append(f'  <text x="{x + 16}" y="138" fill="#64748b" font-family="Segoe UI, Arial, sans-serif" font-size="12">{escape(label)}</text>')
        x += 176

    body = f"""
  <rect x="22" y="22" width="716" height="58" rx="16" fill="url(#header)" filter="url(#shadow)"/>
  <text x="46" y="58" fill="#ffffff" font-family="Segoe UI, Arial, sans-serif" font-size="23" font-weight="750">Engineering Snapshot</text>
  <text x="714" y="58" text-anchor="end" fill="#ecfeff" font-family="Segoe UI, Arial, sans-serif" font-size="13">caomengxuan666</text>
{chr(10).join(cards)}
{chr(10).join(track_rows)}
  <text x="46" y="330" fill="#64748b" font-family="Segoe UI, Arial, sans-serif" font-size="13">Signal: MCP tooling, LLM middleware, Windows/Linux DevEx, Redis-compatible storage.</text>"""
    return svg_frame(width, height, body)


def main():
    ASSETS.mkdir(exist_ok=True)
    data = collect_data()
    (ASSETS / "language-footprint.svg").write_text(language_svg(data), encoding="utf-8")
    (ASSETS / "engineering-snapshot.svg").write_text(snapshot_svg(data), encoding="utf-8")
    print("Generated profile assets:")
    print(f"- assets/language-footprint.svg")
    print(f"- assets/engineering-snapshot.svg")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"failed to generate profile assets: {exc}", file=sys.stderr)
        raise
