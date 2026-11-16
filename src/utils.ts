// import

import * as pw from 'playwright';
import type { Browser } from 'playwright';

// types

type AutomationFn<T> = (b: Browser) => Promise<T>;

// export

/**
 * Manages the browser lifecycle for an automation function.
 *
 * We split the runner from the agents so that we can easily test them with `@playwright/test`. This abstraction is also a great starting point for managing config and parallelization.
 */
export async function runAutomation<T>(fn: AutomationFn<T>): Promise<T> {
  const browser = await pw.chromium.launch({
    devtools: false,
  });

  try {
    return await fn(browser);
  } finally {
    // Always release the browser, even if the automation settles with failures
    // or throws before it can return a result.
    await browser.close();
  }
}
