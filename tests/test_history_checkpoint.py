"""Offline coverage for independent, interruptible historical-revision acquisition."""

from __future__ import annotations

import json
import urllib.parse
import zipfile
from pathlib import Path

import pytest

from infinity_wiki_acquisition import cli as module
from infinity_wiki_acquisition.history_checkpoint import (
    HISTORY_CHECKPOINT_FILENAME,
    read_history_checkpoint,
)


PAGE = module.WikiPage(
    "https://infinitythewiki.com/BS_Attack", "BS_Attack", "BS_Attack",
)
ROOT = module.ROOT_URL


def _api_payload(
    ids: list[int], *, continuation: str | None = None,
) -> bytes:
    document: dict[str, object] = {
        "query": {
            "pages": [{
                "pageid": 45,
                "title": "BS Attack",
                "revisions": [
                    {
                        "revid": oldid,
                        "parentid": oldid - 1,
                        "timestamp": "2026-01-02T03:04:05Z",
                    }
                    for oldid in ids
                ],
            }],
        },
    }
    if continuation is not None:
        document["continue"] = {"continue": "||", "rvcontinue": continuation}
    return json.dumps(document).encode()


def _state(path: Path, work: Path) -> dict:
    return read_history_checkpoint(
        path,
        work=work,
        site=module.DEFAULT_SITE.key,
        language="en",
        pages=[[PAGE.url, PAGE.path, PAGE.title]],
    )


def test_history_resume_continues_revision_api_after_failed_batch(
    tmp_path: Path, monkeypatch,
) -> None:
    checkpoint = tmp_path / HISTORY_CHECKPOINT_FILENAME
    calls: list[str] = []
    second_batch_fails = True

    def fake_fetch(url: str, *, language: str, site: object) -> bytes:
        nonlocal second_batch_fails
        calls.append(url)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if query.get("prop") == ["revisions"]:
            if "rvcontinue" not in query:
                return _api_payload([10], continuation="next")
            assert query["rvcontinue"] == ["next"]
            if second_batch_fails:
                second_batch_fails = False
                raise OSError("revision API unavailable")
            return _api_payload([20])
        if "oldid" in query:
            return f'<html>revision {query["oldid"][0]}</html>'.encode()
        raise AssertionError(url)

    monkeypatch.setattr(module, "fetch_site_bytes", fake_fetch)
    first = module.download_wiki_history(
        (PAGE,), tmp_path, checkpoint_file=checkpoint,
    )
    assert len(first.failures) == 1
    assert "revision API unavailable" in first.failures[0].error
    state = _state(checkpoint, tmp_path)
    assert state["pageIndex"] == 0
    assert state["active"]["continuation"] == "next"
    assert state["active"]["revisions"] == [[10, 9, "2026-01-02T03:04:05Z"]]
    assert not state["complete"]

    calls.clear()
    second = module.download_wiki_history(
        (PAGE,), tmp_path, checkpoint_file=checkpoint,
    )
    assert second.failures == ()
    assert second.revision_count == 2
    assert len(calls) == 3
    assert sum("rvcontinue=next" in url for url in calls) == 1
    assert all("prop=revisions" not in url or "rvcontinue=next" in url for url in calls)
    index = json.loads((tmp_path / "_history/index.json").read_text())
    assert index["revisionCount"] == 2
    assert [item["oldid"] for item in index["pages"][0]["revisions"]] == [10, 20]
    assert _state(checkpoint, tmp_path)["complete"]
    assert len(second.files) == 3


