import pytest

from deobf import deobfuscate, sandbox
from deobf import astclean, fetch


URL = "https://example.invalid/payload.lua"
LOADER = 'loadstring(game:HttpGet("%s"))()' % URL


def test_unfetched_loader_is_retained_and_not_reported_as_empty_recovery():
    result = deobfuscate(LOADER)
    assert result.source == LOADER
    assert result.layers == 0
    assert result.unresolved_urls == [URL]
    assert result.stop_reason == "unresolved-http"
    assert result.output_kind == "partial-source"
    assert result.to_report()["unresolved_urls"] == [URL]


def test_sandbox_distinguishes_a_supplied_response_from_an_empty_stub():
    missing = sandbox.run(LOADER)
    supplied = sandbox.run(LOADER, http={URL: 'print("payload")'})
    assert missing.unresolved_gets == [URL]
    assert supplied.unresolved_gets == []
    assert supplied.prints == ["payload"]


def test_opt_in_fetch_can_follow_two_public_loader_stages(monkeypatch):
    second = "https://example.invalid/final.lua"
    responses = {URL: 'loadstring(game:HttpGet("%s"))()' % second, second: 'print("payload")'}
    called = []

    def download(url, **options):
        called.append(url)
        return responses[url]

    monkeypatch.setattr(fetch, "fetch", download)
    result = deobfuscate(LOADER, fetch=True)
    assert result.source == 'print("payload")'
    assert result.stop_reason == "complete" and not result.unresolved_urls
    assert called == [URL, second]


@pytest.mark.parametrize("response", ["", None])
def test_failed_or_empty_download_keeps_the_loader(monkeypatch, response):
    def download(*args, **options):
        if response is None:
            raise fetch.FetchError("not available")
        return response

    monkeypatch.setattr(fetch, "fetch", download)
    result = deobfuscate(LOADER, fetch=True)
    assert result.source == LOADER
    assert result.stop_reason == "unresolved-http"
    assert result.unresolved_urls == [URL]


def test_a_deliberately_empty_literal_loader_is_still_valid():
    result = deobfuscate('loadstring("")()', dynamic=False)
    assert result.source == "" and result.layers == 1
    assert result.output_kind == "unwrapped-source"


def test_unavailable_remote_responses_cannot_validate_cleanup():
    source, reason = astclean.clean(LOADER)
    assert source is None and "remote response unavailable" in reason
