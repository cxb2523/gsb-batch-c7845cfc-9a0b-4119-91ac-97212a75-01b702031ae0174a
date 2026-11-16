import * as cluster from "cluster";
import { IScheduler } from "./scheduler";
import { v4 as uuid } from "uuid";
import { ITask, Task, TaskFn } from "./task";
import { INSPECT_MAX_BYTES } from "buffer";

//
// Options to the cluster scheduler.
//
export interface IClusterSchedulerOptions {
    //
    // Enable verbose debug logging.
    //
    verbose?: boolean;

    //
    // Use this to set the maximum task allocations per worker.
    //
    maxAllocations?: number;

    //
    // Optional per-task timeout in milliseconds.
    // When set, any task that doesn't settle within this time is failed on the
    // master process instead of waiting forever for its worker to respond.
    // Omit (or set to 0) to disable timeouts, preserving the default behaviour.
    //
    taskTimeout?: number;
}

//
// Records details for a work and when it is busy.
//
export interface IWorkerRecord {
    //
    // The index of the worker, good for debugging.
    //
    workerIndex: number;

    //
    // The unique ID of the worker.
    //
    workerId: string;

    //
    // Records how many tasks a worker is running.
    //
    allocations: number;

    //
    // Records the maximum number of tasks a worker can run.
    //
    maxAllocations: number;

    //
    // The Node.js cluster object that represents the worker.
    //
    worker: cluster.Worker;

    //
    // Tasks currently executing on this worker, keyed by task ID.
    // Used to reject in-flight tasks if the worker process exits unexpectedly.
    //
    executingTasks: ITaskMap;

    //
    // Set to true once the worker process has exited.
    // A dead worker is removed from rotation and must never be scheduled again.
    //
    isDead: boolean;

    //
    // Set to true once the worker has signalled that its application code is
    // loaded and its message handler is ready to accept "run-task" messages.
    //
    isReady?: boolean;
}

//
// Maps workers to their details by unique ID>
//
export interface IWorkerMap {
    [index: string]: IWorkerRecord;
}

//
// Records a task that is scheduled to run.
//
export interface ITaskRecord {
    inputs: any[]; //fio: are all these fields needed.

    //
    // The unique ID of the task.
    // Used to track the task on the master process.
    //
    taskId: string;

    //
    // The name of the task.
    // Used to lookup the task in the worker process.
    //
    taskName: string;

    //
    // Promise to be resolved when the task completes successfully.
    //
    resolve: (result: any) => void;

    //
    // Promise to be rejected if the tasks throws an error.
    //
    reject: (error: any) => void;

    //
    // The ID of the worker the task was dispatched to.
    // Undefined while the task is still queued.
    //
    workerId?: string;

    //
    // Handle for the optional per-task timeout timer.
    //
    timeoutHandle?: NodeJS.Timer;

    //
    // Guards against resolving/rejecting the task's promise more than once.
    // Every completion path (worker response, timeout, worker exit) must go
    // through settleTask so that each task is settled exactly once.
    //
    settled?: boolean;
}

//
// Lookup table for tasks by unique ID.
//
export interface ITaskMap {
    [index: string]: ITaskRecord;
}

//
// An implementation of a scheduler that creates worker processes for running tasks.
// Uses the Node.js cluster module.
//
export class ClusterScheduler implements IScheduler {

    //
    // Enable debug logging.
    //
    private enableVerboseLogging: boolean;

    //
    // Maximum tasks that can be allocated per worker at any one time.
    //
    private maxAllocations: number;

    //
    // Optional per-task timeout in milliseconds. 0 means no timeout.
    //
    private taskTimeout: number;

    //
    // Number of worker processes to create.
    //
    private numWorkers: number;

    //
    // A list of all worker processes created.
    //
    private workers: cluster.Worker[] = [];

    //
    // A lookup table of workers by unique ID.
    //
    private workerMap: IWorkerMap = {};

    //
    // A queue of tasks to be run.
    //
    private taskQueue: ITaskRecord[] = [];

    //
    // How many forked workers have emitted their "online" event so far.
    //
    private onlineWorkerCount: number = 0;

    //
    // How many forked workers have signalled they are ready to run tasks.
    //
    private readyWorkerCount: number = 0;

    //
    // How many forked workers have exited before/during operation.
    //
    private deadWorkerCount: number = 0;

    //
    // Tasks currenlty executing.
    //
    private pendingTasks: ITaskMap = {};

    //
    // Records if I'm master or worker.
    //
    private whoami: string = "unknown";

