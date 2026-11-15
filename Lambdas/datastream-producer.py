import json
import boto3


def _record_identifier(record, index):
    """Best-effort identifier for a single S3 event record, used in error reports."""
    try:
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]
        return "s3://{}/{}".format(bucket, key)
    except (KeyError, TypeError):
        return "Records[{}]".format(index)


def lambda_handler(event, context):

    kinesis = boto3.client('kinesis', region_name='us-east-1')
    s3 = boto3.client('s3', region_name='us-east-1')
    print(event)

    succeeded = 0
    failures = []

    # Each S3 event record is read and delivered independently: a failure on
    # one record (bad event shape, S3 read error, Kinesis put_record error)
    # is recorded and skipped, the remaining records keep processing.
    for index, record in enumerate(event.get("Records", [])):
        record_id = _record_identifier(record, index)
        try:
            bucket = record["s3"]["bucket"]["name"]
            key = record["s3"]["object"]["key"]

            print(bucket, key)

            log = s3.get_object(Bucket=bucket, Key=key)['Body'].read().decode('utf-8')

            response = kinesis.put_record(
                StreamName='log-stream',
                Data=json.dumps(log),
                PartitionKey='aa-bb'
            )

            print(response)
            succeeded += 1
        except Exception as exc:
            reason = "{}: {}".format(type(exc).__name__, exc)
            print("failed to deliver {}: {}".format(record_id, reason))
            failures.append({"record": record_id, "reason": reason})

    summary = {
        "total": succeeded + len(failures),
        "succeeded": succeeded,
        "failed": len(failures),
        "failures": failures,
    }
    print("delivery summary: {}".format(json.dumps(summary)))

    return {
        'statusCode': 200,
        'body': json.dumps(summary)
    }
