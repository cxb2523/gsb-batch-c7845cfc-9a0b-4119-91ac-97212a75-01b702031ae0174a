import * as chromium from "chrome-aws-lambda";
import * as AWS from "aws-sdk";
const db = new AWS.DynamoDB.DocumentClient();
const TABLE_NAME = process.env.TABLE_NAME || "";
const PRIMARY_KEY = process.env.PRIMARY_KEY || "";

// Metadata stored alongside every scraped PDF download URL
const DOCUMENT_TYPE = "WELL ABANDONMENT REPORT (INTENT)";
const DOCUMENT_DATE = "02/03/2020";

// NOTE - we only insert the first 5 items into DynamoDB to curb
// unindended AWS spend associated with running Textract against PDFs.
const MAX_DOCUMENTS = 5;

// Marker used by the COGCC download URLs to carry the document id
const DOCUMENT_ID_MARKER = "DocumentId=";

// // // //

interface ScrapeSummary {
    added: number;
    skipped: number;
    failed: number;
    failedUrls: string[];
}

/**
 * buildFetchUrl
 * Builds a url to the page with all the PDF download URLs
 */
function buildFetchUrl(): string {
    // The URL from which the PDF download URLs are being fetched
    const baseUrl =
        "http://ogccweblink.state.co.us/Results.aspx?DocName=WELL%20ABANDONMENT%20REPORT%20(INTENT)&DocDate=02/03/2020";

    // Returns base URL with date param
    return baseUrl;
}

/**
 * parseDocumentId
 * Pulls the documentId out of a PDF download URL.
 * Returns `null` when the URL doesn't contain a usable DocumentId, so that a
 * single malformed link can be skipped instead of aborting the whole scrape.
 */
function parseDocumentId(downloadUrl: string): string | null {
    if (typeof downloadUrl !== "string") {
        return null;
    }

    // Finds the DocumentId= marker. `lastIndexOf` keeps the original
    // behaviour, where the id is whatever follows the final `DocumentId=`.
    const markerIndex = downloadUrl.lastIndexOf(DOCUMENT_ID_MARKER);
    if (markerIndex === -1) {
        return null;
    }

    // Pulls everything after the marker, stopping at the next query param /
    // fragment so that URLs like `...?DocumentId=123&foo=bar` yield `123`
    const rawId = downloadUrl.slice(markerIndex + DOCUMENT_ID_MARKER.length);
    const documentId = rawId.split("&")[0].split("#")[0].trim();

    // Empty / whitespace-only ids are not usable
    return documentId.length > 0 ? documentId : null;
}

/**
 * insertDocument
 * Writes a new document to `pdfUrlsTable` only if it doesn't exist yet.
 * Returns `"added"` for a new document and `"skipped"` when the conditional
 * write fails because the documentId is already present.
 */
async function insertDocument(
    documentId: string,
    downloadUrl: string,
): Promise<"added" | "skipped"> {
    // Defines the item we're inserting into the database
    const item: any = {
        [PRIMARY_KEY]: documentId,
        documentType: DOCUMENT_TYPE,
        date: DOCUMENT_DATE,
        downloadUrl: downloadUrl,
    };

    // Defines the params for db.put
    // Conditional write: only insert the document if this documentId has not
    // already been scraped. This prevents duplicate documents from being
    // re-enqueued (and re-billed downstream by Textract).
    const params = {
        TableName: TABLE_NAME,
        Item: item,
        ConditionExpression: "attribute_not_exists(#primaryKey)",
        ExpressionAttributeNames: {
            "#primaryKey": PRIMARY_KEY,
        },
    };

    try {
        await db.put(params).promise();
        return "added";
    } catch (error: any) {
        // The document already exists -> skip it
        if (error && error.code === "ConditionalCheckFailedException") {
            return "skipped";
        }
        throw error;
    }
}

// // // //

