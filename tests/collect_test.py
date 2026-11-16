# -*- coding: utf-8 -*-
"""Checks of the collector (collect.py) on temporary local repositories (Python 3.10+, standard library
only; git must be installed).

    python -B tests/collect_test.py     exit 0: every check passes; exit 1: a check failed

What is proved:
- one broken project source does not cancel the others: the good project still gets current/events;
- a failed project keeps its last successful result (auto.json "last_ok") instead of an empty object,
  and the readable reason of the new failure is stored in "error";
- a project read from the computer only (fetch off or GitHub unreachable) gets status "local";
- a bad repository in the statistics folder is listed in stats.commits.errors while every readable
  repository keeps its per-day commit data;
- projects.json is never rewritten and auto.json is written through a temporary file + os.replace;
- the manual-stage rule (a source stage below the manual one keeps the manual stage) and the since
  filter keep working.
Nothing is written outside a temporary folder; no network call runs (there is no "origin" to fetch).
"""
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f"  - {detail}"))


ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
           GIT_COMMITTER_EMAIL="t@example.com", GIT_TERMINAL_PROMPT="0")


def sh(cwd, *args):
    done = subprocess.run(["git", "-C", str(cwd)] + list(args), capture_output=True, env=ENV)
    if done.returncode != 0:
        raise RuntimeError(f"git {args}: {done.stderr.decode('utf-8', 'replace')}")
    return done.stdout.decode("utf-8", "replace").strip()