def test_history_resume_after_interrupt_reuses_uncommitted_oldid_files(
    tmp_path: Path, monkeypatch,
) -> None:
    checkpoint = tmp_path / HISTORY_CHECKPOINT_FILENAME
    api_calls: list[str] = []
    downloaded: list[int] = []
    interrupt_once = True

    def fake_fetch(url: str, *, language: str, site: object) -> bytes:
        nonlocal interrupt_once
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if query.get("prop") == ["revisions"]:
            api_calls.append(url)
            return _api_payload(list(range(1, 106)))
        oldid = int(query["oldid"][0])
        if oldid == 103 and interrupt_once:
            interrupt_once = False
            raise KeyboardInterrupt()
        downloaded.append(oldid)
        return f"<html>oldid {oldid}</html>".encode()

    monkeypatch.setattr(module, "fetch_site_bytes", fake_fetch)
    with pytest.raises(KeyboardInterrupt):
        module.download_wiki_history(
            (PAGE,), tmp_path, checkpoint_file=checkpoint,
        )

    progress = _state(checkpoint, tmp_path)
    assert progress["active"]["enumerationComplete"]
    assert progress["active"]["downloadIndex"] == 100
    assert len(progress["saved"]) == 100
    assert len(api_calls) == 1
    assert len(downloaded) == 102

    second = module.download_wiki_history(
        (PAGE,), tmp_path, checkpoint_file=checkpoint,
    )
    assert second.failures == ()
    assert second.revision_count == 105
    assert len(api_calls) == 1
    assert downloaded == list(range(1, 106))
    assert _state(checkpoint, tmp_path)["pageIndex"] == 1


def test_history_retries_only_failed_oldid_without_reenumeration(
    tmp_path: Path, monkeypatch,
) -> None:
    checkpoint = tmp_path / HISTORY_CHECKPOINT_FILENAME
    requests: list[str] = []
    unavailable = True

    def fake_fetch(url: str, *, language: str, site: object) -> bytes:
        nonlocal unavailable
        requests.append(url)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if query.get("prop") == ["revisions"]:
            return _api_payload([10, 11])
        if query.get("oldid") == ["11"] and unavailable:
            unavailable = False
            raise OSError("oldid missing")
        return b"<html>old revision</html>"

    monkeypatch.setattr(module, "fetch_site_bytes", fake_fetch)
    first = module.download_wiki_history(
        (PAGE,), tmp_path, checkpoint_file=checkpoint,
    )
    assert len(first.failures) == 1
    assert "oldid missing" in first.failures[0].error
    assert not _state(checkpoint, tmp_path)["complete"]
    assert (tmp_path / "_history/oldid/10.html").is_file()

    requests.clear()
    second = module.download_wiki_history(
        (PAGE,), tmp_path, checkpoint_file=checkpoint,
    )
    assert second.failures == ()
    assert len(requests) == 1
    assert "oldid=11" in requests[0]
    assert second.revision_count == 2


def test_history_checkpoint_rejects_corruption_and_page_mismatch(
    tmp_path: Path, monkeypatch,
) -> None:
    checkpoint = tmp_path / HISTORY_CHECKPOINT_FILENAME
    monkeypatch.setattr(
        module, "fetch_site_bytes",
        lambda url, *, language, site: (
            _api_payload([1])
            if "prop=revisions" in url else b"<html>revision</html>"
        ),
    )
    done = module.download_wiki_history(
        (PAGE,), tmp_path, checkpoint_file=checkpoint,
    )
    assert done.failures == ()
    (tmp_path / "_history/oldid/1.html").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="missing or changed"):
        module.download_wiki_history(
            (PAGE,), tmp_path, checkpoint_file=checkpoint,
        )
    with pytest.raises(ValueError, match="incompatible"):
        read_history_checkpoint(
            checkpoint,
            work=tmp_path,
            site="human-sphere",
            language="en",
            pages=[[PAGE.url, PAGE.path, PAGE.title]],
        )


