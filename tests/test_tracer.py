"""Tests for flowview.tracer — @trace decorator with proxy wrapping."""

import asyncio

import polars as pl
import pytest

import flowview as fv


def _clean(df: pl.DataFrame) -> pl.DataFrame:
    """Dummy step: rename columns to lowercase."""
    return df.rename({col: col.lower() for col in df.columns})


def _filter_positive(df: pl.DataFrame) -> pl.DataFrame:
    """Dummy step: filter rows where value > 0."""
    return df.filter(pl.col("value") > 0)


def _add_double(df: pl.DataFrame) -> pl.DataFrame:
    """Dummy step: add a 'double' column."""
    return df.with_columns((pl.col("value") * 2).alias("double"))


class TestTraceDecorator:
    def test_basic_trace(self, capsys):
        @fv.trace
        def pipeline(df: pl.DataFrame) -> pl.DataFrame:
            return df.pipe(_filter_positive).pipe(_add_double)

        df = pl.DataFrame({"value": [-1, 0, 1, 2, 3]})
        result = pipeline(df)

        # The pipeline still works correctly
        assert result.shape == (3, 2)
        assert "double" in result.columns
        assert result["double"].to_list() == [2, 4, 6]

    def test_trace_with_options(self, capsys):
        @fv.trace(sample_rows=2, show_schema=True)
        def pipeline(df: pl.DataFrame) -> pl.DataFrame:
            return df.pipe(_add_double)

        df = pl.DataFrame({"value": [10, 20, 30]})
        result = pipeline(df)

        assert result.shape == (3, 2)

    def test_multi_step_pipeline(self, capsys):
        @fv.trace
        def pipeline(df: pl.DataFrame) -> pl.DataFrame:
            return df.pipe(_clean).pipe(_filter_positive).pipe(_add_double)

        df = pl.DataFrame({"VALUE": [-1, 0, 1, 2, 3]})
        result = pipeline(df)

        assert result.shape == (3, 2)
        assert list(result.columns) == ["value", "double"]

    def test_filter_now_captured(self, capsys):
        """After proxy migration, direct method calls like .filter()
        are captured as steps (unlike the old monkey-patching approach)."""

        @fv.trace
        def pipeline(df: pl.DataFrame) -> pl.DataFrame:
            return df.filter(pl.col("value") > 0)

        df = pl.DataFrame({"value": [1, 2, 3]})
        result = pipeline(df)

        assert result.shape == (3, 1)
        # Verify output includes trace (filter step should appear)
        captured = capsys.readouterr()
        assert "filter" in captured.out

    def test_return_value_unchanged(self):
        @fv.trace
        def pipeline(df: pl.DataFrame) -> pl.DataFrame:
            return df.pipe(_add_double)

        df = pl.DataFrame({"value": [10, 20]})
        result = pipeline(df)

        expected = df.with_columns((pl.col("value") * 2).alias("double"))
        assert result.equals(expected)

    def test_result_is_real_dataframe(self):
        """The decorator should always return a real pl.DataFrame,
        never a TracedDataFrame proxy."""

        @fv.trace
        def pipeline(df: pl.DataFrame) -> pl.DataFrame:
            return df.filter(pl.col("value") > 0)

        df = pl.DataFrame({"value": [1, 2, 3]})
        result = pipeline(df)

        assert type(result) is pl.DataFrame

    def test_method_chain_tracing(self, capsys):
        @fv.trace
        def pipeline(df: pl.DataFrame) -> pl.DataFrame:
            return (
                df.filter(pl.col("value") > 0)
                .with_columns((pl.col("value") * 2).alias("double"))
                .sort("double", descending=True)
            )

        df = pl.DataFrame({"value": [-1, 0, 1, 2, 3]})
        result = pipeline(df)

        assert result.shape == (3, 2)
        assert result["double"].to_list() == [6, 4, 2]

        captured = capsys.readouterr()
        assert "filter" in captured.out
        assert "with_columns" in captured.out
        assert "sort" in captured.out

    def test_mixed_pipe_and_method_chain(self, capsys):
        @fv.trace
        def pipeline(df: pl.DataFrame) -> pl.DataFrame:
            return df.filter(pl.col("value") > 0).pipe(_add_double).sort("double")

        df = pl.DataFrame({"value": [-1, 0, 1, 2, 3]})
        result = pipeline(df)

        assert result.shape == (3, 2)

        captured = capsys.readouterr()
        assert "filter" in captured.out
        assert "_add_double" in captured.out
        assert "sort" in captured.out

    def test_error_propagates(self):
        @fv.trace
        def pipeline(df: pl.DataFrame) -> pl.DataFrame:
            return df.filter(pl.col("nonexistent") > 0)

        df = pl.DataFrame({"value": [1, 2, 3]})
        with pytest.raises(pl.exceptions.ColumnNotFoundError):
            pipeline(df)

    def test_df_as_keyword_arg(self, capsys):
        @fv.trace
        def pipeline(*, data: pl.DataFrame) -> pl.DataFrame:
            return data.filter(pl.col("value") > 0)

        df = pl.DataFrame({"value": [1, 2, 3]})
        result = pipeline(data=df)

        assert type(result) is pl.DataFrame
        assert result.shape == (3, 1)

    def test_multiple_dataframes_only_first_traced(self):
        """Only the first DataFrame argument should be wrapped in a proxy."""

        @fv.trace
        def pipeline(df: pl.DataFrame, other: pl.DataFrame) -> pl.DataFrame:
            return df.join(other, on="id", how="left")

        df = pl.DataFrame({"id": [1, 2, 3], "value": [10, 20, 30]})
        other = pl.DataFrame({"id": [1, 2], "label": ["a", "b"]})
        result = pipeline(df, other)

        assert type(result) is pl.DataFrame
        assert result.shape[0] == 3
        assert "label" in result.columns

    def test_async_function(self):
        """Async traced functions should return a real DataFrame."""

        @fv.trace
        async def pipeline(df: pl.DataFrame) -> pl.DataFrame:
            return df.filter(pl.col("value") > 1)

        df = pl.DataFrame({"value": [1, 2, 3]})
        result = asyncio.run(pipeline(df))

        assert type(result) is pl.DataFrame
        assert result.shape == (2, 1)
        assert result["value"].to_list() == [2, 3]

    def test_async_with_options(self):
        @fv.trace(sample_rows=2)
        async def pipeline(df: pl.DataFrame) -> pl.DataFrame:
            return df.with_columns((pl.col("value") * 2).alias("double"))

        df = pl.DataFrame({"value": [10, 20]})
        result = asyncio.run(pipeline(df))

        assert type(result) is pl.DataFrame
        assert "double" in result.columns

    def test_nested_trace_no_double_wrapping(self, capsys):
        """Nested @fv.trace should not double-wrap the proxy."""

        @fv.trace
        def inner(df: pl.DataFrame) -> pl.DataFrame:
            return df.filter(pl.col("value") > 1)

        @fv.trace
        def outer(df: pl.DataFrame) -> pl.DataFrame:
            return inner(df)

        df = pl.DataFrame({"value": [1, 2, 3]})
        result = outer(df)

        assert type(result) is pl.DataFrame
        assert result.shape == (2, 1)

    def test_unwrap_exported(self):
        """unwrap() should be accessible from the public API."""
        assert hasattr(fv, "unwrap")
        df = pl.DataFrame({"a": [1]})
        assert fv.unwrap(df) is df


