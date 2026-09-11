import { useEffect, useMemo, useRef } from "react";
import {
  Alert,
  Button,
  Filter,
  PageAside,
  PrimaryPanePublisher,
  ResourceViewProvider,
  useBreadcrumbLeafLabel,
  useChatter,
  useChatterContent,
  useResourceView,
  type ChatterContent,
} from "@angee/ui";
import { InboxConversation } from "./inbox/Conversation";
import { InboxMessageReader } from "./inbox/MessageReader";
import { InboxRelatedPane } from "./inbox/Related";
import { InboxNavigatorPane } from "./inbox/Navigator";
import { InboxResultsPane } from "./inbox/Results";
import { COVERAGE_FIELDS } from "./inbox/contract";
import {
  COLLECTION_INITIAL,
  RELATED_TAB,
  RESULTS_INITIAL,
  inboxQuery,
  useInboxNavigation,
} from "./inbox/state";
import { useNexusT } from "./i18n";

/** The route supplies ConsoleLayout; Nexus publishes its primary and Related panes. */
export function InboxPage() {
  const navigation = useInboxNavigation();
  return (
    <ResourceViewProvider
      namespace="c"
      favoriteKey={`nexus.inbox.results.${navigation.lens}`}
      initialState={RESULTS_INITIAL}
    >
      <InboxExplorer />
    </ResourceViewProvider>
  );
}

function InboxExplorer() {
  const t = useNexusT();
  useBreadcrumbLeafLabel(t("inbox.title"));
  const navigation = useInboxNavigation();
  const view = useResourceView();
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const { setActiveTab, setCollapsed } = useChatter();
  const primary = useMemo(
    () => (
      <ResourceViewProvider
        namespace="s"
        favoriteKey={`nexus.inbox.navigator.${navigation.finder}`}
        initialState={COLLECTION_INITIAL}
      >
        <InboxNavigatorPane
          coverage={view.state.filter}
          timezone={timezone}
          navigation={navigation}
        />
      </ResourceViewProvider>
    ),
    [view.state.filter, timezone, navigation],
  );
  const related = useMemo<ChatterContent>(
    () => ({
      tabs: [
        {
          id: RELATED_TAB,
          label: t("inbox.related"),
          icon: "link",
          panelClassName: "p-0",
          children: (
            <PageAside
              collapse="never"
              gutter="none"
              className="h-full w-full min-w-0 border-0 bg-sheet-2"
            >
              <ResourceViewProvider
                namespace="r"
                initialState={COLLECTION_INITIAL}
              >
                <InboxRelatedPane navigation={navigation} />
              </ResourceViewProvider>
            </PageAside>
          ),
        },
      ],
    }),
    [navigation, t],
  );
  useChatterContent(related);
  useEffect(() => {
    setActiveTab(RELATED_TAB);
    setCollapsed(!navigation.related);
  }, [navigation.related, setActiveTab, setCollapsed]);

  const coverageKey = JSON.stringify(
    Filter.from(view.state.filter).onlyFields(COVERAGE_FIELDS),
  );
  const previousCoverage = useRef(coverageKey);
  useEffect(() => {
    if (previousCoverage.current === coverageKey) return;
    previousCoverage.current = coverageKey;
    navigation.patch({ "s.page": undefined, "c.page": undefined }, true);
  }, [coverageKey, navigation]);

  const scope = useMemo(() => {
    try {
      return {
        value: {
          ...inboxQuery(view.state.filter, timezone),
          sender: navigation.sender,
          circle: navigation.circle,
        },
        error: null,
      };
    } catch (error) {
      return {
        value: null,
        error: error instanceof Error ? error : new Error(String(error)),
      };
    }
  }, [view.state.filter, timezone, navigation.sender, navigation.circle]);
  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col">
      <PrimaryPanePublisher node={primary} />
      {navigation.message ? (
        scope.value ? (
          <InboxMessageReader
            key={navigation.message}
            scope={scope.value}
            navigation={navigation}
          />
        ) : (
          <Alert tone="danger">
            {scope.error?.message}
            <Button onClick={navigation.results}>
              {t("inbox.backResults")}
            </Button>
          </Alert>
        )
      ) : navigation.thread ? (
        scope.value ? (
          <InboxConversation navigation={navigation} scope={scope.value} />
        ) : (
          <Alert tone="danger">
            {scope.error?.message}
            <Button onClick={navigation.results}>
              {t("inbox.backResults")}
            </Button>
          </Alert>
        )
      ) : (
        <InboxResultsPane
          navigation={navigation}
          timezone={scope.value?.coverage.timezone ?? timezone}
        />
      )}
    </div>
  );
}
