//
// Fixture: a normal task graph produces correct results.
// Spawned as a real multi-process cluster by cluster-scheduler.test.ts.
//

import { Task } from "../../lib/task";
import { runCluster } from "./test-helpers";

Task.register("add", async (a: number, b: number) => a + b);
Task.register("double", async (n: number) => n * 2);
Task.register("answer", async (n: number) => n + 1);

runCluster(3, { verbose: false }, async scheduler => {
    // (10 + 20) * 2 + 1 = 61, tasks fan out across the workers.
    const sum = Task.lookup("add").create(10, 20);
    const doubled = Task.lookup("double").create(sum);
    const finalAnswer = Task.lookup("answer").create(doubled);

    const graphResult = await finalAnswer.run(scheduler);

    // A batch of independent tasks all complete as well.
    const batch = await Promise.all(
        [1, 2, 3, 4].map(n => Task.lookup("double").create(n).run(scheduler))
    );

    return { graphResult, batch };
});
