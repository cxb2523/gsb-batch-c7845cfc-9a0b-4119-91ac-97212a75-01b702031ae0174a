// Copyright (c) 2022 MillenniumEarl
//
// This software is released under the MIT License.
// https://opensource.org/licenses/MIT

// Public modules from npm
import { CheerioAPI, AnyNode } from "cheerio";
import { ParameterError } from "../classes/errors";

// Modules from file
import { ILink, IPostElement } from "../interfaces";
import parseCheerioNode from "./post-node-parse/node-parse";
import { createEmptyElement } from "./post-node-parse/node-utility";

/**
 * Node of a tree elements tree of a post on an HTML page.
 */
interface TreeNode {
  /**
   * Unique ID of the node.
   */
  id: number;
  /**
   * Element root.
   * If `null` represents the root of the entire tree.
   */
  parent: TreeNode;
  /**
   * Parsed element contained in the node.
   */
  element: IPostElement;
  /**
   * List of children of this node.
   */
  children: TreeNode[];
}

/**
 * Given a post of a thread page it extracts the information contained in the body.
 *
 * Every malformed node (missing link reference, image-like link text,
 * orphaned nodes during the cleaning steps, ...) is skipped instead of
 * aborting the whole parsing. An empty array is returned when the post
 * contains no valid entry or an unexpected error is encountered.
 *
 * The parsing is fully stateless: parsing multiple posts in sequence in
 * the same process can never influence the result of subsequent calls.
 */
export function extractDataFromFirstThreadPost($: CheerioAPI, post: AnyNode): IPostElement[] {
  try {
    // First create the tree struct of the post.
    // The ID counter is local so two consecutive calls can't share state.
    let root = createTree($, post);

    // Remove all empty elements from the tree.
    root = pruneTreeNode(root) as TreeNode; // Force TS to accept TreeNode as type

    // Clean all the nodes with a link element
    root = cleanLinkNode(root);

    // Clean the spoilers node
    root = parseSpoilers(root);

    // Remove unnecessary elements
    pruneNodesWithUnnecessaryValues(root);

    // Convert from tree to list of properties of the post
    return pairUpTitleWithContent(root);
  } catch (e) {
    // A malformed node must never prevent the caller from obtaining
    // the (possibly empty) list of the entries that could be parsed
    return [];
  }
}

//#region Tree methods

/**
 * Create recursively a tree with the information contained in a Cheerio node.
 *
 * The node ID counter is created fresh for every tree, therefore parsing
 * multiple posts in the same process can never leave a residual counter
 * (or a reference to an already cleaned node) that influences a later parse.
 * @returns Tree root
 */
function createTree($: CheerioAPI, node: AnyNode, parent?: TreeNode): TreeNode {
  // Local ID counter, shared only by the nodes of this tree
  let idcounter = 0;

  /**
   * Recursive worker that assigns the IDs from the local counter.
   */
  function build(current: AnyNode, currentParent?: TreeNode): TreeNode {
    // Create the element
    const treenode = {
      id: idcounter,
      parent: currentParent ?? null,
      element: parseCheerioNode($, current),
      children: []
    } as TreeNode;

    // Set the first node as root
    if (idcounter === 0) treenode.element.type = "Root";

    // Increment the counter
    idcounter = idcounter + 1;

    // Parse the children of the element
    treenode.children = $(current)
      .contents()
      .toArray()
      .map((e) => build(e, treenode));

    return treenode;
  }

  return build(node, parent);
}

/**
 * Debug function used to display the elements that make up a tree in the console.
 */
/* c8 ignore start */
function printTree(root: TreeNode, nindent = 0) {
  // Define the indent
  const indent = `${" ".repeat(nindent)}└─ `;

  // Print the data of this node
  const data = `[${root.element.type}] (${root.id}) ${root.element.text || root.element.name}`;
  // eslint-disable-next-line no-console
  console.log(`${indent}${data}`);

  // Print the data of the children nodes
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  root.children.forEach((child) => printTree(child, nindent + 1));
}
/* c8 ignore stop */

