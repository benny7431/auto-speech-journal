from auto_speech_journal.transcript_quality import (
    compression_ratio,
    is_pathological_repetition,
)


def test_pathological_repetition_detects_decoder_loops() -> None:
    repeated = "是, 及時, " + "何時, " * 55

    assert compression_ratio(repeated) > 2.4
    assert is_pathological_repetition(repeated)


def test_pathological_repetition_keeps_normal_and_short_repetition() -> None:
    assert not is_pathological_repetition("這是一段正常的口語轉錄，內容會包含不同的詞彙與句子。")
    assert not is_pathological_repetition("對，對，對")