    //
    // Resolves once every forked worker has signalled it is ready (or exited).
    // Used by init() to wait for full readiness before running mainFn.
    //
    private workersReady!: Promise<void>;

    //
    // Stored resolver for the workers-ready promise.
    //
    private workersReadyResolve!: () => void;

    constructor(numWorkers: number, options?: IClusterSchedulerOptions) {
        this.numWorkers = numWorkers;
        this.enableVerboseLogging = options && options.verbose || false;
        this.maxAllocations = options && options.maxAllocations || 1;
        this.taskTimeout = options && options.taskTimeout || 0;

        this.workersReady = new Promise<void>(resolve => {
            this.workersReadyResolve = resolve;
        });
    }

    //
    // Call this function to initialize the cluster and fork worker processes.
    // Calls user-defined "mainFn" to run in the master process.
    //
    public async init(mainFn: () => Promise<void>): Promise<void> {

        if (cluster.isMaster) {
            this.whoami = "MASTER";

            // Running on the master.
            this.verbose(`Starting ${this.numWorkers} worker processes.`);

            for (let workerIndex = 0; workerIndex < this.numWorkers; ++workerIndex) {
                const workerId = uuid();
                const worker = cluster.fork({
                    WORKER_ID: workerId,
                    WORKER_INDEX: workerIndex,
                });
                this.trackWorker(workerIndex, workerId, worker, this.maxAllocations);
            }

            // Don't run application code until every worker is ready, so task
            // timeouts only measure real execution rather than process startup.
            await this.workersReady;

            await mainFn();

            this.shutdownWorkers();

            this.verbose("Master done.");
        }
        else {
            this.whoami = `WORKER[${process.env.WORKER_INDEX}]`;

            this.initWorker();

            // Tell the master the worker's message handler is installed and it
            // is ready to accept "run-task" messages. This is more reliable
            // than the cluster "online" event, which fires before the worker's
            // application code has finished loading (e.g. under ts-node).
            process.send!({
                type: "worker-ready",
                workerId: process.env.WORKER_ID,
            });

            this.verbose("Worker online.");
        }
    }

    //
    // Run a task when possible and resolve promise when completed.
    // Rejects the promise if the task throws an error.
    //
    public runTask(inputs: any[], task: ITask<any>): Promise<any> {

        if (cluster.isWorker) {
            return this.sendTask(task, inputs);
        }
        else {
            const taskId = task.getTaskId();
            const taskName = task.getTaskDef().getTaskName();
            return this.queueTask(inputs, taskId, taskName);
        }
    }

    //
    // Send a task to the master to be queued.
    //
    private sendTask(task: ITask<any>, inputs: any[]): Promise<void> {
        if (!cluster.isWorker) {
            throw new Error("Expect sendTask to only run on a worker.");
        }

        const taskId = task.getTaskId();
        const taskName = task.getTaskDef().getTaskName();
        this.verbose(`Sending task ${taskName} (${taskId}) to master.`);
        // Track the task so that the promise can be resolved once it's done.
        const taskPromise = new Promise<any>((resolve, reject) => {
            this.pendingTasks[taskId] = {
                inputs,
                taskId,
                taskName,
                resolve,
                reject,
            };
        });
        // Send the task to the master.
        process.send!({
            type: "queue-task",
            inputs,
            taskId,
            taskName,
            workerId: process.env.WORKER_ID,
        });

        return taskPromise;
    }

    //
    // Queue a task on the master.
    //
    private queueTask(inputs: any[], taskId: string, taskName: string): Promise<any> {
        if (!cluster.isMaster) {
            throw new Error("Expect queueTask to only run on the master.");
        }

        this.verbose(`Queuing task ${taskName} (${taskId}) on master.`);
        const taskPromise = new Promise<any>((resolve, reject) => {
            this.taskQueue.push({
                inputs,
                taskId,
                taskName,
                resolve,
                reject,
            });
        });

        this.scheduleTasks(); // Run tasks if workers are currently free.

        return taskPromise;
    }

    //
    // Optional verbose logging.
    //
    private verbose(msg: any) {
        if (this.enableVerboseLogging) {
            console.log(this.whoami + ": " + msg);
        }
    }

    //
    // Resolve the workers-ready promise exactly once.
    //
    private markWorkersReady(): void {
        if (this.workersReadyResolve) {
            this.workersReadyResolve();
        }
    }

