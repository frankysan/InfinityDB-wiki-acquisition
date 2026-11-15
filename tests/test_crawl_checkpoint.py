"""Offline integration checks for a completed crawl's retry-only checkpoint."""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

import pytest

from infinity_wiki_acquisition import cli as module
from infinity_wiki_acquisition.crawl_checkpoint import (
    CHECKPOINT_FILENAME,
    read_crawl_checkpoint,
)


def test_retry_failed_only_uses_checkpoint_without_discovery_or_refetch(
    tmp_path: Path, monkeypatch,
) -> None:
    site = module.HUMAN_SPHERE_SITE
    root = site.root_urls["en"]
    good = "https://www.human-sphere.com/Good"
    failed = "https://www.human-sphere.com/Failed"
    new_link = "https://www.human-sphere.com/New"
    checkpoint = tmp_path / CHECKPOINT_FILENAME
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        module, "fetch_all_page_title_batch",
        lambda *, language, continuation, site: (("Main Page", "Good"), None),
    )
    payloads = {
        root: b'<html><body><a href="/Failed">Missing</a></body></html>',
        good: b"<html><body>Already fetched</body></html>",
    }
    calls: list[str] = []

    def fetch(url: str, *, language: str = "en") -> bytes:
        calls.append(url)
        if url not in payloads:
            raise OSError("unavailable")
        return payloads[url]

    monkeypatch.setattr(module, "fetch_bytes", fetch)
    first = module.download_wiki(
        root, tmp_path, site=site, checkpoint_file=checkpoint,
    )
    assert len(first.failures) == 1
    assert first.failures[0].url == failed
    assert checkpoint.is_file()
    state = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert state["complete"] is True
    assert state["failed"] == {failed: "OSError: unavailable"}
    assert root in state["discovered"]
    assert state["saved"][good][0] == "Good.html"
    assert CHECKPOINT_FILENAME not in {
        file.relative_to(tmp_path).as_posix() for file in first.files
    }

    def fail_enumeration(**_: object) -> tuple[str, ...]:
        raise AssertionError("retry-only must not query the allpages API")

    monkeypatch.setattr(module, "fetch_all_page_title_batch", fail_enumeration)
    payloads[failed] = b'<html><body><a href="/New">New link</a></body></html>'
    payloads[new_link] = b"<html><body>New page</body></html>"
    calls.clear()
    second = module.download_wiki(
        root, tmp_path, site=site, checkpoint_file=checkpoint,
        retry_failed_only=True,
    )

    assert second.failures == ()
    assert calls == [failed, new_link]
    assert {page.path for page in second.pages} == {
        "Main_Page.html", "Good.html", "Failed.html", "New.html",
    }
    assert len(second.files) == 4
    new_state = read_crawl_checkpoint(
        checkpoint, work=tmp_path, site=site.key, language="en", root_url=root,
    )
    assert new_state["failed"] == {}
    assert new_state["discovered"][new_link] is False


def test_retry_failed_only_retains_new_failures_and_can_retry_again(
    tmp_path: Path, monkeypatch,
) -> None:
    site = module.HUMAN_SPHERE_SITE
    root = site.root_urls["en"]
    missing = "https://www.human-sphere.com/Missing"
    checkpoint = tmp_path / CHECKPOINT_FILENAME
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    monkeypatch.setattr(module, "fetch_all_page_title_batch", lambda **_: (("Main Page",), None))
    monkeypatch.setattr(
        module, "fetch_bytes",
        lambda url, *, language="en": (
            b'<a href="/Missing">Missing</a>'
            if url == root else (_ for _ in ()).throw(OSError("not found"))
        ),
    )
    first = module.download_wiki(root, tmp_path, site=site, checkpoint_file=checkpoint)
    assert len(first.failures) == 1

    again = module.download_wiki(
        root, tmp_path, site=site, checkpoint_file=checkpoint,
        retry_failed_only=True,
    )
    assert len(again.failures) == 1
    assert json.loads(checkpoint.read_text())["failed"] == {
        missing: "OSError: not found"
    }


