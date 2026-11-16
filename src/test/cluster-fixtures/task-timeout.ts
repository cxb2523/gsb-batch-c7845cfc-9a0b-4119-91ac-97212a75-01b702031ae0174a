//
// Fixture: a per-task timeout fails a task whose worker never responds.
// The task must settle exactly once (by rejection) and must never be
// dispatched a second time, while normal tasks still complete.
// Spawned as a real multi-process cluster by cluster-scheduler.test.ts.
//

import * as cluster from "cluster";
import { Task } from "../../lib/task";
import { delay, observe, runCluster } from "./test-helpers";

Task.register("slow", async () => {
    // Much longer than the configured timeout, and long enough that the
    // worker's late completion can only arrive well after the scenario ends.
    await delay(10000);
    return "slow-result";
});
Task.register("fast", async (n: number) => {
    await delay(20);
    return n + 1;
});
Task.register("ping", async () => {
    // Lets the test confirm worker processes are still alive and serving
    // tasks after the timed-out task was abandoned.
    return "pong";
});

runCluster(2, { verbose: false, maxAllocations: 1, taskTimeout: 1500 }, async scheduler => {
    // Count every "run-task" instruction the master sends to a worker so the
    // test can prove a timed-out task is never dispatched more than once.
    let slowDispatches = 0;
    let totalDispatches = 0;
    for (const workerId of Object.keys(cluster.workers)) {
        const worker = cluster.workers[workerId];
        if (worker) {
            const originalSend = worker.send.bind(worker);
            worker.send = (message: any, ...rest: any[]) => {
                if (message && message.type === "run-task") {
                    ++totalDispatches;
                    if (message.taskName === "slow") {
                        ++slowDispatches;
                    }
                }
                return originalSend(message, ...rest);
            };
        }
    }

    const slow = observe(Task.lookup("slow").create().run(scheduler));
    const fast = observe(Task.lookup("fast").create(41).run(scheduler));
    const ping = observe(Task.lookup("ping").create().run(scheduler));

    const [slowResult, fastResult, pingResult] = await Promise.all([slow, fast, ping]);

    // Keep the cluster alive long enough that a hypothetical second dispatch
    // of the slow task (or a late worker response) would have surfaced.
    await delay(500);

    return {
        slow: slowResult,
        fast: fastResult,
        ping: pingResult,
        slowDispatches,
        totalDispatches,
    };
});
