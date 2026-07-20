#!/usr/bin/env python3
"""
internal-link-opportunities — find the links you should already have.

Somewhere on your site is a paragraph that talks about exactly the thing another
one of your pages is about, and does not link to it. That is the cheapest ranking
work available and nobody does it, because finding those paragraphs by hand across
a few hundred pages is unbearable.

This crawls the pages you give it, works out what each one is about, then finds
every page that discusses that subject without linking to it — and prints the
actual sentence to put the link in.

No API keys, no dependencies.

MIT © SEO Pro Check
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import glob
import gzip
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from html.parser import HTMLParser

UA = "internal-link-opportunities/1.0 (+https://github.com/seoprocheck/internal-link-opportunities)"
CHROME_TAGS = {"script", "style", "nav", "header", "footer", "aside", "form",
               "noscript", "svg", "template", "button", "select"}

# Tag-based detection alone is not enough: most CMS themes build their menus,
# related-post rails and share bars out of plain <div>s. Measured on a real
# WordPress site that leaked ~340 nav links and ~260 menu list items into every
# page's "content" counts.
CHROME_CONTAINERS = {"div", "section", "ul", "ol", "aside", "span", "table"}
CHROME_HINT = re.compile(
    r"(^|[-_ ])("
    r"nav(bar|igation)?|(sub|main|primary|top)?menu|breadcrumbs?|pagination|pager|"
    r"sidebar|widgets?|footer|(site|global|page|main)[-_]header|masthead|banner|"
    r"related|recirc|share|social|subscribe|newsletter|cookie|consent|promo|popup|"
    r"modal|offcanvas|drawer|comments?|disqus|toc|tableofcontents|skip|screen-reader"
    r")([-_ ]|$)", re.I)
VOID_TAGS = {"br", "hr", "img", "input", "meta", "link", "source", "track", "wbr",
             "col", "area", "base", "embed", "param"}


def is_chrome_attrs(attrs):
    """Site chrome by class/id/role. Deliberately does NOT match entry-header or
    post-header — those hold the H1 on most themes."""
    blob = " ".join(filter(None, (attrs.get("class"), attrs.get("id"), attrs.get("role"))))
    if not blob:
        return False
    if attrs.get("role") in ("navigation", "banner", "contentinfo", "complementary", "search"):
        return True
    return bool(CHROME_HINT.search(blob))

BLOCK_TAGS = {"p", "li", "td", "th", "blockquote", "dd", "dt"}
HEADING_TAGS = {"h1", "h2", "h3"}
MIN_TERMS = 2          # a topic needs this many distinctive words to be matchable
MAX_ANCHOR_WORDS = 45

STOP = set("""a an the and or of to in for is are was were be been being on at as it its this that
these those with from by about into over under again then there here when where which who what how
why can could will would shall should may might must do does did have has had i me my we our you
your he she his her they them their but not no nor so than too very just also only own same such
each few more most other some any all both out up down off above below now get got make made use
used using like new see may one two three best top guide guides review reviews vs versus your home
page site blog post article read more click here learn find out us contact about""".split())


class Page(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.chrome = 0
        self._stack = []
        self.title = ""
        self._in_title = False
        self.h1 = ""
        self._h = None
        self._hbuf = []
        self.blocks = []
        self._buf = []
        self.links = set()          # normalised internal targets, content area only
        self.all_links = set()      # including chrome, for orphan accuracy

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a":
            href = (a.get("href") or "").strip()
            if href and not href.startswith(("#", "mailto:", "tel:", "javascript:")):
                u = normalise(urllib.parse.urljoin(self.base, href))
                if u:
                    self.all_links.add(u)
                    if not self.chrome:
                        self.links.add(u)
        chrome = False
        if tag in CHROME_TAGS:
            chrome = True
        elif tag in CHROME_CONTAINERS and is_chrome_attrs(a):
            chrome = True
        if tag not in VOID_TAGS:
            self._stack.append((tag, chrome))
        if chrome:
            self.chrome += 1
            return
        if tag == "title":
            self._in_title = True
        if self.chrome:
            return
        if tag in HEADING_TAGS:
            self._flush()
            self._h = tag
            self._hbuf = []
        elif tag in BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag):
        closed_chrome = False
        if any(t == tag for t, _ in self._stack):
            while self._stack:
                t, was_chrome = self._stack.pop()
                if was_chrome:
                    self.chrome = max(0, self.chrome - 1)
                    closed_chrome = True
                if t == tag:
                    break
        if closed_chrome or tag in CHROME_TAGS:
            return
        if tag == "title":
            self._in_title = False
        if tag in HEADING_TAGS and self._h:
            t = " ".join(" ".join(self._hbuf).split())
            if t:
                if self._h == "h1" and not self.h1:
                    self.h1 = t
                self.blocks.append(t)
            self._h = None
            self._hbuf = []
        elif tag in BLOCK_TAGS:
            self._flush()

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self.chrome:
            return
        if self._h:
            self._hbuf.append(data)
        elif data.strip():
            self._buf.append(data)

    def _flush(self):
        t = " ".join(" ".join(self._buf).split())
        if len(t) > 1:
            self.blocks.append(t)
        self._buf = []

    def close(self):
        self._flush()
        super().close()


def normalise(url):
    """Compare URLs the way a site actually serves them: no fragment, no trailing
    slash, no index.html, lowercase host. Without this, /a/ and /a look like two
    different pages and every internal link appears to be missing."""
    if not url:
        return ""
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https", ""):
        return ""
    path = re.sub(r"/index\.html?$", "/", p.path or "/")
    if len(path) > 1:
        path = path.rstrip("/")
    return urllib.parse.urlunsplit(("", p.netloc.lower(), path or "/", p.query, ""))


def words(t):
    return re.findall(r"[a-z0-9']+", t.lower())


def stem(w):
    if len(w) <= 3:
        return w
    for suf, n in (("ies", 5), ("ing", 6), ("ed", 5), ("es", 5), ("s", 4)):
        if w.endswith(suf) and len(w) >= n:
            b = w[:-len(suf)] + ("y" if suf == "ies" else "")
            if len(b) > 3 and b[-1] == b[-2] and b[-1] not in "lsz":
                b = b[:-1]
            return b
    return w


def terms(t):
    return [stem(w) for w in words(t) if w not in STOP and len(w) > 2 and not w.isdigit()]


def sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


# Breadcrumb trails and menu strips survive chrome stripping on plenty of themes,
# and they match a target's subject perfectly — while being useless as anchors.
BREADCRUMB = re.compile(r"\s(/|»|›|→|\|)\s")


def is_prose(s):
    """Reject navigation debris that happens to contain the right words."""
    if BREADCRUMB.search(s):
        return False
    w = s.split()
    if len(w) < 5:
        return False
    # Menu strips are mostly capitalised labels; real sentences are mostly not.
    capped = sum(1 for x in w if x[:1].isupper())
    if capped / float(len(w)) > 0.6:
        return False
    # A sentence with no lowercase run of any length is a label, not prose.
    return bool(re.search(r"[a-z]{3,}", s))


def read_source(src, timeout=30):
    if not src.startswith(("http://", "https://")):
        with open(src, "rb") as f:
            return f.read().decode("utf-8", "replace"), src
    req = urllib.request.Request(src, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if (r.headers.get("Content-Encoding") or "") == "gzip" or raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return raw.decode(r.headers.get_content_charset() or "utf-8", "replace"), r.geturl()


def load(src):
    html, final = read_source(src)
    p = Page(final)
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    subject = p.h1 or " ".join(p.title.split())
    return {
        "url": normalise(final),
        "raw_url": final,
        "title": " ".join(p.title.split()),
        "h1": p.h1,
        "subject": subject,
        "subject_terms": terms(subject),
        "sentences": [s for b in p.blocks for s in sentences(b)],
        "links": {l for l in p.links},
        "all_links": {l for l in p.all_links},
    }


def find_opportunities(pages, min_terms, max_per_target):
    by_url = {p["url"]: p for p in pages}
    inbound = Counter()
    for p in pages:
        for l in p["all_links"]:
            if l in by_url and l != p["url"]:
                inbound[l] += 1

    opps = defaultdict(list)
    for target in pages:
        tset = set(target["subject_terms"])
        if len(tset) < min_terms:
            continue
        phrase = " ".join(target["subject_terms"])
        for source in pages:
            if source["url"] == target["url"]:
                continue
            if target["url"] in source["links"] or target["url"] in source["all_links"]:
                continue
            best = None
            for s in source["sentences"]:
                if len(words(s)) > MAX_ANCHOR_WORDS or not is_prose(s):
                    continue
                sset = set(terms(s))
                if not tset.issubset(sset):
                    continue
                exact = phrase in " ".join(terms(s))
                sc = len(tset) + (2 if exact else 0)
                if best is None or sc > best[0]:
                    best = (sc, s, exact)
            if best:
                opps[target["url"]].append({
                    "source": source["url"], "sentence": best[1],
                    "score": best[0], "exact_phrase": best[2],
                })
    out = []
    for url, items in opps.items():
        items.sort(key=lambda x: -x["score"])
        out.append({
            "target": url, "subject": by_url[url]["subject"],
            "inbound_links": inbound.get(url, 0),
            "opportunities": items[:max_per_target],
            "total_found": len(items),
        })
    # Pages starved of links first — that is where a new link is worth most.
    out.sort(key=lambda t: (t["inbound_links"], -t["total_found"]))
    return out, inbound


def orphans(pages, inbound):
    """Normalised URLs are comparison keys — protocol-relative and ugly. Always
    show the URL the crawl actually fetched."""
    return sorted(p["raw_url"] for p in pages if inbound.get(p["url"], 0) == 0)


def render(pages, results, inbound, args):
    disp = {p["url"]: p["raw_url"] for p in pages}
    total_links = sum(len([l for l in p["all_links"] if l in {q["url"] for q in pages}])
                      for p in pages)
    orph = orphans(pages, inbound)
    print()
    print("Internal Link Opportunities")
    print("=" * 76)
    print("  %d pages · %d internal link%s between them · %d page%s with no inbound link"
          % (len(pages), total_links, "" if total_links == 1 else "s",
             len(orph), "" if len(orph) == 1 else "s"))
    print()
    if orph:
        print("  ORPHANS — nothing on the site links to these")
        for u in orph[:args.orphans]:
            print("    %s" % u)
        if len(orph) > args.orphans:
            print("    … %d more" % (len(orph) - args.orphans))
        print()
    if not results:
        print("  No opportunities found. Either the site is already well linked, or the")
        print("  page subjects are too generic to match — check the H1s.")
        return
    shown = 0
    for r in results:
        if shown >= args.limit:
            break
        shown += 1
        print("  → %s" % disp.get(r["target"], r["target"]))
        print("    subject: %s   (%d inbound link%s, %d opportunit%s found)"
              % (r["subject"][:60], r["inbound_links"], "" if r["inbound_links"] == 1 else "s",
                 r["total_found"], "y" if r["total_found"] == 1 else "ies"))
        for o in r["opportunities"]:
            mark = "exact phrase" if o["exact_phrase"] else "topic match"
            print("      from %s  [%s]" % (disp.get(o["source"], o["source"]), mark))
            print("        \"%s\"" % o["sentence"][:120])
        print()
    if len(results) > args.limit:
        print("  … %d more targets. Raise --limit." % (len(results) - args.limit))


def selftest():
    checks = []
    def ok(l, c):
        checks.append((l, bool(c)))

    ok("trailing slash normalised", normalise("https://x.com/a/") == normalise("https://x.com/a"))
    ok("fragment stripped", normalise("https://x.com/a#b") == normalise("https://x.com/a"))
    ok("index.html normalised", normalise("https://x.com/a/index.html") == normalise("https://x.com/a/"))
    ok("host lowercased", normalise("https://X.COM/a") == normalise("https://x.com/a"))
    ok("mailto rejected", normalise("mailto:a@b.c") == "")

    ok("stopwords dropped from subject", "the" not in terms("The best water filter"))
    ok("terms stemmed", terms("filters")[0] == terms("filter")[0])

    target = {"url": "/guides/water-filters", "raw_url": "/guides/water-filters",
              "title": "", "h1": "Water filters", "subject": "Water filters",
              "subject_terms": terms("Water filters"), "sentences": [],
              "links": set(), "all_links": set()}
    linker = {"url": "/a", "raw_url": "/a", "title": "", "h1": "A", "subject": "A",
              "subject_terms": terms("A"),
              "sentences": ["We tested several water filters last season."],
              "links": {"/guides/water-filters"}, "all_links": {"/guides/water-filters"}}
    opportunity = {"url": "/b", "raw_url": "/b", "title": "", "h1": "B", "subject": "B",
                   "subject_terms": terms("B"),
                   "sentences": ["A good water filter matters more than the bottle."],
                   "links": set(), "all_links": set()}
    unrelated = {"url": "/c", "raw_url": "/c", "title": "", "h1": "C", "subject": "C",
                 "subject_terms": terms("C"),
                 "sentences": ["Sleeping bags are rated by comfort temperature."],
                 "links": set(), "all_links": set()}

    res, inb = find_opportunities([target, linker, opportunity, unrelated], MIN_TERMS, 5)
    hit = next((r for r in res if r["target"] == "/guides/water-filters"), None)
    srcs = {o["source"] for o in hit["opportunities"]} if hit else set()
    ok("mentioning page without a link is an opportunity", "/b" in srcs)
    ok("page that already links is not reported", "/a" not in srcs)
    ok("unrelated page is not reported", "/c" not in srcs)
    ok("the anchor sentence is returned",
       hit and "water filter" in hit["opportunities"][0]["sentence"].lower())
    ok("inbound links counted", inb.get("/guides/water-filters", 0) == 1)
    ok("orphans detected", "/b" in orphans([target, linker, opportunity, unrelated], inb))

    generic = dict(target, url="/d", subject="Home", subject_terms=terms("Home"))
    res2, _ = find_opportunities([generic, opportunity], MIN_TERMS, 5)
    ok("too-generic subject is skipped", not any(r["target"] == "/d" for r in res2))

    long_sentence = dict(opportunity, url="/e",
                         sentences=["water filter " + "padding " * 60])
    res3, _ = find_opportunities([target, long_sentence], MIN_TERMS, 5)
    ok("over-long sentence is not offered as an anchor",
       not any(o["source"] == "/e" for r in res3 for o in r["opportunities"]))

    ok("breadcrumb rejected as an anchor",
       not is_prose("Home / Cross-Industry / Water Filters For Hiking"))
    ok("menu strip rejected as an anchor",
       not is_prose("Water Filters Sleeping Bags Stoves Tents Packs Boots"))
    ok("real prose accepted as an anchor",
       is_prose("We carry a water filter for hiking on every trip."))
    crumb = dict(opportunity, url="/f",
                 sentences=["Home / Guides / Water Filters For Hiking Reviews"])
    res4, _ = find_opportunities([target, crumb], MIN_TERMS, 5)
    ok("breadcrumb page yields no opportunity",
       not any(o["source"] == "/f" for r in res4 for o in r["opportunities"]))

    w = max(len(c[0]) for c in checks) + 2
    for l, passed in checks:
        print("  %s %s" % ("PASS" if passed else "FAIL", l.ljust(w)))
    bad = [c[0] for c in checks if not c[1]]
    print("\n%d/%d passed" % (len(checks) - len(bad), len(checks)))
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(
        prog="internal_link_opportunities.py",
        description="Find pages that discuss another page's subject without linking to it.",
        epilog="Each page's subject comes from its H1 (falling back to <title>), so the "
               "results are only as good as your headings.")
    ap.add_argument("pages", nargs="*", help="page URLs or local .html paths")
    ap.add_argument("--sitemap", help="sitemap.xml to pull URLs from")
    ap.add_argument("--urls", metavar="FILE", help="text file of URLs, one per line")
    ap.add_argument("--limit", type=int, default=20, help="target pages to detail (default 20)")
    ap.add_argument("--per-target", type=int, default=3, dest="per_target",
                    help="opportunities shown per target (default 3)")
    ap.add_argument("--orphans", type=int, default=15, help="orphan pages to list")
    ap.add_argument("--min-terms", type=int, default=MIN_TERMS, dest="min_terms",
                    help="distinctive words a subject needs to be matchable (default 2)")
    ap.add_argument("--max-pages", type=int, default=300, dest="max_pages")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--selftest", action="store_true", help="verify the analysis internals")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(selftest())

    srcs = list(args.pages)
    for pat in list(srcs):
        if not pat.startswith(("http://", "https://")) and any(c in pat for c in "*?["):
            srcs.remove(pat)
            srcs.extend(sorted(glob.glob(pat)))
    if args.urls:
        with open(args.urls) as f:
            srcs += [l.strip() for l in f if l.strip() and not l.startswith("#")]
    if args.sitemap:
        xml, _ = read_source(args.sitemap)
        srcs += re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml, re.I)
    srcs = list(dict.fromkeys(srcs))[: args.max_pages]
    if not srcs:
        ap.error("give page URLs/paths, --urls FILE, or --sitemap URL")

    verbose = not args.quiet and not args.json
    if verbose:
        print("Crawling %d pages..." % len(srcs), file=sys.stderr)

    pages, failed = [], []
    def job(s):
        if args.delay:
            time.sleep(args.delay)
        return load(s)
    with futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        for src, fut in zip(srcs, [ex.submit(job, s) for s in srcs]):
            try:
                pages.append(fut.result())
            except Exception as e:
                failed.append((src, str(e)))
    if failed and verbose:
        for s, e in failed[:5]:
            print("  ! %s — %s" % (s, e), file=sys.stderr)
    if not pages:
        raise SystemExit("No pages could be crawled.")

    results, inbound = find_opportunities(pages, args.min_terms, args.per_target)
    if args.json:
        disp = {p["url"]: p["raw_url"] for p in pages}
        for r in results:
            r["target"] = disp.get(r["target"], r["target"])
            for o in r["opportunities"]:
                o["source"] = disp.get(o["source"], o["source"])
        json.dump({"pages": len(pages), "orphans": orphans(pages, inbound),
                   "targets": results}, sys.stdout, indent=2)
        print()
    else:
        render(pages, results, inbound, args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
