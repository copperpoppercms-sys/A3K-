import pytest

from deobf import Result, deobfuscate


@pytest.mark.parametrize('result,kind,status,prefix', [
    (Result(source='bad syntax', stop_reason='syntax-error'), 'unexecuted-source', 'not-recovered', 'unrecovered_'),
    (Result(source='opaque', changed=True, obf_after=50), 'still-obfuscated-source', 'not-recovered', 'unrecovered_'),
    (Result(source='opaque', recon='-- reconstructed observable behaviour\nprint(1)', obf_after=50), 'behavior-trace', 'behavior-only', 'trace_'),
    (Result(source='loader()', unresolved_urls=['https://example.com/a.lua']), 'partial-source', 'partial', 'deobf_'),
    (Result(source='print(1)', changed=True), 'cleaned-source', 'source-output', 'deobf_'),
    (Result(source='print(1)'), 'unchanged-source', 'unchanged', 'deobf_'),
])
def test_reports_headers_and_discord_files_distinguish_recovery(result, kind, status, prefix):
    from bot.bot import _result_file, _summary

    assert result.output_kind == kind and result.recovery_status == status
    report = result.to_report()
    assert report['output_kind'] == kind and report['recovery_status'] == status
    assert result.best.splitlines()[0] == '-- Analyzed by A3K Deobfuscator | ' + kind
    file = _result_file('sample.lua', result)
    assert file.filename == prefix + 'sample.lua' + ('.txt' if result.shows_recon else '')
    assert file.fp.read().decode() == result.best
    file.close()
    embed = _summary('sample.lua', result)
    explanation = next(field.value for field in embed.fields if field.name == 'recovery')
    if status in ('not-recovered','behavior-only'):
        assert 'not recovered' in explanation
        assert embed.colour.value == 0xFEE75C


def test_static_edits_to_an_obfuscated_script_are_not_reported_as_success():
    source = 'local a=(1+2); local env=getfenv(); local pad="' + 'x' * 2500 + '"; print(a)'
    result = deobfuscate(source, dynamic=False)
    assert result.changed
    assert result.output_kind == 'still-obfuscated-source'
    assert result.recovery_status == 'not-recovered'
    assert 'Readable source was not recovered' in result.best
