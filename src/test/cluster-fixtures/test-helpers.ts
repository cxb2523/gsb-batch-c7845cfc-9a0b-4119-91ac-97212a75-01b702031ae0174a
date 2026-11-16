//
// Helpers shared by the cluster-scheduler acceptance test fixtures.
// These files are real cluster entry points spawned as separate Node processes
// by cluster-scheduler.test.ts; they are not Jest test files themselves.
//

import * as cluster from "cluster";
import * as path from "path";
import { ClusterScheduler } from "../../lib/cluster-scheduler";

//
// Result reported by a spawned scenario back to the Jest test runner.
//
export interface IScenarioResult {
    status: "ok" | "error";
    error?: string;
    data?: any;
}

//
// Wait for the given number of milliseconds.
//
export function delay(ms: number): Promise<void> {
    return new Promise<void>(resolve => setTimeout(resolve, ms));
}

//
// Print the scenario result in a machine-readable line, then return.
// The Jest runner reads the last line that starts with this marker.
//
export function emitResult(data: any): void {
    const result: IScenarioResult = {
        status: "ok",
        data,
    };
    console.log("SCENARIO_RESULT " + JSON.stringify(result));
}

//
// Bootstrap a cluster, run the scenario and always emit a result line,
// even if the scenario itself throws.
//
export async function runCluster(
    numWorkers: number,
    options: any,
    scenario: (scheduler: ClusterScheduler) => Promise<any>
): Promise<void> {
    // This module is loaded by every forked worker too. Only the master runs
    // the scenario and emits a result; workers just initialise via init().
    if (!cluster.isMaster) {
        const workerScheduler = new ClusterScheduler(numWorkers, options);
        await workerScheduler.init(async () => {});
        return;
    }

    const scheduler = new ClusterScheduler(numWorkers, options);
    try {
        let data: any;
        await scheduler.init(async () => {
            data = await scenario(scheduler);
        });
        emitResult(data);
    } catch (err) {
        const result: IScenarioResult = {
            status: "error",
            error: err && err.stack || err && err.toString() || String(err),
        };
        console.log("SCENARIO_RESULT " + JSON.stringify(result));
    }
}

//
// Wrap a task promise so the test can observe exactly how (and how many
// times) it settles. Rejections are caught here so an unhandled rejection
// can never crash the master process before the result is emitted.
//
export interface ISettlement {
    state: "fulfilled" | "rejected";
    value?: any;
    reason?: string;
    settleCount: number;
}

export function observe(promise: Promise<any>): Promise<ISettlement> {
    const settlement: ISettlement = {
        state: undefined as any,
        settleCount: 0,
    };

    return promise
        .then((value: any) => {
            settlement.settleCount += 1;
            settlement.state = "fulfilled";
            settlement.value = value;
            return settlement;
        })
        .catch((err: any) => {
            settlement.settleCount += 1;
            settlement.state = "rejected";
            settlement.reason = err && err.message || (err && err.toString()) || String(err);
            return settlement;
        });
}

//
// Absolute path to a fixture file in this directory.
//
export function fixturePath(fileName: string): string {
    return path.join(__dirname, fileName);
}
