from fdsx.core.init import needs_init


class TestAutoInit:
    def test_needs_init_true_when_missing(self, tmp_path):
        result = needs_init(tmp_path)
        assert result is True

    def test_needs_init_false_when_exists(self, tmp_path):
        (tmp_path / ".fdsx").mkdir()
        result = needs_init(tmp_path)
        assert result is False

    def test_needs_init_false_when_partial(self, tmp_path):
        (tmp_path / ".fdsx").mkdir()
        result = needs_init(tmp_path)
        assert result is False