/* c8 ignore start */
// eslint-disable-next-line @typescript-eslint/no-unused-vars
function printPairs(pairs: IPostElement[]) {
  for (const pair of pairs) {
    const childData = pair.content.length > 0 ? `(children: ${pair.content.length})` : "";
    const text = pair.text || "No Text";
    // eslint-disable-next-line no-console
    console.log(`+ [${pair.name}]: ${text} ${childData}`);
  }
}
/* c8 ignore stop */

//#endregion Tree methods

//#region Prune methods

/**
 * Removes the node passed by parameter from the tree,
 * removing any reference from the father and assigning
 * all their children to the father of this removed node.
 *
 * If the node has no parent or it is not referenced anymore
 * among the children of its parent (i.e. it was already
 * cleaned by a previous step) the call is a no-op so that a
 * stale reference can't corrupt the rest of the tree.
 */
function purgeNode(node: TreeNode) {
  // Check if the node has a parent
  if (!node.parent) return;

  // Find the index of this node in the array of children of its parent
  const cloneIndex = node.parent.children.findIndex((child) => child.id === node.id);

  // The node is not a child of its parent anymore, nothing to purge
  if (cloneIndex === -1) return;

  // Remove this node from the array of children of its parent
  node.parent.children.splice(cloneIndex, 1);

  // After cleaning every child, assign them as children of this node parent
  let lastChildrenLength = node.parent.children.length;
  let addedChildren = 0;
  let newElementIndex = cloneIndex;

  for (const child of node.children) {
    // Change the parent of the children
    child.parent = node.parent;

    // Find the location to add this child to
    newElementIndex += addedChildren;

    // Add this children nodes as children of this node parent
    // starting from the same ID of this node
    node.parent.children.splice(newElementIndex, 0, child);

    // Clean the children that otherwise will not be cleanes
    // because it is added to the children of a already cleaned node.
    // It may also be purged from the tree is is a uninformative node.
    const clean = pruneTreeNode(child);

    // If the children IS uninformative it will be purged,
    // so this instruction will overwrite a different valid node.
    // This check is used to prevent this.
    if (clean) node.parent.children[newElementIndex] = clean;

    // Calculates the number of child elements (even recursive)
    // of child added to the parent node. This is necessary to
    // avoid that, after a recursive call, an element is added
    // before its position if the previous element had more than
    // one child.
    addedChildren = node.parent.children.length - lastChildrenLength;

    // Save the actual length of the parent's children
    lastChildrenLength = node.parent.children.length;
  }
}

/**
 * Removes a node together with its whole subtree from the tree.
 *
 * Unlike `purgeNode`, the children of the node are NOT reassigned to
 * its parent: a malformed node (for example a link without a valid
 * reference) must disappear completely, leaving no orphaned text behind.
 *
 * The call is a no-op when the node has no parent or it is not
 * referenced anymore among the children of its parent.
 */
function detachNode(node: TreeNode) {
  // Check if the node has a parent
  if (!node.parent) return;

  // Find the index of this node in the array of children of its parent
  const index = node.parent.children.findIndex((child) => child.id === node.id);

  // The node is not a child of its parent anymore, nothing to remove
  if (index === -1) return;

  // Detach the node (and its subtree) from the tree
  node.parent.children.splice(index, 1);
  node.parent = null as any;
}

/**
 * Eliminates connection nodes that do not bring additional information to the tree.
 *
 * Returns the cleaned node or `null` is the node has been purged.
 */
function pruneTreeNode(node: TreeNode): TreeNode | null {
  // Remove all the children that haven't a value AND other childrens
  node.children = node.children.filter((child) => !isUninformativeNode(child));

  // Clone the array of children
  const children = [...node.children];

  // Determines if the node should be removed
  // from the tree (resulting in moving children)
  const isEmpty = node.element.type === "Empty";
  const toBePurged = (isUninformativeNode(node) || isEmpty) && node.parent !== null;

  // If in this node `element` has no information value, remove this node
  // as parent and link all the children to this node parent
  if (toBePurged) purgeNode(node);
  // Remove uninformative elements ricursively (from the ACTUAL children)
  else children.map((child) => pruneTreeNode(child));

  // Return cleaned node
  return toBePurged ? null : node;
}

