# puppeteer-service <a href="https://www.npmjs.com/package/puppeteer-service"><img src="https://img.shields.io/npm/v/puppeteer-service.svg"></a>

<img src="https://user-images.githubusercontent.com/10379601/29446482-04f7036a-841f-11e7-9872-91d1fc2ea683.png" height="200" align="right">

🎠 Run [GoogleChrome/puppeteer](https://github.com/GoogleChrome/puppeteer) as a service.

## Usage

### Server

```bash
$ npm install puppeteer-service --save
```

```js
const PuppeteerService = require('puppeteer-service');
const { koaApp, server } = PuppeteerService({
    cluster: true, // default: false
    port: 3000, // default
    api: 'run', // default
    test: true, // default: false
    puppeteer: {
        // See https://github.com/GoogleChrome/puppeteer/blob/master/docs/api.md#puppeteerlaunchoptions
        headless: true, // default
        args: ['--no-sandbox']
    }
});
```

😯 If the `test` option is set `true` like above, you can visit the test page via `http://your.host:3000/test/`.

### Client

#### 👉 Option 1: Use puppeteer-service-client

```bash
$ npm install puppeteer-service-client --save
```

Use [puppeteer-service-client](https://github.com/shenfe/puppeteer-service-client) to communicate with the server. It's runnable at **both browser and Node.js**.

```js
const Run = require('puppeteer-service-client');
Run('http://your.host:3000/run', {
    /* Entry page url */
    url: 'https://target.com/',

    /* Runner function */
    run: async page => {
        const title = await page.title();
        echo({ url: page.url(), title });
        return {
            info: b(a, title)
        };
    },

    /* Options (Optional) */
    options: {
        /* Variables to inject */
        /* Identifiers and their corresponding literal values will be injected 
            as variable declarations into the runner function. */
        injection: {
            a: 'Welcome to ',
            b: function (x, y) {
                return x + y;
            }
        }
    },

    /* WebSocket data handler (Optional) */
    socket: data => {
        /**/
    }
})
    .then(data => {
        /**/
    }).catch(error => {
        /**/
    });
```

**socket and echo**

The `socket` option specifies a handler for WebSocket data at client side. Correspondingly, the function `echo`, which is callable inside the "page runner function", is a built-in function whose responsibility is to transfer data to the right socket connection with the client.

#### 👉 Option 2: Send a request directly

As the following does:

```js
const pageRunner = async page => {
    const title = await page.title();
    return {
        info: b(a, title)
    };
};
fetch('http://your.host:3000/run', {
    method: 'POST',
    /*...*/
    headers: {
        'Content-Type': 'application/json'
    },
    body: JSON.stringify({
        data: `{
            url: 'https://www.sogou.com',
            run: ${pageRunner},
            options: {
                injection: {
                    a: 'Welcome to ',
                    b: function (x, y) {
                        return x + y;
                    }
                }
            }
        }`
    })
})
    .then(res => {
        if (res.ok) return res.json();
        throw new Error('Response is not ok');
    })
    .then(data => {
        /**/
    }).catch(error => {
        /**/
    });
```

⚠️ This way is lightweight but too simple to communicate with the server via WebSocket.

## Development

Some commands:

```bash
npm start # start
npm start -- -p 3000 # port
npm start -- -c # cluster
npm run debug # debugging mode
npm test # test
npm test -- -u http://127.0.0.1:3000/run # api url
npm test -- -n 10 # batch number
```

### Page pool tests

Every crawl request borrows a page from a pool (`src/scheduler.js`) and returns
it when it is done. The pool is capped at `poolLimit` pages (100 by default);
once it is full, further requests queue and are woken in submission order. A
page is returned whether the crawl succeeds, the navigation fails or the runner
script throws — a failed crawl still answers with `{}`.

Three levels of tests cover that behaviour, from cheapest to most realistic:

```bash
npm run test:pool            # unit tests, fake browser, no Chromium needed
npm run test:pool:e2e        # real headless Chromium + local page server
npm run test:pool:service    # real service, real concurrent POST /run requests
```

* `test/pool.test.js` — drives the broker with a fake browser. It can make a
  reset fail, a page crash, `newPage()` hang, or `close()` be called twice, and
  asserts the FIFO wake-up order, that a page is never lent twice at once, and
  that capacity never shrinks after a failure.
* `test/pool.integration.js` — starts a local HTTP server whose responses can be
  held, delayed or aborted, then runs `Ppt.run()` concurrently against it with
  `poolLimit: 2`. It checks that queued crawls are served in order, that
  failures return `{}` and leave the pool at full capacity, and that a page
  that dies while lent is replaced.
* `test/pool.service.js` — boots the real Koa service and fires concurrent
  `POST /run` requests at it, i.e. the same path a client uses.

To build a failure sample by hand: point a request at a URL whose connection is
dropped (`/boom` in the tests), or pass a runner that throws. To build a
concurrency sample: set `poolLimit` to a small number and issue more requests
than that at once — the extra ones queue until a page comes back.

`CHROME_EXE=/path/to/chrome` can be set for the two Chromium-based tests to use
a specific browser binary instead of the one bundled with `puppeteer`.

## License

[MIT](http://opensource.org/licenses/MIT)

Copyright © 2018-present, [shenfe](https://github.com/shenfe)
