from airflow.exceptions import AirflowException
from airflow.models.baseoperator import BaseOperator
from airflow.utils.decorators import apply_defaults

from log import log
from suite_connect.settlement import (
    FAILED,
    NO_DATA,
    STATUS_XCOM_KEY,
    SUCCESS,
    SUMMARY_XCOM_KEY,
    format_summary,
    has_failures,
    summarize_subquery_statuses,
)


@log
class NetSuiteExtractionSettlementOperator(BaseOperator):
    """Settle a NetSuite extraction group once every subquery has finished.

    Each :class:`NetSuiteToS3Operator` in the group pushes its own settlement
    to XCom.  This operator collects them, reports how many subqueries
    succeeded, returned no data, or failed, and fails the group when any
    subquery failed or never reported a status.  That keeps a partial
    extraction from being mistaken for a complete backup.
    """

    @apply_defaults
    def __init__(self, subquery_task_ids, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.subquery_task_ids = list(subquery_task_ids)

    def __repr__(self):
        return (
            f'Settling {len(self.subquery_task_ids)} NetSuite subqueries: '
            f'{self.subquery_task_ids}'
        )

    def _pull_statuses(self, context):
        task_instance = context.get('ti')
        statuses = []

        for task_id in self.subquery_task_ids:
            status = None
            if task_instance is not None:
                try:
                    status = task_instance.xcom_pull(
                        task_ids=task_id,
                        key=STATUS_XCOM_KEY
                    )
                except Exception as err:
                    self.logger.warning(
                        f'Could not read settlement status for {task_id}: {err}'
                    )
            statuses.append(status)

        return statuses

    def execute(self, context=None):
        context = context or {}
        statuses = self._pull_statuses(context)
        summary = summarize_subquery_statuses(
            statuses,
            task_ids=self.subquery_task_ids
        )

        message = format_summary(summary)
        self.logger.info(message)

        self._push_summary(context, summary)

        if has_failures(summary):
            raise AirflowException(
                'Incomplete NetSuite extraction; not all subqueries were '
                f'settled successfully.\n{message}'
            )

        self.logger.info('All NetSuite subqueries settled without failures.')
        return summary

    def _push_summary(self, context, summary):
        task_instance = context.get('ti')
        if task_instance is None:
            return

        try:
            task_instance.xcom_push(
                key=SUMMARY_XCOM_KEY,
                value={
                    'counts': summary['counts'],
                    SUCCESS: summary[SUCCESS],
                    NO_DATA: summary[NO_DATA],
                    FAILED: summary[FAILED],
                    'missing': summary['missing'],
                    'errors': summary['errors'],
                }
            )
        except Exception as err:
            self.logger.warning(f'Could not push settlement summary: {err}')