    //
    // Track a worker process that was created.
    //
    private trackWorker(workerIndex: number, workerId: string, worker: cluster.Worker, maxAllocations: number) {
        this.workers.push(worker);
        this.workerMap[workerId] = {
            workerIndex,
            workerId,
            allocations: 0,
            maxAllocations,
            worker,
            executingTasks: {},
            isDead: false,
            isReady: false,
        };

        worker.on("online", () => {
            // Tracked for diagnostics; readiness itself is driven by the
            // worker's explicit "worker-ready" handshake below.
            ++this.onlineWorkerCount;
        });

        worker.on("message", msg => {

            if (msg.type === "worker-ready") { // Worker has loaded its code and installed its message handler.
                const readyRecord = this.workerMap[workerId];
                if (readyRecord && !readyRecord.isDead && !readyRecord.isReady) {
                    readyRecord.isReady = true;
                    ++this.readyWorkerCount;
                    this.checkWorkersReady();
                }
                return;
            }

            if (msg.type === "task-complete") { // Worker notifying master that a task has completed.
                const taskRecord = this.pendingTasks[msg.taskId];
                if (!taskRecord || taskRecord.settled) {
                    // The task was already settled (e.g. it timed out) or never
                    // existed, so this is a late response from the worker.
                    this.verbose(`Ignoring late completion for unknown/settled task (${msg.taskId}).`);
                    return;
                }

                this.completeTask(taskRecord, msg.result);
            }
            else if (msg.type === "task-error") { // Worker notifying master that a task has thrown an error.
                const taskRecord = this.pendingTasks[msg.taskId];
                if (!taskRecord || taskRecord.settled) {
                    // Late error response for a task that was already settled.
                    this.verbose(`Ignoring late error for unknown/settled task (${msg.taskId}).`);
                    return;
                }

                this.verbose(`Task ${taskRecord.taskName} (${taskRecord.taskId}) has thrown error:`);
                this.verbose(msg.error);

                this.failTask(taskRecord, msg.error);
            }
            else if (msg.type === "queue-task") { // Worker requesting master to queue a task.
                const requestingWorker = this.workerMap[msg.workerId];
                if (!requestingWorker || requestingWorker.isDead) {
                    // The requesting worker has already gone; nothing to do.
                    return;
                }

                requestingWorker.maxAllocations += 1; // When a worker requests a task be queued we increase its max allocations by 1 to help avoid deadlocks.

                this.queueTask(msg.inputs, msg.taskId, msg.taskName)
                    .then(result => {
                        if (worker.isConnected()) { // The requesting worker may have died while waiting.
                            worker.send({
                                type: "task-complete",
                                taskId: msg.taskId,
                                result,
                            });
                        }
                    })
                    .catch(err => {
                        if (worker.isConnected()) {
                            worker.send({
                                type: "task-error",
                                taskId: msg.taskId,
                                error: err && err.stack || err.toString(),
                            });
                        }
                    })
                    .then(() => {
                        const currentWorker = this.workerMap[msg.workerId];
                        if (currentWorker) { // The worker may have exited while its nested task was running.
                            currentWorker.maxAllocations -= 1;
                        }
                    });
            }
            else {
                // Unknown messages (e.g. application-specific IPC from a task)
                // are ignored so they can never take down the master process.
                this.verbose(`Ignoring unrecognised message ${msg.type} from worker.`);
            }
        });

        //
        // A worker process has exited. Anything still running on it can never
        // respond, so fail those tasks explicitly and take the worker out of
        // rotation so that no further tasks are dispatched to it.
        //
        worker.on("exit", (code, signal) => {
            this.handleWorkerExit(workerId, code, signal);
        });

        //
        // Log worker errors rather than letting them crash the master process.
        //
        worker.on("error", err => {
            this.verbose(`Worker ${workerId} raised an error:`);
            this.verbose(err && err.stack || err.toString());
        });
    }

    //
    // Resolve the readiness barrier once every worker is either ready to run
    // tasks or has already exited, so startup can never hang.
    //
    private checkWorkersReady(): void {
        const accountedFor = this.readyWorkerCount + this.deadWorkerCount;
        if (accountedFor >= this.numWorkers) {
            this.markWorkersReady();
        }
    }

