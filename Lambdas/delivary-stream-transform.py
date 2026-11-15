import base64
import json

REQUIRED_STRING_FIELDS = ["action", "httpSourceName", "httpSourceId", "httpStatus"]
REQUIRED_REQUEST_FIELDS = ["country", "httpMethod"]


def lambda_handler(event, context):
    output = []
    succeeded = 0
    failures = []
    print(event)

    # loop through records in incoming Event; each record is transformed
    # independently so one malformed record no longer aborts the whole batch
    for index, record in enumerate(event.get("records", [])):
        record_id = record.get("recordId", "records[{}]".format(index)) if isinstance(record, dict) else "records[{}]".format(index)
        try:
            message = decode_message(record)
            validate_message(message)

            print('timestamp: ', message["timestamp"])
            print('action: ', message["action"])
            print('httpSourceName: ', message["httpSourceName"])
            print('httpSourceId: ', message["httpSourceId"])
            print('httpStatus: ', message["httpStatus"])
            print('country: ', message["httpRequest"]["country"])
            print('httpMethod: ', message["httpRequest"]["httpMethod"])

            action = message["action"]
            httpSourceName = message["httpSourceName"]
            httpStatus = message["httpStatus"]
            country = message["httpRequest"]["country"]
            httpMethod = message["httpRequest"]["httpMethod"]

            K_action, B_action = filter_action(action) # ALLOW, CAPTCHA, Challenge, Count, BLOCK
            K_source, UNK_source = filter_httpSourceName(httpSourceName) # ALB, APIGW, APPSYNC, CF, UNK
            G_httpstatus, B_httpstatus = filter_httpStatus(httpStatus) # two, three, four, five
            K_country, UNK_country = filter_country(country) # US, UK, IN, UNK_country
            common_method, uncommon_method = filter_httpMethod(httpMethod) # common_method, uncommon_method

            # append new fields in message dict
            message["K_action"] = K_action
            message["B_action"] = B_action
            message["K_source"] = K_source
            message["UNK_source"] = UNK_source
            message["G_httpstatus"] = G_httpstatus
            message["B_httpstatus"] = B_httpstatus
            message["K_country"] = K_country
            message["UNK_country"] = UNK_country
            message["common_method"] = common_method
            message["uncommon_method"] = uncommon_method

            # base64-encoding
            data = base64.b64encode(json.dumps(message).encode('utf-8'))

            output_record = {
                "recordId": record['recordId'], # retain same record id from the Kinesis data Firehose
                "result": "Ok",
                "data": data.decode('utf-8')
            }
            output.append(output_record)
            succeeded += 1
        except Exception as exc:
            # malformed record (missing/wrong-typed fields, bad base64/JSON):
            # mark only this record as failed, keep its original payload, and
            # let Firehose route it to the error prefix instead of dropping the batch
            reason = "{}: {}".format(type(exc).__name__, exc)
            print("failed to transform {}: {}".format(record_id, reason))
            failures.append({"recordId": record_id, "reason": reason})
            output.append({
                "recordId": record_id,
                "result": "ProcessingFailed",
                "data": record.get("data", "") if isinstance(record, dict) else ""
            })

    summary = {
        "total": succeeded + len(failures),
        "succeeded": succeeded,
        "failed": len(failures),
        "failures": failures,
    }
    print("transform summary: {}".format(json.dumps(summary)))

    return {"records": output, "summary": summary}


def decode_message(record):
    """Decode one Firehose record back into the log message dict."""
    if not isinstance(record, dict):
        raise ValueError("record is not an object")
    if "data" not in record:
        raise ValueError("record has no 'data' field")
    if "recordId" not in record:
        raise ValueError("record has no 'recordId' field")

    try:
        raw = base64.b64decode(record["data"], validate=True)
    except Exception:
        raise ValueError("record data is not valid base64")

    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("record data is not valid JSON: {}".format(exc))

    # the producer ships the log file as a JSON-encoded string, so the first
    # json.loads may unwrap one level of string encoding
    if isinstance(decoded, str):
        try:
            decoded = json.loads(decoded)
        except json.JSONDecodeError as exc:
            raise ValueError("inner log payload is not valid JSON: {}".format(exc))

    if not isinstance(decoded, dict):
        raise ValueError("log payload is not a JSON object")
    return decoded


def validate_message(message):
    """Ensure every field the transform relies on exists and has the expected type."""
    if "timestamp" not in message:
        raise ValueError("missing required field: timestamp")
    for field in REQUIRED_STRING_FIELDS:
        if field not in message:
            raise ValueError("missing required field: {}".format(field))
        if not isinstance(message[field], str):
            raise ValueError(
                "field {} must be a string, got {}".format(field, type(message[field]).__name__))
    if not message["httpStatus"]:
        raise ValueError("field httpStatus must not be empty")

    if "httpRequest" not in message:
        raise ValueError("missing required field: httpRequest")
    if not isinstance(message["httpRequest"], dict):
        raise ValueError(
            "field httpRequest must be an object, got {}".format(type(message["httpRequest"]).__name__))
    for field in REQUIRED_REQUEST_FIELDS:
        if field not in message["httpRequest"]:
            raise ValueError("missing required field: httpRequest.{}".format(field))
        if not isinstance(message["httpRequest"][field], str):
            raise ValueError(
                "field httpRequest.{} must be a string, got {}".format(
                    field, type(message["httpRequest"][field]).__name__))


def filter_action(action):
    K_action, B_action = 0, 0

    if action in ["ALLOW", "CAPTCHA", "Challenge", "Count"]:
        K_action = 1
    elif action == "BLOCK":
        # Anomaly
        B_action = 1

    return K_action, B_action

def filter_httpSourceName(httpSourceName):
    K_source, UNK_source = 0, 0

    if httpSourceName == "ALB":
        K_source = 1
    elif httpSourceName == "APIGW":
        K_source = 1
    elif httpSourceName == "APPSYNC":
        K_source = 1
    elif httpSourceName == "CF":
        K_source = 1
    else:
        UNK_source = 1

    return K_source, UNK_source

def filter_httpStatus(httpStatus):
    G_httpstatus, B_httpstatus = 0, 0

    if httpStatus[0] == "2":
        G_httpstatus = 1
    elif httpStatus[0] == "3":
        G_httpstatus = 1
    elif httpStatus[0] == "4":
        B_httpstatus = 1
    elif httpStatus[0] == "5":
        B_httpstatus = 1

    return G_httpstatus, B_httpstatus

def filter_country(country):
    K_country, UNK_country = 0, 0

    if country == "US":
        K_country = 1
    elif country == "UK":
        K_country = 1
    elif country == "IN":
        K_country = 1
    else:
        UNK_country = 1

    return K_country, UNK_country

def filter_httpMethod(httpMethod):
    common_method, uncommon_method = 0, 0

    if httpMethod == "DELETE":
        uncommon_method = 1
    else:
        common_method = 1

    return common_method, uncommon_method