def rm_tree(path):
    """shutil.rmtree that also removes read-only git objects (Windows marks them read-only)."""
    def make_writable(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    shutil.rmtree(path, onerror=make_writable)


def make_repo(base, name, stages_file=None, initial_commit=True):
    """A local git repository with main as its branch."""
    repo = base / name
    sh(base, "init", "-q", "-b", "main", str(repo))
    if initial_commit:
        (repo / "app.txt").write_text("v1\n", encoding="utf-8")
        sh(repo, "add", "app.txt")
        sh(repo, "commit", "-q", "-m", "start")
    if stages_file is not None:
        (repo / "docs").mkdir(exist_ok=True)
        (repo / "docs" / "stages.json").write_text(json.dumps(stages_file, ensure_ascii=False), encoding="utf-8")
        sh(repo, "add", "docs/stages.json")
        sh(repo, "commit", "-q", "-m", "stages")
    return repo


def write_projects(work, projects):
    path = work / "projects.json"
    path.write_text(json.dumps({"projects": projects}, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def load_auto(work):
    return json.loads((work / "auto.json").read_text(encoding="utf-8"))


def main():
    if not shutil.which("git"):
        print("FAIL git is not installed: the collector check needs git")
        return 1
    import collect

    base = pathlib.Path(tempfile.mkdtemp(prefix="collect-test-"))
    work = base / "pult"                 # the program folder: projects.json + auto.json live here
    work.mkdir()
    repos = base / "repos"
    repos.mkdir()
    try:
        # ---------------------------------------------------------------- world: two "stages" projects
        stages = ["Design", "Build", "Content", "Publish"]
        good = make_repo(repos, "good", {"stages": [
            {"id": "design", "date": "2026-10-01", "merge_commit": "HEAD~0"},
            {"id": "build", "date": "2026-10-10"}]})
        bad = make_repo(repos, "bad", {"stages": [{"id": "design", "date": "2026-10-02", "merge_commit": "HEAD~0"}]})
        # an extra repository next to the project repos: statistics must read it too
        other = make_repo(repos, "neighbour")
        (other / "more.txt").write_text("x\n", encoding="utf-8")
        sh(other, "add", "more.txt")
        sh(other, "commit", "-q", "-m", "second")

        def project(pid, name, repo_path, fetch=True, since="2026-01-01", manual=0):
            src = {"kind": "stages", "repo": str(repo_path), "file": "docs/stages.json", "since": since}
            if not fetch:
                src["fetch"] = False
            return {"id": pid, "name": name, "stages": stages, "current": manual, "source": src}

        projects_path = write_projects(work, [
            project("good", "Good project", good),
            project("bad", "Bad project", bad),
        ])
        collect.SOURCE = work / "projects.json"
        collect.TARGET = work / "auto.json"

        # C1: a healthy run — both projects read, overall status ok, stats has all three repositories.
        first = collect.collect(fetch=False)
        auto = load_auto(work)
        check("C1 healthy run: both projects ok with current/events and overall status ok",
              first["status"] == "ok" and first["counts"] == {"ok": 2, "local": 0, "failed": 0, "stats_errors": 0}
              and auto["projects"]["good"]["ok"] is True and auto["projects"]["good"]["status"] == "ok"
              and auto["projects"]["bad"]["ok"] is True
              and auto["projects"]["good"]["current"] == 1 and bool(auto["projects"]["good"]["events"]),
              json.dumps(first, ensure_ascii=False))
        stats_names = sorted(r["name"] for r in first["stats"]["commits"]["repos"])
        check("C1b statistics read every repository of the folder", stats_names == ["bad", "good", "neighbour"],
              str(stats_names))
        check("C1c statistics carry per-day data", all(r["days"] for r in first["stats"]["commits"]["repos"]))
        check("C1d statistics report no errors", first["stats"]["commits"]["errors"] == [])
        good_first = json.loads(json.dumps(auto["projects"]["good"]))     # snapshot of the last good result
        bad_first = json.loads(json.dumps(auto["projects"]["bad"]))
        projects_first = projects_path.read_text(encoding="utf-8")

        # C2: the bad project's repository disappears — its source can no longer be read.
        rm_tree(bad)
        second = collect.collect(fetch=False)
        auto2 = load_auto(work)
        bad_res = auto2["projects"]["bad"]
        good_res = auto2["projects"]["good"]
        check("C2 broken source: overall partial, the broken project failed with a readable error",
              second["status"] == "partial" and second["counts"]["failed"] == 1
              and bad_res["ok"] is False and bad_res["status"] == "failed" and bad_res["error"]
              and "нет репозитория" in bad_res["error"], json.dumps(bad_res, ensure_ascii=False))
        check("C2b the good project still has fresh current/events and stays ok",
              good_res["ok"] is True and good_res["current"] == 1 and bool(good_res["events"])
              and good_res.get("last_ok") is None, json.dumps(good_res, ensure_ascii=False))
        check("C2c the failed project kept its last successful result, not an empty object",
              isinstance(bad_res.get("last_ok"), dict) and bad_res["last_ok"].get("ok") is True
              and bad_res["last_ok"].get("current") == 1 and bool(bad_res["last_ok"].get("events"))
              and bad_res["last_ok"].get("name") == "Bad project", json.dumps(bad_res, ensure_ascii=False))
        check("C2d the top-level failure object carries no current/events (the old values live in last_ok)",
              "current" not in bad_res and "events" not in bad_res)
        check("C2e the failure reason is in the top-level errors too",
              any("Bad project" in e for e in second["errors"]), json.dumps(second["errors"], ensure_ascii=False))

        # C3: projects.json is untouched; auto.json is written through a temp file + os.replace (no tmp left).
        check("C3 projects.json is never rewritten", projects_path.read_text(encoding="utf-8") == projects_first)
        check("C3b no temporary auto.json files are left behind",
            not list(work.glob("auto.json.*.tmp")) and (work / "auto.json").is_file())

        # C4: a second failure in a row still shows the original last_ok (the failure itself never overwrites it).
        third = collect.collect(fetch=False)
        bad3 = load_auto(work)["projects"]["bad"]
        check("C4 repeated failure keeps the original last_ok and a fresh readable error",
              bad3.get("last_ok", {}).get("current") == 1 and bad3["ok"] is False and bool(bad3["error"]),
              json.dumps(bad3, ensure_ascii=False))

        # C5: the source recovers — the project reads again, last_ok disappears and the result is fresh.
        bad = make_repo(repos, "bad", {"stages": [
            {"id": "design", "date": "2026-10-02", "merge_commit": "HEAD~0"},
            {"id": "build", "date": "2026-11-01", "merge_commit": "HEAD~0"}]})
        projects_path.write_text(projects_first, encoding="utf-8")    # same projects.json content as in C1
        fourth = collect.collect(fetch=False)
        bad4 = load_auto(work)["projects"]["bad"]
        check("C5 recovered source: ok again, last_ok removed, current reflects the source",
              fourth["status"] == "ok" and bad4["ok"] is True and bad4.get("last_ok") is None
              and bad4["current"] == 2, json.dumps(bad4, ensure_ascii=False))

        # C6: a bad repository inside the statistics folder: its error is listed, the other repos keep their days.
        broken = repos / "broken-stats"
        broken.mkdir()
        (broken / ".git").mkdir()                                # looks like a repository, but has no objects
        fifth = collect.collect(fetch=False)
        commits = fifth["stats"]["commits"]
        check("C6 statistics with a bad repo: readable repos keep their per-day data",
              sorted(r["name"] for r in commits["repos"]) == ["bad", "good", "neighbour"]
              and all(r["days"] for r in commits["repos"]),
              json.dumps([(r["name"], r["days"]) for r in commits["repos"]], ensure_ascii=False))
        check("C6b the bad statistics repo is named in errors",
              len(commits["errors"]) == 1 and "broken-stats" in commits["errors"][0],
              json.dumps(commits["errors"], ensure_ascii=False))
        check("C6c partial overall: stats failure counts, projects are still all ok",
              fifth["status"] == "partial" and fifth["counts"] == {"ok": 2, "local": 0, "failed": 0, "stats_errors": 1}
              and any("Статистика" in e for e in fifth["errors"]), json.dumps(fifth["counts"], ensure_ascii=False))
        rm_tree(broken)

        # C7: fetch turned off for a project — it succeeds on computer data and gets the "local" status.
        local_project = project("good", "Good project", good, fetch=False)
        write_projects(work, [local_project, project("bad", "Bad project", bad, fetch=False)])
        seventh = collect.collect(fetch=True)
        good7 = load_auto(work)["projects"]["good"]
        check("C7 fetch off: project read from the computer only, status local",
              good7["ok"] is True and good7["status"] == "local" and good7.get("local_only") is True
              and good7["current"] == good_first["current"], json.dumps(good7, ensure_ascii=False))

        # C8: fetch asked for, but the repository has no "origin" — local data is used and the note explains why.
        write_projects(work, [project("good", "Good project", good, fetch=True)])
        eighth = collect.collect(fetch=True)
        good8 = load_auto(work)["projects"]["good"]
        check("C8 unreachable GitHub: local data used, status local, a note (not an error) explains it",
              good8["ok"] is True and good8["status"] == "local" and good8.get("fetched") is False
              and any("Good project" in n and "данные с компьютера" in n for n in eighth["notes"])
              and eighth["errors"] == [] and eighth["status"] == "ok",
              json.dumps({"status": good8.get("status"), "notes": eighth["notes"]}, ensure_ascii=False))

        # C9: every project source fails — overall status failed, each project keeps its last_ok.
        write_projects(work, [project("good", "Good project", good), project("bad", "Bad project", bad)])
        collect.collect(fetch=False)                                  # one more healthy baseline
        rm_tree(good)
        rm_tree(bad)
        ninth = collect.collect(fetch=False)
        auto9 = load_auto(work)
        check("C9 all sources broken: overall failed, both projects failed with errors and last_ok kept",
              ninth["status"] == "failed" and ninth["counts"]["failed"] == 2
              and all(not r["ok"] and r["status"] == "failed" and r.get("error") and r.get("last_ok", {}).get("ok")
                      for r in auto9["projects"].values()),
              json.dumps(ninth["counts"], ensure_ascii=False))

        # C10: the manual-stage rule and the since filter survive the rework.
        good = make_repo(repos, "good", {"stages": [{"id": "design", "date": "2026-10-01", "merge_commit": "HEAD~0"}]})
        write_projects(work, [project("good", "Good project", good, since="2027-01-01", manual=3)])
        tenth = collect.collect(fetch=False)
        r10 = load_auto(work)["projects"]["good"]
        check("C10 manual stage kept when the source is behind; events before since filtered out",
              r10["ok"] is True and r10["current"] == 3 and r10["events"] == []
              and any("записанный вручную" in n for n in tenth["notes"]),
              json.dumps({"current": r10.get("current"), "events": r10.get("events"),
                          "notes": tenth["notes"]}, ensure_ascii=False))

        # C11: a "progress" kind project with an unreadable progress file fails readably and keeps last_ok.
        prog_path = work / "PROGRESS.json"
        prog_path.write_text(json.dumps({
            "stages": stages, "current": 1,
            "events": [{"date": "2026-10-05", "text": "Урок 06 пройден: раз"}]}, ensure_ascii=False), encoding="utf-8")
        prog_project = {"id": "course", "name": "Course", "stages": stages, "current": 0,
                        "source": {"kind": "progress", "path": str(prog_path), "since": "2026-01-01"}}
        write_projects(work, [prog_project])
        ok_run = collect.collect(fetch=False)
        check("C11 progress project: ok on a readable file, lesson collected",
              ok_run["projects"]["course"]["ok"] is True
              and ok_run["stats"]["lessons"]["course"] == [{"code": "06", "date": "2026-10-05"}],
              json.dumps(ok_run["stats"]["lessons"], ensure_ascii=False))
        prog_path.unlink()
        fail_run = collect.collect(fetch=False)
        course = load_auto(work)["projects"]["course"]
        check("C11b missing progress file: readable error, last_ok kept, its lessons stay in stats",
              fail_run["status"] == "failed" and course["status"] == "failed" and course["ok"] is False
              and "файл продвижения" in course["error"]
              and course["last_ok"]["ok"] is True
              and fail_run["stats"]["lessons"].get("course") == [{"code": "06", "date": "2026-10-05"}],
              json.dumps({"error": course.get("error"), "lessons": fail_run["stats"]["lessons"]}, ensure_ascii=False))

        # C12: no project carries a source — nothing can fail, the overall status is ok, not failed.
        write_projects(work, [{"id": "manual", "name": "Manual only", "stages": stages, "current": 1}])
        twelfth = collect.collect(fetch=False)
        check("C12 no sourced projects: overall ok, empty project results",
              twelfth["status"] == "ok" and twelfth["counts"] == {"ok": 0, "local": 0, "failed": 0, "stats_errors": 0}
              and twelfth["projects"] == {}, json.dumps(twelfth["counts"], ensure_ascii=False))
    finally:
        shutil.rmtree(base, ignore_errors=True, onerror=lambda f, t, e: (os.chmod(t, stat.S_IWRITE), f(t)))
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
