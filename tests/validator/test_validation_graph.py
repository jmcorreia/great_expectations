from typing import Any, Dict, Optional, Set, Tuple, Union, cast
from unittest import mock

import pandas as pd
import pytest

import great_expectations.exceptions as ge_exceptions
from great_expectations.core import IDDict
from great_expectations.core.expectation_configuration import ExpectationConfiguration
from great_expectations.execution_engine import ExecutionEngine, PandasExecutionEngine
from great_expectations.expectations.core import (
    ExpectColumnMaxToBeBetween,
    ExpectColumnValueZScoresToBeLessThan,
)
from great_expectations.validator.exception_info import ExceptionInfo
from great_expectations.validator.metric_configuration import MetricConfiguration
from great_expectations.validator.validation_graph import (
    MAX_METRIC_COMPUTATION_RETRIES,
    ExpectationValidationGraph,
    MetricEdge,
    ValidationGraph,
)


@pytest.fixture
def metric_edge(
    table_head_metric_config: MetricConfiguration,
    column_histogram_metric_config: MetricConfiguration,
) -> MetricEdge:
    return MetricEdge(
        left=table_head_metric_config, right=column_histogram_metric_config
    )


@pytest.fixture
def validation_graph_with_single_edge(metric_edge: MetricEdge) -> ValidationGraph:
    edges = [metric_edge]
    return ValidationGraph(edges=edges)


@pytest.fixture
def expect_column_values_to_be_unique_expectation_config() -> ExpectationConfiguration:
    return ExpectationConfiguration(
        expectation_type="expect_column_values_to_be_unique",
        meta={},
        kwargs={"column": "provider_id", "result_format": "BASIC"},
    )


@pytest.fixture
def expect_column_value_z_scores_to_be_less_than_expectation_config() -> ExpectationConfiguration:
    return ExpectationConfiguration(
        expectation_type="expect_column_value_z_scores_to_be_less_than",
        kwargs={
            "column": "a",
            "mostly": 0.9,
            "threshold": 4,
            "double_sided": True,
        },
    )


@pytest.fixture
def expect_column_values_to_be_unique_expectation_validation_graph(
    expect_column_values_to_be_unique_expectation_config: ExpectationConfiguration,
) -> ExpectationValidationGraph:
    return ExpectationValidationGraph(
        configuration=expect_column_values_to_be_unique_expectation_config
    )


@pytest.fixture
def expect_column_value_z_scores_to_be_less_than_expectation_validation_graph():
    class PandasExecutionEngineStub:
        pass

    PandasExecutionEngineStub.__name__ = "PandasExecutionEngine"
    pandas_execution_engine_stub = cast(ExecutionEngine, PandasExecutionEngineStub())

    expectation_configuration = ExpectationConfiguration(
        expectation_type="expect_column_value_z_scores_to_be_less_than",
        kwargs={
            "column": "a",
            "mostly": 0.9,
            "threshold": 4,
            "double_sided": True,
        },
    )
    graph = ValidationGraph(execution_engine=pandas_execution_engine_stub)
    validation_dependencies: Dict[
        str, Union[dict, Dict[str, MetricConfiguration]]
    ] = ExpectColumnValueZScoresToBeLessThan().get_validation_dependencies(
        expectation_configuration, pandas_execution_engine_stub
    )

    for metric_configuration in validation_dependencies["metrics"].values():
        graph.build_metric_dependency_graph(
            metric_configuration=metric_configuration,
            runtime_configuration=None,
        )

    return graph


