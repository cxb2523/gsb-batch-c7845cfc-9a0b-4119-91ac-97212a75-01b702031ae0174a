//
// Fixture: a task kills its own worker process (simulating a crash or an
// external kill). The master must reject the in-flight task and keep
// running the remaining tasks on the surviving worker.
// Spawned as a real multi-process cluster by cluster-scheduler.test.ts.
//

import { Task } from "../../lib/task";
import { delay, observe, runCluster } from "./test-helpers";

Task.register("crash", async () => {
    // The worker process dies hard before reporting anything back.
    process.exit(1);
    // Unreachable, keeps the task looking asynchronous to the type system.
    return new Promise(() => {});
});
Task.register("slow", async (n: number) => {
    await delay(50);
    return n;
});

runCluster(2, { verbose: false, maxAllocations: 1 }, async scheduler => {
    const crashed = observe(Task.lookup("crash").create().run(scheduler));
    const rest = [1, 2, 3].map(n =>
        observe(Task.lookup("slow").create(n).run(scheduler))
    );

    const [crashedResult, ...restResults] = await Promise.all([crashed, ...rest]);

    return {
        crashed: crashedResult,
        rest: restResults,
    };
});
