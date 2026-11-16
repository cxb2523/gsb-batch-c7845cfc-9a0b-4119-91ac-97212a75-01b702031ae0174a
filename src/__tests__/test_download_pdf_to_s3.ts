// Local verification for src/download-pdf-to-s3/lambda.ts
// Spins up a real local HTTP server to simulate good / bad responses, and
// replaces S3 + DynamoDB with fakes, so no AWS credentials are needed.

import * as http from "http";
import { AddressInfo } from "net";

// // // //
// Shared fake state (see note about `mock` prefix in the scrape test)

const mockState: {
    uploads: any[];
    uploadErrorKeys: Set<string>;
    uploadError: Error | null;
    updates: any[];
    updateError: Error | null;
    server: http.Server | null;
    baseUrl: string;
    paths: string[];
} = {
    uploads: [],
    uploadErrorKeys: new Set<string>(),
    uploadError: null,
    updates: [],
    updateError: null,
    server: null,
    baseUrl: "",
    paths: [],
};

jest.mock("aws-sdk", () => {
    class S3 {
        upload(params: any) {
            return {
                promise: async () => {
                    // Fails the upload for specific S3 keys only
                    if (
                        mockState.uploadError &&
                        mockState.uploadErrorKeys.has(params.Key)
                    ) {
                        throw mockState.uploadError;
                    }
                    mockState.uploads.push(params);
                    return {};
                },
            };
        }
    }

    class DocumentClient {
        update(params: any) {
            return {
                promise: async () => {
                    if (mockState.updateError) {
                        throw mockState.updateError;
                    }
                    mockState.updates.push(params);
                    return {};
                },
            };
        }
    }

    return { S3, DynamoDB: { DocumentClient } };
});

// // // //

const PDF_BYTES = Buffer.from("%PDF-1.4 fake pdf body");

/**
 * startServer
 * Starts a local HTTP server whose behaviour is keyed off the request path:
 *   /ok/<id>.pdf      -> 200 with a PDF body
 *   /notfound/<id>    -> 404
 *   /error/<id>       -> 500
 *   /partial/<id>     -> 200, then the socket is destroyed mid-body
 *   /hang/<id>        -> 200 headers, then never finishes
 */
