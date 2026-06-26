import { createApp, defineBaseAddon } from "@angee/app";
import { ConsoleLayout } from "@angee/ui";
import notes from "@angee-example/notes-web";
import agents from "@angee/agents";
import iam, { IamLoginPage } from "@angee/iam";
import integrate from "@angee/integrate";
import knowledge from "@angee/knowledge";
import messaging from "@angee/messaging";
import operator from "@angee/operator";
import parties from "@angee/parties";
import platform from "@angee/platform";
import resources from "@angee/resources-addon";
import storage from "@angee/storage";
import {
  actionDocuments as publicActionDocuments,
  aggregateDocuments as publicAggregateDocuments,
  deletePreviewDocuments as publicDeletePreviewDocuments,
  groupDocuments as publicGroupDocuments,
  revisionDocuments as publicRevisionDocuments,
} from "@angee/gql/public/actions";
import {
  actionDocuments as consoleActionDocuments,
  aggregateDocuments as consoleAggregateDocuments,
  deletePreviewDocuments as consoleDeletePreviewDocuments,
  groupDocuments as consoleGroupDocuments,
  revisionDocuments as consoleRevisionDocuments,
} from "@angee/gql/console/actions";

import publicMetadata from "../../runtime/schemas/public.metadata.json";
import consoleMetadata from "../../runtime/schemas/console.metadata.json";
import { DemoForgotPasswordHint } from "./demo-auth";
import "./index.css";

const authAddon = defineBaseAddon({
  id: "auth",
  routes: [
    {
      name: "auth.login",
      path: "/login",
      layout: "public",
      component: LoginRoute,
    },
  ],
});

createApp({
  // Platform apps cluster at the bottom of the rail (group: "platform"): IAM,
  // Integrate, then the Platform app. Operator and Resources contribute their
  // sections into Platform (parentId), so they carry no rail glyph of their own.
  addons: [notes, authAddon, iam, parties, messaging, integrate, agents, operator, storage, knowledge, resources, platform],
  layouts: {
    console: { chrome: ConsoleLayout },
    // Chrome defaults to PassthroughChrome and a public-keyed layout is
    // unauthenticated by default (createApp owns both), but the schema must be
    // pinned: defaultSchema is "console", so the public login layout points back
    // to the public client explicitly.
    public: { schema: "public" },
  },
  schemas: {
    public: {
      url: "/graphql/public/",
      metadata: publicMetadata,
      operationDocuments: {
        actions: publicActionDocuments,
        aggregates: publicAggregateDocuments,
        deletePreviews: publicDeletePreviewDocuments,
        groups: publicGroupDocuments,
        revisions: publicRevisionDocuments,
      },
    },
    console: {
      url: "/graphql/console/",
      metadata: consoleMetadata,
      operationDocuments: {
        actions: consoleActionDocuments,
        aggregates: consoleAggregateDocuments,
        deletePreviews: consoleDeletePreviewDocuments,
        groups: consoleGroupDocuments,
        revisions: consoleRevisionDocuments,
      },
      live: true,
    },
  },
  // The console is the primary surface, so it is the default schema; the public
  // login layout pins itself back to the public client above.
  defaultSchema: "console",
  home: "/notes",
}).mount("#root");

function LoginRoute() {
  return (
    <IamLoginPage
      redirectTo="/notes"
      passwordHelp={<DemoForgotPasswordHint />}
    />
  );
}
