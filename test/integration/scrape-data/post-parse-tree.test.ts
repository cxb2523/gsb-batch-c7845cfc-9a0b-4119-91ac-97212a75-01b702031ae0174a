// Copyright (c) 2022 MillenniumEarl
//
// This software is released under the MIT License.
// https://opensource.org/licenses/MIT

// Core modules
import { promises as fs } from "fs";
import { join } from "path";

// Public module from npm
import { expect } from "chai";
import { load } from "cheerio";

// Modules from file
import { extractDataFromFirstThreadPost } from "../../../src/scripts/scrape-data/post-parse-tree";
import { ILink, IPostElement } from "../../../src/scripts/interfaces";

/**
 * Parse an HTML fragment as if it were the body of the first post of a thread.
 * The fragment is wrapped in the same markup used by the platform.
 */
function parseFragment(fragment: string): IPostElement[] {
  const html = `<article class="message-body"><div class="bbWrapper">${fragment}</div></article>`;
  const $ = load(html);
  const node = $("article.message-body > div.bbWrapper").get().shift();
  return extractDataFromFirstThreadPost($, node);
}

/**
 * Valid, well-formed post used as a control sample. It contains titles,
 * plain content, a link (with matching text) and a spoiler block.
 */
const VALID_POST = `<b>Developer</b>: Good Dev<br>
<a href="https://example.com/site">https://example.com/site</a><br>
<b>Version</b>: 1.0.0<br>
<b>Genre</b>:<br>
<div class="bbCodeSpoiler">
  <button type="button" class="bbCodeSpoiler-button"><span class="button-text"><span>Spoiler</span></span></button>
  <div class="bbCodeSpoiler-content">
    <div class="bbCodeBlock bbCodeBlock--spoiler">
      <div class="bbCodeBlock-content">Drama, Romance</div>
    </div>
  </div>
</div><br>
<b>Final Note</b>: thanks<br>`;