/**
 * Removes all those elements that are not
 * useful to collect information on the post.
 *
 * This for example includes spoilers without
 * content or text elements with special
 * characters only (except :)
 */
function pruneNodesWithUnnecessaryValues(node: TreeNode) {
  // Clen the element's text from chars like * - , \
  const cleanText = node.element.text.replace(/\*|-|,|\|/gmu, "").trim();

  const functionMap = [
    // Used to remove spoilers without children
    (node: TreeNode) => node.element.type === "Spoiler" && node.children.length === 0,
    // Used to remove text elements without text
    (node: TreeNode) => node.element.type === "Text" && cleanText === ""
  ];

  // Save the childrens of this node in the event that this node is eliminated
  const children = node.children;

  // Check if the element should be purged
  const shouldPurge = functionMap.some((f) => f(node));
  if (shouldPurge) purgeNode(node);

  // Recursively clean the childrens of this node
  children.map((child) => pruneNodesWithUnnecessaryValues(child));
}

/**
 * Check if a node has some information or is
 * not useful in order to process the post.
 *
 * This check is performed before deleting a node
 * from the tree (with possible moving of children).
 */
function isUninformativeNode(node: TreeNode): boolean {
  // Alias used for cleaner coding
  const e = node.element;

  const hasInformation = e.text.trim() !== "" || e.name.trim() !== "" || e.content.length > 0;
  const hasChildren = node.children.length !== 0;

  return !hasInformation && !hasChildren;
}

//#endregion Prune methods

//#region Parse node methods

/**
 * Given the tree root, return a list of elements
 * with the data extracted and parsed from the tree.
 */
function pairUpTitleWithContent(root: TreeNode): IPostElement[] {
  // Local variables
  let lastActiveTitle: IPostElement = null as any;
  const pairs: IPostElement[] = [];
  const rootClone = Object.assign({}, root);

  // Only the root of the tree can be used to pair up the elements
  if (rootClone.element.type !== "Root") throw new ParameterError("The node must be a root node");

  // Get the cover and the previews
  const coverAndPrevies = parseCoverAndPreviews(rootClone);
  pairs.push(...coverAndPrevies);

  // Remove all the images from the root
  rootClone.children = rootClone.children.filter((c) => c.element.type !== "Image");

  // The scheme is as follows:
  //  + Textual element "TITLE"
  //  + Textual element ":"
  //  + Generic element CONTENT
  rootClone.children.forEach((child, index, children) => {
    try {
      // Convert from TreeNode to IPostElement
      const e = elementsToContent(child);

      // Get the next element (if any)
      const isLastChild = index + 1 >= children.length;
      const next = isLastChild ? null : children[index + 1].element;

      // Check if this child's element is a title
      const title = isTitle(e, next);
      const isPunctuation = e.type === "Text" && e.text === ":";

      // This element is a title:
      //
      // If no title is currently "active", set this element as "active title".
      //
      // If another title is currently active, this means that all the content
      // of the currently active title is already been "seen". Save it in the
      // list and manage the content of the new title (this element)
      if (title) {
        // Push a clean clone of the currently active title (if exists)
        if (lastActiveTitle) pairs.push(parseTitleElement(lastActiveTitle));

        // Set the current element as the active title
        lastActiveTitle = e;
      }
      // This element is not a title nor a puntuaction,
      // so that means that is content!
      else if (lastActiveTitle && !isPunctuation && !title) {
        lastActiveTitle.content.push(e);
      }
    } catch (e) {
      // Skip this malformed node and keep parsing the remaining entries
    }
  });

  // The last active title is only flushed when another title is found,
  // therefore the title closing the post must be pushed explicitly or
  // its entry (and all its content) would be lost
  if (lastActiveTitle) pairs.push(parseTitleElement(lastActiveTitle));

  // Clean pairs and remove all the elements that haven't text AND content
  return pairs.filter((e) => e.text !== "" || e.content.length > 0);
}

/**
 * Given the tree root, extract the cover image and the previews in a array
 */
