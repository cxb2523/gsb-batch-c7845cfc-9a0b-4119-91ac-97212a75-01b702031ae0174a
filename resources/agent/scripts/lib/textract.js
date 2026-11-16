'use strict';

/**
 * Pure helpers that turn AWS Textract `getDocumentAnalysis` blocks into the
 * flat `{ "Key": "Value" }` object the Onify form consumes.
 *
 * This module deliberately has no AWS dependency (and no lodash dependency)
 * so it can be exercised in tests without credentials or network access.
 */

const getText = (result, blocksMap) => {
    let text = '';

    if (result && Array.isArray(result.Relationships)) {
        result.Relationships.forEach(relationship => {
            if (relationship.Type === 'CHILD') {
                relationship.Ids.forEach(childId => {
                    const word = blocksMap[childId];
                    if (!word) return;

                    if (word.BlockType === 'WORD') {
                        text += `${word.Text} `;
                    }

                    if (word.BlockType === 'SELECTION_ELEMENT') {
                        if (word.SelectionStatus === 'SELECTED') {
                            text += `X `;
                        }
                    }
                });
            }
        });
    }

    return text.trim();
};

const findValueBlock = (keyBlock, valueMap) => {
    let valueBlock;
    if (!keyBlock || !Array.isArray(keyBlock.Relationships)) {
        return valueBlock;
    }

    keyBlock.Relationships.forEach(relationship => {
        if (relationship.Type === 'VALUE') {
            // eslint-disable-next-line array-callback-return
            relationship.Ids.every(valueId => {
                if (valueMap[valueId] !== undefined) {
                    valueBlock = valueMap[valueId];
                    return false;
                }
            });
        }
    });

    return valueBlock;
};

const getKeyValueRelationship = (keyMap, valueMap, blockMap) => {
    const keyValues = {};

    Object.values(keyMap).forEach(keyMapValue => {
        const valueBlock = findValueBlock(keyMapValue, valueMap);
        const key = getText(keyMapValue, blockMap);
        const value = getText(valueBlock, blockMap);
        keyValues[key] = value;
    });

    return keyValues;
};

const getKeyValueMap = blocks => {
    const keyMap = {};
    const valueMap = {};
    const blockMap = {};

    blocks.forEach(block => {
        const blockId = block.Id;
        blockMap[blockId] = block;

        if (block.BlockType === 'KEY_VALUE_SET') {
            if (Array.isArray(block.EntityTypes) && block.EntityTypes.indexOf('KEY') !== -1) {
                keyMap[blockId] = block;
            } else {
                valueMap[blockId] = block;
            }
        }
    });

    return { keyMap, valueMap, blockMap };
};

/**
 * Extract the form key/value pairs from a Textract analysis response.
 *
 * Returns `null` when the response contains no blocks at all. A successful
 * API call that returned nothing is a failure for this pipeline (the form
 * would be empty), and the caller must be able to report it against the file
 * that produced it.
 */
function extractKeyValues(data) {
    if (!data || !Array.isArray(data.Blocks) || data.Blocks.length === 0) {
        return null;
    }
    const { keyMap, valueMap, blockMap } = getKeyValueMap(data.Blocks);
    return getKeyValueRelationship(keyMap, valueMap, blockMap);
}

module.exports = {
    getText,
    findValueBlock,
    getKeyValueRelationship,
    getKeyValueMap,
    extractKeyValues
};