def _invoke_progress_bar_using_resolve_validation_graph_with_mocked_internal_methods(
    value_to_assert: bool, show_progress_bars: Optional[bool] = None
) -> None:
    """
    This utility method creates mocked environment for progress bar tests; it then executes the method under test that
    utilizes progress bar, "ValidationGraph.resolve_validation_graph()", with composed arguments, and verifies result.
    """

    class DummyMetricConfiguration:
        pass

    class DummyExecutionEngine:
        pass

    dummy_metric_configuration = cast(MetricConfiguration, DummyMetricConfiguration)
    dummy_execution_engine = cast(ExecutionEngine, DummyExecutionEngine)

    # ValidationGraph is a complex object that requires len > 3 to not trigger tqdm
    with mock.patch(
        "great_expectations.validator.validation_graph.ValidationGraph._parse",
        return_value=(
            {},
            {},
        ),
    ), mock.patch(
        "great_expectations.validator.validation_graph.ValidationGraph.edges",
        new_callable=mock.PropertyMock,
        return_value=[
            MetricEdge(left=dummy_metric_configuration),
            MetricEdge(left=dummy_metric_configuration),
            MetricEdge(left=dummy_metric_configuration),
        ],
    ), mock.patch(
        "great_expectations.validator.validation_graph.tqdm",
    ) as mock_tqdm:
        call_args = {
            "metrics": {},
            "runtime_configuration": None,
        }
        if show_progress_bars is not None:
            call_args.update(
                {
                    "show_progress_bars": show_progress_bars,
                }
            )

        graph = ValidationGraph(execution_engine=dummy_execution_engine)
        graph.resolve_validation_graph(**call_args)
        assert mock_tqdm.called is True
        assert mock_tqdm.call_args[1]["disable"] is value_to_assert


@pytest.mark.unit
def test_ValidationGraph_init_no_input_edges() -> None:
    graph = ValidationGraph()

    assert graph.edges == []
    assert graph.edge_ids == set()


@pytest.mark.unit
def test_ValidationGraph_init_with_input_edges(
    metric_edge: MetricEdge,
) -> None:
    edges = [metric_edge]
    graph = ValidationGraph(edges=edges)

    assert graph.edges == edges
    assert graph.edge_ids == {e.id for e in edges}


@pytest.mark.unit
def test_ValidationGraph_add(metric_edge: MetricEdge) -> None:
    graph = ValidationGraph()

    assert graph.edges == []
    assert graph.edge_ids == set()

    graph.add(edge=metric_edge)

    assert graph.edges == [metric_edge]
    assert metric_edge.id in graph.edge_ids


@pytest.mark.unit
def test_ExpectationValidationGraph_update(
    expect_column_values_to_be_unique_expectation_validation_graph: ExpectationValidationGraph,
    validation_graph_with_single_edge: ValidationGraph,
) -> None:
    assert (
        len(expect_column_values_to_be_unique_expectation_validation_graph.graph.edges)
        == 0
    )

    expect_column_values_to_be_unique_expectation_validation_graph.update(
        validation_graph_with_single_edge
    )

    assert (
        len(expect_column_values_to_be_unique_expectation_validation_graph.graph.edges)
        == 1
    )


@pytest.mark.unit
def test_ExpectationValidationGraph_get_exception_info(
    expect_column_values_to_be_unique_expectation_validation_graph: ExpectationValidationGraph,
    validation_graph_with_single_edge: ValidationGraph,
    metric_edge: MetricEdge,
) -> None:
    left = metric_edge.left
    right = metric_edge.right

    left_exception = ExceptionInfo(
        exception_traceback="my first traceback",
        exception_message="my first message",
    )
    right_exception = ExceptionInfo(
        exception_traceback="my second traceback",
        exception_message="my second message",
        raised_exception=False,
    )

    metric_info = {
        left.id: {"exception_info": {left_exception}},
        right.id: {"exception_info": {right_exception}},
    }

    expect_column_values_to_be_unique_expectation_validation_graph.update(
        validation_graph_with_single_edge
    )
    exception_info = expect_column_values_to_be_unique_expectation_validation_graph.get_exception_info(
        metric_info=metric_info
    )

    assert left_exception in exception_info
    assert right_exception in exception_info


