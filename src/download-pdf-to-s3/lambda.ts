// event data:
// {
//   "Records": [
//     {
//       "eventID": "4cff18fbf6c5a7fb9db7008add5af874",
//       "eventName": "INSERT",
//       "eventVersion": "1.1",
//       "eventSource": "aws:dynamodb",
//       "awsRegion": "us-east-1",
//       "dynamodb": {
//         "ApproximateCreationDateTime": 1582405136,
//         "Keys": {
//           "itemId": {
//             "S": "5055310"
//           }
//         },
//         "NewImage": {
//           "date": {
//             "S": "02/03/2020"
//           },
//           "itemId": {
//             "S": "5055310"
//           },
//           "documentType": {
//             "S": "WELL ABANDONMENT REPORT (INTENT)"
//           },
//           "downloadUrl": {
//             "S": "http://ogccweblink.state.co.us/DownloadDocumentPDF.aspx?DocumentId=5055310"
//           }
//         },
//         "SequenceNumber": "89300000000054786958328",
//         "SizeBytes": 169,
//         "StreamViewType": "NEW_IMAGE"
//       },
//       "eventSourceARN": "arn:aws:dynamodb:us-east-1:839811712080:table/cogcc-pdf-urls/stream/2020-02-22T20:53:55.247"
//     }
//   ]
// }

import * as http from "http";
import * as https from "https";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import * as AWS from "aws-sdk";
const s3obj = new AWS.S3();
const db = new AWS.DynamoDB.DocumentClient();
const S3_BUCKET_NAME = process.env.S3_BUCKET_NAME || "";
const TABLE_NAME = process.env.TABLE_NAME || "";
const PRIMARY_KEY = process.env.PRIMARY_KEY || "itemId";

// Max time to wait for a response before failing that document.
// Overridable so the timeout path can be exercised without waiting a minute.
const REQUEST_TIMEOUT_MS = Number(process.env.REQUEST_TIMEOUT_MS) || 60 * 1000;

// Failures longer than this are truncated before being stored in DynamoDB
const MAX_FAILURE_REASON_LENGTH = 1000;

// // // //

interface DownloadSummary {
    succeeded: number;
    failed: number;
    skipped: number;
    failedDocumentIds: string[];
}

/**
 * downloadFile
 * Downloads a file from a URL and writes it to `dest`.
 * Rejects when the response is not 2xx, when the request fails, or when the
 * response / file stream errors. Never leaves a partial file behind.
 */
function downloadFile(url: string, dest: string): Promise<void> {
    return new Promise((resolve, reject) => {
        // Guards against resolving / rejecting more than once
        let settled = false;

        // Reference to the in-flight request, so a failure can abort it
        let request: http.ClientRequest | null = null;

        // Creates the destination file stream
        const file = fs.createWriteStream(dest);
        console.log("created file write stream: " + dest);

        // Removes the partial file before rejecting
        const fail = (error: Error) => {
            if (settled) {
                return;
            }
            settled = true;

            // Stops any in-flight download
            if (request) {
                request.destroy();
            }

            // Closes / destroys the write stream so the file handle is released
            file.destroy();

            // Removes any partially written file
            fs.unlink(dest, () => {
                // Ignore errors - the file may not have been created at all
            });

            reject(error);
        };

        // Resolves once the file stream has been flushed + closed
        const succeed = () => {
            if (settled) {
                return;
            }
            settled = true;
            resolve();
        };

        file.on("error", fail);

        // Picks the http / https implementation based on the URL protocol
        const get = url.startsWith("https:") ? https.get : http.get;

        // Fetches URL using HTTP(S)
        request = get(url, (response) => {
            // Pulls status code from the response
            const statusCode = response.statusCode || 0;

            // Non-2xx responses are treated as failed downloads for this
            // document. The body is drained so the socket can be reused.
            if (statusCode < 200 || statusCode >= 300) {
                response.resume();
                console.log(
                    `download-pdf-to-s3 -> [FAILED] HTTP ${statusCode} for ${url}`,
                );
                fail(new Error(`HTTP ${statusCode} response for ${url}`));
                return;
            }

            // Logs downloaded file message
            console.log("downloaded file: " + url);

            // Propagates stream errors (e.g. socket reset mid-download)
            response.on("error", fail);

            // The server can abort the response without emitting "error" on
            // every Node version
            response.on("aborted", () =>
                fail(
                    new Error(`response aborted before completion for ${url}`),
                ),
            );

            // Pipes response to file
            response.pipe(file);

            // Defines callback for stream "finish" event
            file.on("finish", function () {
                // A truncated response still ends the writable stream. Checking
                // `complete` catches bodies that were cut short without an
                // error, which would otherwise upload a corrupt PDF.
                if (response.complete === false) {
                    fail(new Error(`incomplete response body for ${url}`));
                    return;
                }

                // Closes file stream, then resolves the promise once the file
                // handle is actually closed
                file.close((closeError) => {
                    if (closeError) {
                        fail(closeError);
                        return;
                    }

                    // Logs wrote-to-file message
                    console.log("wrote to file: " + dest);
                    succeed();
                });
            });
        });

        // Propagates connection / DNS errors
        request.on("error", fail);

        // Fails the document instead of hanging forever on a stalled socket
        request.setTimeout(REQUEST_TIMEOUT_MS, () => {
            if (request) {
                request.destroy(
                    new Error(
                        `request timed out after ${REQUEST_TIMEOUT_MS}ms for ${url}`,
                    ),
                );
            }
        });
    });
}

