// Local verification for src/scrape-pdfs-from-website/lambda.ts
// Uses a fake Puppeteer page and a fake DynamoDB DocumentClient that honours
// the conditional write, so no AWS credentials or network access are needed.

// Mutable state shared with the jest module factories below.
// NOTE - `jest.mock` factories are hoisted, so shared state must be named with
// the `mock` prefix and only dereferenced when the module is actually required.
const mockState: {
    links: string[];
    existingIds: Set<string>;
    puts: any[];
    putErrors: { [documentId: string]: Error };
    pageGotoCalls: number;
    browserClosed: boolean;
} = {
    links: [],
    existingIds: new Set<string>(),
    puts: [],
    putErrors: {},
    pageGotoCalls: 0,
    browserClosed: false,
};

jest.mock("chrome-aws-lambda", () => {
    const page = {
        goto: async () => {
            mockState.pageGotoCalls++;
        },
        $$eval: async (
            _selector: string,
            fn: (anchors: any[]) => string[],
        ): Promise<string[]> => {
            // Gives the page callback real anchor-like objects
            return fn(
                mockState.links.map((href) => {
                    return { href };
                }),
            );
        },
    };

    const browser = {
        newPage: async () => page,
        close: async () => {
            mockState.browserClosed = true;
        },
    };

    return {
        args: ["--no-sandbox"],
        defaultViewport: { width: 1280, height: 800 },
        headless: true,
        executablePath: Promise.resolve("/fake/chromium"),
        puppeteer: {
            launch: async () => browser,
        },
    };
});

jest.mock("aws-sdk", () => {
    class DocumentClient {
        put(params: any) {
            return {
                promise: async () => {
                    mockState.puts.push(params);

                    const primaryKey = process.env.PRIMARY_KEY || "itemId";
                    const documentId = params.Item[primaryKey];

                    // Simulates a per-document DynamoDB failure
                    if (mockState.putErrors[documentId]) {
                        throw mockState.putErrors[documentId];
                    }

                    // Simulates the conditional write
                    if (mockState.existingIds.has(documentId)) {
                        const error: any = new Error(
                            "The conditional request failed",
                        );
                        error.code = "ConditionalCheckFailedException";
                        throw error;
                    }

                    mockState.existingIds.add(documentId);
                    return {};
                },
            };
        }
    }

    return { DynamoDB: { DocumentClient } };
});

// // // //

const DOCUMENT_TYPE = "WELL ABANDONMENT REPORT (INTENT)";
const DOCUMENT_DATE = "02/03/2020";
const BASE_URL = "http://ogccweblink.state.co.us";

function buildLink(documentId: string): string {
    return `${BASE_URL}/DownloadDocumentPDF.aspx?DocumentId=${documentId}`;
}

function loadHandler(): (event?: any, context?: any) => Promise<any> {
    // Resets the module so the environment variables below are picked up
    let handler: any;
    jest.isolateModules(() => {
        handler = require("../scrape-pdfs-from-website/lambda").handler;
    });
    return handler;
}

beforeEach(() => {
    process.env.TABLE_NAME = "cogcc-pdf-urls";
    process.env.PRIMARY_KEY = "itemId";

    mockState.links = [];
    mockState.existingIds = new Set<string>();
    mockState.puts = [];
    mockState.putErrors = {};
    mockState.pageGotoCalls = 0;
    mockState.browserClosed = false;

    // Keeps test output readable
    jest.spyOn(console, "log").mockImplementation(() => undefined);
});

afterEach(() => {
    jest.restoreAllMocks();
});

// // // //

describe("scrape-pdfs-from-website", () => {
    test("writes only the first 5 download links", async () => {
        mockState.links = Array.from({ length: 8 }, (_value, index) => {
            return buildLink(String(5000000 + index));
        });

        const handler = loadHandler();
        const summary = await handler({}, {});

        expect(mockState.pageGotoCalls).toBe(1);
        expect(mockState.puts).toHaveLength(5);
        expect(summary.added).toBe(5);
        expect(mockState.browserClosed).toBe(true);
    });

    test("uses a conditional write and preserves the item fields", async () => {
        mockState.links = [buildLink("5055310")];

        const handler = loadHandler();
        await handler({}, {});

        expect(mockState.puts).toHaveLength(1);
        const params = mockState.puts[0];

        expect(params.TableName).toBe("cogcc-pdf-urls");
        expect(params.ConditionExpression).toBe(
            "attribute_not_exists(#primaryKey)",
        );
        expect(params.ExpressionAttributeNames).toEqual({
            "#primaryKey": "itemId",
        });
        expect(params.Item).toEqual({
            itemId: "5055310",
            documentType: DOCUMENT_TYPE,
            date: DOCUMENT_DATE,
            downloadUrl: buildLink("5055310"),
        });
    });

    test("counts existing documents as skipped and new ones as added", async () => {
        mockState.links = [
            buildLink("111"),
            buildLink("222"),
            buildLink("333"),
        ];
        mockState.existingIds = new Set<string>(["111", "333"]);

        const handler = loadHandler();
        const summary = await handler({}, {});

        expect(summary.added).toBe(1);
        expect(summary.skipped).toBe(2);
        expect(summary.failed).toBe(0);
        expect(mockState.existingIds.size).toBe(3);
    });

    test("skips unparseable links as failures and still processes the rest", async () => {
        mockState.links = [
            `${BASE_URL}/DownloadDocumentPDF.aspx?NoIdHere=true`,
            buildLink("444"),
            `${BASE_URL}/DownloadDocumentPDF.aspx?DocumentId=`,
            buildLink("555"),
        ];

        const handler = loadHandler();
        const summary = await handler({}, {});

        expect(summary.failed).toBe(2);
        expect(summary.added).toBe(2);
        expect(summary.failedUrls).toHaveLength(2);
        expect(mockState.puts.map((params) => params.Item.itemId)).toEqual([
            "444",
            "555",
        ]);
    });

    test("does not abort the batch when DynamoDB fails for one document", async () => {
        mockState.links = [
            buildLink("666"),
            buildLink("777"),
            buildLink("888"),
        ];
        mockState.putErrors["777"] = new Error("ProvisionedThroughputExceeded");

        const handler = loadHandler();
        const summary = await handler({}, {});

        expect(summary.added).toBe(2);
        expect(summary.failed).toBe(1);
        expect(summary.failedUrls).toEqual([buildLink("777")]);
        expect(mockState.puts).toHaveLength(3);
    });

    test("parses documentId when the url carries extra query params", async () => {
        mockState.links = [
            `${BASE_URL}/DownloadDocumentPDF.aspx?DocumentId=999&foo=bar#page`,
        ];

        const handler = loadHandler();
        const summary = await handler({}, {});

        expect(summary.added).toBe(1);
        expect(mockState.puts[0].Item.itemId).toBe("999");
    });

    test("logs one line per document, tagged NEW / SKIPPED / FAILED", async () => {
        mockState.links = [
            buildLink("1"),
            `${BASE_URL}/DownloadDocumentPDF.aspx?NoIdHere=true`,
        ];
        mockState.existingIds = new Set<string>(["1"]);

        const handler = loadHandler();
        await handler({}, {});

        const logged = (console.log as jest.Mock).mock.calls
            .map((args) => String(args[0]))
            .join("\n");

        expect(logged).toContain("[SKIPPED] documentId=1");
        expect(logged).toContain("could not parse documentId");
        expect(logged).toContain('"skipped":1');
    });
});