async function startServer(): Promise<string> {
    const server = http.createServer((req, res) => {
        const url = req.url || "";
        mockState.paths.push(url);

        if (url.startsWith("/notfound/")) {
            res.writeHead(404, { "Content-Type": "text/html" });
            res.end("<html>not found</html>");
            return;
        }

        if (url.startsWith("/error/")) {
            res.writeHead(500, { "Content-Type": "text/html" });
            res.end("<html>server error</html>");
            return;
        }

        if (url.startsWith("/partial/")) {
            res.writeHead(200, {
                "Content-Type": "application/pdf",
                "Content-Length": String(PDF_BYTES.length * 4),
            });
            res.write(PDF_BYTES);
            // Kills the connection mid-response
            setTimeout(() => {
                res.destroy();
            }, 10);
            return;
        }

        if (url.startsWith("/hang/")) {
            res.writeHead(200, { "Content-Type": "application/pdf" });
            // Never ends
            return;
        }

        res.writeHead(200, { "Content-Type": "application/pdf" });
        res.end(PDF_BYTES);
    });

    mockState.server = server;

    await new Promise<void>((resolve) => {
        server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address() as AddressInfo;
    return `http://127.0.0.1:${address.port}`;
}

async function stopServer(): Promise<void> {
    if (!mockState.server) {
        return;
    }
    await new Promise<void>((resolve) => {
        mockState.server!.close(() => resolve());
    });
    mockState.server = null;
}

/**
 * buildRecord
 * Builds a DynamoDB stream record shaped like the real event.
 */
function buildRecord(
    documentId: string,
    downloadUrl: string,
    extra: { [key: string]: any } = {},
): any {
    const newImage: any = {
        itemId: { S: documentId },
        documentType: { S: "WELL ABANDONMENT REPORT (INTENT)" },
        date: { S: "02/03/2020" },
    };

    // Omitting `downloadUrl` lets a test cover the missing-url path
    if (downloadUrl !== "") {
        newImage["downloadUrl"] = { S: downloadUrl };
    }

    Object.keys(extra).forEach((key) => {
        newImage[key] = extra[key];
    });

    return {
        eventID: `event-${documentId}`,
        eventName: "INSERT",
        eventSource: "aws:dynamodb",
        dynamodb: {
            Keys: { itemId: { S: documentId } },
            NewImage: newImage,
            StreamViewType: "NEW_IMAGE",
        },
    };
}

function loadHandler(): (event?: any) => Promise<any> {
    let handler: any;
    jest.isolateModules(() => {
        handler = require("../download-pdf-to-s3/lambda").handler;
    });
    return handler;
}

beforeAll(async () => {
    process.env.TABLE_NAME = "cogcc-pdf-urls";
    process.env.PRIMARY_KEY = "itemId";
    process.env.S3_BUCKET_NAME = "fake-downloads-bucket";
    process.env.TMPDIR_PATH = require("os").tmpdir();

    // Short timeout so the stalled-response path fails fast in tests
    process.env.REQUEST_TIMEOUT_MS = "300";

    mockState.baseUrl = await startServer();
});

afterAll(async () => {
    await stopServer();
});

beforeEach(() => {
    mockState.uploads = [];
    mockState.uploadErrorKeys = new Set<string>();
    mockState.uploadError = null;
    mockState.updates = [];
    mockState.updateError = null;
    mockState.paths = [];
    jest.spyOn(console, "log").mockImplementation(() => undefined);
});

afterEach(() => {
    jest.restoreAllMocks();
});

// // // //

describe("download-pdf-to-s3", () => {
    test("downloads and uploads a successful document", async () => {
        const handler = loadHandler();
        const summary = await handler({
            Records: [
                buildRecord("5055310", `${mockState.baseUrl}/ok/5055310.pdf`),
            ],
        });

        expect(summary).toEqual({
            succeeded: 1,
            failed: 0,
            skipped: 0,
            failedDocumentIds: [],
        });
        expect(mockState.uploads).toHaveLength(1);
        expect(mockState.uploads[0].Bucket).toBe("fake-downloads-bucket");
        expect(mockState.uploads[0].Key).toBe("5055310.pdf");
        expect(mockState.uploads[0].Body.equals(PDF_BYTES)).toBe(true);
        expect(mockState.updates).toHaveLength(0);
    });

    test("processes every record in the event, not just the first", async () => {
        const handler = loadHandler();
        const summary = await handler({
            Records: [
                buildRecord("1", `${mockState.baseUrl}/ok/1.pdf`),
                buildRecord("2", `${mockState.baseUrl}/ok/2.pdf`),
                buildRecord("3", `${mockState.baseUrl}/ok/3.pdf`),
            ],
        });

        expect(summary.succeeded).toBe(3);
        expect(mockState.uploads.map((upload) => upload.Key)).toEqual([
            "1.pdf",
            "2.pdf",
            "3.pdf",
        ]);
    });

    test("rejects a non-2xx response and keeps going with later records", async () => {
        const handler = loadHandler();
        const summary = await handler({
            Records: [
                buildRecord("404doc", `${mockState.baseUrl}/notfound/404doc`),
                buildRecord("okdoc", `${mockState.baseUrl}/ok/okdoc.pdf`),
                buildRecord("500doc", `${mockState.baseUrl}/error/500doc`),
            ],
        });

        expect(summary.succeeded).toBe(1);
        expect(summary.failed).toBe(2);
        expect(summary.failedDocumentIds).toEqual(["404doc", "500doc"]);

        // Only the successful document reaches S3
        expect(mockState.uploads.map((upload) => upload.Key)).toEqual([
            "okdoc.pdf",
        ]);

        // Both failures are recorded, with reason and documentId
        expect(mockState.updates).toHaveLength(2);
        expect(mockState.updates[0].Key).toEqual({ itemId: "404doc" });
        expect(
            mockState.updates[0].ExpressionAttributeValues[":reason"],
        ).toContain("HTTP 404");
        expect(
            mockState.updates[1].ExpressionAttributeValues[":reason"],
        ).toContain("HTTP 500");
    });

    test("treats a stream error mid-download as a failed document", async () => {
        const handler = loadHandler();
        const summary = await handler({
            Records: [
                buildRecord(
                    "partialdoc",
                    `${mockState.baseUrl}/partial/partialdoc`,
                ),
            ],
        });

        expect(summary.succeeded).toBe(0);
        expect(summary.failed).toBe(1);
        expect(mockState.uploads).toHaveLength(0);
        expect(mockState.updates).toHaveLength(1);
        expect(mockState.updates[0].Key).toEqual({ itemId: "partialdoc" });
    });

    test("records the failure when the S3 upload fails", async () => {
        mockState.uploadError = new Error("S3 Access Denied");
        mockState.uploadErrorKeys = new Set<string>(["s3fail.pdf"]);

        const handler = loadHandler();
        const summary = await handler({
            Records: [
                buildRecord("s3fail", `${mockState.baseUrl}/ok/s3fail.pdf`),
                buildRecord("s3ok", `${mockState.baseUrl}/ok/s3ok.pdf`),
            ],
        });

        // The first upload fails, so only the second document succeeds
        expect(summary.failed).toBe(1);
        expect(summary.succeeded).toBe(1);
        expect(summary.failedDocumentIds).toEqual(["s3fail"]);
        expect(mockState.updates[0].Key).toEqual({ itemId: "s3fail" });
        expect(
            mockState.updates[0].ExpressionAttributeValues[":reason"],
        ).toContain("S3 Access Denied");
    });

    test("records a failure for a document that has no downloadUrl", async () => {
        const handler = loadHandler();
        const summary = await handler({
            Records: [buildRecord("nourl", "")],
        });

        expect(summary.failed).toBe(1);
        expect(mockState.updates[0].Key).toEqual({ itemId: "nourl" });
        expect(
            mockState.updates[0].ExpressionAttributeValues[":reason"],
        ).toContain("missing downloadUrl");
    });

    test("skips records that were already marked as failed", async () => {
        const handler = loadHandler();
        const summary = await handler({
            Records: [
                buildRecord(
                    "retrydoc",
                    `${mockState.baseUrl}/ok/retrydoc.pdf`,
                    {
                        failureReason: { S: "HTTP 404" },
                    },
                ),
            ],
        });

        expect(summary.skipped).toBe(1);
        expect(summary.succeeded).toBe(0);
        expect(mockState.paths).toHaveLength(0);
        expect(mockState.uploads).toHaveLength(0);
    });

    test("skips REMOVE events that carry no NewImage", async () => {
        const handler = loadHandler();
        const summary = await handler({
            Records: [
                {
                    eventID: "removed",
                    eventName: "REMOVE",
                    dynamodb: {
                        Keys: { itemId: { S: "gone" } },
                        StreamViewType: "NEW_IMAGE",
                    },
                },
            ],
        });

        expect(summary.skipped).toBe(1);
        expect(mockState.uploads).toHaveLength(0);
        expect(mockState.updates).toHaveLength(0);
    });

    test("failure bookkeeping never stops the batch", async () => {
        mockState.updateError = new Error("DynamoDB unavailable");

        const handler = loadHandler();
        const summary = await handler({
            Records: [
                buildRecord("bad1", `${mockState.baseUrl}/error/bad1`),
                buildRecord("good1", `${mockState.baseUrl}/ok/good1.pdf`),
            ],
        });

        expect(summary.failed).toBe(1);
        expect(summary.succeeded).toBe(1);
        expect(mockState.uploads.map((upload) => upload.Key)).toEqual([
            "good1.pdf",
        ]);
    });

    test("fails a stalled response instead of hanging", async () => {
        const handler = loadHandler();
        const summary = await handler({
            Records: [
                buildRecord("hangdoc", `${mockState.baseUrl}/hang/hangdoc`),
            ],
        });

        expect(summary.failed).toBe(1);
        expect(mockState.uploads).toHaveLength(0);
        expect(
            mockState.updates[0].ExpressionAttributeValues[":reason"],
        ).toMatch(/timed out|closed before download finished/);
    });

    test("handles an empty event without throwing", async () => {
        const handler = loadHandler();
        const summary = await handler({});

        expect(summary).toEqual({
            succeeded: 0,
            failed: 0,
            skipped: 0,
            failedDocumentIds: [],
        });
    });
});
