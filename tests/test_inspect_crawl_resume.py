"""Offline read-only preflight for ordinary and legacy checkpoint-less crawl work."""

from __future__ import annotations

from pathlib import Path

from infinity_wiki_acquisition import cli
from infinity_wiki_acquisition.crawl_checkpoint import (
    CHECKPOINT_FILENAME, CHECKPOINT_FORMAT, CHECKPOINT_VERSION,
    write_crawl_checkpoint,
)
from tools.inspect_crawl_resume import inspect


def test_missing_checkpoint_reports_preserved_files_without_claiming_resume(tmp_path: Path):
    work = tmp_path / "old.work"
    work.mkdir()
    (work / "Example.html").write_text("<html>historical work</html>", encoding="utf-8")
    (work / "images").mkdir()
    (work / "images" / "icon.png").write_bytes(b"fake")
    result = inspect(work, site_key="human-sphere", language="en")
    assert result["checkpointPresent"] is False
    assert result["directResumeSafe"] is False
    assert result["retryFailedOnlyEligible"] is False
    assert result["topLevelEntries"] == 2
    assert result["privateEvidence"] == []
    assert (work / "Example.html").exists()
    assert not (work / CHECKPOINT_FILENAME).exists()


def test_cli_refuses_checkpointless_nonempty_legacy_work_without_network_or_rewrite(
    tmp_path: Path, monkeypatch, capsys,
):
    work = tmp_path / "HUMAN-SPHERE old.work"
    work.mkdir()
    saved = work / "Example.html"
    saved.write_bytes(b"<html><body>preserved</body></html>")
    monkeypatch.setattr(
        cli, "fetch_site_bytes",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("No network calls permitted"),
        ),
    )
    assert cli.main([
        "--site", "human-sphere", "--language", "en",
        "--resume-work", str(work),
        "--root", str(tmp_path / "archives"),
    ]) == 1
    assert "Cannot resume nonempty Wiki work without" in capsys.readouterr().err
    assert saved.read_bytes() == b"<html><body>preserved</body></html>"
    assert not (work / CHECKPOINT_FILENAME).exists()
    assert not list((tmp_path / "archives").glob("*.zip"))


def test_completed_checkpoint_can_retry_failed_url_without_reenumeration(tmp_path: Path):
    site = cli.HUMAN_SPHERE_SITE
    root = cli.canonical_crawl_url(site.root_urls["en"], site=site)
    work = tmp_path / "ready.work"
    work.mkdir()
    (work / "index.html").write_bytes(b"<html></html>")
    failed = root.rstrip("/") + "/Module_talk:Infobox/styles.css"
    write_crawl_checkpoint(work / CHECKPOINT_FILENAME, {
        "format": CHECKPOINT_FORMAT, "version": CHECKPOINT_VERSION,
        "site": site.key, "language": "en", "rootUrl": root,
        "phase": "complete", "complete": True,
        "enumerationContinuation": None, "queue": [],
        "discovered": {root: False, failed: False},
        "required": [root], "pageIds": {},
        "saved": {root: ["index.html", len(b"<html></html>")]},
        "pages": [{"url": root, "path": "index.html", "title": "Main Page"}],
        "ignored": {}, "failed": {failed: "original HTML classified as asset"},
    })
    result = inspect(work, site_key="human-sphere", language="en")
    assert result["checkpointPresent"] is True
    assert result["directResumeSafe"] is True
    assert result["phase"] == "complete"
    assert result["retryFailedOnlyEligible"] is True
    assert result["savedFiles"] == 1
    assert result["failedUrls"] == 1
    assert result["failedDetails"][0]["url"] == failed
