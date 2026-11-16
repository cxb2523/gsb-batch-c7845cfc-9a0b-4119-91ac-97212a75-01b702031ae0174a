from airflow.models.baseoperator import BaseOperator
from airflow.utils.decorators import apply_defaults

from log import log
from suite_connect import (
    SuiteQuery,
    SuiteExport
)
from suite_connect.settlement import (
    FAILED,
    STATUS_XCOM_KEY,
    build_status,
    describe_error,
    settle_subquery,
)


@log
class NetSuiteToS3Operator(BaseOperator):
    template_fields = ('filename',)

    @apply_defaults
    def __init__(
        self,
        search_types,
        search_type,
        search_id,
        conn_id,
        bucket_name,
        filename,
        filter_expression = None,
        columns = None,
        *args, **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.search_types = search_types
        self.search_type = search_type
        self.search_id = search_id
        self.conn_id = conn_id
        self.bucket_name = bucket_name
        self.filename = filename
        self.filter_expression = [] if filter_expression is None else filter_expression
        self.columns = [] if columns is None else columns

    def __repr__(self):
        return f'Executing search_id {self.search_id} for {self.search_type}'

    def _check_search_type(self):
        return any(search_type == self.search_type for search_type in self.search_types)

    def _check_search_id(self):
        return self.search_id.startswith('customsearch_')

    def _check_filters(self):
        if self.filter_expression:
            return all(
                operator in ('AND', 'OR')
                for (i, operator) in enumerate(self.filter_expression)
                if i % 2 != 0
            )
        return True

    def _check_columns(self):
        if self.columns:
            return type(self.columns) == list and self.columns
        return True

    def _validate_search(self):
        checks = {
            "Invalid NetSuite search type": self._check_search_type,
            "Invalid script_id property": self._check_search_id,
            "Invalid filter operator separation": self._check_filters,
            "Invalid columns and must be entered as list": self._check_columns,
        }

        for error_message, check_function in checks.items():
            if not check_function():
                raise ValueError(error_message)

    def _extract(self):
        extracted_results = SuiteQuery(
            filters=self.filter_expression,
            columns=self.columns
        ).extract_results()

        count = 'no' if extracted_results is None else len(extracted_results)
        self.logger.info(
            f'search_id {self.search_id} extracted {count} results'
        )
        return extracted_results

    def _export(self, extracted_results):
        return SuiteExport(
            conn_id = self.conn_id,
            bucket_name = self.bucket_name,
            filename = self.filename,
            results = extracted_results
        ).load_to_s3()

    def _field_values(self):
        return {
            'task_id': self.task_id,
            'search_type': self.search_type,
            'search_id': self.search_id,
            'filename': self.filename,
            'bucket_name': self.bucket_name,
        }

    def _push_settlement(self, context, status):
        task_instance = (context or {}).get('ti')
        if task_instance is None:
            return

        try:
            task_instance.xcom_push(key=STATUS_XCOM_KEY, value=status)
        except Exception as push_error:
            self.logger.warning(
                f'Could not push settlement status for search_id '
                f'{self.search_id}: {push_error}'
            )

    def _record_validation_failure(self, context, error):
        """Record a rejected search without changing the exception it raises."""
        status = build_status(
            status=FAILED,
            error=describe_error(error),
            **self._field_values()
        )
        self.logger.error(
            f'FAILED: search_id {self.search_id} was rejected before extraction: '
            f"{status['error']}"
        )
        self._push_settlement(context, status)
        return status

    def execute(self, context=None):
        try:
            self._validate_search()
        except Exception as error:
            self._record_validation_failure(context, error)
            raise

        # Each subquery settles on its own: an empty result is recorded as
        # "no data" and skips the S3 export, while an extraction or export
        # error is recorded together with its reason.  The error is not
        # re-raised here, so one broken subquery cannot abort the other
        # subqueries of the same extraction group; the enclosing
        # NetSuiteExtractionSettlementOperator reads every settlement and fails
        # the group once all subqueries have finished, so incomplete data is
        # never treated as a complete backup.
        status, _ = settle_subquery(
            extract=self._extract,
            export=self._export,
            field_values=self._field_values(),
            logger=self.logger
        )

        self._push_settlement(context, status)

        return status