    //
    // Handle termination of a worker process.
    // Rejects every task that was still executing on that worker, removes the
    // worker from the available pool and schedules queued tasks on the
    // remaining live workers.
    //
    private handleWorkerExit(workerId: string, code: number, signal: string): void {
        const workerRecord = this.workerMap[workerId];
        if (!workerRecord || workerRecord.isDead) {
            return; // Already handled.
        }

        ++this.deadWorkerCount;

        // Never leave the readiness barrier hanging if a worker dies during startup.
        this.checkWorkersReady();

        workerRecord.isDead = true;
        this.verbose(`Worker ${workerId} exited (code=${code}, signal=${signal}), failing its in-flight tasks.`);

        // Remove the dead worker first so it can never receive new tasks,
        // even while the settle callbacks below trigger rescheduling.
        delete this.workerMap[workerId];
        this.workers = this.workers.filter(w => w.id !== workerRecord.worker.id);

        // Reject each task that was still executing on the dead worker.
        const abandonedTasks = Object.keys(workerRecord.executingTasks)
            .map(taskId => workerRecord.executingTasks[taskId]);
        for (const taskRecord of abandonedTasks) {
            this.failTask(taskRecord, new Error(
                `Worker process ${workerId} exited (code=${code}, signal=${signal}) ` +
                `while executing task ${taskRecord.taskName} (${taskRecord.taskId}).`
            ));
        }

        // Reclaim any allocations that weren't accounted for by the failed tasks.
        workerRecord.allocations = 0;
        workerRecord.executingTasks = {};

        // Hand the queued work over to whatever workers are still alive.
        this.scheduleTasks();
    }

    //
    // Settle a task exactly once. Cleans up all bookkeeping shared by the
    // completion, error, timeout and worker-exit paths.
    //
    private settleTask(taskRecord: ITaskRecord, settle: () => void): void {
        if (taskRecord.settled) {
            // Every failure path funnels through here, so a task can only ever
            // be resolved or rejected a single time.
            return;
        }
        taskRecord.settled = true;

        if (taskRecord.timeoutHandle) {
            clearTimeout(taskRecord.timeoutHandle as NodeJS.Timer);
            taskRecord.timeoutHandle = undefined;
        }

        if (taskRecord.workerId) {
            const workerRecord = this.workerMap[taskRecord.workerId];
            if (workerRecord) {
                delete workerRecord.executingTasks[taskRecord.taskId];
            }
        }

        delete this.pendingTasks[taskRecord.taskId];

        settle();

        // A slot has freed up (or the worker is gone), try to run queued tasks.
        this.scheduleTasks();
    }

    //
    // Resolve a task's promise exactly once and release its allocation.
    //
    private completeTask(taskRecord: ITaskRecord, result: any): void {
        this.verbose(`Task ${taskRecord.taskName} (${taskRecord.taskId}) has completed.`);

        const workerRecord = taskRecord.workerId && this.workerMap[taskRecord.workerId];
        this.settleTask(taskRecord, () => {
            if (workerRecord) {
                workerRecord.allocations -= 1;
            }
            taskRecord.resolve(result); // Resolve the task's promise.
        });
    }

    //
    // Reject a task's promise exactly once and release its allocation.
    // Works whether the worker is still alive (task error / timeout) or has
    // already been removed (worker exit).
    //
    private failTask(taskRecord: ITaskRecord, error: any): void {
        const workerRecord = taskRecord.workerId && this.workerMap[taskRecord.workerId];
        this.settleTask(taskRecord, () => {
            if (workerRecord) {
                workerRecord.allocations -= 1; // Reclaim the worker's allocation.
            }
            taskRecord.reject(error); // Reject the task's promise.
        });
    }

    //
    // Arm the optional per-task timeout. When it fires the task is failed on
    // the master without waiting for the worker; the task is never requeued so
    // it cannot be dispatched a second time.
    //
    private armTaskTimeout(taskRecord: ITaskRecord): void {
        if (!this.taskTimeout) {
            return; // No timeout configured: keep the original waiting behaviour.
        }

        taskRecord.timeoutHandle = setTimeout(() => {
            if (taskRecord.settled) {
                return; // The worker responded first, nothing to do.
            }

            this.verbose(`Task ${taskRecord.taskName} (${taskRecord.taskId}) timed out after ${this.taskTimeout}ms.`);

            this.failTask(taskRecord, new Error(
                `Task ${taskRecord.taskName} (${taskRecord.taskId}) timed out after ${this.taskTimeout}ms.`
            ));
        }, this.taskTimeout);
    }

