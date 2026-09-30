"""Render the repository's public Markdown guides as static, base-path-safe pages."""

from __future__ import annotations

import argparse
import html
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from markdown import Markdown


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "playground" / "public" / "docs"
REPO = "https://github.com/Sanjays2402/retrace"
GUIDES = (
    ("getting-started", "Quickstart", "Start here", "Install Retrace and recover your first workflow."),
    ("examples", "Real integrations", "Start here", "Work through CSV and HTTP delivery examples."),
    ("choosing-retrace", "Is Retrace a fit?", "Start here", "Check the scope and tradeoffs before adopting it."),
    ("architecture", "Architecture", "Under the hood", "Explore leases, fencing, checkpoints, and scheduling."),
    ("recovery", "Recovery contract", "Under the hood", "Understand retries, preserved outputs, and external effects."),
    ("api", "API and CLI", "Reference", "Find the Python API and command-line usage."),
    ("benchmark", "Benchmarks", "Reference", "See the method behind the local overhead baseline."),
    ("security", "Security", "Reference", "Read the trust and data-handling boundaries."),
    ("roadmap", "Roadmap", "Community", "Find implemented work and contribution ideas."),
)
SLUGS = {guide[0] for guide in GUIDES}


def source(slug: str) -> Path:
    return ROOT / ("SECURITY.md" if slug == "security" else f"docs/{slug}.md")


def rewrite_link(url: str) -> str:
    parts = urlsplit(html.unescape(url))
    if parts.scheme or parts.netloc or not parts.path:
        return url
    path = parts.path
    if path.endswith(".md"):
        slug = Path(path).stem.lower()
        if slug not in SLUGS:
            raise ValueError(f"No site page for Markdown link: {url}")
        path = f"../{slug}/"
    elif path.startswith("assets/") or path == "benchmark-baseline.json":
        path = f"../{path}"
    else:
        raise ValueError(f"Unhandled local documentation link: {url}")
    return html.escape(urlunsplit(("", "", path, parts.query, parts.fragment)), quote=True)


def render_markdown(text: str) -> tuple[str, list[dict]]:
    parser = Markdown(extensions=["fenced_code", "tables", "toc", "sane_lists"])
    body = parser.convert(text)
    body = re.sub(
        r'(?P<attr>href|src)="(?P<url>[^"]+)"',
        lambda match: f'{match.group("attr")}="{rewrite_link(match.group("url"))}"',
        body,
    )
    return body, parser.toc_tokens


def nav(active: str | None, *, index: bool = False) -> str:
    prefix = "" if index else "../"
    current_class = " current" if active is None else ""
    current_attr = ' aria-current="page"' if active is None else ""
    links = [f'<a class="doc-nav-link{current_class}" href="{prefix}"{current_attr}>Overview</a>']
    previous_group = None
    for slug, title, group, _ in GUIDES:
        if group != previous_group:
            links.append(f'<span class="doc-nav-label">{html.escape(group)}</span>')
            previous_group = group
        current = slug == active
        current_class = " current" if current else ""
        current_attr = ' aria-current="page"' if current else ""
        links.append(
            f'<a class="doc-nav-link{current_class}" '
            f'href="{prefix}{slug}/"{current_attr}>'
            f'{html.escape(title)}</a>'
        )
    return "\n".join(links)


def toc_items(tokens: list[dict]) -> str:
    items = []
    for token in tokens:
        if token["level"] in (2, 3):
            items.append(
                f'<a class="toc-level-{token["level"]}" href="#{html.escape(token["id"], quote=True)}">'
                f'{html.escape(token["name"])}</a>'
            )
        items.extend(toc_items_nested(token.get("children", [])))
    return "\n".join(items)


def toc_items_nested(tokens: list[dict]) -> list[str]:
    result = []
    for token in tokens:
        result.append(
            f'<a class="toc-level-{token["level"]}" href="#{html.escape(token["id"], quote=True)}">'
            f'{html.escape(token["name"])}</a>'
        )
        result.extend(toc_items_nested(token.get("children", [])))
    return result


def document(title: str, description: str, content: str, active: str | None, toc: str = "") -> str:
    index = active is None
    root = "../" if index else "../../"
    docs = "./" if index else "../"
    source_link = "" if index else (
        f'<a class="source-link" href="{REPO}/blob/main/'
        f'{"SECURITY.md" if active == "security" else f"docs/{active}.md"}">Edit source on GitHub ↗</a>'
    )
    toc_html = (
        f'<aside class="doc-toc" aria-label="On this page"><span>ON THIS PAGE</span>{toc}</aside>'
        if toc else ""
    )
    return f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{html.escape(description, quote=True)}">
  <title>{html.escape(title)} · Retrace docs</title>
  <link rel="icon" href="{root}mark.svg" type="image/svg+xml">
  <link rel="stylesheet" href="{root}docs.css">
  <script src="{root}docs.js" defer></script>