function parseCoverAndPreviews(root: TreeNode): IPostElement[] {
  // Local variables
  const returnValue: IPostElement[] = [];

  // Only the root of the tree can be used to pair up the elements
  if (root.element.type !== "Root") throw new ParameterError("The node must be a root node");

  // Find all the images that are direct children of the root node
  const images = root.children.filter((c) => c.element.type === "Image").map((i) => i.element);

  if (images.length > 0) {
    // The first image is the cover
    // Ignore the case where there are two or more covers
    const cover = images.splice(0, 1)[0];
    cover.name = "Cover";
    returnValue.push(cover);

    // All the other images are previews
    const previews = createEmptyElement();
    previews.name = "Previews";
    previews.content = images;

    // Return previews only if there is at least one image
    if (previews.content.length > 0) returnValue.push(previews);
  }

  return returnValue;
}

/**
 * Given a "title" element, assign its name and
 * text and parse and/or extract its content.
 */
function parseTitleElement(e: IPostElement): IPostElement {
  // Clone the element
  const clone = Object.assign({}, e);

  // Regex values used to clean the texts.
  // The patterns are anchored, so no global flag is needed; avoiding it
  // also prevents any shared `lastIndex` state between calls.
  const RX_ENDS_SPECIAL_CHARS = /[-:]$/mu;
  const RX_STARTS_COLON = /^[:]/mu;

  // Clean the text of the element and swap it with the name
  clone.name = clone.text.replace(RX_ENDS_SPECIAL_CHARS, "").trim();

  // If the content contains only a spoiler, the content of
  // the spoiler goes to the content of the clne, thant it
  // will be removed
  if (clone.content.length === 1 && clone.content[0].type === "Spoiler") {
    const spoiler = clone.content.pop() as IPostElement; // Force TS to accept IPostElement as type
    clone.content.push(...spoiler.content);
  }

  // The next steps are to avoid if the name of the element
  // is in the following list of special "titles"
  const SPECIAL_TITLE_NAMES = ["CHANGELOG", "CHANGE-LOG"];

  if (!SPECIAL_TITLE_NAMES.includes(clone.name.toUpperCase())) {
    // Parse and clean the text elements (only direct children)
    // in the content of the title, then join them to create
    // the new text of the element
    clone.text = clone.content
      .filter((v) => v.type === "Text")
      .map((v) => v.text.replace(RX_STARTS_COLON, "").trim())
      .join(" ");

    // Remove the text elements (direct children) from the content of the title
    clone.content = clone.content.filter((v) => v.type !== "Text");
  }

  return clone;
}

/**
 * Check if the `element` has the criteria to be considered a "title",
 * ie an element that identifies its content.
 */
function isTitle(element: IPostElement, next: IPostElement | null): boolean {
  // Regex value used to check for colon at the ends of the text.
  // NOTE: the global flag must NOT be used: a shared regex with `g`
  // keeps a `lastIndex` between `.test()` calls, so consecutive titles
  // (within a post or across posts parsed in the same process) would
  // alternate between detected and missed, polluting later results.
  const RX_ENDS_COLON = /[:]$/mu;

  // Parameter used to check if this element is a title or not
  const thisIsText = element.type === "Text";
  const thisIsPunctuation = thisIsText && element.text.trim() === ":";
  const thisEndsWithColon = RX_ENDS_COLON.test(element.text);
  const nextStartsWithColon = next?.text.trim().startsWith(":");

  return thisIsText && !thisIsPunctuation && (nextStartsWithColon || thisEndsWithColon);
}

/**
 * Recursively assigns the children of a
 * node to the content of the element itself.
 */
function elementsToContent(node: TreeNode): IPostElement {
  // Create a clone of the parameter object
  const clone = Object.assign({}, node);

  // First go to the leaves of the tree and get leaves element
  const childElements = clone.children.map((child) => elementsToContent(child));

  // Assign to this node's element the found children
  clone.element.content = childElements;

  // Return the node element with the children
  return clone.element;
}

/**
 * Starting from a root node, removes all
 * the text elements with "Spoiler" value
 * found inside a `Spoiler` element.
 */