    //
    // Code to run in the work process to initalized.
    //
    private initWorker(): void {
        process.on("message",  msg => {
            if (msg.type === "exit") { // Master has instructed the worker to shutdown.
                this.verbose("Exiting worker.");
                process.exit(0);
            }
            else if (msg.type === "run-task") { // Master has instructed the worker to run a task.

                this.verbose(`Running task ${msg.taskName} (${msg.taskId}) on worker.`);

                const taskDef = Task.lookup(msg.taskName); // Look up the task by name.
                const taskFn = taskDef.getTaskFn();
                taskFn(...msg.inputs, this) // Execute the task's function, passing in direct inputs.
                    .then(result => { // Worker completed sucessfully.

                        this.verbose(`Task ${msg.taskName} (${msg.taskId}) has completed on worker.`);

                        process.send!({ // Tell the master the task has completed.
                            type: "task-complete",
                            taskId: msg.taskId,
                            result,
                            workerId: process.env.WORKER_ID,
                        });
                    })
                    .catch(err => { // Worker has thrown an error.

                        this.verbose(`Task ${msg.taskName} (${msg.taskId}) has errored on worker.`);
                        this.verbose(err && err.stack || err.toString());

                        process.send!({ // Tell the master the task has errored.
                            type: "task-error",
                            taskId: msg.taskId,
                            error: err && err.stack || err.toString(),
                            workerId: process.env.WORKER_ID,
                        });
                    });
            }
            else if (msg.type === "task-complete") { // Master telling process that a task it queued has completed.
                const taskRecord = this.pendingTasks[msg.taskId];
                this.verbose(`Task ${taskRecord.taskName} (${taskRecord.taskId}) has completed.`);

                delete this.pendingTasks[msg.taskId];
                taskRecord.resolve(msg.result); // Resolve the task's promise.
            }
            else if (msg.type === "task-error") { // Master telling process that a task it queued has errorred.
                const taskRecord = this.pendingTasks[msg.taskId];
                this.verbose(`Task ${taskRecord.taskName} (${taskRecord.taskId}) has thrown error:`);
                this.verbose(msg.error);

                delete this.pendingTasks[msg.taskId];
                taskRecord.reject(msg.error); // Reject the task's promise.
           }
            else {
                throw new Error(`Unrecognised message ${msg.type} from master.`);
            }
        });
    }

    //
    // Run a task if there is a task to run and there is a worker available to run it.
    //
    private scheduleTask(): boolean {

        if (!cluster.isMaster) {
            throw new Error("Can only schedule tasks on the master.");
        }

        if (this.taskQueue.length <= 0) {
            // No tasks to be executed.
            return false;
        }

        const freeWorkers = Object.keys(this.workerMap)
            .filter(workerId => !this.workerMap[workerId].isDead) // Never dispatch to a worker that has exited.
            .filter(workerId => this.workerMap[workerId].isReady) // Only workers that signalled readiness.
            .filter(workerId => this.workerMap[workerId].allocations < this.workerMap[workerId].maxAllocations)
            .map(workerId => this.workerMap[workerId]);
        if (freeWorkers.length <= 0) {
            // No worker is available.
            this.verbose("Task are ready, no workers are free.");
            return false;
        }

        // Sort workers so that workers with less allocations come first.
        freeWorkers.sort((worker1, worker2) => worker1.allocations - worker2.allocations);

        const nextTask = this.taskQueue.shift()!; // Remove the next task.
        this.pendingTasks[nextTask.taskId] = nextTask;

        const nextFreeWorker = freeWorkers[0]; // Get the next free worker.
        ++nextFreeWorker.allocations;

        // Bind the task to this worker so an unexpected exit can fail it.
        nextTask.workerId = nextFreeWorker.workerId;
        nextFreeWorker.executingTasks[nextTask.taskId] = nextTask;

        this.armTaskTimeout(nextTask); // Start the optional per-task timeout.

        this.verbose(`Scheduling task ${nextTask.taskName} (${nextTask.taskId}) on worker.`);

        nextFreeWorker.worker.send({ // Instruct the worker to run the task.
            type: "run-task",
            taskId: nextTask.taskId,
            taskName: nextTask.taskName,
            inputs: nextTask.inputs,
        });

        return true;
    }

    //
    // Schedule one or more tasks to run on worker processes.
    //
    private scheduleTasks() {
        while (true) { // Keep scheduling tasks as long as there are tasks to run and workers free.
            if (!this.scheduleTask()) {
                break; // No more tasks or no more workers. Just have to wait now.
            }
        }
    }

    //
    // Shutdown all the workers.
    //
    private shutdownWorkers(): void {
        for (const worker of this.workers) {
            if (worker.isConnected()) { // Don't bother workers that have already exited.
                worker.send({ type: "exit" }); // Instruct workers to shutdown.
            }
        }
    }

}