/**
 * recordFailure
 * Persists a failure record for a single document in `pdfUrlsTable`, including
 * the documentId and the failure reason, so the failure can be identified on a
 * later run. Errors while recording are logged, never thrown, because one
 * document's bookkeeping must not stop the rest of the batch.
 */
async function recordFailure(
    documentId: string,
    reason: string,
): Promise<void> {
    try {
        await db
            .update({
                TableName: TABLE_NAME,
                Key: {
                    [PRIMARY_KEY]: documentId,
                },
                UpdateExpression:
                    "SET #status = :status, failureReason = :reason, failedAt = :failedAt",
                ExpressionAttributeNames: {
                    "#status": "status",
                    "#primaryKey": PRIMARY_KEY,
                },
                ExpressionAttributeValues: {
                    ":status": "failed",
                    ":reason": reason.slice(0, MAX_FAILURE_REASON_LENGTH),
                    ":failedAt": new Date().toISOString(),
                },
                // Only annotate an existing record - never create a stub item.
                // `downloadUrl` is deliberately left in place so the record can
                // be retried after `failureReason` is cleared.
                ConditionExpression: "attribute_exists(#primaryKey)",
            })
            .promise();

        console.log(
            `download-pdf-to-s3 -> [FAILED] recorded failure documentId=${documentId}`,
        );
    } catch (error) {
        console.log(
            `download-pdf-to-s3 -> [FAILED] could not record failure documentId=${documentId}`,
        );
        console.log(error);
    }
}

interface RecordOutcome {
    outcome: "succeeded" | "failed" | "skipped";
    documentId: string;
}

/**
 * processRecord
 * Handles a single DynamoDB stream record: downloads the PDF and uploads it to
 * S3. Returns the outcome so the handler can sum up the batch.
 */
