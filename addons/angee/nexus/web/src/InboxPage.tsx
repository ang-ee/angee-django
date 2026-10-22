import { useCallback, useEffect, useMemo, useRef } from "react";
import {
  Button,
  ErrorBanner,
  Filter,
  PrimaryPanePublisher,
  ResourceViewProvider,
  localTimeZone,
  useBreadcrumbLeafLabel,
  useChatter,
  useChatterContent,
  useLatestRef,
  useResourceView,
  type ChatterContent,
  type ResourceViewFilter,
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
  const timezone = localTimeZone();
  const { setActiveTab, setCollapsed } = useChatter();
  // Stable across every unrelated `c`-view change so the published navigator node
  // only republishes when coverage actually changes (shell-publisher pitfall).
  const viewRef = useLatestRef(view);
  const onCoverage = useCallback(
    (next: ResourceViewFilter) => viewRef.current.setFilter(next),
    [viewRef],
  );
  const primary = useMemo(
    () => (
      <ResourceViewProvider
        namespace="s"
        favoriteKey={`nexus.inbox.navigator.${navigation.finder}`}
        initialState={COLLECTION_INITIAL}
      >
        <InboxNavigatorPane
          coverage={view.state.filter}
          onCoverage={onCoverage}
          timezone={timezone}
          navigation={navigation}
        />
      </ResourceViewProvider>
    ),
    [view.state.filter, onCoverage, timezone, navigation],
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
            <ResourceViewProvider
              namespace="r"
              initialState={COLLECTION_INITIAL}
            >
              <InboxRelatedPane navigation={navigation} />
            </ResourceViewProvider>
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
      {scope.error ? (
        <ScopeError error={scope.error} onBack={navigation.results} />
      ) : navigation.message && scope.value ? (
        <InboxMessageReader
          key={navigation.message}
          scope={scope.value}
          navigation={navigation}
        />
      ) : navigation.thread && scope.value ? (
        <InboxConversation navigation={navigation} scope={scope.value} />
      ) : (
        <InboxResultsPane
          navigation={navigation}
          timezone={scope.value?.coverage.timezone ?? timezone}
        />
      )}
    </div>
  );
}

/** A coverage the server cannot read: the error, and the way back to plain results. */
function ScopeError({ error, onBack }: { error: Error; onBack: () => void }) {
  const t = useNexusT();
  return (
    <div className="grid gap-3 p-4">
      <ErrorBanner title={t("inbox.scopeInvalid")} description={error.message} />
      <div><Button size="sm" variant="secondary" onClick={onBack}>{t("inbox.backResults")}</Button></div>
    </div>
  );
}
