//
// Fixture: with no taskTimeout option the scheduler keeps the original
// behaviour and waits for a long-running task instead of failing it.
// Spawned as a real multi-process cluster by cluster-scheduler.test.ts.
//

import { Task } from "../../lib/task";
import { delay, observe, runCluster } from "./test-helpers";

Task.register("long", async () => {
    await delay(400);
    return "finally-done";
});
Task.register("short", async (n: number) => n * 3);

runCluster(2, { verbose: false, maxAllocations: 1 }, async scheduler => {
    // No taskTimeout option: the long task must be awaited, not failed.
    const long = observe(Task.lookup("long").create().run(scheduler));
    const short = observe(Task.lookup("short").create(7).run(scheduler));

    const [longResult, shortResult] = await Promise.all([long, short]);

    return {
        long: longResult,
        short: shortResult,
    };
});