def test_retry_requires_completed_matching_checkpoint_and_unchanged_files(
    tmp_path: Path, monkeypatch,
) -> None:
    site = module.HUMAN_SPHERE_SITE
    root = site.root_urls["en"]
    checkpoint = tmp_path / CHECKPOINT_FILENAME
    monkeypatch.setattr(module.time, "sleep", lambda _: None)

    with pytest.raises(ValueError, match="No completed crawl checkpoint"):
        module.download_wiki(
            root, tmp_path, site=site, checkpoint_file=checkpoint,
            retry_failed_only=True,
        )
    monkeypatch.setattr(module, "fetch_all_page_title_batch", lambda **_: (("Main Page",), None))
    monkeypatch.setattr(
        module, "fetch_bytes",
        lambda url, *, language="en": b"<html><body>Valid</body></html>",
    )
    result = module.download_wiki(root, tmp_path, site=site, checkpoint_file=checkpoint)
    assert not result.failures
    page = tmp_path / "Main_Page.html"
    page.write_text("corrupted", encoding="utf-8")
    with pytest.raises(ValueError, match="missing or changed"):
        module.download_wiki(
            root, tmp_path, site=site, checkpoint_file=checkpoint,
            retry_failed_only=True,
        )
    with pytest.raises(ValueError, match="another site/language"):
        read_crawl_checkpoint(
            checkpoint, work=tmp_path, site="infinity-wiki",
            language="en", root_url=root,
        )


def test_retry_reclassifies_failed_module_talk_and_repairs_saved_links(
    tmp_path: Path, monkeypatch,
) -> None:
    site = module.HUMAN_SPHERE_SITE
    root = site.root_urls["en"]
    talk = "https://www.human-sphere.com/Module_talk:Infobox/styles.css"
    checkpoint = tmp_path / CHECKPOINT_FILENAME
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    monkeypatch.setattr(module, "fetch_all_page_title_batch", lambda **_: (("Main Page",), None))
    original_classifier = module.is_mediawiki_namespace_page_url
    # Simulate the old broken classifier so the source page was first queued
    # as a CSS asset and its HTML payload was (correctly) rejected.
    monkeypatch.setattr(module, "is_mediawiki_namespace_page_url", lambda _: False)
    payloads = {
        root: (
            b'<html><body><link rel="stylesheet" '
            b'href="/Module_talk:Infobox/styles.css"></body></html>'
        ),
        talk: b"<!DOCTYPE html><html><body>Talk page</body></html>",
    }
    monkeypatch.setattr(
        module, "fetch_bytes", lambda url, *, language="en": payloads[url],
    )
    failed = module.download_wiki(root, tmp_path, site=site, checkpoint_file=checkpoint)
    assert len(failed.failures) == 1
    original = (tmp_path / "Main_Page.html").read_text()
    assert 'styles.css"' in original

    monkeypatch.setattr(module, "is_mediawiki_namespace_page_url", original_classifier)
    monkeypatch.setattr(
        module, "fetch_all_page_title_batch",
        lambda **_: (_ for _ in ()).throw(AssertionError("should not enumerate")),
    )
    completed = module.download_wiki(
        root, tmp_path, site=site, checkpoint_file=checkpoint,
        retry_failed_only=True,
    )
    assert completed.failures == ()
    path = module.local_relative_path(talk, site=site)
    assert (tmp_path / path).read_bytes() == payloads[talk]
    root_text = (tmp_path / "Main_Page.html").read_text()
    assert f'href="{path}"' in root_text
    assert not re.search(r'href="[^"]+/styles\.css"', root_text)
    assert read_crawl_checkpoint(
        checkpoint, work=tmp_path, site=site.key, language="en", root_url=root,
    )["failed"] == {}