async function processRecord(record: any): Promise<RecordOutcome> {
    const dynamodb = record && record["dynamodb"];
    const newItem = dynamodb && dynamodb["NewImage"];

    // REMOVE events / records without a NewImage carry nothing to download
    if (!newItem) {
        console.log("download-pdf-to-s3 -> [SKIPPED] record has no NewImage");
        return { outcome: "skipped", documentId: "" };
    }

    // Pulls documentId from newItem, falling back to the stream Keys
    const keyAttribute =
        (newItem[PRIMARY_KEY] && newItem[PRIMARY_KEY]["S"]) ||
        (dynamodb["Keys"] &&
            dynamodb["Keys"][PRIMARY_KEY] &&
            dynamodb["Keys"][PRIMARY_KEY]["S"]);

    // Without a documentId there is no table key to write a failure against
    if (!keyAttribute) {
        console.log("download-pdf-to-s3 -> [SKIPPED] record has no documentId");
        return { outcome: "skipped", documentId: "" };
    }

    const documentId: string = String(keyAttribute);

    // Skips documents that were already marked as failed by a previous run.
    // Clearing `failureReason` on the record makes it eligible again.
    // This also prevents the failure write from re-triggering this lambda in a
    // loop, since the failure write itself produces a stream record.
    if (newItem["failureReason"]) {
        console.log(
            `download-pdf-to-s3 -> [SKIPPED] documentId=${documentId} previously failed, clear failureReason to retry`,
        );
        return { outcome: "skipped", documentId };
    }

    // Pulls downloadUrl from newItem
    const downloadUrl =
        (newItem["downloadUrl"] && newItem["downloadUrl"]["S"]) || "";

    // A document without a downloadUrl can never succeed - record the failure
    if (!downloadUrl) {
        const reason = "missing downloadUrl";
        console.log(
            `download-pdf-to-s3 -> [FAILED] documentId=${documentId} reason=${reason}`,
        );
        await recordFailure(documentId, reason);
        return { outcome: "failed", documentId };
    }

    // Defines filename - used to save locally to lambda (in /tmp) AND in S3 bucket
    const filename = documentId + ".pdf";

    // Lambda's only writable directory is /tmp; on other platforms this falls
    // back to the OS temp directory so the handler can be exercised locally
    const tempDir = process.env.TMPDIR_PATH || os.tmpdir();
    const filepath = path.join(tempDir, filename);

    // Logs downloadUrl
    console.log(`downloadUrl: ${downloadUrl}`);
    console.log(`documentId: ${documentId}`);
    console.log(`filepath: ${filepath}`);

    try {
        // Downloads file to /tmp. Rejects on non-2xx responses + stream errors.
        await downloadFile(downloadUrl, filepath);

        // Saves new file to S3, waiting for the upload to finish before this
        // document is considered successful
        await s3obj
            .upload({
                Bucket: S3_BUCKET_NAME,
                Key: filename,
                Body: fs.readFileSync(filepath),
            })
            .promise();

        console.log(`download-pdf-to-s3 -> [SUCCESS] documentId=${documentId}`);
        return { outcome: "succeeded", documentId };
    } catch (error: any) {
        const reason =
            error && error.message ? String(error.message) : String(error);
        console.log(
            `download-pdf-to-s3 -> [FAILED] documentId=${documentId} reason=${reason}`,
        );

        // Persists the failure so it can be reconciled / retried per document
        await recordFailure(documentId, reason);
        return { outcome: "failed", documentId };
    } finally {
        // Removes the temp file so /tmp doesn't fill up across documents
        try {
            fs.unlinkSync(filepath);
        } catch (error) {
            // Ignore - the file may not exist if the download failed early
        }
    }
}

// // // //

export const handler = async (event: any = {}): Promise<DownloadSummary> => {
    // Logs start message + S3_BUCKET_NAME
    console.log("download-file --> START");
    console.log(`writing to S3 bucket: ${S3_BUCKET_NAME}`);

    // Debug event input
    console.log(JSON.stringify(event, null, 4));

    // Defines summary counters
    const summary: DownloadSummary = {
        succeeded: 0,
        failed: 0,
        skipped: 0,
        failedDocumentIds: [],
    };

    // Processes every record in the event. A single document failing never
    // stops the remaining records from being processed.
    const records: any[] = Array.isArray(event["Records"])
        ? event["Records"]
        : [];

    if (records.length === 0) {
        console.log("download-file --> no records in event");
    }

    for (const record of records) {
        try {
            const result = await processRecord(record);

            if (result.outcome === "succeeded") {
                summary.succeeded++;
            } else if (result.outcome === "failed") {
                summary.failed++;
                if (result.documentId) {
                    summary.failedDocumentIds.push(result.documentId);
                }
            } else {
                summary.skipped++;
            }
        } catch (error) {
            // Defensive: processRecord already handles its own errors, but a
            // truly unexpected error still must not abort the batch
            summary.failed++;
            console.log(
                "download-pdf-to-s3 -> [FAILED] unexpected error for record",
            );
            console.log(error);
        }
    }

    // Logs per-batch summary (succeeded / failed / skipped counts)
    console.log(`download-file --> summary ${JSON.stringify(summary)}`);

    // Logs shutdown message
    console.log("download-file --> SHUTDOWN");

    return summary;
};
