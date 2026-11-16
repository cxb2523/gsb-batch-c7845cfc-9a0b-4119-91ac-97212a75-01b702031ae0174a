//
// Fixture: a task that throws rejects its own promise while the other
// tasks in the batch still complete successfully.
// Spawned as a real multi-process cluster by cluster-scheduler.test.ts.
//

import { Task } from "../../lib/task";
import { observe, runCluster } from "./test-helpers";

Task.register("boom", async () => {
    throw new Error("kaboom");
});
Task.register("ok", async (n: number) => n * 10);

runCluster(3, { verbose: false }, async scheduler => {
    const failing = observe(Task.lookup("boom").create().run(scheduler));
    const good1 = observe(Task.lookup("ok").create(1).run(scheduler));
    const good2 = observe(Task.lookup("ok").create(2).run(scheduler));
    const good3 = observe(Task.lookup("ok").create(3).run(scheduler));

    const [failed, r1, r2, r3] = await Promise.all([failing, good1, good2, good3]);

    return {
        failed,
        good: [r1, r2, r3],
    };
});
