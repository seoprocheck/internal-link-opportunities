<h1 align="center">internal-link-opportunities</h1>

<p align="center">
  <strong>Find the paragraph that talks about your other page and doesn't link to it — with the sentence to link from.</strong>
</p>

<p align="center">
  <img alt="Python 3.8+" src="https://img.shields.io/badge/python-3.8%2B-blue">
  <img alt="Zero dependencies" src="https://img.shields.io/badge/dependencies-0-brightgreen">
  <img alt="No API key" src="https://img.shields.io/badge/API%20key-none-orange">
  <img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-black">
</p>

---

Internal linking is the cheapest ranking work available and almost nobody does it properly, because doing it properly means reading every page looking for sentences that mention another page's subject. That is unbearable by hand past about thirty pages, so it gets replaced with a "related posts" widget and forgotten.

This does the reading. It works out what each page is about from its H1, finds every other page whose prose discusses that subject without linking to it, and prints **the exact sentence** — so the job becomes paste-a-link rather than go-and-find-something.

It also reports **orphans**: pages nothing else links to at all.

```
Internal Link Opportunities
============================================================================
  5 pages · 1 internal link between them · 4 pages with no inbound link

  ORPHANS — nothing on the site links to these
    /blog/trip-report/
    /guides/stoves/

  → /guides/water-filters/
    subject: Water filters for hiking   (0 inbound links, 3 opportunities found)
      from /blog/trip-report/  [exact phrase]
        "The water filter for hiking clogged badly in the silty stream below the col."
      from /guides/sleeping-bags/  [exact phrase]
        "We carry a water filter for hiking on every trip, and a good one is worth the weight."
```

Targets are ordered by how starved of links they are, because a first inbound link is worth far more than a ninth.

## Usage

```bash
python3 internal_link_opportunities.py --sitemap https://example.com/sitemap.xml
python3 internal_link_opportunities.py --urls urls.txt --limit 40 --per-target 5
python3 internal_link_opportunities.py --sitemap https://example.com/sitemap.xml --json > links.json
```

Try it instantly on the bundled fixture — a five-page site with one existing link, several missed ones and an orphan:

```bash
python3 internal_link_opportunities.py "fixtures/*.html" --delay 0
```

Verify the internals — URL normalisation, already-linked exclusion, orphan detection and anchor selection:

```bash
python3 internal_link_opportunities.py --selftest
```

## What it gets right that a naive version doesn't

**URL normalisation.** `/a/` and `/a` and `/a/index.html` and `https://EXAMPLE.com/a` are one page. Without normalising, every internal link looks missing and the tool reports the entire site as orphaned.

**Chrome links still count as links.** A link from the nav is a poor editorial link but it is not *nothing* — counting only body links would flag well-linked pages as orphans. Inbound counts use every link; opportunities are only offered for pages not linked from anywhere.

**Stemming.** "water filters" and "water filter" are the same subject.

**Anchor sentences are capped.** A 200-word run-on sentence technically contains the phrase but is useless as an anchor, so it isn't offered.

**Generic subjects are skipped.** A page whose H1 is "Home" or "Blog" reduces to nothing distinctive after stopwords, and matching on it would flag every page on the site. Those are dropped rather than producing noise.

## Reading the output honestly

**It matches words, not meaning.** A page about jaguars the animal will match a sentence about Jaguars the car. Read the sentence before pasting the link — that is why the sentence is printed.

**Your H1s are the input.** Each page's subject comes from its H1, falling back to `<title>`. Vague headings produce vague matches; a site with "Welcome" as half its H1s will get poor results, which is itself worth knowing.

**Not every opportunity is worth taking.** Cramming a link into every mention reads badly and dilutes the ones that matter. Take the starved targets at the top of the list and stop.

**Relevance is not authority.** This finds relevant places to link. It does not know which of your pages deserves the equity.

**Pages are read as served.** Client-rendered content and JS-injected links are invisible here.

## License

MIT © [SEO Pro Check](https://seoprocheck.com) · built by [@seoprocheck](https://github.com/seoprocheck).
