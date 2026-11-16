//
// Acceptance tests for ClusterScheduler's failure handling.
//
// The cluster module only forks real OS processes when the entry point is a
// standalone script, so each scenario lives in ./cluster-fixtures and is
// spawned here as a separate Node process running under ts-node. The fixture
// prints a single "SCENARIO_RESULT {...}" line which these assertions parse.
//

import { spawn, ChildProcess } from "child_process";
import * as path from "path";
import { fixturePath, IScenarioResult } from "./cluster-fixtures/test-helpers";

const REPO_ROOT = path.join(__dirname, "..", "..");
const TS_NODE_BIN = path.join(REPO_ROOT, "node_modules", "ts-node", "dist", "bin.js");
const RESULT_MARKER = "SCENARIO_RESULT ";

//
// Spawn a cluster fixture and resolve with the result it emitted.
//
function runScenario(fixtureFileName: string): Promise<IScenarioResult> {
    const fixture = fixturePath(fixtureFileName);

    return new Promise<IScenarioResult>((resolve, reject) => {
        const child: ChildProcess = spawn(
            process.execPath,
            [TS_NODE_BIN, fixture],
            {
                cwd: REPO_ROOT,
                env: process.env,
                stdio: ["ignore", "pipe", "pipe"],
            }
        );

        let stdout = "";
        let stderr = "";

        child.stdout!.on("data", chunk => {
            stdout += chunk.toString();
        });
        child.stderr!.on("data", chunk => {
            stderr += chunk.toString();
        });

        child.on("error", err => {
            reject(new Error(`Failed to spawn fixture ${fixtureFileName}: ${err && err.message}`));
        });

        child.on("exit", code => {
            const markerLine = stdout
                .split(/\r?\n/)
                .filter(line => line.indexOf(RESULT_MARKER) === 0)
                .pop();

            if (!markerLine) {
                reject(new Error(
                    `Fixture ${fixtureFileName} produced no result (exit code ${code}).\n` +
                    `STDOUT:\n${stdout}\nSTDERR:\n${stderr}`
                ));
                return;
            }

            try {
                const result: IScenarioResult = JSON.parse(markerLine.substr(RESULT_MARKER.length));
                if (result.status === "error") {
                    reject(new Error(`Fixture ${fixtureFileName} reported an error:\n${result.error}`));
                    return;
                }
                resolve(result);
            } catch (err) {
                reject(new Error(`Could not parse result from ${fixtureFileName}: ${err && err.message}\n${markerLine}`));
            }
        });
    });
}

describe("cluster scheduler failure handling (multi-process)", () => {

    it("runs a normal task graph and returns the correct results", async () => {
        const result = await runScenario("happy-path.ts");

        expect(result.data.graphResult).toBe(61);
        expect(result.data.batch).toEqual([2, 4, 6, 8]);
    }, 60000);

    it("rejects a throwing task while the other tasks still complete", async () => {
        const result = await runScenario("task-error.ts");

        expect(result.data.failed.state).toBe("rejected");
        expect(result.data.failed.reason).toContain("kaboom");
        expect(result.data.failed.settleCount).toBe(1);

        const good = result.data.good;
        expect(good).toHaveLength(3);
        for (const settlement of good) {
            expect(settlement.state).toBe("fulfilled");
            expect(settlement.settleCount).toBe(1);
        }
        expect(good.map((s: any) => s.value)).toEqual([10, 20, 30]);
    }, 60000);

    it("rejects tasks on a worker that exits and keeps running the rest", async () => {
        const result = await runScenario("worker-exit.ts");

        const crashed = result.data.crashed;
        expect(crashed.state).toBe("rejected");
        expect(crashed.settleCount).toBe(1);
        expect(crashed.reason).toContain("exited");

        const rest = result.data.rest;
        expect(rest).toHaveLength(3);
        for (const settlement of rest) {
            expect(settlement.state).toBe("fulfilled");
            expect(settlement.settleCount).toBe(1);
        }
        expect(rest.map((s: any) => s.value)).toEqual([1, 2, 3]);
    }, 60000);

    it("rejects a task on a worker that is forcibly killed from outside", async () => {
        const result = await runScenario("worker-kill.ts");

        const hung = result.data.hung;
        expect(hung.state).toBe("rejected");
        expect(hung.settleCount).toBe(1);
        expect(hung.reason).toContain("exited");

        const survivors = result.data.survivors;
        expect(survivors).toHaveLength(4);
        for (const settlement of survivors) {
            expect(settlement.state).toBe("fulfilled");
            expect(settlement.settleCount).toBe(1);
        }
        expect(survivors.map((s: any) => s.value)).toEqual([101, 102, 103, 104]);
    }, 60000);

    it("fails a timed-out task exactly once without redispatching it", async () => {
        const result = await runScenario("task-timeout.ts");

        const slow = result.data.slow;
        expect(slow.state).toBe("rejected");
        expect(slow.settleCount).toBe(1); // Settled exactly once.
        expect(slow.reason).toContain("timed out");
        expect(slow.reason).toContain("1500");

        // Normal tasks still complete, proving the batch did not stall and
        // workers kept serving work after the timeout.
        expect(result.data.fast.state).toBe("fulfilled");
        expect(result.data.fast.value).toBe(42);
        expect(result.data.fast.settleCount).toBe(1);
        expect(result.data.ping.state).toBe("fulfilled");
        expect(result.data.ping.value).toBe("pong");
        expect(result.data.ping.settleCount).toBe(1);

        // The timed-out task was dispatched exactly once and never requeued.
        expect(result.data.slowDispatches).toBe(1);
        expect(result.data.totalDispatches).toBe(3); // slow, fast and ping once each.
    }, 60000);

    it("keeps waiting for long tasks when no timeout option is given", async () => {
        const result = await runScenario("no-timeout.ts");

        expect(result.data.long.state).toBe("fulfilled");
        expect(result.data.long.value).toBe("finally-done");
        expect(result.data.long.settleCount).toBe(1);

        expect(result.data.short.state).toBe("fulfilled");
        expect(result.data.short.value).toBe(21);
        expect(result.data.short.settleCount).toBe(1);
    }, 60000);
});