export function suite(): void {
  it("Parse opening post", async function () {
    // Arrange
    const path = join(__dirname, "..", "resources", "opening-post-extract.html");
    const html = await fs.readFile(path);
    const $ = load(html);
    const node = $("article.message-body > div.bbWrapper").get().shift();

    // Act
    const data = extractDataFromFirstThreadPost($, node);

    // Assert
    expect(data.length).to.be.equal(23);

    // Assert cover data
    const cover = data.find((e) => e.type === "Image") as ILink;
    expect(cover).to.not.be.undefined;
    expect(cover.href).to.be.equal(
      "https://attachments.f95zone.to/2021/04/1180728_episode7_release_banner.jpg"
    );
    expect(cover.text).to.be.equal("episode7_release_banner.jpg");

    // Assert previews
    const previews = data.find((e) => e.name === "Previews");
    expect(previews).to.not.be.undefined;
    expect(previews.content.length).to.be.equal(18);
    const allImages = previews.content.find((e) => e.type !== "Image") === undefined;
    expect(allImages).to.be.true;

    // Assert date
    const releaseDate = data.find((e) => e.name === "Release Date");
    expect(releaseDate).to.not.be.undefined;
    expect(releaseDate.text).to.be.equal("2021-04-25");

    // Assert developer
    const developer = data.find((e) => e.name === "Developer");
    expect(developer).to.not.be.undefined;
    expect(developer.text).to.be.equal("DrPinkCake");
    expect(developer.content.length).to.be.equal(4);

    // Assert changelog
    const changelog = data.find((e) => e.name === "Changelog");
    expect(changelog).to.not.be.undefined;
    expect(changelog.content.length).to.be.equal(79);
    expect(changelog.content[0].text).to.be.equal("v0.7.2"); // Last version
    expect(changelog.content[77].text).to.be.equal("v0.1.0"); // First released version

    // The last title of the post must be flushed as its own entry.
    // Previously it was silently swallowed because the active title was
    // only pushed when another title was encountered
    const fanSigs = data.find((e) => e.name === "Fan Sigs");
    expect(fanSigs).to.not.be.undefined;
    expect(fanSigs.content.length).to.be.equal(7);
    const fanSigsLinks = fanSigs.content.filter((e) => e.type === "Link") as ILink[];
    expect(fanSigsLinks.length).to.be.equal(7);
    expect(fanSigsLinks.every((e) => e.href !== "")).to.be.true;
  });

  it("Parse generic post", async function () {
    // Arrange
    const path = join(__dirname, "..", "resources", "generic-post-extract.html");
    const html = await fs.readFile(path);
    const $ = load(html);
    const node = $("article.message-body > div.bbWrapper").get().shift();

    // Act
    const data = extractDataFromFirstThreadPost($, node);

    // Assert
    expect(data.length).to.be.equal(0);
  });

  it("Parse a well-formed local post sample", function () {
    // Act
    const data = parseFragment(VALID_POST);

    // Assert: every title is retained, including the last one
    expect(data.map((e) => e.name)).to.deep.equal(["Developer", "Version", "Genre", "Final Note"]);

    // Assert simple title/content values
    const version = data.find((e) => e.name === "Version");
    expect(version.text).to.be.equal("1.0.0");
    const genre = data.find((e) => e.name === "Genre");
    expect(genre.text).to.be.equal("Drama, Romance");
    const finalNote = data.find((e) => e.name === "Final Note");
    expect(finalNote.text).to.be.equal("thanks");

    // Assert link structure (href/text) is preserved unchanged
    const developer = data.find((e) => e.name === "Developer");
    expect(developer.text).to.be.equal("Good Dev");
    expect(developer.content.length).to.be.equal(1);
    const link = developer.content[0] as ILink;
    expect(link.type).to.be.equal("Link");
    expect(link.href).to.be.equal("https://example.com/site");
    expect(link.text).to.be.equal("https://example.com/site");
  });

  it("Skip a link without href while keeping the other entries", function () {
    // Arrange: an anchor missing the `href` attribute sits between valid titles
    const fragment = `<b>Title One</b>: value one<br>
<a>broken-link-without-href</a><br>
<b>Title Two</b>: value two<br>
<b>Title Three</b>: value three<br>`;

    // Act
    const data = parseFragment(fragment);

    // Assert: no throw, malformed node skipped, all valid entries returned
    expect(data.map((e) => e.name)).to.deep.equal(["Title One", "Title Two", "Title Three"]);
    expect(data[0].text).to.be.equal("value one");
    expect(data[1].text).to.be.equal("value two");
    expect(data[2].text).to.be.equal("value three");
  });

  it("Skip an image-like link whose text starts with '<img'", function () {
    // Arrange: anchor whose textual content is an image marker
    const fragment = `<b>Title One</b>: value one<br>
<a href="https://example.com/pic.jpg"><img src="placeholder.gif" data-src="https://example.com/pic.jpg" alt="pic.jpg"></a><br>
<b>Title Two</b>: value two<br>
<b>Title Three</b>: value three<br>`;

    // Act
    const data = parseFragment(fragment);

    // Assert: the image-link node is purged but parsing continues
    expect(data.map((e) => e.name)).to.deep.equal(["Title One", "Title Two", "Title Three"]);
  });

  it("Tolerate a link whose text does not match any child node", function () {
    // Arrange: link text differs from every child, so the cleaning step
    // cannot find the child to remove (index would be -1)
    const fragment = `<b>Title One</b>: value one<br>
<a href="https://example.com/page">completely different text</a><br>
<b>Title Two</b>: value two<br>
<b>Title Three</b>: value three<br>`;

    // Act
    const data = parseFragment(fragment);

    // Assert: no unrelated child removed, no throw, entries still returned
    expect(data.map((e) => e.name)).to.deep.equal(["Title One", "Title Two", "Title Three"]);
    const firstLink = data[0].content.find((e) => e.type === "Link") as ILink;
    expect(firstLink).to.not.be.undefined;
    expect(firstLink.href).to.be.equal("https://example.com/page");
    expect(firstLink.text).to.be.equal("completely different text");
  });

  it("Tolerate a spoiler without the default 'Spoiler' text child", function () {
    // Arrange: spoiler block missing the button text the cleaner looks for
    const fragment = `<b>Title One</b>: value one<br>
<div class="bbCodeSpoiler">
  <div class="bbCodeSpoiler-content">
    <div class="bbCodeBlock bbCodeBlock--spoiler">
      <div class="bbCodeBlock-content">hidden content</div>
    </div>
  </div>
</div><br>
<b>Title Two</b>: value two<br>
<b>Title Three</b>: value three<br>`;

    // Act
    const data = parseFragment(fragment);

    // Assert: splice on a missing child is avoided, entries retained
    expect(data.map((e) => e.name)).to.deep.equal(["Title One", "Title Two", "Title Three"]);
  });

  it("Tolerate repeated colon text inside a title block", function () {
    // Arrange: title carrying duplicate colons, surrounded by valid titles
    const fragment = `<b>Title One</b>: value one<br>
<b>Weird::</b> value weird<br>
<b>Title Two</b>: value two<br>
<b>Title Three</b>: value three<br>`;

    // Act
    const data = parseFragment(fragment);

    // Assert: the malformed title only affects itself, others survive
    expect(data.map((e) => e.name)).to.deep.equal([
      "Title One",
      "Weird:",
      "Title Two",
      "Title Three"
    ]);
  });

  it("Return an empty array when the post has no valid entry", function () {
    // Act & Assert: empty body
    expect(parseFragment("").length).to.be.equal(0);

    // Act & Assert: only non-informative markup
    const onlyMarkup = parseFragment("<br><br><b></b>");
    expect(onlyMarkup.length).to.be.equal(0);

    // Act & Assert: only broken nodes
    const onlyBroken = parseFragment(`<a>no-href</a><br>`);
    expect(onlyBroken.length).to.be.equal(0);
  });

  it("Do not let consecutive parses influence each other", function () {
    // Arrange: a long post (many titles) and a short one
    const longPost = Array.from({ length: 6 }, (_, i) => `<b>K${i}</b>: val${i}<br>`).join("");
    const shortPost = `<b>Alpha</b>: one<br><b>Beta</b>: two<br>`;
    const expectedShort = [
      { name: "Alpha", text: "one" },
      { name: "Beta", text: "two" }
    ];

    // Act: parse the long post first, then the short post twice
    const longResult = parseFragment(longPost);
    const shortResultA = parseFragment(shortPost);
    const shortResultB = parseFragment(shortPost);

    // Assert: the long post keeps every title (no regex/ID state poisoning)
    expect(longResult.length).to.be.equal(6);
    expect(longResult.every((e, i) => e.name === `K${i}` && e.text === `val${i}`)).to.be.true;

    // Assert: both short parses are identical and independent
    const simplify = (list: IPostElement[]) => list.map((e) => ({ name: e.name, text: e.text }));
    expect(simplify(shortResultA)).to.deep.equal(expectedShort);
    expect(simplify(shortResultB)).to.deep.equal(expectedShort);

    // Act & Assert: parsing the long post again after the short ones is stable
    const longResultAgain = parseFragment(longPost);
    expect(simplify(longResultAgain)).to.deep.equal(simplify(longResult));
  });
}