export const handler = async (
    event: any = {},
    context: any = {},
): Promise<ScrapeSummary> => {
    // Log start message
    console.log("scrape-pdfs-from-website -> start");
    console.log(event);

    // Defines summary counters, so that added / skipped / failed documents can
    // be reconciled per run
    const summary: ScrapeSummary = {
        added: 0,
        skipped: 0,
        failed: 0,
        failedUrls: [],
    };

    // Define
    let browser = null;

    try {
        // Defines browser
        // NOTE - the options are typed as `any` because chrome-aws-lambda's
        // bundled typings target an older puppeteer version than the one
        // installed in this repo.
        const launchOptions: any = {
            args: chromium.args,
            defaultViewport: chromium.defaultViewport,
            executablePath: await chromium.executablePath,
            headless: chromium.headless,
        };
        browser = await chromium.puppeteer.launch(launchOptions);

        // Defines page
        let page = await browser.newPage();

        // Gets fetchUrl for puppeteer
        // This is the page with all the PDF download URLs
        const fetchUrl: string = buildFetchUrl();

        // Navigate to page, wait until dom content is loaded
        await page.goto(fetchUrl, {
            waitUntil: "domcontentloaded",
        });

        // Gets ALL urls
        let allHrefs: string[] = await page.$$eval("a", (anchors) =>
            anchors.map((anchor) => (anchor as HTMLAnchorElement).href),
        );

        // Gets Download URLS
        let downloadUrls = allHrefs.filter((href) =>
            href.includes("DownloadDocumentPDF"),
        );

        // Logs downloadUrls
        console.log("downloadUrls");
        console.log(downloadUrls);

        // Insert downloadURLs into DynamoDB
        // NOTE - we only insert the first MAX_DOCUMENTS items into DynamoDB to
        // curb unindended AWS spend associated with running Textract against
        // PDFs.
        const urlsToInsert = downloadUrls.slice(0, MAX_DOCUMENTS);

        // Processes each link individually: one bad link never aborts the batch
        for (const downloadUrl of urlsToInsert) {
            // Pulls documentId from downloadUrl
            const documentId = parseDocumentId(downloadUrl);

            // Skips links that can't be parsed into a documentId, counting them
            // as failures so they can be reviewed manually
            if (!documentId) {
                summary.failed++;
                summary.failedUrls.push(downloadUrl);
                console.log(
                    `scrape-pdfs-from-website -> [FAILED] could not parse documentId from url: ${downloadUrl}`,
                );
                continue;
            }

            try {
                const result = await insertDocument(documentId, downloadUrl);

                if (result === "added") {
                    summary.added++;
                    console.log(
                        `scrape-pdfs-from-website -> [NEW] documentId=${documentId}`,
                    );
                } else {
                    summary.skipped++;
                    console.log(
                        `scrape-pdfs-from-website -> [SKIPPED] documentId=${documentId} already exists`,
                    );
                }
            } catch (error: any) {
                // A DynamoDB error for one document doesn't abort the batch
                summary.failed++;
                summary.failedUrls.push(downloadUrl);
                console.log(
                    `scrape-pdfs-from-website -> [FAILED] documentId=${documentId} reason=${
                        error && error.message ? error.message : error
                    }`,
                );
            }
        }
    } catch (error) {
        console.log("scrape-pdfs-from-website -> [FAILED] batch error");
        console.log(error);
        if (context && typeof context.fail === "function") {
            return context.fail(error);
        }
        throw error;
    } finally {
        // Close the puppeteer browser
        if (browser !== null) {
            try {
                await browser.close();
            } catch (error) {
                console.log(
                    "scrape-pdfs-from-website -> [FAILED] could not close browser",
                );
                console.log(error);
            }
        }
    }

    // Logs per-run summary (added / skipped / failed counts)
    console.log(
        `scrape-pdfs-from-website -> summary ${JSON.stringify(summary)}`,
    );

    // Logs "shutdown" statement
    console.log("scrape-pdfs-from-website -> shutdown");
    return summary;
};
