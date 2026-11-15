import os, sys

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from config import CLASSES, DATA_DIR
"""
Downloads images from the web using the iCrawler library.

- img_nums : Number of images to download per class. Default is 500.
- Before downloading, checks if the folder already contains enough images and
  skips if Yes (reported as "satisfied" in the final summary).
- Each class has multiple search queries; the remaining quota is split evenly
  across them.
- For each query, crawlers are tried in order (Bing -> Google -> Baidu ->
  Flickr). As soon as one source actually increases the on-disk image count,
  the remaining sources are skipped for that query.
- A source that raises an exception or adds no new valid images is recorded as
  failed (with the reason) and the next source is tried — a single failing
  source never aborts the query, the class, or the whole run.
- After all classes finish, a readable summary is printed (and returned):
  per class the target, final count, whether the target was met, and per query
  which sources succeeded or failed and why.
"""

VALID_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}


def get_crawlers():
    """Return the ordered [(name, CrawlerClass), ...] list.

    Imported lazily so that the module (and its tests) work without icrawler
    installed; only an actual download run needs the dependency.
    """
    from icrawler.builtin import (
        BingImageCrawler,
        GoogleImageCrawler,
        BaiduImageCrawler,
        FlickrImageCrawler,
    )

    return [
        ("Bing", BingImageCrawler),
        ("Google", GoogleImageCrawler),
        ("Baidu", BaiduImageCrawler),
        ("Flickr", FlickrImageCrawler),
    ]


def count_valid_images(folder):
    """Count files with a known image extension directly inside `folder`."""
    if not os.path.isdir(folder):
        return 0
    return sum(
        1
        for name in os.listdir(folder)
        if os.path.isfile(os.path.join(folder, name))
        and os.path.splitext(name)[1].lower() in VALID_EXTENSIONS
    )


def _crawl_query(query, quota, folder, crawlers):
    """Try each crawler in order until one grows the image count.

    Returns a list of per-source result dicts:
    {"source", "status": "success"|"failed", "added", "reason"}.
    """
    results = []
    for name, CrawlerClass in crawlers:
        before = count_valid_images(folder)
        try:
            CrawlerClass(storage={"root_dir": folder}).crawl(
                keyword=query, max_num=quota
            )
        except Exception as e:
            reason = f"exception: {e}"
            print(f"    {name} failed: {reason}")
            results.append(
                {"source": name, "status": "failed", "added": 0, "reason": reason}
            )
            continue

        added = count_valid_images(folder) - before
        if added > 0:
            print(f"    {name} added {added} image(s)")
            results.append(
                {"source": name, "status": "success", "added": added, "reason": ""}
            )
            return results

        reason = "no new valid images downloaded"
        print(f"    {name} failed: {reason}")
        results.append(
            {"source": name, "status": "failed", "added": 0, "reason": reason}
        )
    return results


def download_images(img_nums=500, crawlers=None):
    """Download images for every class in CLASSES and print a final summary.

    Returns the summary as a dict keyed by class name.
    """
    if crawlers is None:
        crawlers = get_crawlers()

    os.makedirs(DATA_DIR, exist_ok=True)
    summary = {}

    for class_name, queries in CLASSES.items():
        folder = os.path.join(DATA_DIR, class_name)
        os.makedirs(folder, exist_ok=True)

        current_count = count_valid_images(folder)
        class_report = {
            "target": img_nums,
            "final_count": current_count,
            "met": current_count >= img_nums,
            "skipped": current_count >= img_nums,
            "queries": {},
        }

        # already enough images -> skip downloading, but report it honestly
        if current_count >= img_nums:
            print(
                f"{class_name} already satisfied: {current_count} images "
                f"(target: {img_nums}) - skipped"
            )
            summary[class_name] = class_report
            continue

        images_needed = img_nums - current_count
        print(
            f"\nDownloading {class_name} "
            f"({images_needed} more needed to reach {img_nums})"
        )

        # split the remaining quota evenly across queries
        per_query = max(1, images_needed // len(queries))

        for query in queries:
            remaining = img_nums - count_valid_images(folder)
            if remaining <= 0:
                break

            quota = min(per_query, remaining)
            print(f"  Searching: '{query}' - need {quota}")

            query_results = _crawl_query(query, quota, folder, crawlers)
            class_report["queries"][query] = query_results

        final_count = count_valid_images(folder)
        class_report["final_count"] = final_count
        class_report["met"] = final_count >= img_nums
        print(f"{class_name} done. Total: {final_count} images")
        summary[class_name] = class_report

    _print_summary(summary)
    return summary


def _print_summary(summary):
    """Print a readable final report for the whole download run."""
    print("\n" + "=" * 60)
    print("DOWNLOAD SUMMARY")
    print("=" * 60)
    for class_name, report in summary.items():
        status = "MET" if report["met"] else "NOT MET"
        note = " (already satisfied, skipped)" if report["skipped"] else ""
        print(
            f"\n[{status}] {class_name}: {report['final_count']}/{report['target']}"
            f" images{note}"
        )
        for query, sources in report["queries"].items():
            print(f"  query: '{query}'")
            for src in sources:
                if src["status"] == "success":
                    print(f"    [ok]     {src['source']}: +{src['added']} images")
                else:
                    print(f"    [failed] {src['source']}: {src['reason']}")
            if not any(s["status"] == "success" for s in sources):
                print("    -> no source could fulfill this query")


if __name__ == "__main__":
    download_images()
