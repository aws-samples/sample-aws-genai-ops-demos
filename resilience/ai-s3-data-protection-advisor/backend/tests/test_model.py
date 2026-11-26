from s3dp import model


def test_size_label():
    assert model.size_label(None) == "Not measured"
    assert model.size_label(0) == "empty"
    assert model.size_label(0.5) == "512 GiB"
    assert model.size_label(2.5) == "2.5 TiB"
    assert model.size_label(1000) == "1000 TiB"
    assert model.size_label(1024) == "1 PiB"
    assert model.size_label(1536) == "1.5 PiB"
    assert model.size_label(20480) == "20 PiB"


def test_storage_type_mapping():
    assert model.STORAGE_TYPE_CLASS["StandardStorage"] == "STANDARD"
    assert model.STORAGE_TYPE_CLASS["DeepArchiveStorage"] == "DEEP_ARCHIVE"
    assert model.STORAGE_TYPE_CLASS["IntelligentTieringFAStorage"] == "INTELLIGENT_TIERING"


def test_standard_ia_object_overhead_is_mapped():
    assert model.STORAGE_TYPE_CLASS["StandardIAObjectOverhead"] == "STANDARD_IA"


def test_name_tokens_split_camel_case_and_separators():
    assert model.name_tokens("N2WSCpmRole") == ["n2ws", "cpm", "role"]
    assert model.name_tokens("AcmeProduction") == ["acme", "production"]
    assert model.name_tokens("hr-records_2024") == ["hr", "records", "2024"]


def test_has_word_short_words_match_whole_tokens():
    assert model.has_word("hr-records-prod", ["hr"]) is True
    assert model.has_word("chrome-assets", ["hr"]) is False
    assert model.has_word("ShipHrData", ["hr"]) is True
    assert model.has_word("acme-legaldocs", ["legal"]) is True
