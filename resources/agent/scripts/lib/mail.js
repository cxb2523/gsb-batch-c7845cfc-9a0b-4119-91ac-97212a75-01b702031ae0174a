'use strict';

const { PipelineError } = require('./errors');

/**
 * Resolve the S3 key from an upload result.
 *
 * The original BPMN referenced `uploadFileResult.key` while the AWS SDK
 * returns `Key`; depending on the Onify agent version one or the other is
 * present. Accept both and fall back to the inbox file name so the value used
 * for the form link and mail subject is always the document key.
 */
function resolveUploadKey(uploadResult, fallback) {
    if (uploadResult) {
        if (uploadResult.key !== undefined && uploadResult.key !== null && uploadResult.key !== '') {
            return uploadResult.key;
        }
        if (uploadResult.Key !== undefined && uploadResult.Key !== null && uploadResult.Key !== '') {
            return uploadResult.Key;
        }
    }
    return fallback;
}

/**
 * Build the `POST /tools/sendmail` payload for a successfully processed file.
 *
 * This is a byte-for-byte port of the script that used to live inline in the
 * BPMN `sendMail` task: same base64 encoding, same form URL layout, same HTML
 * body and same subject (the S3 key). Do not "improve" the encoding or add
 * URL escaping: existing form links must keep working.
 */
function buildMailPayload(options) {
    const processResult = options.processResult;
    const uploadResult = options.uploadResult;
    const fileName = options.fileName;
    const mailTo = options.mailTo;
    const onifyURL = options.onifyURL;

    if (!uploadResult || uploadResult.SignedUrl === undefined || uploadResult.SignedUrl === null) {
        throw new PipelineError(
            'Upload result is missing SignedUrl for file=' + fileName,
            { stage: 'mail' }
        );
    }

    const key = resolveUploadKey(uploadResult, fileName);

    const formDataEncoded = Buffer.from(JSON.stringify(processResult)).toString('base64');
    const fileSignedUrlEncoded = Buffer.from(uploadResult.SignedUrl).toString('base64');

    const url =
        onifyURL +
        '/form/processapplication?formdata=' +
        formDataEncoded +
        '&fileurl=' +
        fileSignedUrlEncoded +
        '&filename=' +
        fileName;

    const emailBody = `

Hello!<br>
<br>
${key}<br>
<br>
<a href="${url}">Process application</a>

`;

    return {
        to: mailTo,
        subject: key,
        html: emailBody
    };
}

module.exports = { buildMailPayload, resolveUploadKey };