</head>
<body>
  <a class="skip-link" href="#main">Skip to content</a>
  <div class="docs-shell">
    <aside class="docs-sidebar" aria-label="Documentation navigation">
      <a class="docs-brand" href="{root}"><img src="{root}mark.svg" width="35" height="35" alt="">retrace<span>α</span></a>
      <p class="sidebar-kicker">DOCUMENTATION</p>
      <nav>{nav(active, index=index)}</nav>
      <div class="sidebar-bottom"><span class="live-dot"></span> Local-first · Open source</div>
    </aside>
    <div class="docs-content">
      <header class="docs-topbar">
        <div class="crumbs"><a href="{root}">Playground</a><span>/</span><a href="{docs}">Docs</a>{'' if index else f'<span>/</span><strong>{html.escape(title)}</strong>'}</div>
        <div class="top-actions"><button class="theme-button" type="button" aria-label="Toggle light and dark theme">◐ <span>Theme</span></button><a href="{REPO}">GitHub ↗</a></div>
      </header>
      <details class="mobile-doc-nav"><summary>Browse documentation</summary><nav>{nav(active, index=index)}</nav></details>
      <div class="docs-layout"><main id="main" class="docs-main">{content}{source_link}</main>{toc_html}</div>
      <footer class="docs-footer"><span>Retrace · Early alpha · MIT</span><a href="{REPO}/issues/new/choose">Get help ↗</a></footer>
    </div>
  </div>
</body>
</html>
'''


def overview() -> str:
    groups = []
    for group in dict.fromkeys(guide[2] for guide in GUIDES):
        cards = "".join(
            f'<a class="doc-card" href="{slug}/"><span>{html.escape(group)}</span>'
            f'<strong>{html.escape(title)} <b>↗</b></strong><p>{html.escape(description)}</p></a>'
            for slug, title, section, description in GUIDES if section == group
        )
        groups.append(f'<section class="doc-group"><h2>{html.escape(group)}</h2><div class="doc-card-grid">{cards}</div></section>')
    content = (
        '<div class="docs-hero"><span class="docs-eyebrow"><i></i> THE RETRACE FIELD GUIDE</span>'
        '<h1>Build workflows that<br><em>remember their progress.</em></h1>'
        '<p>Start with a five-minute recovery walkthrough, then explore the contracts '
        'behind the engine. These pages are built from the repository documentation.</p>'
        '<a class="start-button" href="getting-started/">Start the quickstart <span>→</span></a></div>'
        + "".join(groups)
    )
    return document("Documentation", "Guides and reference for the Retrace workflow engine.", content, None)


def article(guide: tuple[str, str, str, str], position: int) -> str:
    slug, title, group, description = guide
    body, tokens = render_markdown(source(slug).read_text(encoding="utf-8"))
    body = re.sub(r"^<h1[^>]*>.*?</h1>\s*", "", body, count=1, flags=re.S)
    previous = GUIDES[position - 1] if position else None
    following = GUIDES[position + 1] if position + 1 < len(GUIDES) else None
    steps = '<nav class="doc-next" aria-label="Adjacent guides">'
    if previous:
        steps += f'<a href="../{previous[0]}/"><small>← PREVIOUS</small><strong>{html.escape(previous[1])}</strong></a>'
    if following:
        steps += f'<a href="../{following[0]}/"><small>NEXT →</small><strong>{html.escape(following[1])}</strong></a>'
    steps += "</nav>"
    content = (
        f'<div class="article-hero"><span class="docs-eyebrow"><i></i> {html.escape(group.upper())}</span>'
        f'<h1>{html.escape(title)}</h1><p>{html.escape(description)}</p></div>'
        f'<article class="markdown-body">{body}</article>{steps}'
    )
    return document(title, description, content, slug, toc_items(tokens))


def write(path: Path, content: str, check: bool) -> None:
    if check:
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            raise SystemExit(f"Documentation out of date: {path.relative_to(ROOT)}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="verify committed pages match sources")
    args = parser.parse_args()
    write(OUT / "index.html", overview(), args.check)
    for position, guide in enumerate(GUIDES):
        write(OUT / guide[0] / "index.html", article(guide, position), args.check)
    assets = (ROOT / "docs" / "assets", ROOT / "docs" / "benchmark-baseline.json")
    for asset in assets:
        destination = OUT / asset.name
        if args.check:
            files = asset.rglob("*") if asset.is_dir() else [asset]
            for file in files:
                target = destination / file.relative_to(asset) if asset.is_dir() else destination
                if file.is_file() and (not target.exists() or target.read_bytes() != file.read_bytes()):
                    raise SystemExit(f"Documentation asset out of date: {file.relative_to(ROOT)}")
        elif asset.is_dir():
            shutil.copytree(asset, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(asset, destination)
    print(f"{'Verified' if args.check else 'Built'} {len(GUIDES) + 1} documentation pages")


if __name__ == "__main__":
    main()