def test_main_failed_history_can_resume_and_publish_complete_zip(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    failed_once = True
    api_calls: list[str] = []

    def fake_current(
        root_url: str,
        staging: Path,
        *,
        language: str = "en",
        site=module.DEFAULT_SITE,
        progress=None,
        checkpoint_file: Path | None = None,
        retry_failed_only: bool = False,
    ) -> module.WikiDownloadResult:
        path = staging / PAGE.path
        path.write_bytes(b"<html><body>Current</body></html>")
        return module.WikiDownloadResult(files=(path,), failures=(), pages=(PAGE,))

    def fake_fetch(url: str, *, language: str, site: object) -> bytes:
        nonlocal failed_once
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if query.get("prop") == ["revisions"]:
            api_calls.append(url)
            return _api_payload([10])
        if query.get("oldid") == ["10"]:
            if failed_once:
                failed_once = False
                raise OSError("temporarily unavailable")
            return b"<html>historical revision</html>"
        raise AssertionError(url)

    monkeypatch.setattr(module, "download_wiki", fake_current)
    monkeypatch.setattr(module, "fetch_site_bytes", fake_fetch)
    args = [
        "--include-history", "--root", "archives", "--work-root", "work",
        "--manifest-dir", "manifests",
    ]
    assert module.main(args) == 1
    work = next((tmp_path / "work").glob("*.work"))
    checkpoint = work / HISTORY_CHECKPOINT_FILENAME
    assert checkpoint.is_file()
    assert not list((tmp_path / "archives").glob("*.zip"))
    assert not _state(checkpoint, work)["complete"]
    assert len(api_calls) == 1

    assert module.main(args + ["--resume-work", str(work)]) == 0
    assert len(api_calls) == 1
    archive = next((tmp_path / "archives").glob("*.zip"))
    with zipfile.ZipFile(archive) as bundle:
        assert set(bundle.namelist()) == {
            PAGE.path, "_history/index.json", "_history/oldid/10.html",
        }
        assert bundle.testzip() is None
    assert len(list((tmp_path / "manifests").glob("*.json"))) == 1
    assert not work.exists()



def test_complete_history_checkpoint_rejects_unindexed_revision(
    tmp_path: Path, monkeypatch,
) -> None:
    checkpoint = tmp_path / HISTORY_CHECKPOINT_FILENAME
    monkeypatch.setattr(
        module, "fetch_site_bytes",
        lambda url, *, language, site: (
            _api_payload([10])
            if "prop=revisions" in url else b"<html>old revision</html>"
        ),
    )
    assert module.download_wiki_history(
        (PAGE,), tmp_path, checkpoint_file=checkpoint,
    ).failures == ()
    state = json.loads(checkpoint.read_text(encoding="utf-8"))
    state["completedPages"][0]["revisions"].clear()
    checkpoint.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError, match="missing or unindexed revisions"):
        module.download_wiki_history(
            (PAGE,), tmp_path, checkpoint_file=checkpoint,
        )


def test_history_interrupt_during_api_pagination_resumes_from_token(
    tmp_path: Path, monkeypatch,
) -> None:
    checkpoint = tmp_path / HISTORY_CHECKPOINT_FILENAME
    tokens: list[str | None] = []
    interrupt_once = True

    def fake_fetch(url: str, *, language: str, site: object) -> bytes:
        nonlocal interrupt_once
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if query.get("prop") == ["revisions"]:
            token = query.get("rvcontinue", [None])[0]
            tokens.append(token)
            if token is None:
                return _api_payload([10], continuation="resume-token")
            if interrupt_once:
                interrupt_once = False
                raise KeyboardInterrupt()
            return _api_payload([20])
        assert "oldid" in query
        return b"<html>valid revision</html>"

    monkeypatch.setattr(module, "fetch_site_bytes", fake_fetch)
    with pytest.raises(KeyboardInterrupt):
        module.download_wiki_history(
            (PAGE,), tmp_path, checkpoint_file=checkpoint,
        )
    state = _state(checkpoint, tmp_path)
    assert state["active"]["continuation"] == "resume-token"
    assert len(state["active"]["revisions"]) == 1
    assert not state["complete"]

    result = module.download_wiki_history(
        (PAGE,), tmp_path, checkpoint_file=checkpoint,
    )
    assert result.failures == ()
    assert tokens == [None, "resume-token", "resume-token"]
    assert result.revision_count == 2
