import { describe, expect, test } from "vitest";

import { defineAngeeSchemaMetadata, resourceOperationTarget, schemaFieldMetadataFromDataResources, schemaFieldMetadataWithVocabulary } from "./artifact";
import { testDataResource } from "./testing";

test("scoped vocabulary projects labels without altering resource or query identity", () => {
  const resource = testDataResource("notes.Note", { fields: [{ name: "title", kind: "scalar", scalar: "String", readable: true,
    aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false }] });
  const base = schemaFieldMetadataFromDataResources([resource]);
  const scoped = schemaFieldMetadataWithVocabulary(base, {
    "notes.Note": { label: "Document", pluralLabel: "Documents", fields: { title: "Subject" } },
  });
  const model = scoped.labels["notes.Note"]!;
  expect(model).toBe(scoped.types["NoteType"]);
  expect(model).toMatchObject({ label: "Document", pluralLabel: "Documents", fields: { title: { label: "Subject" } } });
  expect(model.resource).toBe(base.labels["notes.Note"]!.resource);
  expect(scoped.resources).toBe(base.resources);
  expect(base.labels["notes.Note"]!.fields.title?.label).toBeUndefined();
});

test("scoped field vocabulary projects tone maps without mutating the base schema", () => {
  const resource = testDataResource("notes.Note", { fields: [{ name: "status", kind: "enum", values: [{ value: "HIGH", description: "High" }],
    readable: true, aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false }] });
  const base = schemaFieldMetadataFromDataResources([resource]);
  const scoped = schemaFieldMetadataWithVocabulary(base, { "notes.Note": {
    fields: { status: { label: "Priority", tones: { HIGH: "warning" } } },
  } });
  expect(scoped.labels["notes.Note"]?.fields.status).toMatchObject({ label: "Priority", tones: { HIGH: "warning" } });
  expect(base.labels["notes.Note"]?.fields.status?.tones).toBeUndefined();
});

test("scoped vocabulary labels declared grant relations without changing their ids", () => {
  const resource = testDataResource("notes.Note", { grantable: [{
    relation: "reader", permission: "share", subjects: [],
  }] });
  const base = schemaFieldMetadataFromDataResources([resource]);
  const scoped = schemaFieldMetadataWithVocabulary(base, {
    "notes.Note": { relations: { reader: "Can read" } },
  });
  expect(scoped.labels["notes.Note"]?.resource.grantable?.[0]).toMatchObject({
    relation: "reader", label: "Can read", permission: "share",
  });
  expect(base.labels["notes.Note"]?.resource.grantable?.[0]?.label).toBeUndefined();
});

describe("generated subtitle metadata", () => {
  test("accepts declared dotted selection paths", () => {
    const resource = testDataResource("knowledge.Page", {
      subtitle: {
        created: "created_at",
        updated: "updated_at",
        wordCount: "markdown.word_count",
      },
    });

    expect(defineAngeeSchemaMetadata({ angee: { resources: [resource] } }))
      .toEqual({ angee: { resources: [resource] } });
  });

  test("rejects malformed subtitle selection paths", () => {
    const resource = {
      ...testDataResource("knowledge.Page"),
      subtitle: { wordCount: "markdown..word_count" },
    };

    expect(() =>
      defineAngeeSchemaMetadata({ angee: { resources: [resource] } }),
    ).toThrow(
      "schema metadata.angee.resources[0].subtitle.wordCount must be a dotted selection path.",
    );
  });

  test("rejects non-string subtitle facts", () => {
    const resource = {
      ...testDataResource("knowledge.Page"),
      subtitle: { created: 1 },
    };

    expect(() =>
      defineAngeeSchemaMetadata({ angee: { resources: [resource] } }),
    ).toThrow("schema metadata.angee.resources[0].subtitle.created must be a string.");
  });

  test("rejects subtitle facts outside the renderer vocabulary", () => {
    const resource = {
      ...testDataResource("knowledge.Page"),
      subtitle: { summary: "markdown.excerpt" },
    };

    expect(() =>
      defineAngeeSchemaMetadata({ angee: { resources: [resource] } }),
    ).toThrow(
      "schema metadata.angee.resources[0].subtitle.summary is not a supported subtitle fact.",
    );
  });
});


