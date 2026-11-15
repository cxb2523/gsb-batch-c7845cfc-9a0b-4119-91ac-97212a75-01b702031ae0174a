# top-crawler-agents

[![Last version](https://img.shields.io/github/v/tag/Kikobeats/top-crawler-agents?style=flat-square)](https://github.com/Kikobeats/top-crawler-agents/releases)
[![NPM Status](https://img.shields.io/npm/dm/top-crawler-agents?style=flat-square)](https://www.npmjs.com/package/top-crawler-agents)

> A list of common crawler user agents useful for retrieving metadata from links<br>
> derivated from [crawler-user-agents](https://github.com/monperrus/crawler-user-agents).

Some websites (such as [Twitter](https://twitter.com/Kikobeats/status/1687844145019092993)) only return rich HTML metadata if you are identified as popular crawler (like Slack, or WhatsApp).

The list is derivated from [crawler-user-agents](https://github.com/monperrus/crawler-user-agents) after applying a [script](/scripts/generate.js), so it's always up-to-date to latest changes.

## Verification

Every candidate User-Agent is checked against real pages during generation.
The result of each check is persisted to
[`verification-report.json`](/verification-report.json), which is sorted by
User-Agent and contains one entry per candidate:

- `userAgent`: the User-Agent string
- `outcome`: one of `passed`, `http-error` (the page did not respond with 200),
  `request-error` (the request failed, including timeouts) or
  `content-check-failed` (the response was 200 but missed the expected metadata)
- `statusCode`: the HTTP status code, or `null` when the request failed
- `error`: the error message, or `null`

Only `passed` User-Agents are written to `index.json`. If every candidate
fails, `index.json` is left untouched and the generation exits with a
non-zero status code.

## Install

```bash
$ npm install top-crawler-agents --save
```

## Usage

```js
const uniqueRandomArray = require('unique-random-array')

const randomCrawlerAgent = uniqueRandomArray(require('top-crawler-agents'))

console.log(randomCrawlerAgent())
// => 'Slackbot-LinkExpanding (+https://api.slack.com/robots)'
```

## Related

- [top-user-agents](https://github.com/Kikobeats/top-user-agents) – A list of most common User Agent used on Internet.

**top-crawler-agents** © [Kiko Beats](https://kikobeats.com), released under the [MIT](https://github.com/Kikobeats/top-crawler-agents/blob/master/LICENSE.md) License.<br>
Authored and maintained by [Kiko Beats](https://kikobeats.com) with help from [contributors](https://github.com/Kikobeats/top-crawler-agents/contributors).

> [kikobeats.com](https://kikobeats.com) · GitHub [Kiko Beats](https://github.com/Kikobeats) · X [@Kikobeats](https://x.com/Kikobeats)
