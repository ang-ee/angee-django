import { describe, expect, test } from "vitest";
import { resolveContainer, type ContainersDeclaration, type CoreContainer } from "@angee/ui/runtime";

import { compileContainers, type ContainerLayer } from "./containers";
import { DEPLOYMENT_LAYER_ID } from "./layers";

const core: CoreContainer[] = [{ address: "form#sections", models: true }, { address: "form#actions", models: true }];
const layer = (id: string, containers: Record<string, unknown>, dependsOn: string[] = []): ContainerLayer =>
  ({ id, dependsOn, containers: containers as ContainersDeclaration });
const ids = (children: readonly { id: string }[]): string[] => children.map((child) => child.id);
const scope = (routes: string[] = [], apps: string[] = []) => ({ apps, routes, perspective: null });

describe("compileContainers", () => {
  test("declares kind and model children; a page merges kind, canonical and concrete model in position order", () => {
    const composed = compileContainers([
      layer("iam", { "form#sections": { "iam.share": { content: "share", sequence: 90 } } }),
      layer("integrate", { "integrate.Integration#sections": { "integrate.streams": { content: "streams", sequence: 30 } } }),
      layer("messaging", { "messaging.Channel#sections": {
        "messaging.health": { content: "health", sequence: 30 },
        "messaging.first": { content: "first", before: "integrate.streams" },
      } }, ["integrate"]),
    ], core);
    const models = ["integrate.Integration", "messaging.Channel"];
    expect(ids(resolveContainer(composed, "form#sections", { models }))).toEqual(["messaging.first", "integrate.streams", "messaging.health", "iam.share"]);
    expect(ids(resolveContainer(composed, "form#sections", { models: ["integrate.Integration"] }))).toEqual(["integrate.streams", "iam.share"]);
    expect(composed.provenance["messaging.Channel#sections/messaging.first"]).toEqual({ content: "messaging", before: "messaging" });
  });

  test("model addresses go through the canonicaliser; unknown containers and kinds fail", () => {
    const composed = compileContainers([layer("work", { "Task#sections": { "work.task": { content: 1 } } })], core,
      { canonicalizeModel: (model) => (model === "Task" ? "projects.Task" : model) });
    expect(Object.keys(composed.children)).toEqual(["projects.Task#sections"]);
    expect(() => compileContainers([layer("work", { "projects.Task#nope": {} })], core)).toThrow(/unknown container "projects.Task#nope"/);
  });

  test("alterations follow dependencies: a dependent overrides, unrelated layers collide, others may not alter", () => {
    const work = layer("work", { "projects.Task#sections": { "work.task": { content: 1, sequence: 40 } } });
    const pm = layer("pm", { "projects.Task#sections": { "work.task": { sequence: 10 } } }, ["work"]);
    const product = layer("product", { "projects.Task#sections": { "work.task": { sequence: 5 } } }, ["pm", "work"]);
    expect(compileContainers([work, pm, product], core).children["projects.Task#sections"]![0]!.sequence).toBe(5);
    expect(compileContainers([work, product, pm], core).children["projects.Task#sections"]![0]!.sequence).toBe(5);
    const other = layer("other", { "projects.Task#sections": { "work.task": { sequence: 7 } } }, ["work"]);
    expect(() => compileContainers([work, pm, other], core)).toThrow(/Unrelated addons "pm" and "other" both set child "work.task"/);
    expect(() => compileContainers([work, layer("stray", { "projects.Task#sections": { "work.task": { sequence: 1 } } })], core))
      .toThrow(/"stray" alters child "work.task".*does not depend on/);
  });

  test("remove drops a child at composition and records who removed it", () => {
    const composed = compileContainers([
      layer("work", { "projects.Task#sections": { "work.task": { content: 1 }, "work.other": { content: 2 } } }),
      layer("pm", { "projects.Task#sections": { "work.task": { remove: true } } }, ["work"]),
    ], core);
    expect(ids(resolveContainer(composed, "form#sections", { models: ["projects.Task"] }))).toEqual(["work.other"]);
    expect(composed.removed).toEqual([{ address: "projects.Task#sections", id: "work.task", by: "pm" }]);
  });

  test("only narrows across layers and per route; it never filters children of the narrowing layer's dependents", () => {
    const composed = compileContainers([
      layer("iam", { "form#sections": { "iam.share": { content: 1 } } }),
      layer("intake", { "projects.Task#sections": { "intake.access": { content: 2 }, "intake.needs": { content: 3 } } }),
      layer("pm", { "form#sections": { only: ["intake.access", "intake.needs"] } }, ["iam", "intake"]),
      layer("product", {
        "projects.Task#sections": { "product.extra": { content: 4 } },
        "form#sections": [{ only: ["intake.access", "iam.share", "product.extra"] }, { only: ["product.extra"], when: { route: "product.question" } }],
      }, ["pm", "iam", "intake"]),
    ], core);
    const models = ["projects.Task"];
    // pm's only exempts product's child (pm's dependent); product's only also filters its own; iam.share stays out, as pm narrowed it.
    expect(ids(resolveContainer(composed, "form#sections", { models, scope: scope(["product.project"]) }))).toEqual(["intake.access", "product.extra"]);
    expect(ids(resolveContainer(composed, "form#sections", { models, scope: scope(["product.question.record", "product.question"]) }))).toEqual(["product.extra"]);
    expect(() => compileContainers([layer("x", { "form#sections": { only: ["nobody.here"] } })], core)).toThrow(/unknown child "nobody.here"/);
  });

  test("hide is a render verb with a condition; a dependent shows again what a dependency hid", () => {
    const work = layer("work", { "projects.Task#sections": { "work.task": { content: 1 } } });
    const pm = layer("pm", { "projects.Task#sections": [{ "work.task": { hide: true }, when: { app: "pm" } }] }, ["work"]);
    const composed = compileContainers([work, pm], core);
    const models = ["projects.Task"];
    expect(ids(resolveContainer(composed, "form#sections", { models, scope: scope([], ["pm"]) }))).toEqual([]);
    expect(ids(resolveContainer(composed, "form#sections", { models, scope: scope([], ["work"]) }))).toEqual(["work.task"]);
    const product = layer("product", { "projects.Task#sections": { "work.task": { hide: false } } }, ["pm", "work"]);
    expect(ids(resolveContainer(compileContainers([work, pm, product], core), "form#sections", { models, scope: scope([], ["pm"]) }))).toEqual(["work.task"]);
    expect(() => compileContainers([work, layer("pm", { "projects.Task#sections": [{ "work.task": { sequence: 1 }, when: { app: "pm" } }] }, ["work"])], core))
      .toThrow(/under a condition; only render verbs/);
  });

  test("variants stand in for their original where the row's implementation matches, and carry its admission", () => {
    const integrate = layer("integrate", { "integrate.Integration#actions": { "integrate.resume": { content: "generic", sequence: 12 } } });
    const matrix = layer("matrix", { "messaging.Channel#actions": { "matrix.resume": { content: "matrix", variant: { of: "integrate.resume", impl: "matrix" } } } }, ["integrate"]);
    const signal = layer("signal", { "messaging.Channel#actions": { "signal.resume": { content: "signal", variant: { of: "integrate.resume", impl: "signal" } } } }, ["integrate"]);
    const composed = compileContainers([integrate, matrix, signal], core);
    const models = ["integrate.Integration", "messaging.Channel"];
    expect(resolveContainer(composed, "form#actions", { models, impls: ["matrix"] }).map((child) => child.content)).toEqual(["matrix"]);
    expect(resolveContainer(composed, "form#actions", { models, impls: ["email"] }).map((child) => child.content)).toEqual(["generic"]);
    const narrowed = compileContainers([integrate, matrix, layer("product", { "form#actions": { except: ["integrate.resume"] } }, ["integrate", "matrix"])], core);
    expect(resolveContainer(narrowed, "form#actions", { models, impls: ["matrix"] })).toEqual([]);
    expect(() => compileContainers([integrate, matrix, layer("matrix2", { "messaging.Channel#actions": { "matrix2.resume": { content: "x", variant: { of: "integrate.resume", impl: "matrix" } } } }, ["integrate"])], core))
      .toThrow(/both variants of "integrate.resume" for impl "matrix"/);
    expect(() => compileContainers([layer("matrix", { "messaging.Channel#actions": { "matrix.resume": { content: "x", variant: { of: "integrate.gone", impl: "matrix" } } } })], core))
      .toThrow(/variant of unknown child "integrate.gone"/);
  });

  test("an addon declares containers on its own node; dependents contribute; unique keys are enforced", () => {
    const decisions = layer("decisions", { "decisions#content": { unique: "key" }, "decisions#origin": {} });
    const parties = layer("parties", { "decisions#content": { "parties.review": { content: 1, key: "review-party" } } }, ["decisions"]);
    const composed = compileContainers([parties, decisions], core);
    expect(composed.declared["decisions#content"]).toEqual({ owner: "decisions", models: false, unique: "key" });
    expect(ids(resolveContainer(composed, "decisions#content"))).toEqual(["parties.review"]);
    const twice = layer("other", { "decisions#content": { "other.review": { content: 2, key: "review-party" } } }, ["decisions"]);
    expect(() => compileContainers([decisions, parties, twice], core)).toThrow(/share key "review-party"/);
    expect(() => compileContainers([layer("x", { "nobody#toolbar": { "x.y": { content: 1 } } })], core)).toThrow(/unknown container "nobody#toolbar"/);
    expect(() => compileContainers([decisions, layer("stray", { "decisions#origin": { only: [] } })], core)).toThrow(/"stray" alters container "decisions#origin" of "decisions"/);
  });

  test("permission is checked against the row; the deployment layer alters anything last", () => {
    const work = layer("work", { "projects.Task#sections": { "work.edit": { content: 1, permission: "write" }, "work.view": { content: 2 } } });
    const composed = compileContainers([work], core);
    const models = ["projects.Task"];
    expect(ids(resolveContainer(composed, "form#sections", { models, row: { permissions: ["read"] } as never }))).toEqual(["work.view"]);
    expect(ids(resolveContainer(composed, "form#sections", { models }))).toEqual(["work.edit", "work.view"]);
    const deployment = layer(DEPLOYMENT_LAYER_ID, { "form#sections": { except: ["work.view"] } }, ["work"]);
    expect(ids(resolveContainer(compileContainers([work, deployment], core), "form#sections", { models }))).toEqual(["work.edit"]);
  });

  test("entries are validated: unknown keys, unnamespaced children, conditional declarations, foreign declarations", () => {
    expect(() => compileContainers([layer("work", { "form#sections": { bogus: true } })], core)).toThrow(/unknown key "bogus"/);
    expect(() => compileContainers([layer("work", { "form#sections": { "work.x": { content: 1, colour: "red" } } })], core)).toThrow(/unknown key "colour"/);
    expect(() => compileContainers([layer("work", { "form#sections": [{ "work.x": { content: 1 }, when: { route: "r" } }] })], core)).toThrow(/under a condition/);
    expect(() => compileContainers([layer("work", { "form#sections": { "iam.x": { content: 1 } } })], core)).toThrow(/outside its namespace/);
    expect(() => compileContainers([layer("work", { "form#sections": { when: { device: "phone" } } })], core)).toThrow(/unknown condition "device"/);
  });
});
