//
// Fixture: the master forcibly kills the worker process that is running a
// hung task (the "killed by another process" case). Its in-flight task is
// rejected and the other workers keep draining the queue.
// Spawned as a real multi-process cluster by cluster-scheduler.test.ts.
//

import * as cluster from "cluster";
import * as process from "process";
import { Task } from "../../lib/task";
import { delay, observe, runCluster } from "./test-helpers";

// Set on the master by the "hang-started" message sent from the busy worker.
let hungWorkerPid: number | undefined;

Task.register("hang", async () => {
    // Tell the master which OS process is running this task, then never finish.
    if (cluster.isWorker) {
        process.send!({ type: "hang-started", pid: process.pid });
    }
    return new Promise(() => {}); // Never resolves: only the forced kill ends it.
});
Task.register("quick", async (n: number) => {
    await delay(30);
    return n + 100;
});

//
// Forcibly terminate a worker process from the master (i.e. from outside the
// worker's own task). Node's process.kill maps to TerminateProcess on Windows
// and SIGKILL on POSIX, so no platform-specific shell tool is needed.
//
function forceKill(pid: number): void {
    process.kill(pid, "SIGKILL");
}

runCluster(3, { verbose: false, maxAllocations: 1 }, async scheduler => {
    // Listen for the worker that picks up the hang task.
    for (const workerId of Object.keys(cluster.workers)) {
        const worker = cluster.workers[workerId];
        if (worker) {
            worker.on("message", (msg: any) => {
                if (msg && msg.type === "hang-started") {
                    hungWorkerPid = msg.pid;
                }
            });
        }
    }

    // Dispatched first; occupies one worker forever.
    const hung = observe(Task.lookup("hang").create().run(scheduler));

    // Wait until the busy worker has reported itself, then kill it externally.
    for (let i = 0; i < 150 && hungWorkerPid === undefined; ++i) {
        await delay(20);
    }
    if (hungWorkerPid === undefined) {
        throw new Error("The hang task was never dispatched to a worker.");
    }
    forceKill(hungWorkerPid);

    // Queued tasks must still run on the two surviving workers.
    const survivors = [1, 2, 3, 4].map(n =>
        observe(Task.lookup("quick").create(n).run(scheduler))
    );

    const [hungResult, ...survivorResults] = await Promise.all([hung, ...survivors]);

    return {
        hung: hungResult,
        survivors: survivorResults,
    };
});