# ----------------------------------------------------------------------
# .pipe() internal tracing — acceptance scenarios
# ----------------------------------------------------------------------


def _capture_trace(pipeline, df):
    """Run a decorated pipeline and return (result, captured PipelineTrace)."""
    import flowview.tracer as tracer

    captured: list = []
    original = tracer.render_trace
    tracer.render_trace = lambda trace, config: captured.append(trace)
    try:
        result = pipeline(df)
    finally:
        tracer.render_trace = original
    return result, captured[0]


class TestPipeInternalTracing:
    def test_two_internal_transforms_plus_summary(self):
        def clean(df: pl.DataFrame) -> pl.DataFrame:
            return df.filter(pl.col("value") > 0).with_columns(
                (pl.col("value") * 2).alias("double")
            )

        @fv.trace
        def pipeline(df: pl.DataFrame) -> pl.DataFrame:
            return df.pipe(clean)

        df = pl.DataFrame({"value": [-1, 0, 1, 2, 3]})
        result, trace = _capture_trace(pipeline, df)

        names = [step.step_name for step in trace.steps]
        assert len(names) == 3
        assert "filter(" in names[0]
        assert names[1] == "with_columns(double)"
        assert names[2] == "clean"

        # Summary row count matches the final result.
        assert trace.steps[2].row_count == result.shape[0] == 3
        assert trace.steps[2].col_count == 2
        assert result["double"].to_list() == [2, 4, 6]

    def test_no_internal_transformation_summary_only(self):
        def passthrough(df: pl.DataFrame) -> pl.DataFrame:
            return df

        @fv.trace
        def pipeline(df: pl.DataFrame) -> pl.DataFrame:
            return df.pipe(passthrough)

        df = pl.DataFrame({"value": [1, 2, 3]})
        result, trace = _capture_trace(pipeline, df)

        assert [step.step_name for step in trace.steps] == ["passthrough"]
        assert result.shape == (3, 1)

    def test_exception_keeps_internal_steps_without_summary(self):
        from flowview.models import PipelineTrace
        from flowview.proxy import TracedDataFrame

        def failing(df: pl.DataFrame) -> pl.DataFrame:
            df = df.filter(pl.col("value") > 0)
            return df.filter(pl.col("missing") > 0)

        df = pl.DataFrame({"value": [-1, 0, 1, 2]})
        trace_obj = PipelineTrace(function_name="pipeline")
        proxy = TracedDataFrame(df, trace_obj, sample_rows=5)

        with pytest.raises(pl.exceptions.ColumnNotFoundError):
            proxy.pipe(failing)

        # The completed internal step survives; no function-name summary.
        assert len(trace_obj.steps) == 1
        assert "filter(" in trace_obj.steps[0].step_name
        assert all(step.step_name != "failing" for step in trace_obj.steps)

    def test_extra_proxy_args_are_unwrapped(self):
        from flowview.models import PipelineTrace
        from flowview.proxy import TracedDataFrame

        other = pl.DataFrame({"id": [1, 2], "label": ["a", "b"]})

        def join_with(
            df: pl.DataFrame, right: pl.DataFrame, *, how: str
        ) -> pl.DataFrame:
            # The extra proxy must be unwrapped before reaching the function.
            assert type(right) is pl.DataFrame
            return df.join(right, on="id", how=how)

        df = pl.DataFrame({"id": [1, 2, 3], "value": [10, 20, 30]})
        trace_obj = PipelineTrace(function_name="pipeline")
        other_proxy = TracedDataFrame(other, trace_obj, sample_rows=5)
        main_proxy = TracedDataFrame(df, trace_obj, sample_rows=5)

        out = main_proxy.pipe(join_with, other_proxy, how="left")

        assert type(fv.unwrap(out)) is pl.DataFrame
        assert "label" in out.columns
        # Internal join step plus the function-name summary.
        names = [step.step_name for step in trace_obj.steps]
        assert names == ["join(on=id, how=left)", "join_with"]

    def test_async_pipe_records_internal_steps(self):
        """Async traced functions must still trace pipe internals."""

        def clean(df: pl.DataFrame) -> pl.DataFrame:
            return df.filter(pl.col("value") > 1).with_columns(
                (pl.col("value") * 10).alias("big")
            )

        @fv.trace
        async def pipeline(df: pl.DataFrame) -> pl.DataFrame:
            return df.pipe(clean)

        import flowview.tracer as tracer

        captured: list = []
        original = tracer.render_trace
        tracer.render_trace = lambda trace, config: captured.append(trace)
        try:
            df = pl.DataFrame({"value": [1, 2, 3]})
            result = asyncio.run(pipeline(df))
        finally:
            tracer.render_trace = original

        assert type(result) is pl.DataFrame
        trace = captured[0]
        names = [step.step_name for step in trace.steps]
        assert "filter(" in names[0]
        assert names[1] == "with_columns(big)"
        assert names[2] == "clean"
        assert trace.steps[2].row_count == 2

    def test_pipe_non_dataframe_return_unchanged(self):
        def row_count(df: pl.DataFrame) -> int:
            return df.height

        @fv.trace
        def pipeline(df: pl.DataFrame) -> int:
            return df.pipe(row_count)

        df = pl.DataFrame({"value": [1, 2, 3]})
        assert pipeline(df) == 3
