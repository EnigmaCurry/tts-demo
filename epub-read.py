#!/usr/bin/env python3
"""Extract EPUB chapters to markdown."""

import argparse
import sys
from fast_ebook import epub


def main():
    parser = argparse.ArgumentParser(description="Extract EPUB to markdown")
    parser.add_argument("epub", help="Path to EPUB file")
    parser.add_argument(
        "--toc", action="store_true", help="Show table of contents only"
    )
    parser.add_argument(
        "--chapter", type=int, help="Extract a single chapter by index (0-based)"
    )
    args = parser.parse_args()

    book = epub.read_epub(args.epub)

    if args.toc:
        title = book.get_metadata("DC", "title")
        if title:
            name = title[0][0] if isinstance(title[0], tuple) else title[0]
            print(f"# {name}")
            print()
        for i, entry in enumerate(book.toc):
            print(f"{i}: {entry.title}")
        return

    md = book.to_markdown()

    if args.chapter is not None:
        # Split on markdown H1 headers and pick the requested chapter
        chapters = []
        current = []
        for line in md.splitlines(keepends=True):
            if line.startswith("# ") and current:
                chapters.append("".join(current))
                current = []
            current.append(line)
        if current:
            chapters.append("".join(current))

        if args.chapter < 0 or args.chapter >= len(chapters):
            print(
                f"Chapter {args.chapter} out of range (0-{len(chapters) - 1})",
                file=sys.stderr,
            )
            sys.exit(1)
        print(chapters[args.chapter], end="")
    else:
        print(md)


if __name__ == "__main__":
    main()