def test_main_retry_failed_only_publishes_complete_snapshot_without_checkpoint_member(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    site = module.HUMAN_SPHERE_SITE
    root = site.root_urls["en"]
    missing = "https://www.human-sphere.com/Missing"
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    monkeypatch.setattr(module, "fetch_all_page_title_batch", lambda **_: (("Main Page",), None))
    payloads = {root: b'<html><body><a href="/Missing">Missing</a></body></html>'}
    calls: list[str] = []

    def fetch(url: str, *, language: str = "en") -> bytes:
        calls.append(url)
        if url not in payloads:
            raise OSError("missing")
        return payloads[url]

    monkeypatch.setattr(module, "fetch_bytes", fetch)
    base_args = ["--site", "human-sphere", "--work-root", "work",
                 "--root", "archives", "--manifest-dir", "manifests"]
    assert module.main(base_args) == 1
    work = next((tmp_path / "work").glob("*.work"))
    assert (work / CHECKPOINT_FILENAME).is_file()
    assert list((tmp_path / "archives").glob("*.zip")) == []

    calls.clear()
    payloads[missing] = b"<html><body>Recovered</body></html>"
    monkeypatch.setattr(
        module, "fetch_all_page_title_batch",
        lambda **_: (_ for _ in ()).throw(AssertionError("no discovery")),
    )
    assert module.main(base_args + ["--resume-work", str(work), "--retry-failed-only"]) == 0
    assert calls == [missing]
    archive = next((tmp_path / "archives").glob("*.zip"))
    with zipfile.ZipFile(archive) as bundle:
        assert set(bundle.namelist()) == {"Main_Page.html", "Missing.html"}
        assert bundle.testzip() is None
    assert len(list((tmp_path / "manifests").glob("*.json"))) == 1
    assert not work.exists()


def test_cli_rejects_retry_without_work_directory(capsys) -> None:
    assert module.main(["--retry-failed-only"]) == 1
    assert "--retry-failed-only requires --resume-work" in capsys.readouterr().err



def test_checkpoint_resumes_interrupted_allpages_from_continuation(
    tmp_path: Path, monkeypatch,
) -> None:
    site = module.HUMAN_SPHERE_SITE
    root = site.root_urls["en"]
    work = tmp_path
    checkpoint = work / CHECKPOINT_FILENAME
    calls: list[str | None] = []
    fetches: list[str] = []
    interrupted = True
    monkeypatch.setattr(module.time, "sleep", lambda _: None)

    def batch(*, language: str, continuation: str | None, site: object):
        nonlocal interrupted
        calls.append(continuation)
        if continuation is None:
            return ("Main Page", "Alpha"), "next-batch"
        assert continuation == "next-batch"
        if interrupted:
            interrupted = False
            raise OSError("connection lost during enumeration")
        return ("Beta",), None

    monkeypatch.setattr(module, "fetch_all_page_title_batch", batch)

    def fetch(url: str, *, language: str = "en") -> bytes:
        fetches.append(url)
        return b"<html><body>Present</body></html>"

    monkeypatch.setattr(module, "fetch_bytes", fetch)
    first = module.download_wiki(
        root, work, site=site, checkpoint_file=checkpoint,
    )
    assert len(first.failures) == 1
    assert "connection lost" in first.failures[0].error
    assert fetches == []
    state = read_crawl_checkpoint(
        checkpoint, work=work, site=site.key, language="en",
        root_url=root, require_complete=False,
    )
    assert state["phase"] == "enumerating"
    assert state["enumerationContinuation"] == "next-batch"
    assert state["queue"] == [[root, False], [
        "https://www.human-sphere.com/Alpha", False,
    ]]
    with pytest.raises(ValueError, match="not completed discovery"):
        read_crawl_checkpoint(
            checkpoint, work=work, site=site.key, language="en",
            root_url=root,
        )
    with pytest.raises(ValueError, match="not completed discovery"):
        module.download_wiki(
            root, work, site=site, checkpoint_file=checkpoint,
            retry_failed_only=True,
        )

    result = module.download_wiki(
        root, work, site=site, checkpoint_file=checkpoint,
    )
    assert result.failures == ()
    assert calls == [None, "next-batch", "next-batch"]
    assert fetches == [
        root,
        "https://www.human-sphere.com/Alpha",
        "https://www.human-sphere.com/Beta",
    ]
    assert len(result.files) == 3
    complete = read_crawl_checkpoint(
        checkpoint, work=work, site=site.key, language="en", root_url=root,
    )
    assert complete["phase"] == "complete"
    assert complete["queue"] == []
    assert complete["enumerationContinuation"] is None


def test_checkpoint_resumes_interrupted_queue_without_refetching_saved_pages(
    tmp_path: Path, monkeypatch,
) -> None:
    root = module.ROOT_URL
    urls = [f"https://infinitythewiki.com/Unit_{n:03d}" for n in range(104)]
    checkpoint = tmp_path / CHECKPOINT_FILENAME
    calls: list[str] = []
    interrupt_once = True
    markup = ("<html><body>" + "".join(
        f'<a href="/Unit_{n:03d}">Unit</a>' for n in range(104)
    ) + "</body></html>").encode()

    def fetch(url: str, *, language: str = "en") -> bytes:
        nonlocal interrupt_once
        calls.append(url)
        if interrupt_once and len(calls) == 103:
            interrupt_once = False
            raise KeyboardInterrupt()
        return markup if url == root else b"<html><body>Unit</body></html>"

    monkeypatch.setattr(module, "fetch_bytes", fetch)
    with pytest.raises(KeyboardInterrupt):
        module.download_wiki(root, tmp_path, checkpoint_file=checkpoint)

    state = read_crawl_checkpoint(
        checkpoint, work=tmp_path, site=module.DEFAULT_SITE.key,
        language="en", root_url=root, require_complete=False,
    )
    assert state["phase"] == "crawling"
    assert len(state["saved"]) == 100
    assert len(state["queue"]) == 5
    committed = [root, *urls[:99]]
    before_resume = list(calls)

    result = module.download_wiki(root, tmp_path, checkpoint_file=checkpoint)
    assert result.failures == ()
    assert calls[len(before_resume):] == urls[99:]
    assert all(calls.count(url) == 1 for url in committed)
    assert len(result.files) == 105
    assert read_crawl_checkpoint(
        checkpoint, work=tmp_path, site=module.DEFAULT_SITE.key,
        language="en", root_url=root,
    )["phase"] == "complete"


def test_version_one_completed_checkpoint_remains_usable(
    tmp_path: Path, monkeypatch,
) -> None:
    site = module.HUMAN_SPHERE_SITE
    root = site.root_urls["en"]
    checkpoint = tmp_path / CHECKPOINT_FILENAME
    monkeypatch.setattr(
        module, "fetch_all_page_title_batch", lambda **_: (("Main Page",), None),
    )
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        module, "fetch_bytes",
        lambda url, *, language="en": b"<html><body>Content</body></html>",
    )
    assert module.download_wiki(
        root, tmp_path, site=site, checkpoint_file=checkpoint,
    ).failures == ()
    doc = json.loads(checkpoint.read_text())
    doc["version"] = 1
    for key in ("phase", "queue", "enumerationContinuation"):
        doc.pop(key)
    checkpoint.write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(
        module, "fetch_all_page_title_batch",
        lambda **_: (_ for _ in ()).throw(AssertionError("no discovery")),
    )
    assert module.download_wiki(
        root, tmp_path, site=site, checkpoint_file=checkpoint,
        retry_failed_only=True,
    ).failures == ()