@pytest.mark.unit
def test_parse_validation_graph(
    expect_column_value_z_scores_to_be_less_than_expectation_validation_graph: ValidationGraph,
):
    available_metrics: Dict[Tuple[str, str, str], Any]

    # Parse input "ValidationGraph" object and confirm the numbers of ready and still needed metrics.
    available_metrics = {}
    (
        ready_metrics,
        needed_metrics,
    ) = expect_column_value_z_scores_to_be_less_than_expectation_validation_graph._parse(
        metrics=available_metrics
    )
    assert len(ready_metrics) == 2 and len(needed_metrics) == 9

    # Show that including "nonexistent" metric in dictionary of resolved metrics does not increase ready_metrics count.
    available_metrics = {("nonexistent", "nonexistent", "nonexistent"): "NONE"}
    (
        ready_metrics,
        needed_metrics,
    ) = expect_column_value_z_scores_to_be_less_than_expectation_validation_graph._parse(
        metrics=available_metrics
    )
    assert len(ready_metrics) == 2 and len(needed_metrics) == 9


@pytest.mark.unit
def test_populate_dependencies(
    expect_column_value_z_scores_to_be_less_than_expectation_validation_graph: ValidationGraph,
):
    assert (
        len(
            expect_column_value_z_scores_to_be_less_than_expectation_validation_graph.edges
        )
        == 33
    )


@pytest.mark.unit
def test_populate_dependencies_with_incorrect_metric_name():
    class PandasExecutionEngineStub:
        pass

    PandasExecutionEngineStub.__name__ = "PandasExecutionEngine"
    pandas_execution_engine_stub = cast(ExecutionEngine, PandasExecutionEngineStub())

    graph = ValidationGraph(execution_engine=pandas_execution_engine_stub)

    with pytest.raises(ge_exceptions.MetricProviderError) as e:
        graph.build_metric_dependency_graph(
            metric_configuration=MetricConfiguration(
                "column_values.not_a_metric", IDDict()
            ),
        )

    assert (
        e.value.message
        == "No provider found for column_values.not_a_metric using PandasExecutionEngine"
    )


@pytest.mark.integration
def test_resolve_validation_graph_with_bad_config_catch_exceptions_true():
    df = pd.DataFrame({"a": [1, 5, 22, 3, 5, 10], "b": [1, 2, 3, 4, 5, None]})

    expectation_configuration = ExpectationConfiguration(
        expectation_type="expect_column_max_to_be_between",
        kwargs={"column": "not_in_table", "min_value": 1, "max_value": 29},
    )

    runtime_configuration = {
        "catch_exceptions": True,
        "result_format": {"result_format": "BASIC"},
    }

    execution_engine = PandasExecutionEngine(batch_data_dict={"my_batch_id": df})

    validation_dependencies: Dict[
        str, MetricConfiguration
    ] = ExpectColumnMaxToBeBetween().get_validation_dependencies(
        expectation_configuration, execution_engine, runtime_configuration
    )[
        "metrics"
    ]

    graph = ValidationGraph(execution_engine=execution_engine)

    for metric_configuration in validation_dependencies.values():
        graph.build_metric_dependency_graph(
            metric_configuration=metric_configuration,
            runtime_configuration=runtime_configuration,
        )

    metrics: Dict[Tuple[str, str, str], Any] = {}
    aborted_metrics_info: Dict[
        Tuple[str, str, str],
        Dict[str, Union[MetricConfiguration, Set[ExceptionInfo], int]],
    ] = graph.resolve_validation_graph(
        metrics=metrics,
        runtime_configuration=runtime_configuration,
    )

    assert len(aborted_metrics_info) == 1

    aborted_metric_info_item = list(aborted_metrics_info.values())[0]
    assert aborted_metric_info_item["num_failures"] == MAX_METRIC_COMPUTATION_RETRIES

    assert len(aborted_metric_info_item["exception_info"]) == 1

    exception_info = next(iter(aborted_metric_info_item["exception_info"]))
    assert (
        exception_info["exception_message"]
        == 'Error: The column "not_in_table" in BatchData does not exist.'
    )


@pytest.mark.unit
def test_progress_bar_config_enabled():
    _invoke_progress_bar_using_resolve_validation_graph_with_mocked_internal_methods(
        value_to_assert=False
    )


@pytest.mark.unit
def test_progress_bar_config_disabled():
    _invoke_progress_bar_using_resolve_validation_graph_with_mocked_internal_methods(
        value_to_assert=True, show_progress_bars=False
    )
