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
    const composed = compileContainers([layer("work", { "legacy.Task#sections": { "work.task": { content: 1 } } })], core,
      { canonicalizeModel: (model) => (model === "legacy.Task" ? "projects.Task" : model) });
    expect(Object.keys(composed.children)).toEqual(["projects.Task#sections"]);
    expect(() => compileContainers([layer("work", { "projects.Task#nope": {} })], core)).toThrow(/unknown container "projects.Task#nope"/);
  });

  test("an unqualified model spelling (its only segment capitalised) is a model the canonicaliser resolves", () => {
    // `canonicalModelLabel` exists for spellings such as "Task"; a node whose last segment is capitalised is a model.
    const composed = compileContainers([layer("work", { "Task#sections": { "work.task": { content: 1 } } })], core,
      { canonicalizeModel: (model) => (model === "Task" ? "projects.Task" : model) });
    expect(Object.keys(composed.children)).toEqual(["projects.Task#sections"]);
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

  test("an addon's own container with models: true is addressed per model, along the record's models", () => {
    const iam = layer("iam", { "iam#access-roles": { models: true, "iam.owner": { content: "owner", sequence: 10 } } });
    const parties = layer("parties", { "legacy.Party#access-roles": { "parties.member": { content: "member", sequence: 20 } } });
    const crm = layer("crm", { "crm.Vip#access-roles": { "crm.sponsor": { content: "sponsor", before: "parties.member" } } }, ["parties"]);
    // Listed before their owner: the declaring pass runs first, so dependents contribute in any order.
    const composed = compileContainers([parties, crm, iam], core,
      { canonicalizeModel: (model) => (model === "legacy.Party" ? "parties.Party" : model) });
    expect(composed.declared["iam#access-roles"]).toEqual({ owner: "iam", models: true });
    expect(Object.keys(composed.children).sort()).toEqual(["crm.Vip#access-roles", "iam#access-roles", "parties.Party#access-roles"]);
    expect(ids(resolveContainer(composed, "iam#access-roles", { models: ["parties.Party", "crm.Vip"] })))
      .toEqual(["iam.owner", "crm.sponsor", "parties.member"]);
    expect(ids(resolveContainer(composed, "iam#access-roles", { models: ["notes.Note"] }))).toEqual(["iam.owner"]);
    // The owner's narrowing at the kind address reaches every model's children.
    const narrowed = compileContainers([iam, parties, layer("product", { "iam#access-roles": { except: ["parties.member"] } }, ["iam", "parties"])], core,
      { canonicalizeModel: (model) => (model === "legacy.Party" ? "parties.Party" : model) });
    expect(ids(resolveContainer(narrowed, "iam#access-roles", { models: ["parties.Party"] }))).toEqual(["iam.owner"]);
    expect(() => compileContainers([iam, layer("x", { "iam#access-roles": { models: true } }, ["iam"])], core))
      .toThrow(/sets unique or models on container "iam#access-roles" it does not own/);
    // A second container under a model kind's name is no container of its own.
    expect(() => compileContainers([iam, layer("x", { "x#access-roles": { "x.y": { content: 1 } } })], core))
      .toThrow(/"x#access-roles" shares its name with the model kind "iam#access-roles"/);
    expect(() => compileContainers([layer("x", { "x#sections": {} })], core))
      .toThrow(/"x#sections" shares its name with the model kind "form#sections"/);
    expect(() => compileContainers([iam, layer("x", { "x#access-roles": { models: true } })], core))
      .toThrow(/"#access-roles" belongs to two kinds, "iam#access-roles" and "x#access-roles"/);
  });

  test("the framework's own children are every addon's to alter, without a dependency", () => {
    const aside: CoreContainer = { address: "record#aside", models: true, children: {
      "chatter.comments": { content: "comments", sequence: 10 },
      "chatter.activity": { content: "activity", sequence: 20 },
    } };
    const views: CoreContainer = { address: "resource#views", models: true, children: {
      list: { content: "list", sequence: 10 }, board: { content: "board", sequence: 20 },
    } };
    const messaging = layer("messaging", { "record#aside": {
      "chatter.comments": { remove: true },
      "chatter.activity": { sequence: 5 },
      "messaging.comments": { content: "real comments", sequence: 10 },
    } });
    const product = layer("product", {
      "resource#views": [{ except: ["board"], when: { app: "product" } }],
      "record#aside": { only: ["chatter.activity"] },
    });
    const composed = compileContainers([messaging, product], [...core, aside, views]);
    expect(composed.removed).toEqual([{ address: "record#aside", id: "chatter.comments", by: "messaging" }]);
    // Removing again still takes the right to alter the child.
    const owned = layer("work", { "form#sections": { "work.task": { content: 1 } } });
    const workRemoves = layer("pm", { "form#sections": { "work.task": { remove: true } } }, ["work"]);
    expect(() => compileContainers([owned, workRemoves, layer("stranger", { "form#sections": { "work.task": { remove: true } } })], core))
      .toThrow(/"stranger".*"work"/);
    expect(composed.provenance["record#aside/chatter.activity"]).toEqual({ content: "framework", sequence: "messaging" });
    // product's only names a framework child; messaging is no dependent of product's, so its tab is narrowed out too.
    expect(ids(resolveContainer(composed, "record#aside"))).toEqual(["chatter.activity"]);
    expect(ids(resolveContainer(composed, "resource#views", { scope: scope([], ["product"]) }))).toEqual(["list"]);
    expect(ids(resolveContainer(composed, "resource#views", { scope: scope([], ["other"]) }))).toEqual(["list", "board"]);
    // Unrelated addons still may not both set one field of a framework child.
    expect(() => compileContainers([messaging, layer("other", { "record#aside": { "chatter.activity": { sequence: 7 } } })], [...core, aside]))
      .toThrow(/Unrelated addons "messaging" and "other" both set child "chatter.activity"/);
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
    // A bare key can only alter one of the framework's own children.
    expect(() => compileContainers([layer("work", { "form#sections": { bogus: true } })], core)).toThrow(/child "bogus" must be a mapping/);
    expect(() => compileContainers([layer("work", { "form#sections": { bogus: { hide: true } } })], core)).toThrow(/alters unknown child "bogus"/);
    expect(() => compileContainers([layer("work", { "form#sections": { "work.x": { content: 1, colour: "red" } } })], core)).toThrow(/unknown key "colour"/);
    expect(() => compileContainers([layer("work", { "form#sections": [{ "work.x": { content: 1 }, when: { route: "r" } }] })], core)).toThrow(/under a condition/);
    expect(() => compileContainers([layer("work", { "form#sections": { "iam.x": { content: 1 } } })], core)).toThrow(/outside its namespace/);
    expect(() => compileContainers([layer("work", { "form#sections": { when: { device: "phone" } } })], core)).toThrow(/unknown condition "device"/);
    expect(() => compileContainers([layer("work", { "form#sections": { only: [], when: { route: 5 } } })], core))
      .toThrow(/condition "route" must be an id or a list of ids/);
  });

  test("an id belongs to the addon whose id is its longest prefix", () => {
    const appearance = layer("appearance", { "form#sections": { "appearance.settings": { content: 1 } } });
    const integrate = layer("appearance.integrate", { "form#sections": { "appearance.integrate.sync": { content: 2 } } }, ["appearance"]);
    const composed = compileContainers([appearance, integrate], core);
    expect(composed.children["form#sections"]?.map(({ id, owner }) => ({ id, owner }))).toEqual([
      { id: "appearance.settings", owner: "appearance" },
      { id: "appearance.integrate.sync", owner: "appearance.integrate" },
    ]);
    // appearance's namespace stops where a longer addon id begins.
    expect(() => compileContainers([layer("appearance", { "form#sections": { "appearance.integrate.x": { content: 1 } } }), layer("appearance.integrate", {})], core))
      .toThrow(/"appearance" declares child "appearance.integrate.x" of "form#sections" outside its namespace/);
  });

  test("a model address with an unknown container name fails instead of declaring one", () => {
    // `iam.User#action` is a typo for `#actions`; a model node never declares a container.
    expect(() => compileContainers([layer("iam", { "iam.User#action": { "iam.reset": { content: 1 } } })], core))
      .toThrow(/"iam" names unknown container "iam.User#action": models have no "#action"/);
    // A non-model node under a model kind's name is no model address either: on one's own node it would
    // be a second container of that name, on another's it names no model.
    expect(() => compileContainers([layer("iam", { "iam.user#sections": { "iam.reset": { content: 1 } } })], core))
      .toThrow(/"iam.user#sections" shares its name with the model kind "form#sections"/);
    expect(() => compileContainers([layer("x", { "iam.user#sections": { "x.reset": { content: 1 } } })], core))
      .toThrow(/"#sections" is a model kind \("form#sections"\) and "iam.user" is not a model/);
  });

  test("ids are unique where they render together: the kind with a model, not two models", () => {
    expect(() => compileContainers([layer("work", {
      "form#actions": { "work.archive": { content: 1 } },
      "projects.Task#actions": { "work.archive": { content: 2 } },
    })], core)).toThrow(/"work" declares child "work.archive" at "form#actions" and "projects.Task#actions", which render together; one id per container/);
    // The address carries the model: one id on two models is two children (M3).
    const twoModels = compileContainers([layer("accounting", {
      "parties.Person#sections": { "accounting.party": { content: "person" } },
      "parties.Organization#sections": { "accounting.party": { content: "organization" } },
    })], core);
    expect(resolveContainer(twoModels, "form#sections", { models: ["parties.Person"] }).map((child) => child.content)).toEqual(["person"]);
    // A kind-level alteration reaches the id at every model; a model-level one, its own.
    const hidden = compileContainers([
      layer("accounting", { "parties.Person#sections": { "accounting.party": { content: 1 } }, "parties.Organization#sections": { "accounting.party": { content: 2 } } }),
      layer("product", { "form#sections": { "accounting.party": { remove: true } } }, ["accounting"]),
    ], core);
    expect(hidden.removed.map((entry) => entry.address)).toEqual(["parties.Person#sections", "parties.Organization#sections"]);
    expect(() => compileContainers([
      layer("accounting", { "parties.Person#sections": { "accounting.party": { content: 1 } }, "parties.Organization#sections": { "accounting.party": { content: 2 } } }),
      layer("product", { "parties.Party#sections": { "accounting.party": { hide: true } } }, ["accounting"]),
    ], core)).toThrow(/alters child "accounting.party" at "parties.Party#sections", which "parties.Person#sections" and "parties.Organization#sections" declare/);
    // On a model and its MTI parent, the model's own stands at render.
    const inherited = compileContainers([layer("accounting", {
      "parties.Party#sections": { "accounting.party": { content: "party" } },
      "parties.Person#sections": { "accounting.party": { content: "person" } },
    })], core);
    expect(resolveContainer(inherited, "form#sections", { models: ["parties.Party", "parties.Person"] }).map((child) => child.content)).toEqual(["person"]);
    // Families are separate: one id may sit in #sections and #actions.
    expect(() => compileContainers([layer("work", {
      "form#sections": { "work.archive": { content: 1 } },
      "form#actions": { "work.archive": { content: 2 } },
    })], core)).not.toThrow();
  });

  test("remove is idempotent across unrelated removers; altering a removed child names its remover", () => {
    const aside: CoreContainer = { address: "record#aside", models: true, children: {
      "chatter.comments": { content: "comments", sequence: 10 },
      "chatter.activity": { content: "activity", sequence: 20 },
    } };
    const messaging = layer("messaging", { "record#aside": { "chatter.comments": { remove: true } } });
    const bridge = layer("bridge", { "record#aside": { "chatter.comments": { remove: true } } });
    const composed = compileContainers([messaging, bridge], [aside]);
    expect(ids(resolveContainer(composed, "record#aside"))).toEqual(["chatter.activity"]);
    expect(composed.removed).toEqual([{ address: "record#aside", id: "chatter.comments", by: "messaging" }]);
    // Removing again still takes the right to alter the child.
    const owned = layer("work", { "form#sections": { "work.task": { content: 1 } } });
    const workRemoves = layer("pm", { "form#sections": { "work.task": { remove: true } } }, ["work"]);
    expect(() => compileContainers([owned, workRemoves, layer("stranger", { "form#sections": { "work.task": { remove: true } } })], core))
      .toThrow(/"stranger".*"work"/);
    expect(() => compileContainers([messaging, layer("tags", { "record#aside": { "chatter.comments": { sequence: 5 } } })], [aside]))
      .toThrow(/"tags" alters child "chatter.comments" of container "record#aside", which "messaging" removed/);
    expect(() => compileContainers([messaging, layer("tags", { "record#aside": [{ "chatter.comments": { hide: true }, when: { app: "tags" } }] })], [aside]))
      .toThrow(/which "messaging" removed/);
  });

  test("a container taking render-time children (extras) admits ids composition cannot know in only and except", () => {
    const extras: CoreContainer = { address: "record#aside", models: true, extras: true, children: { "chatter.comments": { content: "comments" } } };
    const composed = compileContainers([layer("files", { "record#aside": [
      { only: ["chatter.comments", "details"], when: { route: "files.browse" } },
      { except: ["records"] },
    ] })], [extras]);
    expect(composed.declared["record#aside"]).toEqual({ owner: "framework", models: true, extras: true });
    const page = [{ id: "details", owner: "page", address: "record#aside", content: "details" },
      { id: "records", owner: "page", address: "record#aside", content: "records" },
      { id: "workflow", owner: "page", address: "record#aside", content: "workflow" }];
    expect(ids(resolveContainer(composed, "record#aside", { extra: page, scope: scope(["files.browse"]) }))).toEqual(["chatter.comments", "details"]);
    expect(ids(resolveContainer(composed, "record#aside", { extra: page, scope: scope(["files.list"]) }))).toEqual(["chatter.comments", "details", "workflow"]);
    // Developer mode lists the ids no declaration holds, in case one is a typo.
    expect(composed.diagnostics).toEqual([
      `Addon "files" narrows container "record#aside" to unknown child "details", unless a page adds it.`,
      `Addon "files" narrows container "record#aside" to unknown child "records", unless a page adds it.`,
    ]);
    // Without extras the same narrowing names an unknown child.
    expect(() => compileContainers([layer("files", { "form#sections": { only: ["details"] } })], core)).toThrow(/unknown child "details"/);
  });

  test("hide and show apply across the kind and model addresses in dependency order", () => {
    const work = layer("work", { "projects.Task#sections": { "work.task": { content: 1, sequence: 1 }, "work.other": { content: 2, sequence: 2 } } });
    // pm hides at the model address; product (a dependent) shows again at the kind address.
    const pm = layer("pm", { "projects.Task#sections": { "work.task": { hide: true } } }, ["work"]);
    const product = layer("product", { "form#sections": { "work.task": { hide: false } } }, ["pm", "work"]);
    const models = ["projects.Task"];
    expect(ids(resolveContainer(compileContainers([work, pm], core), "form#sections", { models }))).toEqual(["work.other"]);
    // Rule order follows the layers' dependency ranks, not addon or address order.
    for (const order of [[work, pm, product], [product, work, pm]]) {
      const composed = compileContainers(order, core);
      expect(composed.rules["form#sections"]?.[0]?.rank).toBeGreaterThan(composed.rules["projects.Task#sections"]?.[0]?.rank ?? Infinity);
      expect(ids(resolveContainer(composed, "form#sections", { models }))).toEqual(["work.task", "work.other"]);
    }
    // The dependency hiding at the kind address after its dependent showed at the model address still loses.
    const pmKind = layer("pm", { "form#sections": { "work.task": { hide: true } } }, ["work"]);
    const productModel = layer("product", { "projects.Task#sections": { "work.task": { hide: false } } }, ["pm", "work"]);
    expect(ids(resolveContainer(compileContainers([work, pmKind, productModel], core), "form#sections", { models }))).toEqual(["work.task", "work.other"]);
  });

  test("narrowing by a variant's own id drops the variant and the original returns; by the original's id it carries the variants", () => {
    const integrate = layer("integrate", { "integrate.Integration#actions": { "integrate.resume": { content: "generic" } } });
    const matrix = layer("matrix", { "messaging.Channel#actions": { "matrix.resume": { content: "matrix", variant: { of: "integrate.resume", impl: "matrix" } } } }, ["integrate"]);
    const models = ["integrate.Integration", "messaging.Channel"];
    const contents = (narrowing: Record<string, unknown>) =>
      resolveContainer(compileContainers([integrate, matrix, layer("product", { "form#actions": narrowing }, ["integrate", "matrix"])], core),
        "form#actions", { models, impls: ["matrix"] }).map((child) => child.content);
    expect(contents({ except: ["matrix.resume"] })).toEqual(["generic"]);
    expect(contents({ "matrix.resume": { hide: true } })).toEqual(["generic"]);
    expect(contents({ only: ["integrate.resume"] })).toEqual(["matrix"]);
    expect(contents({ only: ["matrix.resume"] })).toEqual(["matrix"]);
    expect(contents({ except: ["integrate.resume"] })).toEqual([]);
  });

  test("a built-in view kind is hidden or moved by its bare id", () => {
    const views: CoreContainer = { address: "resource#views", models: true, children: {
      list: { content: "list", sequence: 10 }, board: { content: "board", sequence: 20 }, calendar: { content: "calendar", sequence: 30 },
    } };
    const composed = compileContainers([
      layer("pm", { "resource#views": [{ board: { hide: true }, when: { app: "pm" } }, { calendar: { sequence: 5 } }] }),
    ], [views]);
    expect(ids(resolveContainer(composed, "resource#views", { scope: scope([], ["pm"]) }))).toEqual(["calendar", "list"]);
    expect(ids(resolveContainer(composed, "resource#views", { scope: scope([], ["crm"]) }))).toEqual(["calendar", "list", "board"]);
    expect(composed.provenance["resource#views/calendar"]).toMatchObject({ sequence: "pm" });
  });

  test("a variant whose original is not on the page keeps its own place", () => {
    const composed = compileContainers([layer("work", {
      "projects.Project#actions": { "work.resume": { content: 1 } },
      "projects.Task#actions": { "work.fast": { content: 2, variant: { of: "work.resume", impl: "fast" } }, "work.archive": { content: 3 } },
    })], core);
    expect(ids(resolveContainer(composed, "form#actions", { models: ["projects.Task"], impls: ["fast"] }))).toEqual(["work.archive", "work.fast"]);
    expect(ids(resolveContainer(composed, "form#actions", { models: ["projects.Task"] }))).toEqual(["work.archive"]);
  });

  test("positions are checked where children render together: a cycle throws, a dangling anchor is a diagnostic", () => {
    expect(() => compileContainers([layer("work", {
      "form#sections": { "work.a": { content: 1, before: "work.b" } },
      "projects.Task#sections": { "work.b": { content: 2, before: "work.a" } },
    })], core)).toThrow(/before\/after cycle/);
    const composed = compileContainers([layer("work", { "projects.Task#sections": { "work.a": { content: 1, after: "gone.child" } } })], core);
    expect(composed.diagnostics).toEqual([`Child "work.a" of "projects.Task#sections" positions itself against "gone.child", which "form#sections" does not hold.`]);
    // Two models never render together, so neither their cycles nor their variants clash.
    expect(() => compileContainers([layer("work", {
      "projects.Task#sections": { "work.a": { content: 1, before: "work.b" }, "work.b": { content: 2 } },
      "projects.Project#sections": { "work.b": { content: 2, before: "work.a" }, "work.a": { content: 1 } },
    })], core)).not.toThrow();
    expect(() => compileContainers([layer("work", {
      "form#actions": { "work.resume": { content: 1 } },
      "projects.Task#actions": { "work.fast": { content: 2, variant: { of: "work.resume", impl: "fast" } } },
      "projects.Project#actions": { "work.quick": { content: 3, variant: { of: "work.resume", impl: "fast" } } },
    })], core)).not.toThrow();
    // Variants that render together for one impl fail at compile.
    expect(() => compileContainers([layer("work", {
      "form#actions": { "work.resume": { content: 1 }, "work.quick": { content: 3, variant: { of: "work.resume", impl: "fast" } } },
      "projects.Task#actions": { "work.fast": { content: 2, variant: { of: "work.resume", impl: "fast" } } },
    })], core)).toThrow(/"work.quick" and "work.fast" of "projects.Task#actions" are both variants of "work.resume" for impl "fast"/);
  });
});