def test_checkpoint_rejects_corrupt_pending_queue_before_fetch(
    tmp_path: Path, monkeypatch,
) -> None:
    site = module.HUMAN_SPHERE_SITE
    root = site.root_urls["en"]
    checkpoint = tmp_path / CHECKPOINT_FILENAME
    monkeypatch.setattr(
        module, "fetch_all_page_title_batch",
        lambda **_: (("Main Page",), None),
    )
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        module, "fetch_bytes",
        lambda url, *, language="en": b"<html><body>Content</body></html>",
    )
    module.download_wiki(root, tmp_path, site=site, checkpoint_file=checkpoint)
    doc = json.loads(checkpoint.read_text())
    doc["phase"] = "crawling"
    doc["complete"] = False
    doc["queue"] = [["https://www.human-sphere.com/Unknown", False]]
    checkpoint.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid or inconsistent"):
        module.download_wiki(root, tmp_path, site=site, checkpoint_file=checkpoint)



def test_main_interrupt_retains_work_and_checkpoint(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    site = module.HUMAN_SPHERE_SITE
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        module, "fetch_all_page_title_batch",
        lambda **_: (("Main Page", "Page_One"), None),
    )
    calls = []

    def fetch(url: str, *, language: str = "en") -> bytes:
        calls.append(url)
        if url == site.root_urls["en"]:
            return b'<html><body><a href="/Page_One">Page</a></body></html>'
        raise KeyboardInterrupt()

    monkeypatch.setattr(module, "fetch_bytes", fetch)
    assert module.main([
        "--site", "human-sphere", "--work-root", "local/work/wiki",
        "--root", "archives", "--manifest-dir", "manifests",
    ]) == 130
    work = next((tmp_path / "local/work/wiki").glob("*.work"))
    checkpoint = read_crawl_checkpoint(
        work / CHECKPOINT_FILENAME,
        work=work,
        site=site.key,
        language="en",
        root_url=site.root_urls["en"],
        require_complete=False,
    )
    assert checkpoint["phase"] == "crawling"
    assert work.joinpath("Main_Page.html").is_file()
    assert not list((tmp_path / "archives").glob("*.zip"))
    assert "acquisition checkpoints preserved" in capsys.readouterr().err


