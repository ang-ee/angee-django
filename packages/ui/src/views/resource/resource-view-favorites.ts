import { favoriteFromResourceView } from "./model/favorites";
import {
  useCallback,
  useMemo,
} from "react";
import * as v from "valibot";
import {
  canonicalModelLabelOrNull,
  useSchemaFieldMetadata,
} from "@angee/metadata";

import {
  usePreferenceSlice,
  type RuntimeUserPreferences,
} from "../../runtime";
import {
  resourceViewFavoritesFromUnknown,
  type ResourceViewFavorite,
  type ResourceViewState,
} from "./resource-view-model";

export const RESOURCE_VIEW_FAVORITES_PREFERENCES_KEY =
  "resource-view.favorites";
export const RESOURCE_VIEW_FAVORITES_VERSION = 1;

const EMPTY_FAVORITES: readonly ResourceViewFavorite[] = [];

interface ResourceViewFavoritesPreferences {
  version: typeof RESOURCE_VIEW_FAVORITES_VERSION;
  models: Readonly<Record<string, readonly ResourceViewFavorite[]>>;
}

interface ResourceViewFavoritesSlice {
  document: ResourceViewFavoritesPreferences;
  writable: boolean;
}

const EMPTY_FAVORITES_DOCUMENT: ResourceViewFavoritesPreferences = {
  version: RESOURCE_VIEW_FAVORITES_VERSION,
  models: {},
};
const EMPTY_FAVORITES_SLICE: ResourceViewFavoritesSlice = {
  document: EMPTY_FAVORITES_DOCUMENT,
  writable: true,
};
const LOCKED_FAVORITES_SLICE: ResourceViewFavoritesSlice = {
  document: EMPTY_FAVORITES_DOCUMENT,
  writable: false,
};

const FavoritesVersionEnvelopeSchema = v.object({
  version: v.optional(v.unknown()),
});

const ResourceViewFavoritesPreferencesSchema = v.object({
  version: v.literal(RESOURCE_VIEW_FAVORITES_VERSION),
  models: v.record(v.string(), v.unknown()),
});

export interface ResourceViewFavoritesState {
  savedFavorites: readonly ResourceViewFavorite[];
  saveFavorite?: (label: string) => void;
}

/** Server-backed resource-view favorites over the runtime preference contract. */
export function useResourceViewFavorites(
  modelSpelling: string | undefined,
  state: ResourceViewState,
  collectionKey?: string,
): ResourceViewFavoritesState {
  const metadata = useSchemaFieldMetadata();
  const canonicalModel = useMemo(
    () =>
      collectionKey
        ? `collection:${collectionKey}`
        : modelSpelling
          ? canonicalModelLabelOrNull(
              metadata.resources ?? [],
              modelSpelling,
              "resource-view favorites",
            )
          : null,
    [metadata, modelSpelling, collectionKey],
  );
  const {
    available,
    value: favoritesSlice,
    update: updateFavorites,
  } = usePreferenceSlice(
    RESOURCE_VIEW_FAVORITES_PREFERENCES_KEY,
    readResourceViewFavoritesSlice,
    writeResourceViewFavoritesSlice,
  );
  const savedFavorites = canonicalModel
    ? favoritesSlice.document.models[canonicalModel] ?? EMPTY_FAVORITES
    : EMPTY_FAVORITES;
  const writable =
    available && favoritesSlice.writable && canonicalModel !== null;

  const saveFavorite = useCallback(
    (label: string) => {
      const trimmed = label.trim();
      if (!writable || !canonicalModel || !trimmed) return;
      const favorite = favoriteFromResourceView(state, trimmed, savedFavorites);
      void updateFavorites((current) =>
        appendResourceViewFavorite(current, canonicalModel, favorite),
      ).catch(() => undefined);
    },
    [canonicalModel, savedFavorites, state, updateFavorites, writable],
  );

  if (!writable) return { savedFavorites: EMPTY_FAVORITES };
  return { savedFavorites, saveFavorite };
}

function appendResourceViewFavorite(
  current: ResourceViewFavoritesSlice,
  modelLabel: string,
  favorite: ResourceViewFavorite,
): ResourceViewFavoritesSlice {
  if (!current.writable) return current;
  const models = { ...current.document.models };
  models[modelLabel] = mergeFavorites(models[modelLabel], [favorite]);
  return {
    writable: true,
    document: {
      version: RESOURCE_VIEW_FAVORITES_VERSION,
      models,
    },
  };
}

/**
 * The stored favorites document, discriminated by version: an unknown version
 * reads empty and write-unavailable (never overwrite a future document); a
 * version-matched document reads writable with malformed favorites dropped
 * per model.
 */
export function readResourceViewFavoritesSlice(
  preferences: RuntimeUserPreferences,
): ResourceViewFavoritesSlice {
  const raw = preferences[RESOURCE_VIEW_FAVORITES_PREFERENCES_KEY];
  if (raw === undefined) return EMPTY_FAVORITES_SLICE;
  const envelope = v.safeParse(FavoritesVersionEnvelopeSchema, raw);
  if (
    envelope.success
    && envelope.output.version !== undefined
    && envelope.output.version !== RESOURCE_VIEW_FAVORITES_VERSION
  ) {
    return LOCKED_FAVORITES_SLICE;
  }
  const document = v.safeParse(ResourceViewFavoritesPreferencesSchema, raw);
  if (!document.success) return EMPTY_FAVORITES_SLICE;
  // A version-matched document never loses siblings to one bad entry: each
  // model's list drops only its malformed favorites, so the next save cannot wipe healthy models.
  const models: Record<string, readonly ResourceViewFavorite[]> = {};
  for (const [model, favorites] of Object.entries(document.output.models)) {
    const kept = resourceViewFavoritesFromUnknown(favorites);
    if (kept.length > 0) models[model] = kept;
  }
  return {
    writable: true,
    document: { version: RESOURCE_VIEW_FAVORITES_VERSION, models },
  };
}

function writeResourceViewFavoritesSlice(
  preferences: RuntimeUserPreferences,
  slice: ResourceViewFavoritesSlice,
): RuntimeUserPreferences {
  if (!slice.writable) return preferences;
  return {
    ...preferences,
    [RESOURCE_VIEW_FAVORITES_PREFERENCES_KEY]: slice.document,
  };
}

function mergeFavorites(
  left: readonly ResourceViewFavorite[] | undefined,
  right: readonly ResourceViewFavorite[],
): readonly ResourceViewFavorite[] {
  const merged = new Map<string, ResourceViewFavorite>();
  for (const favorite of [...(left ?? []), ...right]) {
    if (!merged.has(favorite.id)) merged.set(favorite.id, favorite);
  }
  return [...merged.values()];
}
