from fdsx.core.paths import parse_jsonpath


class TestParseJsonpath:
    def test_empty_string(self):
        assert parse_jsonpath("") == []

    def test_single_key(self):
        assert parse_jsonpath("plan") == ["plan"]

    def test_dot_notation(self):
        assert parse_jsonpath("user.name") == ["user", "name"]

    def test_dot_notation_three_levels(self):
        assert parse_jsonpath("a.b.c") == ["a", "b", "c"]

    def test_bracket_integer_index(self):
        assert parse_jsonpath("items[0]") == ["items", 0]

    def test_bracket_integer_index_multi_digit(self):
        assert parse_jsonpath("items[12]") == ["items", 12]

    def test_bracket_quoted_key_double_quotes(self):
        assert parse_jsonpath('data["key"]') == ["data", "key"]

    def test_bracket_quoted_key_single_quotes(self):
        assert parse_jsonpath("data['key']") == ["data", "key"]

    def test_mixed_dot_and_bracket(self):
        assert parse_jsonpath("reviews[0].summary") == ["reviews", 0, "summary"]

    def test_mixed_multiple_brackets(self):
        assert parse_jsonpath("a[0][1]") == ["a", 0, 1]

    def test_dot_after_bracket(self):
        assert parse_jsonpath("arr[2].field") == ["arr", 2, "field"]

    def test_leading_dot_skipped(self):
        # a leading dot is ignored (no empty segment produced before the first key)
        result = parse_jsonpath(".a")
        assert result == ["a"]

    def test_multiple_dots(self):
        assert parse_jsonpath("a.b.c.d") == ["a", "b", "c", "d"]

    def test_bracket_at_start(self):
        assert parse_jsonpath("[0]") == [0]