def test_final_enumeration_checkpoint_is_already_in_crawling_phase(
    tmp_path: Path, monkeypatch,
) -> None:
    site = module.HUMAN_SPHERE_SITE
    root = site.root_urls["en"]
    checkpoint_path = tmp_path / CHECKPOINT_FILENAME
    monkeypatch.setattr(
        module, "fetch_all_page_title_batch",
        lambda **_: (("Main Page", "Page_One"), None),
    )

    def fetch(url: str, *, language: str = "en") -> bytes:
        raise KeyboardInterrupt()

    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    monkeypatch.setattr(module, "fetch_bytes", fetch)
    with pytest.raises(KeyboardInterrupt):
        module.download_wiki(root, tmp_path, site=site, checkpoint_file=checkpoint_path)
    state = read_crawl_checkpoint(
        checkpoint_path, work=tmp_path, site=site.key,
        language="en", root_url=root, require_complete=False,
    )
    assert state["phase"] == "crawling"
    assert state["enumerationContinuation"] is None
    assert len(state["queue"]) == 2



def test_legacy_work_without_checkpoint_reuses_assets_on_normal_resume(
    tmp_path: Path, monkeypatch,
) -> None:
    site = module.HUMAN_SPHERE_SITE
    root = site.root_urls["en"]
    asset = "https://www.human-sphere.com/images/logo.png"
    stored = tmp_path / "images/logo.png"
    stored.parent.mkdir(parents=True)
    stored.write_bytes(b"\x89PNG\r\n\x1a\nimage bytes")
    checkpoint = tmp_path / CHECKPOINT_FILENAME
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        module, "fetch_all_page_title_batch",
        lambda **_: (("Main Page",), None),
    )
    fetched: list[str] = []

    def fetch(url: str, *, language: str = "en") -> bytes:
        fetched.append(url)
        if url != root:
            raise AssertionError("existing asset should be reused")
        return b'<html><body><img src="/images/logo.png"></body></html>'

    monkeypatch.setattr(module, "fetch_bytes", fetch)
    result = module.download_wiki(root, tmp_path, site=site, checkpoint_file=checkpoint)
    assert result.failures == ()
    assert fetched == [root]
    assert len(result.files) == 2
    assert checkpoint.is_file()
    assert read_crawl_checkpoint(
        checkpoint, work=tmp_path, site=site.key, language="en", root_url=root,
    )["phase"] == "complete"
