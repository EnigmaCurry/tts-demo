#!/usr/bin/env python3
"""Extract EPUB chapters to markdown."""

import argparse
import re
import sys
from fast_ebook import epub

# TOC entries matching these patterns are skipped as front/back matter
SKIP_PATTERNS = [
    r"(?i)^contents?$",
    r"(?i)^table\s+of\s+contents?$",
    r"(?i)^(the\s+)?(full\s+)?project\s+gutenberg",
    r"(?i)^license",
    r"(?i)^copyright",
    r"(?i)^colophon",
    r"(?i)^cover",
    r"(?i)^title\s*page",
    r"(?i)edition",
]


def _normalize(s):
    """Normalize unicode quotes/dashes for comparison."""
    s = s.lower()
    for c in "\u2018\u2019\u201a\u201b":
        s = s.replace(c, "'")
    for c in "\u201c\u201d\u201e\u201f":
        s = s.replace(c, '"')
    return s


def get_title(book):
    title = book.get_metadata("DC", "title")
    if not title:
        return None
    return title[0][0] if isinstance(title[0], tuple) else title[0]


def get_chapters(book):
    """Return list of (toc_index, title, href) for real content chapters."""
    book_title = get_title(book)
    # First pass: identify hrefs that contain front/back matter
    skip_hrefs = set()
    for entry in book.toc:
        href_base = entry.href.split("#")[0]
        t = entry.title.strip()
        if any(re.search(p, t) for p in SKIP_PATTERNS):
            skip_hrefs.add(href_base)
    # Title-page entry shares href with other front matter (contents, edition)
    if book_title:
        for entry in book.toc:
            href_base = entry.href.split("#")[0]
            t = entry.title.strip()
            if _normalize(t) == _normalize(book_title) and href_base in skip_hrefs:
                skip_hrefs.add(href_base)  # already there, but explicit
    # Second pass: collect chapters, skipping front/back matter
    chapters = []
    seen_hrefs = set()
    for i, entry in enumerate(book.toc):
        href_base = entry.href.split("#")[0]
        t = entry.title.strip()
        if any(re.search(p, t) for p in SKIP_PATTERNS):
            continue
        # Skip title-page entries (title matches book title AND href is front matter)
        if book_title and _normalize(t) == _normalize(book_title) and href_base in skip_hrefs:
            continue
        if href_base in seen_hrefs:
            continue
        seen_hrefs.add(href_base)
        chapters.append((i, t, href_base))
    return chapters


def get_chapter_text(book, href):
    """Get plain text for a chapter by its href."""
    item = book.get_item_with_href(href)
    if item is None:
        return None
    return item.get_text()


def main():
    parser = argparse.ArgumentParser(description="Extract EPUB to markdown")
    parser.add_argument("epub", help="Path to EPUB file")
    parser.add_argument(
        "--toc", action="store_true", help="Show table of contents only"
    )
    parser.add_argument(
        "--raw-toc",
        action="store_true",
        help="Show raw (unfiltered) table of contents",
    )
    parser.add_argument(
        "--chapter", type=int, help="Extract a single chapter by number (1-based)"
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="Output full book as markdown (unfiltered)",
    )
    args = parser.parse_args()

    book = epub.read_epub(args.epub)
    title = get_title(book)

    if args.raw_toc:
        if title:
            print(f"# {title}")
            print()
        for i, entry in enumerate(book.toc):
            print(f"{i}: {entry.title}")
        return

    if args.markdown:
        print(book.to_markdown())
        return

    chapters = get_chapters(book)

    if args.toc:
        if title:
            print(f"# {title}")
            print()
        for num, (_, ch_title, _) in enumerate(chapters, 1):
            print(f"{num}: {ch_title}")
        return

    if args.chapter is not None:
        if args.chapter < 1 or args.chapter > len(chapters):
            print(
                f"Chapter {args.chapter} out of range (1-{len(chapters)})",
                file=sys.stderr,
            )
            sys.exit(1)
        _, ch_title, href = chapters[args.chapter - 1]
        text = get_chapter_text(book, href)
        if text:
            print(f"# {ch_title}\n")
            print(text)
        return

    # Default: print all chapters
    for num, (_, ch_title, href) in enumerate(chapters, 1):
        text = get_chapter_text(book, href)
        if text:
            print(f"# {ch_title}\n")
            print(text)
            print()


if __name__ == "__main__":
    main()