describe("generated resource wire contract", () => {
  test.each(["pill", "dot", "text"] as const)("round-trips the %s status display", (statusDisplay) => {
    const resource = testDataResource("work.Task", {
      fields: [{
        name: "status", kind: "enum", statusDisplay,
        readable: true, aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false,
      }],
    });
    const wire = { angee: { resources: [resource] } };

    expect(defineAngeeSchemaMetadata(wire)).toEqual(wire);
  });

  test("rejects an unknown status display", () => {
    const resource = testDataResource("work.Task", {
      fields: [{
        name: "status", kind: "enum", statusDisplay: "badge" as never,
        readable: true, aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false,
      }],
    });

    expect(() => defineAngeeSchemaMetadata({ angee: { resources: [resource] } }))
      .toThrow("schema metadata.angee.resources[0].fields[0].statusDisplay");
  });

  test("accepts a computed object field with no relation target", () => {
    const resource = testDataResource("workflows.StepRecord", {
      fields: [{
        name: "target_reference", kind: "object", readable: true,
        aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false,
      }],
    });
    expect(defineAngeeSchemaMetadata({ angee: { resources: [resource] } }))
      .toEqual({ angee: { resources: [resource] } });
  });

  test("preserves extension keys and nullable emitted values", () => {
    const resource = testDataResource("notes.Note", {
      roots: { list: "notes", customRoot: "notes_custom" },
      fields: [{
        name: "status", kind: "enum", values: [{ value: "OPEN", description: null }],
        readable: true, aggregatable: false, creatable: true, updatable: true, requiredOnCreate: false,
        relationModelLabel: null, widget: null,
      }],
      futureResourceFact: { enabled: true },
      createArguments: [{ name: "client_creation_key", type: "String" }],
      updateArguments: [{ name: "expected_revision", type: "Int" }],
      saveArguments: [{ name: "expected_revision", type: "Int" }],
    });
    const wire = { vendor: { retained: true }, angee: { resources: [resource], future: "kept" } };
    expect(defineAngeeSchemaMetadata(wire)).toEqual(wire);
  });

  test("reads an MTI parent's concrete kinds and leaves a plain model without any", () => {
    const party = testDataResource("parties.Party", { concreteKinds: ["parties.Organization", "parties.Person"] });
    const handle = testDataResource("parties.Handle");
    const parsed = defineAngeeSchemaMetadata({ angee: { resources: [party, handle] } });
    const metadata = schemaFieldMetadataFromDataResources(parsed.angee?.resources ?? []);

    expect(metadata.labels["parties.Party"]?.resource.concreteKinds).toEqual(["parties.Organization", "parties.Person"]);
    expect(metadata.labels["parties.Handle"]?.resource.concreteKinds).toBeUndefined();
  });

  test("reads the edges a record type can carry", () => {
    const task = testDataResource("projects.Task", { recordEdges: ["knowledge.RecordBinding", "storage.FileAttachment"] });
    const parsed = defineAngeeSchemaMetadata({ angee: { resources: [task, testDataResource("projects.Tag")] } });
    const metadata = schemaFieldMetadataFromDataResources(parsed.angee?.resources ?? []);

    expect(metadata.labels["projects.Task"]?.resource.recordEdges).toEqual(["knowledge.RecordBinding", "storage.FileAttachment"]);
    expect(metadata.labels["projects.Tag"]?.resource.recordEdges).toBeUndefined();
  });

  test.each([
    { concreteKinds: "parties.Person" },
    { concreteKinds: [42] },
    { recordEdges: "storage.FileAttachment" },
    { query: { identity: { field: 42 } } },
    { aggregateMeasures: [{ op: 42 }] },
    { createArguments: [42] },
    { createArguments: ["client_creation_key"] },
    { updateArguments: [{ name: "expected_revision" }] },
    { saveArguments: [{ name: "expected_revision", type: 42 }] },
    { updateArguments: {} },
    { saveArguments: "expected_revision" },
    { updateArguments: "expected_revision" },
    { saveArguments: [false] },
    { linesResource: { field: "lines", modelLabel: "notes.Line", fields: [{ name: "body", kind: "scalar", readable: "yes" }] } },
  ])("rejects malformed nested resource facts: %j", (patch) => {
    expect(() => defineAngeeSchemaMetadata({ angee: { resources: [{ ...testDataResource("notes.Note"), ...patch }] } }))
      .toThrow(/schema metadata\.angee\.resources\[0\]/);
  });

  test("keeps absent optional envelope sections absent", () => {
    expect(defineAngeeSchemaMetadata({})).toEqual({});
    expect(defineAngeeSchemaMetadata({ angee: {} })).toEqual({ angee: {} });
    expect(defineAngeeSchemaMetadata({ angee: null })).toEqual({ angee: null });
  });
});


test("resource operation targets retain the canonical live model identity", () => {
  const resource = testDataResource("messaging.Message", { roots: { groups: "messages_groups" } });
  expect(resourceOperationTarget(resource, "groups")).toEqual({
    dataProviderName: "console", root: "messages_groups", modelLabel: "messaging.Message",
  });
});
