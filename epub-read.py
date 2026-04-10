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


def get_chapter_markdown(full_md, chapters):
    """Split full markdown into per-chapter sections keyed by href."""
    # Chapters appear as # or ## headers in the markdown; split on them
    sections = re.split(r"(?m)^(?=#{1,2} )", full_md)
    # Find the start of actual content (after Gutenberg preamble)
    content_start = 0
    for i, section in enumerate(sections):
        if "*** START OF" in section:
            content_start = i + 1
            break

    result = {}
    for _, ch_title, href in chapters:
        # Find the section whose header best matches this chapter title
        normalized_title = _normalize(ch_title)
        for section in sections[content_start:]:
            first_line = section.split("\n", 1)[0].strip().lstrip("#").strip()
            if _normalize(first_line) == normalized_title:
                result[href] = section.strip()
                break
            # Handle split titles like "## CHAPTER I.\nDown the Rabbit-Hole"
            first_two = "\n".join(section.split("\n", 2)[:2]).strip().lstrip("#").strip()
            combined = " ".join(first_two.split("\n"))
            if _normalize(combined) == normalized_title:
                result[href] = section.strip()
                break
    return result


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
        "--text",
        action="store_true",
        help="Output as plain text instead of markdown",
    )
    parser.add_argument(
        "--paragraph", type=int,
        help="Extract a single paragraph by index (1-based, requires --chapter)",
    )
    args = parser.parse_args()

    if args.paragraph is not None and args.chapter is None:
        parser.error("--paragraph requires --chapter")

    book = epub.read_epub(args.epub)
    title = get_title(book)

    if args.raw_toc:
        if title:
            print(f"# {title}")
            print()
        for i, entry in enumerate(book.toc):
            print(f"{i}: {entry.title}")
        return

    chapters = get_chapters(book)
    md_sections = None
    if not args.text:
        full_md = book.to_markdown()
        md_sections = get_chapter_markdown(full_md, chapters)

    if args.toc:
        if title:
            print(f"# {title}")
            print()
        for num, (_, ch_title, _) in enumerate(chapters, 1):
            print(f"{num}: {ch_title}")
        return

    def get_chapter_content(ch_title, href):
        if md_sections is not None:
            return md_sections.get(href)
        else:
            text = get_chapter_text(book, href)
            if text:
                return f"# {ch_title}\n\n{text}"
        return None

    def get_paragraphs(text):
        """Split text into paragraphs on double newlines."""
        return [p.strip() for p in re.split(r"\n\n+", text) if p.strip()]

    if args.chapter is not None:
        if args.chapter < 1 or args.chapter > len(chapters):
            print(
                f"Chapter {args.chapter} out of range (1-{len(chapters)})",
                file=sys.stderr,
            )
            sys.exit(1)
        _, ch_title, href = chapters[args.chapter - 1]
        text = get_chapter_content(ch_title, href)
        if text and args.paragraph is not None:
            paras = get_paragraphs(text)
            if args.paragraph < 1 or args.paragraph > len(paras):
                print(
                    f"Paragraph {args.paragraph} out of range (1-{len(paras)})",
                    file=sys.stderr,
                )
                sys.exit(1)
            print(paras[args.paragraph - 1])
        elif text:
            print(text)
        return

    # Default: print all chapters
    for num, (_, ch_title, href) in enumerate(chapters, 1):
        text = get_chapter_content(ch_title, href)
        if text:
            print(text)
            print()


if __name__ == "__main__":
    main()