function parseSpoilers(root: TreeNode): TreeNode {
  // Clone the node
  let clone = Object.assign({}, root);

  // If this node is a spoiler, clean it otherwise
  if (clone.element.type === "Spoiler") clone = parseSingleSpoiler(clone);

  // Parse every children recursively
  clone.children.map((child) => parseSpoilers(child));

  return clone;
}

/**
 * Process a node having a `Spoiler` type element and eventually
 * removing the default "Spoiler" text title present as children.
 */
function parseSingleSpoiler(node: TreeNode): TreeNode {
  // Check the node type
  if (node.element.type !== "Spoiler") throw new ParameterError("This node is not a spoiler");

  // Remove the "title" element that is "Spoiler" when there is no title
  const index = node.children
    .filter((child) => child.element)
    .findIndex((child) => child.element.type === "Text" && child.element.text === "Spoiler");

  // When no default "Spoiler" text is present there is nothing to remove.
  // Guarding the index avoids an out-of-bound splice (which would silently
  // remove the last child) when the node has already been partially cleaned
  if (index !== -1) node.children.splice(index, 1);

  return node;
}

//#endregion Parse node methods

//#region Clean node methods

/**
 * Recursively clean all the nodes of `Link` type in the tree.
 *
 * Every malformed link is handled by its dedicated cleaner so that a
 * single broken node can never abort the cleaning of the remaining nodes.
 * @param node Tree root
 */
function cleanLinkNode(node: TreeNode): TreeNode {
  // Remove every link node that is representing an image
  node.children
    .filter((child) => child.element.type === "Link")
    .forEach((child) => {
      try {
        cleanLinkImage(child);
      } catch (e) {
        // Keep the (possibly invalid) node instead of aborting the parse
      }
    });

  // Clean every link node that is a children of this node
  node.children.forEach((child) => cleanLinkNode(child));

  // If this node is a link, clean it otherwise return it
  return node.element.type === "Link" ? cleanLinkTextChildren(node) : node;
}

/**
 * Removes nodes containing a text element that replicates this `Link` value.
 *
 * If no child matches the link text (the node was already cleaned or the
 * markup is malformed) the node is returned unchanged instead of removing
 * an unrelated child through an out-of-bound splice.
 */
function cleanLinkTextChildren(node: TreeNode): TreeNode {
  // Check the node type
  if (node.element.type != "Link") throw new ParameterError("This node is not a link");

  // Create a clone of the parameter object
  const clone = Object.assign({}, node);

  // Find the index of the children that contains a
  // "Text" element with the same text as this link node
  const index = clone.children.findIndex((child) => child.element.text === clone.element.text);

  // Remove the child only when a matching one actually exists,
  // otherwise leave the node untouched
  if (index !== -1) clone.children.splice(index, 1);

  return clone;
}

/**
 * Cleans a link node that is either an image reference or a malformed
 * text-only link without a valid `href`.
 *
 * - When the link text starts with `<img`, the link wrapper is removed
 *   but its children (the actual image node) are lifted to the parent,
 *   otherwise the cover/previews would be lost.
 * - A text-only link without `href` carries no usable reference, so the
 *   whole subtree (inner text node included) is detached to avoid
 *   leaving orphaned content.
 *
 * An anchor without `href` can still be a valid image wrapper (the
 * actual image is a child node), therefore only links with no image
 * child are considered malformed.
 */
function cleanLinkImage(node: TreeNode) {
  // Check the node type
  if (node.element.type != "Link") throw new ParameterError("This node is not a link");

  const hasImageText =
    typeof node.element.text === "string" && node.element.text.startsWith("<img");
  const hasNoHref = !(node.element as ILink).href;
  const hasImageChild = node.children.some(
    (child) => child.element.type === "Image" || child.element.type === "Link"
  );
  const isMalformedTextLink = hasNoHref && !hasImageChild;

  // Remove the link wrapper but keep the image it contains
  if (hasImageText) purgeNode(node);
  // Remove the malformed text-only link and its orphaned text
  else if (isMalformedTextLink) detachNode(node);
}

//#endregion Clean node methods
